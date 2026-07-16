"""Tests for the high-level Python BackendClient.

Phase 2 of DUAL_UI_IMPLEMENTATION_PLAN.md §9 — the PySide frontend's
counterpart to the C# WorkerHostClient.

These are unit tests using a fake connection that injects pre-programmed
frames into the reader loop, so no real Named Pipe is needed. They cover:
- request/response correlation by request_id;
- success result extraction;
- error envelope → BackendError mapping (code/message/retryable);
- event dispatch + per-task sequence de-duplication;
- deadline timeout → best-effort task.cancel send;
- EOF/disconnect → all pending calls fail.

A real round-trip integration test (client ↔ WorkerHost subprocess) is covered
separately once the worker is launched in-process.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from vibeocr.worker_host.backend_client import BackendClient, BackendError, _PendingCall
from vibeocr.worker_host.contracts import (
    PROTOCOL_VERSION,
    RpcEnvelope,
    RpcErrorBody,
    envelope_to_json_bytes,
)
from vibeocr.worker_host.errors import ErrorCode

# Sentinel pushed to the queue to make the reader loop raise EOF.
_EOF = object()

# Valid v4 UUIDs for envelope task_id validation.
_TASK_ID = "11111111-1111-4111-8111-111111111111"
_TASK_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_TASK_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class _FakeConnection:
    """A scripted pipe connection for unit-testing the reader loop.

    Frames to be read are pushed onto an asyncio queue (awaitable), so the
    reader loop blocks until a frame (or ``_EOF``) is supplied. Writes are
    captured for assertion.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self.written: list[bytes] = []
        self._closed = False

    async def read_frame(self, *, max_bytes: int = 8 << 20) -> bytes:
        item = await self._queue.get()
        if item is _EOF:
            raise EOFError("fake connection closed")
        return item  # type: ignore[return-value]

    async def write_frame(self, payload: bytes, *, max_bytes: int = 8 << 20) -> None:
        self.written.append(payload)

    async def close(self) -> None:
        self._closed = True

    # Test helpers ------------------------------------------------------

    def feed(self, frame: bytes) -> None:
        self._queue.put_nowait(frame)

    def feed_eof(self) -> None:
        self._queue.put_nowait(_EOF)


def _success_response(request_id: str, result: dict[str, Any]) -> bytes:
    env = RpcEnvelope(
        request_id=request_id,
        task_id=_TASK_ID,
        result=result,
    )
    return envelope_to_json_bytes(env)


def _error_response(
    request_id: str, code: ErrorCode, message: str, retryable: bool
) -> bytes:
    env = RpcEnvelope(
        request_id=request_id,
        task_id=_TASK_ID,
        error=RpcErrorBody(code=code, message=message, retryable=retryable),
    )
    return envelope_to_json_bytes(env)


def _event(task_id: str, sequence: int, name: str = "task.progress") -> bytes:
    env = RpcEnvelope(
        task_id=task_id,
        event=name,
        sequence=sequence,
        payload={"current": sequence},
    )
    return envelope_to_json_bytes(env)


async def _start_client(client: BackendClient, conn: _FakeConnection) -> None:
    """Bypass real connect(); inject the fake connection and start the reader."""
    client._conn = conn
    client._reader_task = asyncio.create_task(client._read_loop())


@pytest.fixture()
async def client():
    c = BackendClient(default_timeout=2.0)
    yield c
    await c.close()


# ---------------------------------------------------------------------------
# Correlation & success
# ---------------------------------------------------------------------------


async def test_call_returns_result_dict(client: BackendClient) -> None:
    """A success response is correlated by request_id and its result returned."""
    conn = _FakeConnection()
    await _start_client(client, conn)
    # Capture the request_id the client generates by intercepting the write.
    # We run the call and inject the matching response in a background task.
    call_task = asyncio.create_task(client.call("system.ping", {"nonce": "abc"}))

    # Wait for the client to write the request frame.
    await asyncio.sleep(0.05)
    assert len(conn.written) == 1
    request = json.loads(conn.written[0])
    rid = request["request_id"]
    conn.feed(_success_response(rid, {"nonce": "abc"}))

    result = await asyncio.wait_for(call_task, timeout=2.0)
    assert result == {"nonce": "abc"}


async def test_request_envelope_has_correct_shape(client: BackendClient) -> None:
    """The request envelope carries method, payload, fresh ids and a deadline."""
    conn = _FakeConnection()
    await _start_client(client, conn)
    call_task = asyncio.create_task(client.call("qrcode.decode", {"image": {}}))
    await asyncio.sleep(0.05)
    request = json.loads(conn.written[0])
    assert request["protocol_version"] == PROTOCOL_VERSION
    assert request["method"] == "qrcode.decode"
    assert request["payload"] == {"image": {}}
    assert "request_id" in request and "task_id" in request
    assert request["request_id"] != request["task_id"]
    assert isinstance(request["deadline_unix_ms"], int)
    call_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call_task


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


async def test_error_envelope_raises_backend_error(client: BackendClient) -> None:
    conn = _FakeConnection()
    await _start_client(client, conn)
    call_task = asyncio.create_task(client.call("ocr.recognize", {}))
    await asyncio.sleep(0.05)
    rid = json.loads(conn.written[0])["request_id"]
    conn.feed(_error_response(rid, ErrorCode.WORKER_UNAVAILABLE, "no GPU", True))
    with pytest.raises(BackendError) as exc_info:
        await asyncio.wait_for(call_task, timeout=2.0)
    assert exc_info.value.code is ErrorCode.WORKER_UNAVAILABLE
    assert exc_info.value.message == "no GPU"
    assert exc_info.value.retryable is True


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


async def test_event_callback_receives_events(client: BackendClient) -> None:
    conn = _FakeConnection()
    received: list[RpcEnvelope] = []

    async def on_evt(env: RpcEnvelope) -> None:
        received.append(env)

    client.on_event(on_evt)
    await _start_client(client, conn)

    # Push an event frame (no associated request needed).
    conn.feed(_event(_TASK_A, 0))
    conn.feed(_event(_TASK_A, 1))
    await asyncio.sleep(0.15)
    assert len(received) == 2
    assert received[0].sequence == 0
    assert received[1].sequence == 1


async def test_event_sequence_dedup_drops_stale(client: BackendClient) -> None:
    """An event with sequence <= the last seen for that task is dropped."""
    conn = _FakeConnection()
    received: list[RpcEnvelope] = []

    async def on_evt(env: RpcEnvelope) -> None:
        received.append(env)

    client.on_event(on_evt)
    await _start_client(client, conn)
    conn.feed(_event(_TASK_B, 5))
    conn.feed(_event(_TASK_B, 3))  # stale, dropped
    conn.feed(_event(_TASK_B, 6))  # newer, kept
    await asyncio.sleep(0.15)
    assert [e.sequence for e in received] == [5, 6]


# ---------------------------------------------------------------------------
# Timeout / cancellation
# ---------------------------------------------------------------------------


async def test_deadline_timeout_sends_cancel(client: BackendClient) -> None:
    """A call that times out sends a best-effort task.cancel frame."""
    conn = _FakeConnection()
    # Never feed a response → the call must time out.
    await _start_client(client, conn)
    with pytest.raises(TimeoutError):
        await client.call("system.ping", {"nonce": "x"}, timeout=0.2)
    await asyncio.sleep(0.1)
    # written[0] = the ping request; written[1] = the task.cancel request
    assert len(conn.written) >= 2
    cancel_req = json.loads(conn.written[1])
    assert cancel_req["method"] == "task.cancel"
    assert "task_id" in cancel_req["payload"]


async def test_late_response_for_cancelled_future_keeps_reader_valid(
    client: BackendClient,
) -> None:
    """A response racing with cancellation must not raise InvalidStateError."""
    request_id = "22222222-2222-4222-8222-222222222222"
    pending = _PendingCall("ocr.recognize", _TASK_ID)
    pending.future.cancel()
    client._pending[request_id] = pending

    envelope = RpcEnvelope(
        request_id=request_id,
        task_id=_TASK_ID,
        result={"text": "late", "pipeline": "OCR"},
    )
    client._complete_response(envelope)

    assert request_id not in client._pending
    assert pending.future.cancelled()


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


async def test_eof_fails_pending_calls(client: BackendClient) -> None:
    """When the reader hits EOF, all pending calls fail with a connection error."""
    conn = _FakeConnection()
    await _start_client(client, conn)
    call_task = asyncio.create_task(client.call("system.ping", {"nonce": "z"}))
    await asyncio.sleep(0.05)
    conn.feed_eof()  # simulate worker disconnect
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(call_task, timeout=2.0)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_close_is_idempotent() -> None:
    c = BackendClient()
    await c.close()
    await c.close()  # must not raise


async def test_call_before_connect_raises() -> None:
    c = BackendClient()
    with pytest.raises(RuntimeError):
        await c.call("system.ping", {})
    await c.close()


async def test_close_reclaims_shared_payload_store() -> None:
    """close() shuts down the SharedPayloadStore (no segment leak)."""
    c = BackendClient()
    conn = _FakeConnection()
    await _start_client(c, conn)
    await c.close()
    assert c._store.count_segments() == 0
