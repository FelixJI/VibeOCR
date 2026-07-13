"""Tests for the WorkerHost dispatcher (Task 1.5 Green).

The dispatcher routes incoming RPC envelopes to registered handlers, drives the
task registry state machine, enforces deadlines, and maps handler exceptions to
stable error codes. Cancellation is honoured via cancel tokens.

Pure-Python with fake handlers — no Win32, no real pipe.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import pytest

from vibeocr.worker_host.dispatcher import Dispatcher
from vibeocr.worker_host.errors import ErrorCode, WorkerError


def _rid() -> str:
    return str(uuid.uuid4())


def _tid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# routing: unknown method
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_method_returns_invalid_request() -> None:
    disp = Dispatcher()
    resp = await disp.dispatch(
        _make_request("evil.eval", {}), deadline_unix_ms=0
    )
    assert resp.error is not None
    assert resp.error.code == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_registered_handler_receives_payload() -> None:
    disp = Dispatcher()
    seen: dict[str, Any] = {}

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        seen.update(payload)
        return {"echo": payload}

    disp.register("test.echo", handler, retryable=True)
    resp = await disp.dispatch(_make_request("test.echo", {"x": 1}), deadline_unix_ms=0)
    assert resp.error is None
    assert resp.result == {"echo": {"x": 1}}
    assert seen == {"x": 1}


# ---------------------------------------------------------------------------
# task lifecycle: success -> completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_handler_completes_task() -> None:
    disp = Dispatcher()

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        return {"ok": True}

    disp.register("test.ok", handler, retryable=True)
    tid = _tid()
    req = _make_request("test.ok", {}, task_id=tid)
    await disp.dispatch(req, deadline_unix_ms=0)
    handle = disp.registry.get(tid)
    assert handle is not None
    assert handle.state.value == "completed"


# ---------------------------------------------------------------------------
# worker exception -> mapped error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_error_maps_to_stable_code() -> None:
    disp = Dispatcher()

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        raise WorkerError(ErrorCode.WORKER_UNAVAILABLE, "busy")

    disp.register("test.fail", handler, retryable=True)
    resp = await disp.dispatch(_make_request("test.fail", {}), deadline_unix_ms=0)
    assert resp.error is not None
    assert resp.error.code == "WORKER_UNAVAILABLE"
    assert resp.error.retryable is True


@pytest.mark.asyncio
async def test_generic_exception_maps_to_internal_error() -> None:
    disp = Dispatcher()

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    disp.register("test.boom", handler, retryable=True)
    resp = await disp.dispatch(_make_request("test.boom", {}), deadline_unix_ms=0)
    assert resp.error is not None
    assert resp.error.code == "INTERNAL_ERROR"
    assert resp.error.retryable is False


# ---------------------------------------------------------------------------
# cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelled_task_returns_cancelled() -> None:
    disp = Dispatcher()
    started = asyncio.Event()

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        started.set()
        # Loop until cancelled.
        while not cancel.is_cancelled:
            await asyncio.sleep(0.01)
        raise WorkerError(ErrorCode.TASK_CANCELLED, "cancelled")

    disp.register("test.slow", handler, retryable=True)
    tid = _tid()
    req = _make_request("test.slow", {}, task_id=tid)

    task = asyncio.create_task(disp.dispatch(req, deadline_unix_ms=0))
    await started.wait()
    disp.request_cancel(tid)
    resp = await task
    assert resp.error is not None
    assert resp.error.code == "TASK_CANCELLED"


# ---------------------------------------------------------------------------
# deadline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_deadline_returns_timeout() -> None:
    disp = Dispatcher()

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        return {"ok": True}

    disp.register("test.dl", handler, retryable=True)
    past = int(time.time() * 1000) - 1000
    resp = await disp.dispatch(_make_request("test.dl", {}), deadline_unix_ms=past)
    assert resp.error is not None
    assert resp.error.code == "TASK_TIMEOUT"


@pytest.mark.asyncio
async def test_running_task_is_stopped_at_deadline() -> None:
    disp = Dispatcher()
    cancel_observed = asyncio.Event()

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        try:
            while True:
                await asyncio.sleep(0.01)
        finally:
            if cancel.is_cancelled:
                cancel_observed.set()

    disp.register("test.deadline", handler, retryable=True)
    deadline = int(time.time() * 1000) + 50
    resp = await disp.dispatch(
        _make_request("test.deadline", {}), deadline_unix_ms=deadline
    )

    assert resp.error is not None
    assert resp.error.code == "TASK_TIMEOUT"
    assert cancel_observed.is_set()


@pytest.mark.asyncio
async def test_late_success_after_external_cancel_returns_cancelled() -> None:
    disp = Dispatcher()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        started.set()
        await finish.wait()
        return {"late": True}

    disp.register("test.late", handler, retryable=True)
    tid = _tid()
    task = asyncio.create_task(
        disp.dispatch(_make_request("test.late", {}, task_id=tid), deadline_unix_ms=0)
    )
    await started.wait()
    assert disp.registry.cancel(tid).accepted is True
    disp.request_cancel(tid)
    finish.set()

    resp = await task
    assert resp.error is not None
    assert resp.error.code == "TASK_CANCELLED"


@pytest.mark.asyncio
async def test_late_exception_after_external_cancel_returns_cancelled() -> None:
    disp = Dispatcher()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        started.set()
        await finish.wait()
        raise RuntimeError("late adapter error")

    disp.register("test.late_error", handler, retryable=True)
    tid = _tid()
    task = asyncio.create_task(
        disp.dispatch(
            _make_request("test.late_error", {}, task_id=tid),
            deadline_unix_ms=0,
        )
    )
    await started.wait()
    assert disp.registry.cancel(tid).accepted is True
    disp.request_cancel(tid)
    finish.set()

    resp = await task
    assert resp.error is not None
    assert resp.error.code == "TASK_CANCELLED"


@pytest.mark.asyncio
async def test_public_method_request_payload_is_validated() -> None:
    disp = Dispatcher()

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        return {"nonce": str(payload["nonce"])}

    disp.register("system.ping", handler, retryable=True)
    resp = await disp.dispatch(
        _make_request("system.ping", {"nonce": "x", "extra": True}),
        deadline_unix_ms=0,
    )
    assert resp.error is not None
    assert resp.error.code == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_public_method_response_payload_is_validated() -> None:
    disp = Dispatcher()

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        return {"wrong": "shape"}

    disp.register("system.ping", handler, retryable=True)
    resp = await disp.dispatch(
        _make_request("system.ping", {"nonce": "x"}),
        deadline_unix_ms=0,
    )
    assert resp.error is not None
    assert resp.error.code == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# non-retryable mutation is marked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mutation_handler_marked_non_retryable() -> None:
    disp = Dispatcher()

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        raise WorkerError(ErrorCode.INTERNAL_ERROR, "x")

    disp.register("pdf.save", handler, retryable=False)
    tid = _tid()
    req = _make_request("pdf.save", {"session_id": "sess-1"}, task_id=tid)
    await disp.dispatch(req, deadline_unix_ms=0)
    handle = disp.registry.get(tid)
    assert handle is not None
    assert handle.state.value == "failed"
    assert handle.retryable is False
    assert disp.registry.should_retry(tid) is False


# ---------------------------------------------------------------------------
# duplicate request id rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_request_id_rejected() -> None:
    disp = Dispatcher()

    async def handler(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        return {"ok": True}

    disp.register("test.dup", handler, retryable=True)
    rid = _rid()
    req1 = _make_request("test.dup", {}, request_id=rid)
    await disp.dispatch(req1, deadline_unix_ms=0)
    # Second request with same request_id must be rejected.
    req2 = _make_request("test.dup", {}, request_id=rid)
    resp = await disp.dispatch(req2, deadline_unix_ms=0)
    assert resp.error is not None
    assert resp.error.code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_request(
    method: str,
    payload: dict[str, Any],
    *,
    request_id: str | None = None,
    task_id: str | None = None,
) -> Any:
    from vibeocr.worker_host.contracts import RpcEnvelope

    return RpcEnvelope(
        protocol_version=1,
        request_id=request_id or _rid(),
        task_id=task_id or _tid(),
        method=method,
        payload=payload,
        deadline_unix_ms=0,
    )
