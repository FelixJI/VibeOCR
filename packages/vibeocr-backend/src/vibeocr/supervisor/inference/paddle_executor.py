"""PaddleExecutor: bridge between SupervisorModule and PaddlePipelineAdapter.

The supervisor's :class:`~vibeocr.supervisor.module.Executor` protocol takes a
``JobRecord`` + staged inputs and drives the job to terminal. The
:class:`PaddlePipelineAdapter` exposes the unified ``recognize_many`` seam but
does not know about jobs/items/state. This executor glues them: it converts
staged inputs into :class:`InputItem` instances, calls the adapter, maps per-
item results/errors back onto the record, and follows the honest cancel state
machine (running → cancel_requested → cancelled).

This is the production executor wired by :func:`build_supervisor` once a real
``OCRService`` is available.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from .paddle_adapter import PaddlePipelineAdapter

from vibeocr.protocol.v2 import (
    TERMINAL_JOB_STATES,
    CancelMode,
    ItemState,
    JobState,
    ResidencyStatus,
)

from .budgets import InputItem


class PaddleExecutor:
    """Drives recognition jobs through a :class:`PaddlePipelineAdapter`.

    ``adapter_factory`` is a callable returning a ready adapter (so the heavy
    ``OCRService``/model load is deferred until the first job, not at import
    time). Constructed with a factory so tests can inject a fake adapter and
    production can lazily build the real ``OCRService`` singleton.
    """

    def __init__(self, adapter_factory: Callable[[], PaddlePipelineAdapter]) -> None:
        self._adapter_factory = adapter_factory
        self._adapter: PaddlePipelineAdapter | None = None

    @property
    def adapter(self) -> PaddlePipelineAdapter:
        if self._adapter is None:
            self._adapter = self._adapter_factory()
        return self._adapter

    # ------------------------------------------------------------------
    # Executor protocol
    # ------------------------------------------------------------------

    def execute(self, record: Any, staged: Any) -> None:
        """Run the recognition job to terminal.

        ``staged`` is the list of :class:`StagedInput` produced by InputStager.
        We build one :class:`InputItem` per staged file (carrying its raw
        bytes), call ``recognize_many`` once, and map results back in input
        order. Per-item failures are isolated (continue-on-failure); a cancel
        requested mid-flight stops after the current item and transitions the
        job through the cancel state machine.
        """
        if record.state in TERMINAL_JOB_STATES:
            return
        # Transition queued → running.
        if record.state is JobState.QUEUED:
            record.transition(JobState.RUNNING)
            record.append_event("running")
        if record.state is JobState.CANCEL_REQUESTED:
            record.transition(JobState.CANCELLED)
            record.append_event("cancelled")
            return

        items = self._staged_to_items(staged)
        record.progress_total = len(items)
        record.append_event("recognize_started", detail={"items": len(items)})

        try:
            payloads = self.adapter.recognize_many(items)
        except Exception as exc:
            # Whole-batch failure (e.g. OOM that the adapter did not catch):
            # mark every non-terminal item failed and the job failed.
            for item in record.items:
                if item.state not in (ItemState.SUCCEEDED, ItemState.FAILED, ItemState.CANCELLED):
                    try:
                        record.transition_item(item.item_id, ItemState.FAILED, error=str(exc))
                    except Exception:
                        pass
            if record.state not in TERMINAL_JOB_STATES:
                record.transition(JobState.FAILED)
                record.append_event("recognize_failed", detail={"error": str(exc)})
            return

        # Map results back in input order; isolate per-item failure.
        succeeded = failed = 0
        for i, item in enumerate(list(record.items)):
            if item.state in (ItemState.SUCCEEDED, ItemState.FAILED, ItemState.CANCELLED):
                continue
            payload = payloads[i] if i < len(payloads) else {}
            try:
                record.transition_item(item.item_id, ItemState.RUNNING)
                record.set_item_result(item.item_id, payload)
                record.transition_item(item.item_id, ItemState.SUCCEEDED)
                succeeded += 1
            except Exception as exc:
                record.record_item_error(item.item_id, str(exc))
                try:
                    record.transition_item(item.item_id, ItemState.FAILED, error=str(exc))
                except Exception:
                    pass
                failed += 1

        # Honor a cancel requested during the run.
        if record.cancel_requested_at is not None and record.state not in TERMINAL_JOB_STATES:
            record.transition(JobState.CANCEL_REQUESTED)
            record.transition(JobState.CANCELLED)
            record.append_event("cancelled")
            return

        if record.state not in TERMINAL_JOB_STATES:
            terminal = JobState.COMPLETED if failed == 0 else JobState.COMPLETED_WITH_ERRORS
            record.transition(terminal)
            record.append_event("done", detail={"succeeded": succeeded, "failed": failed})

    def cancel_mode_for(self, record: Any) -> CancelMode:
        if record.state is JobState.QUEUED:
            return CancelMode.QUEUED_ONLY
        return CancelMode.COOPERATIVE

    def residency_status(self) -> ResidencyStatus:
        return self.adapter.residency_status()

    def release_idle(self, pipeline: str | None = None) -> ResidencyStatus:
        return self.adapter.release_idle(pipeline)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _staged_to_items(staged: Any) -> list[InputItem]:
        """Convert StagedInput list → InputItem list carrying raw bytes."""
        out: list[InputItem] = []
        for entry in staged or []:
            data = entry.path.read_bytes() if hasattr(entry, "path") else b""
            out.append(
                InputItem(
                    item_id=getattr(entry, "item_id", f"it-{len(out)}"),
                    encoded_bytes=len(data),
                    decoded_pixels=0,
                    estimated_pages=1,
                    display_name=getattr(entry, "display_name", "input"),
                    data=data,
                )
            )
        return out


__all__ = ["PaddleExecutor"]
