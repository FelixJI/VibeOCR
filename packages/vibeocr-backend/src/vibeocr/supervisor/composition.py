"""Composition root: assemble the SupervisorModule with its adapters.

In Phase 2 the only executor is a fake used by tests. Phase 4/5/6 will plug
the real Paddle/MinerU/PDF adapters here without changing the module or app
shape. The composition root is also where ``stager_root`` is chosen (a
session-scoped temp directory under the OS temp or a portable location).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bootstrap import BootstrapHandle, generate_session_token, new_instance_id
from .module import Executor, SupervisorModule, SupervisorOptions

if TYPE_CHECKING:
    from vibeocr.protocol.v2 import ResidencyStatus


class _NullExecutor:
    """Default no-op executor used until real adapters are plugged.

    Real Phase 4/5/6 work replaces this; keeping a null default lets the
    bootstrap/app smoke tests run without OCR dependencies.
    """

    def execute(self, record, staged) -> None:  # type: ignore[no-untyped-def]
        # Immediately mark the job failed with a typed error: no backend.
        from vibeocr.protocol.v2 import JobState

        if record.state not in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
            try:
                record.transition(JobState.FAILED)
                record.append_event("no_backend", detail={"reason": "null-executor"})
            except Exception:  # pragma: no cover - defensive
                pass

    def cancel_mode_for(self, record) -> str:  # type: ignore[no-untyped-def]
        from vibeocr.protocol.v2 import CancelMode

        return CancelMode.COOPERATIVE

    def residency_status(self) -> ResidencyStatus:
        from vibeocr.protocol.v2 import ResidencyStatus

        return ResidencyStatus()

    def release_idle(self, pipeline: str | None = None) -> ResidencyStatus:
        from vibeocr.protocol.v2 import ResidencyStatus

        return ResidencyStatus()


def _build_paddle_executor() -> Executor:
    """Construct a real PaddleExecutor backed by the singleton OCRService.

    The heavy model load is deferred: OCRService is a lazy singleton, so the
    first ``recognize_many`` call (not import) pays the model-load cost.
    """
    from .inference.paddle_adapter import PaddlePipelineAdapter
    from .inference.paddle_executor import PaddleExecutor

    def factory() -> PaddlePipelineAdapter:
        # Imported lazily so importing composition.py never pulls paddle.
        from vibeocr.services.ocr_service import OCRService

        return PaddlePipelineAdapter(service=OCRService())

    return PaddleExecutor(adapter_factory=factory)


def _paddle_available() -> bool:
    """Return True if a real Paddle backend is importable in this environment."""
    try:
        import paddle  # noqa: F401
    except Exception:
        return False
    return True


def _build_pdf_adapter() -> Any:
    """Construct a PdfProcessAdapter backed by the legacy PdfBackendClient.

    The child factory returns ``PdfBackendClient.instance()`` so the supervisor
    reuses the existing FastAPI PDF child process (``pdf_backend_process.py``)
    rather than reimplementing PyMuPDF. The supervisor becomes the sole owner
    of that child; the GUI no longer holds a ``PdfBackendClient`` reference.
    Import is lazy: the backend client pulls httpx + the process launcher, so
    we defer it to first use (see ``PdfProcessAdapter.ensure_started``).
    """
    from .pdf.adapter import PdfProcessAdapter

    def factory() -> Any:
        from vibeocr.services.pdf_backend_client import PdfBackendClient

        return PdfBackendClient.instance()

    return PdfProcessAdapter(child_factory=factory)


def build_supervisor(
    *,
    instance_id: str | None = None,
    stager_root: Path | None = None,
    executor: Executor | None = None,
    options: SupervisorOptions | None = None,
    bootstrap_handle: BootstrapHandle | None = None,
    use_real_paddle: bool | None = None,
    with_pdf_adapter: bool = False,
) -> tuple[SupervisorModule, BootstrapHandle]:
    """Assemble a supervisor module + bootstrap handle (token out of band).

    When ``use_real_paddle`` is True (or left as None and a Paddle backend is
    importable), the supervisor is wired with a real
    :class:`~vibeocr.supervisor.inference.paddle_executor.PaddleExecutor`
    backed by the singleton :class:`~vibeocr.services.ocr_service.OCRService`,
    so recognition jobs actually run Paddle OCR. Otherwise (or in lightweight
    test environments without paddle) the null executor is used so the job
    engine stays importable and unit-testable without model dependencies.

    When ``with_pdf_adapter`` is True, the module owns a
    :class:`~vibeocr.supervisor.pdf.adapter.PdfProcessAdapter` whose
    ``child_factory`` returns the legacy
    :class:`~vibeocr.services.pdf_backend_client.PdfBackendClient` singleton.
    The v2 PDF session routes then proxy through it instead of the GUI holding
    the client directly (plan §6 / ADR §"Transport"). The PDF child subprocess
    is spawned lazily on first ``open_session``; no cost at import.
    """
    iid = instance_id or new_instance_id()
    opts = options or SupervisorOptions(instance_id=iid)
    root = stager_root or Path(tempfile.mkdtemp(prefix=f"vibeocr-sup-{iid}-"))
    if executor is not None:
        exec_impl = executor
    elif use_real_paddle is True or (use_real_paddle is None and _paddle_available()):
        exec_impl = _build_paddle_executor()
    else:
        exec_impl = _NullExecutor()
    pdf_adapter = _build_pdf_adapter() if with_pdf_adapter else None
    module = SupervisorModule(
        options=opts,
        stager_root=root,
        executor=exec_impl,
        pdf_adapter=pdf_adapter,
    )
    # Clean stale staging left by a previous crashed instance (plan Phase 2).
    # At startup no jobs are known yet, so every existing dir is stale.
    module.stager.cleanup_stale(set())
    handle = bootstrap_handle or BootstrapHandle()
    # Always generate a new token unless the handle already has one set.
    # BootstrapHandle.token raises if unset, so we use a safe check.
    try:
        _ = handle.token  # type: ignore[attr-defined]
    except RuntimeError:
        handle.set_token(generate_session_token())
    return module, handle


__all__ = ["build_supervisor"]
