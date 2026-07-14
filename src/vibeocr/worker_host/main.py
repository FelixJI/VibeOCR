"""WorkerHost process entry point.

Launched by either frontend shell (WinUI or PySide) as::

    python -m vibeocr.worker_host.main \\
        --pipe <name> --token <hex> \\
        --profile <production|winui-dev> \\
        --frontend-id <pyside|winui> \\
        --parent-pid <pid>

The worker creates a current-user-isolated Named Pipe, accepts one client,
authenticates the session token, then serves RPC requests until shutdown,
client disconnect, parent-process exit, or deadline. It always exits within a
bounded drain — never hanging indefinitely.

``--frontend-id`` only labels the owner for logging/temp-dir isolation; it does
NOT gate business capabilities (ADR §7). ``--profile`` selects the path layout
(``app_paths.resolve_app_paths``); both ``production`` and ``winui-dev`` are
accepted — the old "only permits winui-dev" gate has been removed.

``--self-test`` prints one line of machine-readable JSON describing the worker
(version, protocol, capabilities) and exits 0, so the shell can sanity-check a
fresh build without a full handshake.

This module must never import PySide6.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
from typing import TYPE_CHECKING, Any

from vibeocr import __version__ as VIBEOCR_VERSION
from vibeocr.worker_host.contracts import (
    PROTOCOL_VERSION,
    EnvelopeKind,
    RpcEnvelope,
    RpcError,
    envelope_from_json_bytes,
    envelope_to_json_bytes,
)
from vibeocr.worker_host.dispatcher import Dispatcher
from vibeocr.worker_host.errors import ErrorCode, WorkerError
from vibeocr.worker_host.framing import FramingError
from vibeocr.worker_host.security import SessionTokenError
from vibeocr.worker_host.shared_payload import SharedPayloadStore

if TYPE_CHECKING:
    from collections.abc import Sequence

_log = logging.getLogger(__name__)

# Bounded shutdown: how long to wait for in-flight tasks before forcing exit.
_SHUTDOWN_DRAIN_SECONDS = 5.0
# Parent-process liveness poll interval.
_PARENT_POLL_SECONDS = 1.0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibeocr-worker",
        description="VibeOCR WorkerHost: serves OCR/PDF/QR over a Named Pipe.",
    )
    parser.add_argument("--pipe", help="Named Pipe path (server-generated if omitted)")
    parser.add_argument("--token", help="256-bit session token (hex)")
    parser.add_argument(
        "--profile",
        default="winui-dev",
        help="Path/profile selector: 'production' (正式路径) or 'winui-dev' (旁路开发路径). "
        "See app_paths.resolve_app_paths. Does not gate business capabilities.",
    )
    parser.add_argument(
        "--frontend-id",
        dest="frontend_id",
        default="winui",
        choices=("pyside", "winui"),
        help="Which frontend launched this worker (pyside|winui). Used only for "
        "logging/temp-dir/UI-settings isolation; does NOT select business capabilities.",
    )
    parser.add_argument("--parent-pid", type=int, help="Parent process PID to watch")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Print one line of JSON and exit 0; do not start a server",
    )
    return parser


def _self_test() -> int:
    """Emit one line of machine-readable JSON and return 0."""
    doc = {
        "protocol_version": PROTOCOL_VERSION,
        "worker_version": VIBEOCR_VERSION,
        "capabilities": ["ocr", "pdf", "qrcode", "settings"],
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "platform": sys.platform,
    }
    sys.stdout.write(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Process entry point. Returns the process exit code."""
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 0 on --help, 2 on bad args; honour its code.
        return int(exc.code) if isinstance(exc.code, int) else 2

    if args.self_test:
        return _self_test()

    # Serving mode requires a pipe + token.
    if not args.pipe or not args.token:
        sys.stderr.write("error: --pipe and --token are required to serve\n")
        return 2

    try:
        return asyncio.run(_serve(args))
    except KeyboardInterrupt:
        return 130


async def _serve(args: argparse.Namespace) -> int:
    """Run the server loop until shutdown, disconnect, or parent exit."""
    from vibeocr.app_paths import _ALLOWED_PROFILES
    from vibeocr.worker_host.named_pipe import NamedPipeServer

    # The WorkerHost is now frontend-agnostic: it serves whichever frontend
    # (pyside|winui) launched it, under either the production or winui-dev
    # profile. The old "only permits winui-dev" gate is removed (ADR §7).
    if args.profile not in _ALLOWED_PROFILES:
        _log.error(
            "unsupported profile %r; allowed: %s", args.profile, sorted(_ALLOWED_PROFILES)
        )
        return 2
    _log.info(
        "WorkerHost serving frontend_id=%s profile=%s", args.frontend_id, args.profile
    )

    from vibeocr.env_manager import get_project_root
    from vibeocr.worker_host.composition import WorkerServiceComposition

    store = SharedPayloadStore(owner="worker", ttl_seconds=300)
    services = WorkerServiceComposition(
        project_root=get_project_root(),
        profile=args.profile,
    )
    dispatcher = _build_dispatcher(
        store=store,
        domain_handlers=services.handlers(store),
        backend=services.backend(),
    )
    server = await NamedPipeServer.create(
        pipe_name=args.pipe,
        session_token=args.token,
    )
    assert server.endpoint is not None

    # Parent-process watcher: exit if the parent dies (WinUI crashed).
    stop_event = asyncio.Event()
    parent_task: asyncio.Task[None] | None = None
    if args.parent_pid:
        parent_task = asyncio.create_task(
            _watch_parent(args.parent_pid, stop_event)
        )

    sys.stdout.write(
        json.dumps(
            {
                "event": "worker.ready",
                "pipe": server.endpoint.name,
                "protocol_version": PROTOCOL_VERSION,
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    sys.stdout.flush()

    conn = None
    try:
        conn = await asyncio.wait_for(server.accept(timeout_ms=30_000), timeout=30.0)
        await asyncio.wait_for(conn.validate_handshake(), timeout=5.0)
    except (TimeoutError, OSError, ConnectionError, SessionTokenError) as exc:
        _log.warning("no client connected: %s", exc)
        await _shutdown(server, store, parent_task, conn=conn, services=services)
        return 1

    try:
        await _serve_connection(conn, dispatcher, stop_event)
    finally:
        await _shutdown(server, store, parent_task, conn=conn, services=services)
    return 0


async def _serve_connection(
    conn: Any,
    dispatcher: Dispatcher,
    stop_event: asyncio.Event,
) -> None:
    """Read, dispatch and respond until EOF or a shutdown request.

    Requests run concurrently so a ``task.cancel`` frame is not blocked behind
    the task it is trying to cancel. Writes are serialized to preserve frame
    boundaries on the byte-stream pipe.
    """
    write_lock = asyncio.Lock()
    in_flight: set[asyncio.Task[None]] = set()
    handshake_complete = False

    async def dispatch_and_write(request: RpcEnvelope) -> None:
        assert request.deadline_unix_ms is not None
        response = await dispatcher.dispatch(
            request, deadline_unix_ms=request.deadline_unix_ms
        )
        async with write_lock:
            await conn.write_frame(envelope_to_json_bytes(response))
        if request.method == "system.shutdown":
            stop_event.set()

    def task_done(task: asyncio.Task[None]) -> None:
        in_flight.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _log.warning("RPC response task failed: %s", exc)
            stop_event.set()

    try:
        while not stop_event.is_set():
            read_task = asyncio.create_task(conn.read_frame())
            stop_task = asyncio.create_task(stop_event.wait())
            done, _pending = await asyncio.wait(
                {read_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if stop_task in done:
                read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await read_task
                break
            stop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stop_task

            raw = await read_task
            request = envelope_from_json_bytes(raw)
            if request.kind is not EnvelopeKind.REQUEST:
                raise ValueError("client may only send request envelopes")
            if not handshake_complete:
                if request.method != "system.handshake":
                    assert request.request_id is not None
                    assert request.task_id is not None
                    response = RpcError(
                        code=ErrorCode.PROTOCOL_MISMATCH,
                        message="system.handshake must be the first request",
                        request_id=request.request_id,
                        task_id=request.task_id,
                    ).to_envelope()
                    async with write_lock:
                        await conn.write_frame(envelope_to_json_bytes(response))
                    return
                # Complete negotiation before accepting concurrent work so a
                # pipelined request cannot race ahead of the handshake.
                await dispatch_and_write(request)
                handle = dispatcher.registry.get(request.task_id or "")
                if handle is None or handle.state.value != "completed":
                    return
                handshake_complete = True
                continue
            task = asyncio.create_task(dispatch_and_write(request))
            in_flight.add(task)
            task.add_done_callback(task_done)
    except (ConnectionError, EOFError, FramingError):
        _log.info("client disconnected")
    finally:
        # Ask every running handler to stop cooperatively before the bounded
        # process-level drain. Cancelling the asyncio wrappers also propagates
        # each handler's CancelToken in Dispatcher.dispatch().
        for task_id in dispatcher.registry.active_task_ids():
            dispatcher.request_cancel(task_id)
            dispatcher.registry.cancel(task_id)
        for task in in_flight:
            task.cancel()
        if in_flight:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*in_flight, return_exceptions=True),
                    timeout=_SHUTDOWN_DRAIN_SECONDS,
                )


async def _watch_parent(parent_pid: int, stop_event: asyncio.Event) -> None:
    """Set ``stop_event`` when the parent process is gone."""
    while not stop_event.is_set():
        await asyncio.sleep(_PARENT_POLL_SECONDS)
        if not _pid_alive(parent_pid):
            _log.info("parent pid %d gone; shutting down", parent_pid)
            stop_event.set()
            return


def _pid_alive(pid: int) -> bool:
    """Return True if ``pid`` is running. Best-effort, cross-platform."""
    if pid <= 0:
        return True
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_uint32(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


async def _shutdown(
    server: Any,
    store: SharedPayloadStore,
    parent_task: asyncio.Task[None] | None,
    *,
    conn: Any = None,
    services: Any = None,
) -> None:
    # Close the accepted connection first so the peer sees EOF promptly.
    if conn is not None:
        with contextlib.suppress(Exception):
            await conn.close()
    with contextlib.suppress(Exception):
        await server.close()
    with contextlib.suppress(Exception):
        await store.shutdown()
    if services is not None:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(services.shutdown)
    if parent_task is not None:
        parent_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await parent_task


def _build_dispatcher(
    *,
    store: SharedPayloadStore,
    domain_handlers: dict[str, Any] | None = None,
    backend: str = "cpu",
) -> Dispatcher:
    """Build the always-available control dispatcher surface.

    Domain facades are still supplied by the production composition root; if a
    domain adapter is absent, its method returns ``WORKER_UNAVAILABLE`` rather
    than pretending the request succeeded.
    """
    dispatcher = Dispatcher()

    async def handshake(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        if payload.get("protocol_version") != PROTOCOL_VERSION:
            raise WorkerError(ErrorCode.PROTOCOL_MISMATCH, "protocol version mismatch")
        return {
            "worker_version": VIBEOCR_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "capabilities": ["ocr", "pdf", "qrcode", "settings"],
            "python_version": ".".join(map(str, sys.version_info[:3])),
            "backend": backend,
            "max_message_bytes": 8 << 20,
            "max_shared_payload_bytes": 256 << 20,
        }

    async def ping(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        nonce = payload.get("nonce")
        if not isinstance(nonce, str) or not nonce:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "system.ping requires 'nonce'")
        return {"nonce": nonce}

    async def shutdown(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        return {"acknowledged": True}

    async def cancel_task(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        task_id = payload.get("task_id")
        if not isinstance(task_id, str):
            raise WorkerError(ErrorCode.INVALID_REQUEST, "task.cancel requires 'task_id'")
        previous = dispatcher.registry.get(task_id)
        previous_state = previous.state.value if previous is not None else None
        result = dispatcher.registry.cancel(task_id)
        dispatcher.request_cancel(task_id)
        state = previous_state if previous_state is not None else result.state.value
        return {"accepted": result.accepted, "state": state}

    async def release_memory(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        name = payload.get("name")
        if not isinstance(name, str):
            raise WorkerError(ErrorCode.INVALID_REQUEST, "memory.release requires 'name'")
        try:
            released = await store.release_owned(name)
        except ValueError as exc:
            raise WorkerError(ErrorCode.INVALID_REQUEST, str(exc)) from exc
        return {"released": released}

    dispatcher.register("system.handshake", handshake, retryable=False)
    dispatcher.register("system.ping", ping, retryable=True)
    dispatcher.register("system.shutdown", shutdown, retryable=False)
    dispatcher.register("task.cancel", cancel_task, retryable=False)
    dispatcher.register("memory.release", release_memory, retryable=False)

    async def _not_implemented(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        raise WorkerError(
            ErrorCode.WORKER_UNAVAILABLE,
            "handler not wired in this build",
        )

    for method, retryable in (
        ("ocr.recognize", True),
        ("pdf.open", True),
        ("qrcode.decode", True),
        ("qrcode.generate", False),
        ("settings.snapshot", True),
    ):
        handler = (domain_handlers or {}).get(method, _not_implemented)
        dispatcher.register(method, handler, retryable=retryable)
    return dispatcher


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]
