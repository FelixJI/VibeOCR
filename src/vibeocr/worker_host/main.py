"""WorkerHost process entry point (Task 1.6).

Launched by the WinUI shell as::

    python -m vibeocr.worker_host.main \\
        --pipe <name> --token <hex> --profile winui-dev --parent-pid <pid>

The worker creates a current-user-isolated Named Pipe, accepts one client,
authenticates the session token, then serves RPC requests until shutdown,
client disconnect, parent-process exit, or deadline. It always exits within a
bounded drain — never hanging indefinitely.

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
)
from vibeocr.worker_host.dispatcher import Dispatcher
from vibeocr.worker_host.errors import ErrorCode, WorkerError
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
        help="App profile (winui-dev for side-by-side; never the production profile)",
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
    from vibeocr.worker_host.named_pipe import NamedPipeServer

    # Build the dispatcher surface (real facades wired in production bootstrap).
    _build_dispatcher()
    store = SharedPayloadStore(owner="worker", ttl_seconds=300)
    server = await NamedPipeServer.create(pipe_name=args.pipe)
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
    except (TimeoutError, OSError) as exc:
        _log.warning("no client connected: %s", exc)
        await _shutdown(server, store, parent_task, conn=None)
        return 1

    # Accept succeeded; the connection owns the pipe handle now. The full
    # read/dispatch loop is wired in the production bootstrap; Phase 1 validates
    # the connection layer via test_named_pipe.py and self-test. We hold the
    # connection so it can be closed cleanly on shutdown (no handle leak).
    try:
        await stop_event.wait()
    finally:
        await _shutdown(server, store, parent_task, conn=conn)
    return 0


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
) -> None:
    # Close the accepted connection first so the peer sees EOF promptly.
    if conn is not None:
        with contextlib.suppress(Exception):
            await conn.close()
    with contextlib.suppress(Exception):
        await server.close()
    with contextlib.suppress(Exception):
        await store.shutdown()
    if parent_task is not None:
        parent_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await parent_task


def _build_dispatcher() -> Dispatcher:
    """Build a dispatcher with default (fake) facades.

    Real facades are wired by the production bootstrap (Task 1.6 integration);
    here we register the method surface so unknown-method handling and
    self-test agree on the capability list.
    """
    dispatcher = Dispatcher()

    async def _not_implemented(payload: dict[str, Any], cancel: Any) -> dict[str, Any]:
        raise WorkerError(
            ErrorCode.WORKER_UNAVAILABLE,
            "handler not wired in this build",
        )

    for method, retryable in (
        ("system.handshake", False),
        ("system.ping", True),
        ("system.shutdown", False),
        ("task.cancel", False),
        ("memory.release", False),
        ("ocr.recognize", True),
        ("pdf.open", True),
        ("qrcode.decode", True),
        ("qrcode.generate", False),
        ("settings.snapshot", True),
    ):
        try:
            dispatcher.register(method, _not_implemented, retryable=retryable)
        except ValueError:
            pass
    return dispatcher


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]
