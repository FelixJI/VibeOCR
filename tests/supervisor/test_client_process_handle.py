"""Tests for the client-side JobHandle, process ready envelope and contracts re-export."""

from __future__ import annotations

import json

import pytest

from vibeocr.protocol.v2 import (
    JobKind,
    JobPriority,
    JobRef,
    JobSnapshot,
    JobState,
    JobSummary,
    StageEvent,
)
from vibeocr.supervisor.client import SupervisorClient
from vibeocr.supervisor.contracts import (
    CancelMode,
    parse_job_snapshot,
)
from vibeocr.supervisor.contracts import (
    JobSnapshot as CSnapshot,
)
from vibeocr.supervisor.job_handle import JobHandle
from vibeocr.supervisor.process import (
    ReadyEnvelope,
    SupervisorLaunchError,
    generate_token,
)

# ---------------------------------------------------------------------------
# ReadyEnvelope parsing
# ---------------------------------------------------------------------------


def test_ready_envelope_from_line_roundtrip() -> None:
    line = json.dumps(
        {
            "ready": True,
            "pid": 4321,
            "port": 5432,
            "instance_id": "sup-abc",
            "protocol_version": 2,
            "schema_version": 2,
            "capabilities": ["recognition", "pdf_ocr"],
        }
    )
    env = ReadyEnvelope.from_line(line)
    assert env.ready is True
    assert env.port == 5432
    assert env.protocol_version == 2
    assert env.capabilities == ("recognition", "pdf_ocr")
    assert env.base_url == "http://127.0.0.1:5432"


def test_ready_envelope_rejects_malformed_line() -> None:
    import json

    with pytest.raises(json.JSONDecodeError):
        ReadyEnvelope.from_line("not json")


def test_generate_token_is_unique() -> None:
    assert generate_token() != generate_token()


# ---------------------------------------------------------------------------
# Contracts re-export module is importable and aliases work
# ---------------------------------------------------------------------------


def test_contracts_reexport_aliases_match_v2() -> None:
    # The client contracts module re-exports the v2 DTOs.
    assert CancelMode is not None
    from vibeocr.protocol.v2 import CancelMode as V2CancelMode

    assert CancelMode is V2CancelMode
    assert CSnapshot is JobSnapshot


def test_contracts_parse_job_snapshot_works() -> None:
    snap = JobSnapshot(
        job_id="x",
        kind=JobKind.RECOGNITION,
        priority=JobPriority.INTERACTIVE,
        state=JobState.ACCEPTED,
    )
    parsed = parse_job_snapshot(snap.to_payload())
    assert parsed.job_id == "x"


# ---------------------------------------------------------------------------
# JobHandle polling/streaming against a fake client
# ---------------------------------------------------------------------------


class _FakeClient(SupervisorClient):
    """A SupervisorClient that doesn't open a real http connection."""

    def __init__(self) -> None:
        # Bypass __init__'s loopback/base-url validation.
        object.__setattr__(self, "_base_url", "http://127.0.0.1")
        object.__setattr__(self, "_token", "t")
        object.__setattr__(self, "instance_id", "test")
        object.__setattr__(self, "_client", None)
        self._snapshots: list[JobSnapshot] = []
        self._events: list[list[StageEvent]] = []
        self._cancel_mode = None
        self._cancel_calls = 0

    def queue_snapshots(self, *snaps: JobSnapshot) -> None:
        self._snapshots = list(snaps)

    def queue_events(self, *event_lists: list[StageEvent]) -> None:
        self._events = list(event_lists)

    async def status(self, job_id: str) -> JobSnapshot:  # type: ignore[override]
        if not self._snapshots:
            raise AssertionError("no snapshots queued")
        return self._snapshots.pop(0)

    async def events(self, job_id: str, *, after_sequence: int = 0) -> list[StageEvent]:  # type: ignore[override]
        if not self._events:
            return []
        return self._events.pop(0)

    async def cancel(self, job_id: str):  # type: ignore[override]
        self._cancel_calls += 1
        from vibeocr.protocol.v2 import CancelMode

        return CancelMode.COOPERATIVE


@pytest.mark.asyncio
async def test_job_handle_wait_for_terminal_polls() -> None:
    fake = _FakeClient()
    fake.queue_snapshots(
        JobSnapshot(
            job_id="j",
            kind=JobKind.RECOGNITION,
            priority=JobPriority.INTERACTIVE,
            state=JobState.RUNNING,
        ),
        JobSnapshot(
            job_id="j",
            kind=JobKind.RECOGNITION,
            priority=JobPriority.INTERACTIVE,
            state=JobState.COMPLETED,
            summary=JobSummary(succeeded=1, total=1),
        ),
    )
    fake.queue_events([], [])
    handle = JobHandle(
        client=fake,
        ref=JobRef(job_id="j"),
    )
    snap = await handle.wait_for_terminal(timeout=2.0)
    assert snap.state is JobState.COMPLETED


@pytest.mark.asyncio
async def test_job_handle_stream_events_until_terminal() -> None:
    fake = _FakeClient()
    # Two polls: first returns an event + running, second returns no events + completed.
    fake.queue_snapshots(
        JobSnapshot(
            job_id="j",
            kind=JobKind.RECOGNITION,
            priority=JobPriority.INTERACTIVE,
            state=JobState.RUNNING,
        ),
        JobSnapshot(
            job_id="j",
            kind=JobKind.RECOGNITION,
            priority=JobPriority.INTERACTIVE,
            state=JobState.COMPLETED,
        ),
    )
    fake.queue_events(
        [StageEvent(sequence=1, stage="running", item_id=None)],
        [],
    )
    handle = JobHandle(client=fake, ref=JobRef(job_id="j"))
    collected: list[str] = []
    async for event in handle.stream_events():
        collected.append(event.stage)
    assert collected == ["running"]


@pytest.mark.asyncio
async def test_job_handle_cancel_delegates_to_client() -> None:
    fake = _FakeClient()
    handle = JobHandle(client=fake, ref=JobRef(job_id="j"))
    mode = await handle.cancel()
    assert mode.value == "cooperative"
    assert fake._cancel_calls == 1


# ---------------------------------------------------------------------------
# SupervisorProcess launch error path (no real subprocess)
# ---------------------------------------------------------------------------


def test_supervisor_process_not_launched_accessors_raise() -> None:
    from vibeocr.supervisor.process import SupervisorProcess

    proc = SupervisorProcess(python_exe="python")
    with pytest.raises(SupervisorLaunchError):
        _ = proc.ready
    with pytest.raises(SupervisorLaunchError):
        _ = proc.base_url
    with pytest.raises(SupervisorLaunchError):
        _ = proc.session_token
    # shutdown on an unlaunched proc is a no-op.
    assert proc.shutdown() == 0
