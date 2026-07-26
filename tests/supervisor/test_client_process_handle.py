"""Tests for the client-side JobHandle, process ready envelope and contracts re-export."""

from __future__ import annotations

import json

import pytest

from vibeocr.protocol.v2 import (
    CancelMode,
    JobCommand,
    JobCommandKind,
    JobKind,
    JobPriority,
    JobRef,
    JobSnapshot,
    JobState,
    JobSummary,
    JobUpdate,
    StageEvent,
)
from vibeocr.supervisor.client import SupervisorClient
from vibeocr.supervisor.contracts import (
    JobSnapshot as CSnapshot,
)
from vibeocr.supervisor.contracts import (
    parse_job_snapshot,
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
        self._cancel_calls = 0

    def queue_snapshots(self, *snaps: JobSnapshot) -> None:
        self._snapshots = list(snaps)

    def queue_events(self, *event_lists: list[StageEvent]) -> None:
        self._events = list(event_lists)

    async def observe(
        self, job_id: str, *, after_sequence: int = 0
    ) -> JobUpdate:
        if not self._snapshots:
            raise AssertionError("no snapshots queued")
        events = tuple(self._events.pop(0)) if self._events else ()
        through_sequence = max(
            (event.sequence for event in events),
            default=after_sequence,
        )
        return JobUpdate(
            snapshot=self._snapshots.pop(0),
            events=events,
            outcomes=(),
            through_sequence=through_sequence,
        )

    async def command(
        self, command: JobCommand
    ) -> JobRef | CancelMode | None:
        assert command.kind is JobCommandKind.CANCEL
        assert command.job_id == "j"
        self._cancel_calls += 1
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


def test_supervisor_process_binds_job_object_and_closes_it(monkeypatch) -> None:
    from io import StringIO
    from unittest.mock import Mock

    import vibeocr.supervisor.process as process_module

    guard = Mock()
    monkeypatch.setattr(process_module, "JobObjectGuard", lambda: guard)
    popen = Mock(pid=4321)
    popen.stdout = StringIO()
    popen.stderr = StringIO()
    popen.wait.return_value = 0
    popen_factory = Mock(return_value=popen)
    monkeypatch.setattr(process_module.subprocess, "Popen", popen_factory)
    monkeypatch.setattr(
        process_module.SupervisorProcess, "_read_ready", lambda self: None
    )

    proc = process_module.SupervisorProcess(python_exe="python")
    proc._start(None)

    assert popen_factory.call_args.kwargs["stdout"] is process_module.subprocess.PIPE
    assert popen_factory.call_args.kwargs["stderr"] is process_module.subprocess.PIPE
    assert popen_factory.call_args.kwargs["encoding"] == "utf-8"
    assert popen_factory.call_args.kwargs["errors"] == "replace"
    assert popen_factory.call_args.kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert popen_factory.call_args.kwargs["env"]["PYTHONUTF8"] == "1"
    guard.assign_from_popen.assert_called_once_with(popen)
    assert proc.shutdown() == 0
    popen.terminate.assert_called_once_with()
    guard.close.assert_called_once_with()

    # Idempotent shutdown must not close the same kernel handle twice.
    assert proc.shutdown() == 0
    guard.close.assert_called_once_with()


def test_supervisor_process_decodes_utf8_logs_independent_of_windows_locale(
    monkeypatch,
) -> None:
    """Popen 必须显式按 UTF-8 解码，不能继承 Windows 默认 GBK。"""
    from io import BytesIO, TextIOWrapper
    from unittest.mock import Mock

    import vibeocr.supervisor.process as process_module

    payload = "Supervisor 预加载完成\n".encode()
    popen = Mock(pid=4321)

    def popen_factory(*_args, **kwargs):
        encoding = kwargs.get("encoding") or "gbk"
        errors = kwargs.get("errors") or "strict"
        popen.stdout = TextIOWrapper(
            BytesIO(), encoding=encoding, errors=errors
        )
        popen.stderr = TextIOWrapper(
            BytesIO(payload), encoding=encoding, errors=errors
        )
        popen.wait.return_value = 0
        return popen

    class DormantThread:
        def start(self) -> None:
            return

    guard = Mock()
    monkeypatch.setattr(process_module, "JobObjectGuard", lambda: guard)
    monkeypatch.setattr(process_module.subprocess, "Popen", popen_factory)
    monkeypatch.setattr(
        process_module.threading,
        "Thread",
        lambda *_args, **_kwargs: DormantThread(),
    )
    monkeypatch.setattr(
        process_module.SupervisorProcess, "_read_ready", lambda self: None
    )

    proc = process_module.SupervisorProcess(python_exe="python")
    proc._start(None)
    assert popen.stderr is not None
    proc._drain_stream(popen.stderr)

    assert proc.log_lines == ["Supervisor 预加载完成"]
    proc.shutdown()


def test_supervisor_process_forwards_child_output_to_application_log(caplog) -> None:
    import logging
    from io import StringIO

    import vibeocr.supervisor.process as process_module

    proc = process_module.SupervisorProcess(python_exe="python")
    child_line = (
        "[Supervisor][Recognize] pipeline=OCR items=1 "
        "result=success elapsed_ms=12.3\n"
    )

    with caplog.at_level(logging.INFO, logger="vibeocr.supervisor.process"):
        proc._drain_stream(StringIO(child_line))

    assert proc.log_lines == [child_line.rstrip()]
    assert any(child_line.rstrip() in record.getMessage() for record in caplog.records)


def test_supervisor_process_discards_http_access_log_noise(caplog) -> None:
    import logging
    from io import StringIO

    import vibeocr.supervisor.process as process_module

    proc = process_module.SupervisorProcess(python_exe="python")
    access_line = (
        'INFO:     127.0.0.1:57001 - '
        '"GET /v2/runtime/residency HTTP/1.1" 200 OK\n'
    )

    with caplog.at_level(logging.DEBUG, logger="vibeocr.supervisor.process"):
        proc._drain_stream(StringIO(access_line))

    assert proc.log_lines == []
    assert not caplog.records


def test_supervisor_process_launch_failure_releases_process_and_job(monkeypatch) -> None:
    from io import StringIO
    from unittest.mock import Mock

    import vibeocr.supervisor.process as process_module

    guard = Mock()
    monkeypatch.setattr(process_module, "JobObjectGuard", lambda: guard)
    popen = Mock(pid=4321)
    popen.stdout = StringIO()
    popen.stderr = StringIO()
    popen.wait.return_value = 7
    monkeypatch.setattr(process_module.subprocess, "Popen", Mock(return_value=popen))

    def fail_ready(self) -> None:
        raise SupervisorLaunchError("bad ready")

    monkeypatch.setattr(
        process_module.SupervisorProcess, "_read_ready", fail_ready
    )
    proc = process_module.SupervisorProcess(python_exe="python")

    with pytest.raises(SupervisorLaunchError, match="bad ready"):
        proc._start(None)

    popen.terminate.assert_called_once_with()
    guard.close.assert_called_once_with()
