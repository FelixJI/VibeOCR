"""Runtime TTL controller for the separately managed MinerU API process.

MinerU does not live in ``OCRService._pipelines``.  It is a local FastAPI child
process owned by :class:`MinerUService`, so the Paddle worker's cache manager
cannot observe or release it.  This module supplies the missing half of the
per-pipeline TTL contract without starting MinerU merely to query its status.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any

logger = logging.getLogger(__name__)

_PATCH_MARKER = "_vibeocr_mineru_runtime_cache_patch_v1"
_ORIGINAL_SHUTDOWN_ATTR = "_vibeocr_original_shutdown_v1"


class MinerURuntimeCache:
    """Track MinerU use and stop the API after its configured idle TTL."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ttl_seconds = 0
        self._last_used: float | None = None
        self._active_count = 0
        self._release_when_idle = False
        self._service_cls: type[Any] | None = None
        self._original_shutdown: Any = None
        self._stop_event = threading.Event()
        self._wakeup_event = threading.Event()
        self._thread = threading.Thread(
            target=self._watch_loop,
            name="MinerUTTLWatcher",
            daemon=True,
        )
        self._thread.start()

    def _ensure_service_patch(self) -> None:
        if self._service_cls is not None:
            return
        with self._lock:
            if self._service_cls is not None:
                return
            from vibeocr.services.mineru_service import MinerUService

            self._service_cls = MinerUService
            if getattr(MinerUService, _PATCH_MARKER, False):
                self._original_shutdown = getattr(
                    MinerUService,
                    _ORIGINAL_SHUTDOWN_ATTR,
                    MinerUService.shutdown,
                )
                return

            original_parse = MinerUService.parse
            original_shutdown = MinerUService.shutdown
            self._original_shutdown = original_shutdown
            runtime = self

            @functools.wraps(original_parse)
            def parse(service: Any, *args: Any, **kwargs: Any) -> Any:
                with runtime.lease():
                    return original_parse(service, *args, **kwargs)

            @functools.wraps(original_shutdown)
            def shutdown(service: Any, *args: Any, **kwargs: Any) -> Any:
                try:
                    return original_shutdown(service, *args, **kwargs)
                finally:
                    runtime.mark_released()

            setattr(MinerUService, _ORIGINAL_SHUTDOWN_ATTR, original_shutdown)
            MinerUService.parse = parse
            MinerUService.shutdown = shutdown
            setattr(MinerUService, _PATCH_MARKER, True)

    def _is_loaded_unlocked(self) -> bool:
        service_cls = self._service_cls
        if service_cls is None:
            return False
        process = getattr(service_cls, "_api_process", None)
        if process is None:
            return False
        try:
            return process.poll() is None
        except Exception:
            return False

    def is_loaded(self) -> bool:
        self._ensure_service_patch()
        with self._lock:
            return self._is_loaded_unlocked()

    @contextmanager
    def lease(self) -> Iterator[None]:
        """Protect a MinerU request and start idle timing when it finishes."""
        self._ensure_service_patch()
        with self._lock:
            self._active_count += 1
        try:
            yield
        finally:
            release_now = False
            with self._lock:
                self._active_count = max(0, self._active_count - 1)
                if self._is_loaded_unlocked():
                    self._last_used = time.time()
                if self._active_count == 0 and self._release_when_idle:
                    self._release_when_idle = False
                    release_now = True
            self._wakeup_event.set()
            if release_now:
                self._shutdown_now("explicit_release_after_active_request")

    def set_ttl(self, ttl_seconds: int) -> None:
        """Apply the MinerU TTL.  ``0`` means no idle-time shutdown."""
        self._ensure_service_patch()
        ttl = max(0, int(ttl_seconds))
        with self._lock:
            self._ttl_seconds = ttl
            if self._is_loaded_unlocked() and self._last_used is None:
                # The API may have been started before this controller was
                # configured.  Start a finite lease at configuration time.
                self._last_used = time.time()
        self._wakeup_event.set()

    def release(self) -> bool:
        """Request explicit MinerU release; active work drains first."""
        self._ensure_service_patch()
        with self._lock:
            if not self._is_loaded_unlocked():
                self._last_used = None
                self._release_when_idle = False
                return False
            if self._active_count > 0:
                self._release_when_idle = True
                return True
        self._shutdown_now("explicit_release")
        return True

    def mark_released(self) -> None:
        with self._lock:
            self._last_used = None
            self._release_when_idle = False
        self._wakeup_event.set()

    def status(self) -> dict[str, object]:
        self._ensure_service_patch()
        with self._lock:
            loaded = self._is_loaded_unlocked()
            return {
                "loaded": loaded,
                "ttl_seconds": self._ttl_seconds,
                "last_used_unix_ms": (
                    int(self._last_used * 1000)
                    if loaded and self._last_used is not None
                    else None
                ),
                "active": self._active_count > 0,
                "release_pending": self._release_when_idle,
            }

    def close(self) -> None:
        """Stop this controller's watcher (mainly for deterministic tests)."""
        self._stop_event.set()
        self._wakeup_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _shutdown_now(self, reason: str) -> None:
        self._ensure_service_patch()
        with self._lock:
            if not self._is_loaded_unlocked():
                self._last_used = None
                return
            service_cls = self._service_cls
            original_shutdown = self._original_shutdown
        if service_cls is None or original_shutdown is None:
            return

        logger.info("[MinerUCache] 释放 MinerU API（%s）", reason)
        try:
            # shutdown only touches class-level process fields; bypass __init__
            # so a status/release operation can never start MinerU first.
            service = object.__new__(service_cls)
            original_shutdown(service)
        except Exception:
            logger.exception("[MinerUCache] 释放 MinerU API 失败（%s）", reason)
            return
        self.mark_released()

    def _seconds_until_check(self) -> float:
        with self._lock:
            if (
                self._ttl_seconds <= 0
                or self._active_count > 0
                or not self._is_loaded_unlocked()
            ):
                return 60.0
            if self._last_used is None:
                self._last_used = time.time()
            assert self._last_used is not None
            return max(
                0.0,
                min(30.0, self._last_used + self._ttl_seconds - time.time()),
            )

    def _watch_loop(self) -> None:
        while not self._stop_event.is_set():
            timeout = self._seconds_until_check()
            self._wakeup_event.wait(timeout=timeout)
            self._wakeup_event.clear()
            if self._stop_event.is_set():
                return

            due = False
            with self._lock:
                if (
                    self._ttl_seconds > 0
                    and self._active_count == 0
                    and self._is_loaded_unlocked()
                    and self._last_used is not None
                    and self._last_used + self._ttl_seconds <= time.time()
                ):
                    due = True
            if due:
                self._shutdown_now("ttl_expired")


_RUNTIME = MinerURuntimeCache()


def get_mineru_runtime_cache() -> MinerURuntimeCache:
    return _RUNTIME


__all__ = ["MinerURuntimeCache", "get_mineru_runtime_cache"]
