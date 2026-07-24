"""Process-wide exclusive WorkerHost session for the PySide frontend."""

from __future__ import annotations

import atexit
import os
import threading
from typing import TYPE_CHECKING

from vibeocr.worker_host.sync_client import SyncBackendClient

if TYPE_CHECKING:
    from collections.abc import Callable

_lock = threading.RLock()
_client: SyncBackendClient | None = None
_client_factory: Callable[[], SyncBackendClient] = SyncBackendClient


def _use_http_transport() -> bool:
    """是否用 HTTP worker（ocr_worker_http）替代 SHM worker。

    默认走 HTTP worker（``OcrHttpClient``，FastAPI 子进程）；``VIBEOCR_OCR_TRANSPORT=shm``
    可应急回退到旧 SHM（``SyncBackendClient``，命名管道 + shared_payload）——但 SHM
    代码已在阶段 3 删除，此回退标识仅用于 git revert 期间的过渡，正常构建无 SHM。
    两种客户端的 ``*_sync`` 方法签名一致，UI 调用面零改动。
    """
    return os.environ.get("VIBEOCR_OCR_TRANSPORT", "http").lower() != "shm"


def get_backend_client() -> SyncBackendClient:
    """Return the single production client owned by this PySide process.

    按 ``VIBEOCR_OCR_TRANSPORT`` 选择传输：默认 ``http`` 走 HTTP worker
    （``OcrHttpClient``）；``=shm`` 应急回退旧 SHM（``SyncBackendClient``）。
    返回类型标注为 ``SyncBackendClient``，但 HTTP 模式实际返回的
    ``OcrHttpClient`` 与之方法签名一致（duck typing），调用方无感知。
    """
    global _client
    with _lock:
        if _client is None:
            if _use_http_transport():
                from vibeocr.worker_host.ocr_http_client import OcrHttpClient

                candidate = OcrHttpClient()  # type: ignore[assignment]
            else:
                candidate = _client_factory()
            candidate.start(profile="production", frontend_id="pyside")
            _client = candidate  # type: ignore[assignment]
        return _client  # type: ignore[return-value]


def shutdown_backend_client() -> None:
    """Boundedly stop the shared WorkerHost and clear the process session."""
    global _client
    with _lock:
        client, _client = _client, None
    if client is not None:
        # OcrHttpClient 用 stop()，SyncBackendClient 用 shutdown()。
        stop = getattr(client, "shutdown", None) or getattr(client, "stop", None)
        if callable(stop):
            stop()


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
