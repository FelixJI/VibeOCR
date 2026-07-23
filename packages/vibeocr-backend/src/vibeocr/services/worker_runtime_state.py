"""Track and restore OCR worker cache state across process restarts.

The worker process owns the actual Paddle pipeline objects and GPU context.  A
worker restart necessarily destroys those objects, but the manager process can
remember the desired TTL policy and which pipelines were loaded.  This module
wraps worker RPC calls to maintain that small control-plane snapshot and can
replay it after a restart.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from enum import Enum
from typing import TYPE_CHECKING, Any

from vibeocr.core.constants import Constants

if TYPE_CHECKING:
    from vibeocr.services.ocr_worker_process import OCRWorkerProcess

logger = logging.getLogger(__name__)


def _process_token(worker: OCRWorkerProcess) -> tuple[int, int | None]:
    """Return a cheap identity token that changes when the child is replaced."""
    process = getattr(worker, "process", None)
    return id(process), getattr(process, "pid", None)


def _pipeline_name(value: object, default: str | None = None) -> str | None:
    if value is None:
        return default
    if isinstance(value, Enum):
        value = value.value
    elif hasattr(value, "value"):
        value = getattr(value, "value")
    text = str(value).strip()
    return text or default


class RuntimeStateTrackingWorker:
    """Duck-typed worker proxy that records successful cache-affecting RPCs."""

    def __init__(self, state: WorkerRuntimeState, worker: OCRWorkerProcess) -> None:
        self._state = state
        self._worker = worker
        self.restart_detected = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._worker, name)

    def _invoke(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        before = _process_token(self._worker)
        try:
            return getattr(self._worker, method_name)(*args, **kwargs)
        finally:
            if _process_token(self._worker) != before:
                self.restart_detected = True

    def recognize(
        self, image_data: bytes, options: dict | None = None, **kwargs: Any
    ) -> Any:
        result = self._invoke("recognize", image_data, options, **kwargs)
        name = _pipeline_name((options or {}).get("pipeline"), default="OCR")
        if name:
            self._state.record_loaded(self._worker, [name])
        return result

    def recognize_batch(
        self, images: list[bytes], options: dict | None = None, **kwargs: Any
    ) -> Any:
        result = self._invoke("recognize_batch", images, options, **kwargs)
        name = _pipeline_name((options or {}).get("pipeline"), default="OCR")
        if name:
            self._state.record_loaded(self._worker, [name])
        return result

    def preload_pipelines(self, pipelines: list[str], *args: Any, **kwargs: Any) -> Any:
        result = self._invoke("preload_pipelines", pipelines, *args, **kwargs)
        if isinstance(result, dict):
            loaded = [str(name) for name, ok in result.items() if ok]
            self._state.record_loaded(self._worker, loaded)
        return result

    def warmup_pipelines(self, pipelines: list[str], *args: Any, **kwargs: Any) -> Any:
        result = self._invoke("warmup_pipelines", pipelines, *args, **kwargs)
        if isinstance(result, dict):
            loaded = [str(name) for name, ok in result.items() if ok]
            self._state.record_loaded(self._worker, loaded)
        return result

    def release_pipelines(self, *args: Any, **kwargs: Any) -> Any:
        result = self._invoke("release_pipelines", *args, **kwargs)
        if isinstance(result, (list, tuple, set)):
            self._state.record_released(self._worker, [str(name) for name in result])
        return result

    def set_ttls(self, pipeline_ttls: dict[str, int], *args: Any, **kwargs: Any) -> Any:
        result = self._invoke("set_ttls", pipeline_ttls, *args, **kwargs)
        if result:
            self._state.record_ttls(pipeline_ttls)
            # Capture pipelines that were already loaded when the user changed
            # their TTL to zero.  This is a second sequential RPC under the
            # manager's existing SHM lock, not a nested lock acquisition.
            with contextlib.suppress(Exception):
                status = self._worker.cache_status(
                    timeout=Constants.Timeout.WORKER_TIMEOUT
                )
                self._state.record_status(self._worker, status)
        return result

    def cache_status(self, *args: Any, **kwargs: Any) -> Any:
        status = self._invoke("cache_status", *args, **kwargs)
        self._state.record_status(self._worker, status)
        return status


class WorkerRuntimeState:
    """Manager-side snapshot of worker TTL policy and loaded pipelines."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pipeline_ttls: dict[str, int] = {}
        self._loaded_by_worker: dict[int, set[str]] = {}

    @staticmethod
    def _worker_key(worker: OCRWorkerProcess) -> int:
        return int(getattr(worker, "worker_id", 0))

    def wrap(self, worker: OCRWorkerProcess) -> RuntimeStateTrackingWorker:
        return RuntimeStateTrackingWorker(self, worker)

    def record_ttls(self, pipeline_ttls: dict[str, int]) -> None:
        validated: dict[str, int] = {}
        for name, ttl in pipeline_ttls.items():
            try:
                validated[str(name)] = max(0, int(ttl))
            except (TypeError, ValueError):
                logger.warning("[RuntimeState] 忽略无效 TTL: %s=%r", name, ttl)
        with self._lock:
            self._pipeline_ttls = validated

    def record_loaded(self, worker: OCRWorkerProcess, pipelines: list[str]) -> None:
        if not pipelines:
            return
        key = self._worker_key(worker)
        with self._lock:
            loaded = self._loaded_by_worker.setdefault(key, set())
            loaded.update(str(name) for name in pipelines)

    def record_released(self, worker: OCRWorkerProcess, pipelines: list[str]) -> None:
        key = self._worker_key(worker)
        with self._lock:
            loaded = self._loaded_by_worker.setdefault(key, set())
            loaded.difference_update(str(name) for name in pipelines)

    def record_status(self, worker: OCRWorkerProcess, status: object) -> None:
        if not isinstance(status, dict):
            return
        loaded_raw = status.get("loaded_pipelines")
        if not isinstance(loaded_raw, (list, tuple, set)):
            return
        key = self._worker_key(worker)
        with self._lock:
            self._loaded_by_worker[key] = {str(name) for name in loaded_raw}

    def snapshot(self, worker: OCRWorkerProcess) -> tuple[dict[str, int], list[str]]:
        """Return TTLs and previously loaded pipelines that are explicitly pinned."""
        key = self._worker_key(worker)
        with self._lock:
            ttls = dict(self._pipeline_ttls)
            loaded = set(self._loaded_by_worker.get(key, set()))
        pinned = sorted(name for name in loaded if ttls.get(name) == 0)
        return ttls, pinned

    def restore(self, worker: OCRWorkerProcess, *, reason: str) -> bool:
        """Replay TTLs and warm pinned pipelines after a worker process restart.

        A restart cannot preserve physical CUDA allocations.  This method
        restores the application-level contract immediately: TTL policy first,
        then pipeline objects, then a real warm-up inference so weights/CUDA
        state are materialized again.
        """
        ttls, pinned = self.snapshot(worker)
        if not ttls and not pinned:
            return True
        if not worker.is_ready:
            logger.warning(
                "[RuntimeState] Worker %s 未就绪，无法恢复驻留状态 (%s)",
                getattr(worker, "worker_id", "?"),
                reason,
            )
            return False

        logger.info(
            "[RuntimeState] 恢复 Worker %s 状态 (%s): ttls=%s, pinned=%s",
            getattr(worker, "worker_id", "?"),
            reason,
            ttls,
            pinned,
        )

        lock = worker._shm_lock
        if not lock.acquire(timeout=15.0):
            logger.warning(
                "[RuntimeState] Worker %s 恢复状态时获取 _shm_lock 超时",
                getattr(worker, "worker_id", "?"),
            )
            return False

        success = True
        final_status: object = None
        try:
            if ttls:
                ttl_ok = worker.set_ttls(
                    ttls, timeout=Constants.Timeout.SHM_WRITE
                )
                if not ttl_ok:
                    success = False
                    logger.warning("[RuntimeState] TTL 恢复失败: %s", ttls)

            loaded: set[str] = set()
            with contextlib.suppress(Exception):
                status = worker.cache_status(
                    timeout=Constants.Timeout.WORKER_TIMEOUT
                )
                if isinstance(status, dict):
                    loaded = {str(name) for name in status.get("loaded_pipelines", [])}

            missing = [name for name in pinned if name not in loaded]
            restored: list[str] = []
            if missing:
                preload = worker.preload_pipelines(
                    missing, timeout=Constants.Timeout.PIPELINE_PRELOAD_DEFAULT
                )
                if isinstance(preload, dict):
                    restored = [str(name) for name, ok in preload.items() if ok]
                    failed = [str(name) for name, ok in preload.items() if not ok]
                    if failed:
                        success = False
                        logger.warning("[RuntimeState] 驻留管道重载失败: %s", failed)
                else:
                    success = False

            if restored:
                warmup = worker.warmup_pipelines(
                    restored, timeout=Constants.Timeout.PIPELINE_PRELOAD_DEFAULT
                )
                if isinstance(warmup, dict):
                    failed_warmup = [
                        str(name) for name, ok in warmup.items() if not ok
                    ]
                    if failed_warmup:
                        success = False
                        logger.warning(
                            "[RuntimeState] 驻留管道预热失败: %s", failed_warmup
                        )
                else:
                    success = False

            with contextlib.suppress(Exception):
                final_status = worker.cache_status(
                    timeout=Constants.Timeout.WORKER_TIMEOUT
                )
        except Exception:
            success = False
            logger.exception(
                "[RuntimeState] Worker %s 状态恢复异常 (%s)",
                getattr(worker, "worker_id", "?"),
                reason,
            )
        finally:
            lock.release()

        if final_status is not None:
            self.record_status(worker, final_status)
        if success:
            logger.info(
                "[RuntimeState] Worker %s 状态恢复完成: pinned=%s",
                getattr(worker, "worker_id", "?"),
                pinned,
            )
        return success
