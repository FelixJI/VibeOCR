"""High-level async RPC client for the WorkerHost (Python BackendClient).

This is the PySide frontend's counterpart to the C# ``WorkerHostClient``
(``src/dotnet/VibeOCR.Platform/Worker/WorkerHostClient.cs``). It provides the
typed request/response, request correlation, event dispatch, cooperative
cancellation, deadline enforcement and shared-payload management that the
frontend needs to drive a WorkerHost over the Named Pipe protocol.

Per ADR §5.1: the frontend depends on ``BackendClient`` + protocol DTOs only;
it never imports backend application/domain services. This client lives in
``vibeocr.worker_host`` so both the PySide app and integration tests can use it
without pulling in UI code.

Lifecycle::

    client = BackendClient(default_timeout=30.0)
    await client.connect(pipe, token)
    await client.call("system.handshake", {"app_version": ...,
                                           "protocol_version": 1})
    result = await client.call("qrcode.decode", {"image": ref.to_descriptor()})
    await client.close()

Concurrent calls are safe: a single reader task correlates responses by
``request_id`` while writes are serialized to preserve frame boundaries.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from vibeocr.worker_host.contracts import (
    EnvelopeKind,
    RpcEnvelope,
    envelope_from_json_bytes,
    envelope_to_json_bytes,
)
from vibeocr.worker_host.errors import ErrorCode
from vibeocr.worker_host.shared_payload import (
    SharedPayloadError,
    SharedPayloadRef,
    SharedPayloadStore,
)

if TYPE_CHECKING:
    from vibeocr.worker_host.named_pipe import PipeConnection, PipeEndpoint

_log = logging.getLogger(__name__)

# Cancellation best-effort timeout: matches the C# SendCancelAsync (5s).
_CANCEL_DEADLINE_SECONDS = 5.0
# Retry interval when reading a worker-produced shared payload that may not be
# published yet (mirrors C# ReadPayload 20 ms sleep).
_PAYLOAD_READ_RETRY_SECONDS = 0.02


class BackendError(Exception):
    """A WorkerHost RPC call returned an error envelope."""

    def __init__(self, code: ErrorCode, message: str, retryable: bool, detail: str | None) -> None:
        super().__init__(f"[{code.value}] {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.detail = detail


class DecodedCode:
    """One decoded QR/barcode from ``qrcode.decode``."""

    __slots__ = ("data", "format", "is_url")

    def __init__(self, *, data: str, fmt: str, is_url: bool) -> None:
        self.data = data
        self.format = fmt
        self.is_url = is_url


class _PendingCall:
    """One in-flight request awaiting its response."""

    __slots__ = ("future", "method", "task_id")

    def __init__(self, method: str, task_id: str) -> None:
        self.method = method
        self.task_id = task_id
        self.future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()


# Type alias for the event callback: receives the event envelope.
EventCallback = Callable[[RpcEnvelope], Awaitable[None]]


class BackendClient:
    """Async RPC client for the WorkerHost.

    Thread-safety: asyncio single-loop only. Concurrent ``call()`` coroutines
    are safe (they share the reader task and serialize writes).
    """

    def __init__(
        self,
        *,
        default_timeout: float = 30.0,
        payload_ttl_seconds: int = 300,
    ) -> None:
        self._default_timeout = default_timeout
        self._store = SharedPayloadStore(owner="client", ttl_seconds=payload_ttl_seconds)
        self._conn: PipeConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, _PendingCall] = {}
        # Per-task event sequence de-duplication (taskId -> last seen sequence).
        self._event_sequences: dict[str, int] = {}
        self._event_callback: EventCallback | None = None
        self._closed = False
        self._shutdown_event = asyncio.Event()

    # -- connection -----------------------------------------------------

    async def connect(
        self,
        endpoint: PipeEndpoint,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        """Open the pipe, send the session token frame, start the reader loop.

        The caller MUST send ``system.handshake`` as the first ``call()`` after
        this returns (enforced server-side).
        """
        from vibeocr.worker_host.named_pipe import NamedPipeClient

        client = NamedPipeClient()
        self._conn = await client.connect(endpoint, timeout_ms=int(timeout_seconds * 1000))
        self._reader_task = asyncio.create_task(self._read_loop())

    @property
    def is_connected(self) -> bool:
        return self._conn is not None and not self._closed

    # -- the core RPC method --------------------------------------------

    async def call(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a request envelope and await the response ``result`` dict.

        Raises:
            BackendError: the worker returned an error envelope.
            asyncio.TimeoutError: the call exceeded its deadline.
        """
        if self._conn is None or self._closed:
            raise RuntimeError("BackendClient is not connected")
        deadline = timeout if timeout is not None else self._default_timeout
        request_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())
        envelope = RpcEnvelope(
            request_id=request_id,
            task_id=task_id,
            method=method,
            payload=payload or {},
            deadline_unix_ms=int((time.time() + deadline) * 1000),
        )
        pending = _PendingCall(method, task_id)
        self._pending[request_id] = pending
        try:
            async with self._write_lock:
                await self._conn.write_frame(envelope_to_json_bytes(envelope))
            return await asyncio.wait_for(pending.future, timeout=deadline)
        except (TimeoutError, asyncio.CancelledError):
            # On caller cancel or deadline, best-effort send task.cancel.
            if not self._shutdown_event.is_set():
                await self._send_cancel(task_id)
            raise
        finally:
            self._pending.pop(request_id, None)

    # -- events ---------------------------------------------------------

    def on_event(self, callback: EventCallback | None) -> None:
        """Register (or clear with None) a callback for task events."""
        self._event_callback = callback

    # -- shared payload passthrough -------------------------------------

    async def create_payload(
        self, data: bytes, *, media_type: str, ttl_seconds: int | None = None
    ) -> SharedPayloadRef:
        """Put ``data`` into client-owned shared memory; return its descriptor."""
        return await self._store.put(data, media_type=media_type, ttl_seconds=ttl_seconds)

    async def read_payload(
        self,
        reference: SharedPayloadRef,
        *,
        timeout: float = 5.0,
    ) -> bytes:
        """Read a shared payload, retrying briefly if the worker hasn't published it yet."""
        deadline = time.monotonic() + timeout
        last_exc: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return await self._store.read(reference)
            except SharedPayloadError as exc:
                last_exc = exc
                await asyncio.sleep(_PAYLOAD_READ_RETRY_SECONDS)
        if last_exc is not None:
            raise last_exc
        raise SharedPayloadError("payload read timed out")

    async def release_payload(self, name: str) -> bool:
        """Release a client-owned payload by name (idempotent)."""
        return await self._store.release_owned(name)

    # -- typed QR helpers ----------------------------------------------

    async def decode_qrcode(self, image_bytes: bytes) -> list[DecodedCode]:
        """Decode QR/barcodes from image bytes via ``qrcode.decode``.

        The image is staged in client-owned shared memory; the worker reads it,
        decodes, and returns ``codes``. Each code is returned as a
        :class:`DecodedCode`.
        """
        ref = await self._store.put(image_bytes, media_type="image/png")
        result = await self.call("qrcode.decode", {"image": ref.to_descriptor()})
        codes: list[DecodedCode] = []
        for item in result.get("codes", []):
            codes.append(
                DecodedCode(
                    data=str(item.get("data", "")),
                    fmt=str(item.get("format", "")),
                    is_url=bool(item.get("is_url", False)),
                )
            )
        await self._store.release_owned(ref.name)
        return codes

    async def generate_qrcode(
        self, data: str, *, options: dict[str, Any] | None = None
    ) -> bytes:
        """Generate a styled QR/barcode PNG via ``qrcode.generate``.

        ``options`` mirrors the request fields defined in
        ``contracts/v1/methods.schema.json`` (format, barcode_format, size,
        error_correction, fg_color, bg_color, invert, logo_path, logo_ratio,
        label_text, label_position, label_font_size). Returns PNG bytes.
        """
        payload: dict[str, Any] = {"data": data}
        if options:
            payload.update(options)
        result = await self.call("qrcode.generate", payload)
        ref = SharedPayloadRef.from_descriptor(result["image"])
        # The worker owns this segment; the client reads it and lets the worker
        # reclaim it via TTL.
        return await self.read_payload(ref)

    async def generate_qrcode_svg(
        self, data: str, *, options: dict[str, Any] | None = None
    ) -> str:
        """Generate a QR code as an SVG string via ``qrcode.generate_svg``."""
        payload: dict[str, Any] = {"data": data}
        if options:
            payload.update(options)
        result = await self.call("qrcode.generate_svg", payload)
        return str(result["svg"])

    # -- shutdown -------------------------------------------------------

    async def close(self) -> None:
        """Cancel the reader, close the pipe, fail all pending calls, reclaim payloads."""
        if self._closed:
            return
        self._closed = True
        self._shutdown_event.set()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with _suppress(asyncio.CancelledError):
                await self._reader_task
        if self._conn is not None:
            with _suppress(Exception):
                await self._conn.close()
        # Fail any still-pending callers.
        terminal = ConnectionError("WorkerHost connection closed.")
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(terminal)
        self._pending.clear()
        with _suppress(Exception):
            await self._store.shutdown()

    async def __aenter__(self) -> BackendClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # -- internals ------------------------------------------------------

    async def _read_loop(self) -> None:
        """Continuously read frames and dispatch to pending callers / events."""
        assert self._conn is not None
        try:
            while not self._closed:
                raw = await self._conn.read_frame()
                envelope = envelope_from_json_bytes(raw)
                if envelope.kind is EnvelopeKind.EVENT:
                    await self._dispatch_event(envelope)
                else:
                    self._complete_response(envelope)
        except (EOFError, ConnectionError) as exc:
            _log.info("BackendClient reader: connection closed (%s)", exc)
        except Exception as exc:
            _log.warning("BackendClient reader: terminal error: %s", exc)
        finally:
            # Always surface a stable ConnectionError to pending callers,
            # regardless of the underlying transport error type.
            self._fail_all_pending(ConnectionError("WorkerHost connection closed."))

    def _complete_response(self, envelope: RpcEnvelope) -> None:
        """Match a response envelope to its pending caller and resolve/reject it."""
        if envelope.request_id is None:
            _log.warning("BackendClient: response without request_id: %s", envelope)
            return
        pending = self._pending.pop(envelope.request_id, None)
        if pending is None:
            # Stale response for an already-cancelled/timed-out call; ignore.
            return
        if envelope.kind is EnvelopeKind.RESPONSE_ERROR:
            err = envelope.error
            code = err.code if err is not None else ErrorCode.INTERNAL_ERROR
            pending.future.set_exception(
                BackendError(
                    code=code,
                    message=err.message if err is not None else "unknown error",
                    retryable=err.retryable if err is not None else False,
                    detail=err.detail if err is not None else None,
                )
            )
        elif envelope.kind is EnvelopeKind.RESPONSE_SUCCESS:
            pending.future.set_result(envelope.result or {})
        else:
            pending.future.set_exception(
                BackendError(
                    code=ErrorCode.PROTOCOL_MISMATCH,
                    message=f"unexpected envelope kind {envelope.kind} for response",
                    retryable=False,
                    detail=None,
                )
            )

    async def _dispatch_event(self, envelope: RpcEnvelope) -> None:
        """De-duplicate events by per-task sequence, then invoke the callback."""
        task_id = envelope.task_id
        seq = envelope.sequence if envelope.sequence is not None else 0
        if task_id is not None:
            prev = self._event_sequences.get(task_id, -1)
            if seq <= prev:
                return  # stale/duplicate event
            self._event_sequences[task_id] = seq
        if self._event_callback is not None:
            try:
                await self._event_callback(envelope)
            except Exception:
                _log.exception("BackendClient event callback raised")

    def _fail_all_pending(self, exc: Exception) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(exc)
        self._pending.clear()

    async def _send_cancel(self, task_id: str) -> None:
        """Best-effort ``task.cancel`` RPC so the worker stops cooperatively."""
        if self._conn is None or self._closed:
            return
        envelope = RpcEnvelope(
            request_id=str(uuid.uuid4()),
            task_id=str(uuid.uuid4()),
            method="task.cancel",
            payload={"task_id": task_id},
            deadline_unix_ms=int((time.time() + _CANCEL_DEADLINE_SECONDS) * 1000),
        )
        try:
            async with self._write_lock:
                await self._conn.write_frame(envelope_to_json_bytes(envelope))
        except Exception:
            _log.debug("BackendClient: best-effort cancel send failed", exc_info=True)


class _suppress:
    """Inline ``contextlib.suppress`` to avoid a top-level import cycle in __del__."""

    def __init__(self, *exceptions: type[BaseException]) -> None:
        self._exceptions = exceptions

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        return exc_type is not None and issubclass(exc_type, self._exceptions)


__all__ = ["BackendClient", "BackendError", "DecodedCode"]
