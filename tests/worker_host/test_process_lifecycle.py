"""Tests for WorkerHost process lifecycle (Task 1.6 Green).

Verifies the entry point contract: ``--self-test`` emits one line of
machine-readable JSON and exits 0; ``--help`` exits 0; invalid args exit
non-zero. The full pipe round-trip is covered by the named_pipe + handler
tests; here we focus on the process entry point.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from vibeocr.worker_host.contracts import (
    RpcEnvelope,
    envelope_from_json_bytes,
    envelope_to_json_bytes,
)
from vibeocr.worker_host.main import _build_dispatcher, _serve_connection, main
from vibeocr.worker_host.shared_payload import SharedPayloadStore

# ---------------------------------------------------------------------------
# main() as a library: self-test
# ---------------------------------------------------------------------------


def test_self_test_returns_zero_and_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--self-test"])
    captured = capsys.readouterr()
    assert rc == 0
    # Output is exactly one line of JSON.
    lines = [ln for ln in captured.out.strip().splitlines() if ln.strip()]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["protocol_version"] == 1
    assert "worker_version" in doc
    assert isinstance(doc["capabilities"], list)


def test_help_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--help"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "vibeocr-worker" in (captured.out + captured.err)


def test_no_args_returns_non_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([])
    assert rc != 0


def test_unknown_arg_returns_non_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--definitely-not-a-flag"])
    assert rc != 0


# ---------------------------------------------------------------------------
# Subprocess: the real entry point behaves the same
# ---------------------------------------------------------------------------


def test_self_test_as_subprocess() -> None:
    """Run the worker as a real subprocess to validate the console entry point."""
    env = os.environ.copy()
    src = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (src, env.get("PYTHONPATH"))))
    result = subprocess.run(
        [sys.executable, "-m", "vibeocr.worker_host.main", "--self-test"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["protocol_version"] == 1


class _FakeConnection:
    def __init__(self, frames: list[bytes]) -> None:
        self.frames = frames
        self.writes: list[bytes] = []

    async def read_frame(self) -> bytes:
        if self.frames:
            return self.frames.pop(0)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def write_frame(self, payload: bytes) -> None:
        self.writes.append(payload)


@pytest.mark.asyncio
async def test_connection_loop_handshake_ping_then_shutdown() -> None:
    handshake = RpcEnvelope(
        request_id="00000000-0000-4000-8000-000000000100",
        task_id="00000000-0000-4000-8000-000000000100",
        method="system.handshake",
        payload={"app_version": "0.5.0", "protocol_version": 1},
        deadline_unix_ms=0,
    )
    ping = RpcEnvelope(
        request_id="00000000-0000-4000-8000-000000000101",
        task_id="00000000-0000-4000-8000-000000000101",
        method="system.ping",
        payload={"nonce": "phase1"},
        deadline_unix_ms=0,
    )
    shutdown = RpcEnvelope(
        request_id="00000000-0000-4000-8000-000000000102",
        task_id="00000000-0000-4000-8000-000000000102",
        method="system.shutdown",
        payload={"reason": "test"},
        deadline_unix_ms=0,
    )
    conn = _FakeConnection(
        [
            envelope_to_json_bytes(handshake),
            envelope_to_json_bytes(ping),
            envelope_to_json_bytes(shutdown),
        ]
    )
    store = SharedPayloadStore(owner="worker")
    stop_event = asyncio.Event()

    await _serve_connection(conn, _build_dispatcher(store=store), stop_event)

    responses = [envelope_from_json_bytes(raw) for raw in conn.writes]
    assert len(responses) == 3
    by_task = {response.task_id: response for response in responses}
    assert by_task[ping.task_id].result == {"nonce": "phase1"}
    assert by_task[shutdown.task_id].result == {"acknowledged": True}
    assert stop_event.is_set()


@pytest.mark.asyncio
async def test_connection_loop_rejects_request_before_handshake() -> None:
    ping = RpcEnvelope(
        request_id="00000000-0000-4000-8000-000000000103",
        task_id="00000000-0000-4000-8000-000000000103",
        method="system.ping",
        payload={"nonce": "too-early"},
        deadline_unix_ms=0,
    )
    conn = _FakeConnection([envelope_to_json_bytes(ping)])
    store = SharedPayloadStore(owner="worker")

    await _serve_connection(conn, _build_dispatcher(store=store), asyncio.Event())

    response = envelope_from_json_bytes(conn.writes[0])
    assert response.error is not None
    assert response.error.code == "PROTOCOL_MISMATCH"
