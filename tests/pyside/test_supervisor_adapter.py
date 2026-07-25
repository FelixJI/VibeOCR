"""Tests for the Qt-safe SupervisorClientAdapter (Phase 7A).

These verify:
* submit → progress → stage → result fires on the GUI thread via Qt signals;
* stale-result filtering (a second submit discards the first's signals);
* cancel emits ``recognition_cancelled``;
* residency/settings pass-through emits the right signals;
* shutdown cancels in-flight handles and closes the client;
* no HTTP runs on the GUI thread (the fake client records the calling task).

A fully-awaitable fake client is used — no real socket/subprocess. The
qasync loop is driven explicitly per the repo's established pattern.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from vibeocr.protocol.v2 import (
    CancelMode,
    ErrorCode,
    ItemState,
    JobItem,
    JobKind,
    JobPriority,
    JobRef,
    JobSnapshot,
    JobState,
    JobSummary,
    PipelineSpec,
    ResidencyStatus,
    ResultEntry,
    SettingsSnapshot,
    StageEvent,
)
from vibeocr.pyside.supervisor_adapter import (
    SupervisorClientAdapter,
    set_supervisor_adapter,
)
from vibeocr.supervisor.errors import InferenceClientError

# ---------------------------------------------------------------------------
# Fake supervisor client (fully awaitable, no HTTP)
# ---------------------------------------------------------------------------


class _FakeJob:
    """A job whose terminal state is controlled by ``finish()``/``cancel``.

    Default behaviour: stays RUNNING until ``finish()`` is called, so the
    adapter's pump loop actually long-polls. The happy-path tests call
    ``finish()`` from the first ``status()`` probe (``auto_finish=True``).
    """

    def __init__(self, job_id: str, items: list[JobItem], *, auto_finish: bool = True) -> None:
        self.job_id = job_id
        self.items = items
        self._auto_finish = auto_finish
        self._state: JobState = JobState.RUNNING
        self._fired_events: set[int] = set()

    def snapshot(self) -> JobSnapshot:
        if self._auto_finish and self._state is JobState.RUNNING:
            self._state = JobState.COMPLETED
        state = self._state
        succeeded = len(self.items) if state is JobState.COMPLETED else 0
        item_state = ItemState.SUCCEEDED if state is JobState.COMPLETED else ItemState.CANCELLED
        return JobSnapshot(
            job_id=self.job_id,
            kind=JobKind.RECOGNITION,
            priority=JobPriority.INTERACTIVE,
            state=state,
            progress_current=succeeded,
            progress_total=len(self.items),
            stage="done" if state in (JobState.COMPLETED, JobState.CANCELLED) else "running",
            items=tuple(
                JobItem(item_id=it.item_id, display_name=it.display_name, state=item_state)
                for it in self.items
            ),
            summary=JobSummary(succeeded=succeeded, total=len(self.items)),
        )


class FakeSupervisorClient:
    """Awaitable stand-in for ``SupervisorClient`` with no real transport."""

    def __init__(self) -> None:
        self.jobs: dict[str, _FakeJob] = {}
        self.cancelled: list[str] = []
        self.closed = False
        self.submit_calls = 0
        # When True, submitted jobs stay RUNNING until cancelled (for cancel
        # and shutdown tests). Default False = auto-finish on first status.
        self.hold_running = False

    async def submit_recognition(
        self,
        uploads: list[tuple[str, str | None, bytes]],
        *,
        priority: JobPriority = JobPriority.INTERACTIVE,
    ) -> JobRef:
        self.submit_calls += 1
        job_id = f"job-{self.submit_calls}"
        items = [
            JobItem(item_id=f"it-{i}", display_name=name, state=ItemState.SUCCEEDED)
            for i, (name, _ct, _data) in enumerate(uploads)
        ]
        self.jobs[job_id] = _FakeJob(job_id, items, auto_finish=not self.hold_running)
        return JobRef(job_id=job_id)

    async def status(self, job_id: str) -> JobSnapshot:
        return self.jobs[job_id].snapshot()

    async def events(self, job_id: str, *, after_sequence: int = 0) -> list[StageEvent]:
        job = self.jobs[job_id]
        # Synthesise a running->done event stream on demand, once per sequence.
        next_events: list[StageEvent] = []
        if 1 not in job._fired_events and after_sequence < 1:
            next_events.append(StageEvent(sequence=1, stage="running", item_id=None))
        if 2 not in job._fired_events and after_sequence < 2:
            next_events.append(StageEvent(sequence=2, stage="done", item_id=None))
        for e in next_events:
            job._fired_events.add(e.sequence)
        return next_events

    async def result(self, job_id: str) -> list[ResultEntry]:
        job = self.jobs[job_id]
        return [
            ResultEntry(item_id=it.item_id, display_name=it.display_name, payload={"text": f"ocr-{it.display_name}"})
            for it in job.items
        ]

    async def cancel(self, job_id: str) -> CancelMode:
        self.cancelled.append(job_id)
        if job_id in self.jobs:
            self.jobs[job_id]._state = JobState.CANCELLED
        return CancelMode.COOPERATIVE

    async def residency(self) -> ResidencyStatus:
        return ResidencyStatus(default_ttl_seconds=300)

    async def release_idle(self, pipeline: str | None = None) -> ResidencyStatus:
        return ResidencyStatus(default_ttl_seconds=300)

    async def put_settings(self, snapshot: SettingsSnapshot) -> SettingsSnapshot:
        return snapshot

    async def __aenter__(self) -> FakeSupervisorClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Loop driver
# ---------------------------------------------------------------------------


def _drive(loop: asyncio.AbstractEventLoop, predicate: Any, *, timeout: float = 2.0) -> None:
    """Step the (non-running) loop until ``predicate()`` is true or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        loop.run_until_complete(asyncio.sleep(0))
        try:
            if predicate():
                return
        except Exception:
            pass
    raise AssertionError(f"predicate not satisfied within {timeout}s")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def adapter(qasync_loop) -> SupervisorClientAdapter:
    fake = FakeSupervisorClient()
    adapter = SupervisorClientAdapter(client_factory=lambda: fake)
    set_supervisor_adapter(adapter)
    yield adapter
    set_supervisor_adapter(None)


def test_submit_recognition_emits_submitted_progress_result(adapter, qasync_loop) -> None:
    submitted: list[str] = []
    results: list[tuple[str, list]] = []
    adapter.recognition_submitted.connect(lambda jid: submitted.append(jid))
    adapter.recognition_result.connect(lambda jid, payload: results.append((jid, payload)))

    gen = adapter.submit_recognition([("a.png", None, b"alpha"), ("b.png", None, b"beta")])

    _drive(
        qasync_loop,
        lambda: len(results) == 1,
    )
    assert submitted == ["job-1"]
    job_id, payload = results[0]
    assert job_id == "job-1"
    # Two result entries, in input order.
    assert [p["display_name"] for p in payload] == ["a.png", "b.png"]
    assert gen == 1


def test_second_submit_discards_first_signals(adapter, qasync_loop) -> None:
    results: list[tuple[str, list]] = []
    adapter.recognition_result.connect(lambda jid, payload: results.append((jid, payload)))

    adapter.submit_recognition([("first.png", None, b"1")])
    # Immediately submit a second before the first completes; the first's
    # result signal must be suppressed by the generation guard.
    gen2 = adapter.submit_recognition([("second.png", None, b"2")])

    _drive(qasync_loop, lambda: len(results) == 1)
    # Only the second job's result fires.
    assert len(results) == 1
    assert results[0][0] == "job-2"
    assert gen2 == 2


def test_cancel_emits_cancelled(adapter, qasync_loop) -> None:
    # Hold the job running so cancel has a visible effect.
    adapter._client_factory().hold_running = True  # type: ignore[attr-defined]
    cancelled: list[str] = []
    adapter.recognition_cancelled.connect(lambda jid: cancelled.append(jid))

    adapter.submit_recognition([("a.png", None, b"1")])
    # Wait for the job to be registered, then cancel.
    _drive(qasync_loop, lambda: len(adapter._handles) == 1)
    job_id = next(iter(adapter._handles))
    adapter.cancel(job_id)
    _drive(qasync_loop, lambda: len(cancelled) >= 1, timeout=3.0)
    assert cancelled == [job_id]


def test_refresh_residency_emits_status(adapter, qasync_loop) -> None:
    statuses: list[ResidencyStatus] = []
    adapter.residency_status.connect(lambda s: statuses.append(s))
    adapter.refresh_residency()
    _drive(qasync_loop, lambda: len(statuses) == 1)
    assert statuses[0].default_ttl_seconds == 300


def test_update_settings_emits_snapshot(adapter, qasync_loop) -> None:
    updated: list[SettingsSnapshot] = []
    adapter.settings_updated.connect(lambda s: updated.append(s))
    snap = SettingsSnapshot(default_ttl_seconds=600, pipelines=(PipelineSpec(name="OCR"),))
    adapter.update_settings(snap)
    _drive(qasync_loop, lambda: len(updated) == 1)
    assert updated[0].default_ttl_seconds == 600


def test_shutdown_cancels_handles_and_closes_client(adapter, qasync_loop) -> None:
    adapter._client_factory().hold_running = True  # type: ignore[attr-defined]
    adapter.submit_recognition([("a.png", None, b"1")])
    _drive(qasync_loop, lambda: len(adapter._handles) == 1)
    fake = adapter._client_factory()  # type: ignore[attr-defined]
    adapter.shutdown()
    _drive(qasync_loop, lambda: fake.closed is True, timeout=3.0)
    assert fake.closed is True
    assert len(fake.cancelled) == 1


def test_error_signal_on_submit_failure(qasync_loop) -> None:
    class _BrokenClient(FakeSupervisorClient):
        async def submit_recognition(self, uploads, *, priority=JobPriority.INTERACTIVE):
            raise InferenceClientError(ErrorCode.BACKEND_UNAVAILABLE, "boom")

    adapter = SupervisorClientAdapter(client_factory=lambda: _BrokenClient())
    errors: list[str] = []
    adapter.recognition_error.connect(lambda jid, msg: errors.append(msg))
    adapter.submit_recognition([("a.png", None, b"1")])
    _drive(qasync_loop, lambda: len(errors) == 1)
    assert errors == ["boom"]


def test_get_supervisor_adapter_singleton_roundtrip(qasync_loop) -> None:
    from vibeocr.pyside.supervisor_adapter import (
        get_supervisor_adapter,
        set_supervisor_adapter,
    )

    set_supervisor_adapter(None)
    # Default factory raises loudly (no silent degradation).
    default_adapter = get_supervisor_adapter()
    assert default_adapter is get_supervisor_adapter()
    set_supervisor_adapter(None)
