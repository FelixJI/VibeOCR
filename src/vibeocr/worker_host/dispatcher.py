"""RPC dispatcher for the WorkerHost (Task 1.5).

Routes incoming request envelopes to registered async handlers, drives the task
registry state machine, enforces deadlines, honours cancellation via cancel
tokens, and maps handler exceptions to stable error codes.

Handlers are async callables ``(payload, cancel) -> result_dict``. The
dispatcher wraps each call in a task-registry lifecycle: create -> mark_running
-> complete | fail. Late results after a terminal state are silently discarded
by the registry. Unknown methods return ``INVALID_REQUEST``; generic exceptions
map to ``INTERNAL_ERROR``.

Retry policy (design §7): query-type handlers register with ``retryable=True``;
mutations (pdf save/mutation, dependency install, backend switch, update)
register with ``retryable=False`` and the registry never marks them eligible.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from vibeocr.application.contracts import CancelToken
from vibeocr.worker_host.contracts import PROTOCOL_VERSION, RpcEnvelope, RpcErrorBody
from vibeocr.worker_host.errors import ErrorCode, WorkerError
from vibeocr.worker_host.method_validation import (
    PUBLIC_METHODS,
    MethodPayloadError,
    validate_method_payload,
)
from vibeocr.worker_host.task_registry import TaskRegistry, TaskStateError

Handler = Callable[[dict[str, Any], CancelToken], Awaitable[dict[str, Any]]]


@dataclass
class _HandlerEntry:
    handler: Handler
    retryable: bool


class Dispatcher:
    """Routes RPC requests to handlers and manages task lifecycle."""

    def __init__(self, *, registry: TaskRegistry | None = None) -> None:
        self.registry = registry or TaskRegistry()
        self._handlers: dict[str, _HandlerEntry] = {}
        # task_id -> cancel token for in-flight handlers, so an external cancel
        # (registry.cancel / task.cancel RPC) propagates to the handler.
        self._cancel_tokens: dict[str, CancelToken] = {}

    def register(self, method: str, handler: Handler, *, retryable: bool) -> None:
        """Register an async handler for a method name."""
        if method in self._handlers:
            raise ValueError(f"method already registered: {method}")
        self._handlers[method] = _HandlerEntry(handler=handler, retryable=retryable)

    def request_cancel(self, task_id: str) -> None:
        """Propagate a cancel to the in-flight handler's cancel token.

        This is called when a ``task.cancel`` RPC arrives or when the session
        disconnects. The registry state transition is handled separately.
        """
        token = self._cancel_tokens.get(task_id)
        if token is not None:
            token.cancel()

    async def dispatch(self, request: RpcEnvelope, *, deadline_unix_ms: int) -> RpcEnvelope:
        """Execute one request and return the response envelope.

        Always returns a response envelope (never raises): the caller writes it
        back to the pipe. Errors are encoded as ``error`` bodies with stable
        codes.
        """
        assert request.method is not None
        assert request.request_id is not None
        assert request.task_id is not None
        assert request.payload is not None

        entry = self._handlers.get(request.method)
        if entry is None:
            return _error_response(
                request, ErrorCode.INVALID_REQUEST, f"unknown method: {request.method}"
            )

        if request.method in PUBLIC_METHODS:
            try:
                validate_method_payload(request.method, "request", request.payload)
            except MethodPayloadError as exc:
                return _error_response(request, ErrorCode.INVALID_REQUEST, str(exc))

        # Register the task (rejects duplicate request_id).
        try:
            self.registry.create(
                request_id=request.request_id,
                task_id=request.task_id,
                method=request.method,
                retryable=entry.retryable,
                deadline_unix_ms=deadline_unix_ms,
            )
        except TaskStateError:
            return _error_response(
                request, ErrorCode.INVALID_REQUEST, "duplicate request_id"
            )

        # Deadline check before running.
        if deadline_unix_ms > 0 and self.registry.is_expired(request.task_id):
            self.registry.mark_running(request.task_id)
            self.registry.fail(
                request.task_id,
                error_code="TASK_TIMEOUT",
                message="task deadline already passed",
            )
            return _error_response(
                request,
                ErrorCode.TASK_TIMEOUT,
                "task deadline already passed",
                retryable=True,
            )

        cancel = CancelToken()
        self.registry.mark_running(request.task_id)
        self._cancel_tokens[request.task_id] = cancel
        try:
            if deadline_unix_ms > 0:
                remaining = max(0.0, (deadline_unix_ms - time.time() * 1000) / 1000)
                handler_task = asyncio.ensure_future(
                    entry.handler(request.payload, cancel)
                )
                done, _pending = await asyncio.wait({handler_task}, timeout=remaining)
                if not done:
                    cancel.cancel()
                    handler_task.cancel()
                    try:
                        await handler_task
                    except asyncio.CancelledError:
                        pass
                    self.registry.fail(
                        request.task_id,
                        error_code="TASK_TIMEOUT",
                        message="task deadline exceeded",
                    )
                    return _error_response(
                        request,
                        ErrorCode.TASK_TIMEOUT,
                        "task deadline exceeded",
                        retryable=entry.retryable,
                    )
                result = handler_task.result()
            else:
                result = await entry.handler(request.payload, cancel)
        except WorkerError as err:
            handle = self.registry.get(request.task_id)
            was_cancelled = handle is not None and handle.state.value == "cancelled"
            if err.code is ErrorCode.TASK_CANCELLED or was_cancelled:
                self.registry.cancel(request.task_id)
                return _error_response(
                    request,
                    ErrorCode.TASK_CANCELLED,
                    "task cancelled",
                    retryable=False,
                )
            self.registry.fail(
                request.task_id,
                error_code=err.code.value,
                message=err.message,
                detail=err.detail,
            )
            return _error_response(
                request, err.code, err.message, detail=err.detail
            )
        except asyncio.CancelledError:
            # Cooperative cancellation propagated from the handler.
            cancel.cancel()
            self.registry.cancel(request.task_id)
            raise
        except Exception as exc:
            handle = self.registry.get(request.task_id)
            if handle is not None and handle.state.value == "cancelled":
                return _error_response(
                    request,
                    ErrorCode.TASK_CANCELLED,
                    "task cancelled",
                    retryable=False,
                )
            self.registry.fail(
                request.task_id,
                error_code="INTERNAL_ERROR",
                message=f"handler raised {type(exc).__name__}",
                detail=str(exc),
            )
            return _error_response(
                request,
                ErrorCode.INTERNAL_ERROR,
                "internal error",
                detail=str(exc),
            )
        finally:
            self._cancel_tokens.pop(request.task_id, None)

        # Guard against a terminal state reached by a concurrent cancel. A
        # cooperative handler may return normally after observing its token;
        # its late result must never escape as a successful response.
        handle = self.registry.get(request.task_id)
        if handle is not None and handle.state.value == "cancelled":
            return _error_response(
                request,
                ErrorCode.TASK_CANCELLED,
                "task cancelled",
                retryable=False,
            )
        if request.method in PUBLIC_METHODS:
            try:
                validate_method_payload(request.method, "response", result)
            except MethodPayloadError as exc:
                self.registry.fail(
                    request.task_id,
                    error_code=ErrorCode.INTERNAL_ERROR.value,
                    message="handler returned an invalid response",
                    detail=str(exc),
                )
                return _error_response(
                    request,
                    ErrorCode.INTERNAL_ERROR,
                    "handler returned an invalid response",
                    detail=str(exc),
                )
        self.registry.complete(request.task_id, result=result)
        return RpcEnvelope(
            protocol_version=PROTOCOL_VERSION,
            request_id=request.request_id,
            task_id=request.task_id,
            result=result,
        )


def _error_response(
    request: RpcEnvelope,
    code: ErrorCode,
    message: str,
    *,
    detail: str | None = None,
    retryable: bool | None = None,
) -> RpcEnvelope:
    assert request.request_id is not None
    assert request.task_id is not None
    return RpcEnvelope(
        protocol_version=PROTOCOL_VERSION,
        request_id=request.request_id,
        task_id=request.task_id,
        error=RpcErrorBody(
            code=code,
            message=message,
            retryable=retryable if retryable is not None else code.default_retryable(),
            detail=detail,
        ),
    )


__all__ = ["Dispatcher", "Handler"]
