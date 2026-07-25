"""Shared fake "HTTP server" for the Phase 7A client contract suite.

This module is imported by both the PySide adapter contract tests and the
raw-Python-client contract test so both consumers exercise the *same* fake
server shape — that is the plan's §7A exit criterion ("两套 UI 的 client
contract tests 对同一 fake HTTP server 全绿"). A future WinUI mirror will
reuse the same payload contract.

The fake is a plain awaitable object implementing the
:class:`vibeocr.supervisor.client.SupervisorClient` surface — no real socket
or subprocess. It models a supervisor that auto-completes every submitted
job and records calls for assertions.
"""

from __future__ import annotations

from typing import Any

from vibeocr.protocol.v2 import (
    CancelMode,
    ItemState,
    JobItem,
    JobKind,
    JobPriority,
    JobRef,
    JobSnapshot,
    JobState,
    JobSummary,
    ResidencyStatus,
    ResultEntry,
    SettingsSnapshot,
    StageEvent,
)


class SharedFakeJob:
    def __init__(self, job_id: str, uploads: list[tuple[str, str | None, bytes]]) -> None:
        self.job_id = job_id
        self.display_names = [name for name, _ct, _data in uploads]
        self._status_calls = 0
        self._cancelled = False
        self._fired_events: set[int] = set()

    def snapshot(self) -> JobSnapshot:
        self._status_calls += 1
        # Stay RUNNING for the first couple of probes so cancel has a window
        # to land before the job auto-completes; after that, terminal.
        if self._status_calls <= 2 and not self._cancelled:
            state = JobState.RUNNING
        else:
            state = JobState.CANCELLED if self._cancelled else JobState.COMPLETED
        succeeded = 0 if state is JobState.RUNNING else (0 if self._cancelled else len(self.display_names))
        item_state = (
            ItemState.RUNNING
            if state is JobState.RUNNING
            else (ItemState.CANCELLED if self._cancelled else ItemState.SUCCEEDED)
        )
        return JobSnapshot(
            job_id=self.job_id,
            kind=JobKind.RECOGNITION,
            priority=JobPriority.INTERACTIVE,
            state=state,
            progress_current=succeeded,
            progress_total=len(self.display_names),
            stage="running" if state is JobState.RUNNING else "done",
            items=tuple(
                JobItem(item_id=f"it-{i}", display_name=n, state=item_state)
                for i, n in enumerate(self.display_names)
            ),
            summary=JobSummary(succeeded=succeeded, total=len(self.display_names)),
        )


class SharedFakeSupervisorServer:
    """The shared fake. Import once per test module; reset via ``reset()``."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.jobs: dict[str, SharedFakeJob] = {}
        self.submit_calls: int = 0
        self.cancel_calls: list[str] = []
        self.put_settings_calls: list[SettingsSnapshot] = []
        self.release_calls: list[str | None] = []
        self.closed = False

    # --- async context manager (matches SupervisorClient) ---
    async def __aenter__(self) -> SharedFakeSupervisorServer:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.closed = True

    # --- recognition surface ---
    async def submit_recognition(
        self,
        uploads: list[tuple[str, str | None, bytes]],
        *,
        priority: JobPriority = JobPriority.INTERACTIVE,
    ) -> JobRef:
        self.submit_calls += 1
        job_id = f"job-{self.submit_calls}"
        self.jobs[job_id] = SharedFakeJob(job_id, uploads)
        return JobRef(job_id=job_id)

    async def status(self, job_id: str) -> JobSnapshot:
        return self.jobs[job_id].snapshot()

    async def events(self, job_id: str, *, after_sequence: int = 0) -> list[StageEvent]:
        job = self.jobs[job_id]
        out: list[StageEvent] = []
        if 1 not in job._fired_events and after_sequence < 1:
            out.append(StageEvent(sequence=1, stage="running", item_id=None))
            job._fired_events.add(1)
        if 2 not in job._fired_events and after_sequence < 2:
            out.append(StageEvent(sequence=2, stage="done", item_id=None))
            job._fired_events.add(2)
        return out

    async def result(self, job_id: str) -> list[ResultEntry]:
        job = self.jobs[job_id]
        return [
            ResultEntry(item_id=f"it-{i}", display_name=n, payload={"text": f"ocr-{n}"})
            for i, n in enumerate(job.display_names)
        ]

    async def cancel(self, job_id: str) -> CancelMode:
        self.cancel_calls.append(job_id)
        if job_id in self.jobs:
            self.jobs[job_id]._cancelled = True
        return CancelMode.COOPERATIVE

    # --- runtime / settings ---
    async def residency(self) -> ResidencyStatus:
        return ResidencyStatus(default_ttl_seconds=300)

    async def release_idle(self, pipeline: str | None = None) -> ResidencyStatus:
        self.release_calls.append(pipeline)
        return ResidencyStatus(default_ttl_seconds=300)

    async def put_settings(self, snapshot: SettingsSnapshot) -> SettingsSnapshot:
        self.put_settings_calls.append(snapshot)
        return snapshot


# Module-level singleton: the "shared fake HTTP server".
SHARED_FAKE_SERVER = SharedFakeSupervisorServer()


def factory() -> SharedFakeSupervisorServer:
    """Return the shared fake server instance (resets state on first import)."""
    return SHARED_FAKE_SERVER


__all__ = ["SHARED_FAKE_SERVER", "SharedFakeSupervisorServer", "factory"]
