"""Process-wide exclusive WorkerHost session for the PySide frontend."""

from __future__ import annotations

import atexit
import threading
from typing import TYPE_CHECKING

from vibeocr.worker_host.sync_client import SyncBackendClient

if TYPE_CHECKING:
    from collections.abc import Callable

_lock = threading.RLock()
_client: SyncBackendClient | None = None
_client_factory: Callable[[], SyncBackendClient] = SyncBackendClient


def get_backend_client() -> SyncBackendClient:
    """Return the single production client owned by this PySide process."""
    global _client
    with _lock:
        if _client is None:
            candidate = _client_factory()
            candidate.start(profile="production", frontend_id="pyside")
            _client = candidate
        return _client


def shutdown_backend_client() -> None:
    """Boundedly stop the shared WorkerHost and clear the process session."""
    global _client
    with _lock:
        client, _client = _client, None
    if client is not None:
        client.shutdown()


def restart_backend_client() -> SyncBackendClient:
    """Replace a failed shared WorkerHost once and return the new client."""
    shutdown_backend_client()
    return get_backend_client()


# Python waits for non-daemon ``asyncio.to_thread`` executor threads before
# ordinary ``atexit`` callbacks.  WorkerHost owns one such blocking pipe read,
# so a normal interpreter exit could otherwise wait forever before reaching
# the cleanup callback.  ``threading`` callbacks run immediately before that
# join phase; retain the public atexit API as a fallback for other runtimes.
_register_thread_exit = getattr(threading, "_register_atexit", atexit.register)
_register_thread_exit(shutdown_backend_client)


__all__ = ["get_backend_client", "restart_backend_client", "shutdown_backend_client"]
