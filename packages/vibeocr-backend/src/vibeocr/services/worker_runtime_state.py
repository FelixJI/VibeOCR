"""Preserve OCR worker cache policy and eligible residency across restarts.

The actual Paddle pipeline objects and CUDA context live in ``ocr_worker``.  A
child-process restart necessarily destroys those allocations, but the parent
``OCRWorkerProcess`` object survives.  This module installs a small, idempotent
wrapper on that parent object so it can remember:

- the latest per-pipeline TTL policy;
- which pipelines have been loaded;
- the wall-clock timestamp of their most recent completed use.

After every worker restart the policy is replayed.  Pipelines with ``TTL=0``
are reloaded, and finite-TTL pipelines are reloaded only while their original
idle lease is still valid.  Their original ``last_used`` timestamp is restored
after warm-up, so restarting the worker does not silently reset or extend a
finite TTL.
"""

from __future__ import annotations

import functools
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from vibeocr.core.constants import Constants

logger = logging.getLogger(__name__)

_PATCH_MARKER = "_vibeocr_runtime_residency_patch_v1"
_STATE_ATTR = "_vibeocr_runtime_residency_state"


@dataclass
class _RuntimeResidencyState:
    """Parent-side control-plane snapshot for one OCR worker object."""

    pipeline_ttls: dict[str, int] = field(default_factory=dict)
    last_used: dict[str, float] = field(default_factory=dict)
    restoring: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)

    @staticmethod
    def _normalize_ttls(raw: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for name, value in raw.items():
            if isinstance(value, bool):
                logger.warning("[RuntimeState] 忽略 bool TTL: %s=%r", name, value)
                continue
            try:
                normalized[str(name)] = max(0, int(value))
            except (TypeError, ValueError):
                logger.warning("[RuntimeState] 忽略无效 TTL: %s=%r", name, value)
        return normalized

    def _prune_expired_locked(self, now: float) -> None:
        if not self.pipeline_ttls:
            return
        expired = [
            name
            for name, last in self.last_used.items()
            if (ttl := self.pipeline_ttls.get(name)) is not None
            and ttl > 0
            and last + ttl <= now
        ]
        for name in expired:
            self.last_used.pop(name, None)

    def apply_ttls(self, raw: dict[str, int]) -> None:
        """Record a successful TTL update without reviving expired entries."""
        with self.lock:
            now = time.time()
            # First remove entries that were already expired under the old policy.
            self._prune_expired_locked(now)
            self.pipeline_ttls = self._normalize_ttls(raw)
            # A shorter new TTL may make an existing entry immediately expired.
            self._prune_expired_locked(now)

    def record_loaded(self, pipeline_name: str, *, used_at: float | None = None) -> None:
        with self.lock:
            self.last_used[str(pipeline_name)] = (
                time.time() if used_at is None else float(used_at)
            )

    def record_released(self, pipeline_names: list[str]) -> None:
        with self.lock:
            for name in pipeline_names:
                self.last_used.pop(str(name), None)

    def record_status(self, status: object) -> None:
        """Replace the loaded set from a real worker cache snapshot."""
        if not isinstance(status, dict):
            return
        loaded_raw = status.get("loaded_pipelines")
        if not isinstance(loaded_raw, (list, tuple, set)):
            return
        timestamps_raw = status.get("last_used_unix_ms")
        timestamps = timestamps_raw if isinstance(timestamps_raw, dict) else {}
        now = time.time()
        with self.lock:
            previous = dict(self.last_used)
            rebuilt: dict[str, float] = {}
            for raw_name in loaded_raw:
                name = str(raw_name)
                raw_ms = timestamps.get(name)
                if isinstance(raw_ms, bool):
                    raw_ms = None
                if isinstance(raw_ms, (int, float)):
                    rebuilt[name] = min(now, max(0.0, float(raw_ms) / 1000.0))
                else:
                    rebuilt[name] = previous.get(name, now)
            self.last_used = rebuilt

    def restore_snapshot(self) -> tuple[dict[str, int], dict[str, float]]:
        """Return TTLs and still-valid leases, ordered oldest to newest."""
        with self.lock:
            self._prune_expired_locked(time.time())
            ttls = dict(self.pipeline_ttls)
            ordered = dict(sorted(self.last_used.items(), key=lambda item: item[1]))
            return ttls, ordered


def _state_for(worker: object) -> _RuntimeResidencyState:
    state = getattr(worker, _STATE_ATTR, None)
    if isinstance(state, _RuntimeResidencyState):
        return state
    state = _RuntimeResidencyState()
    setattr(worker, _STATE_ATTR, state)
    return state


def _pipeline_name_from_options(options: object) -> str:
    value: object = "OCR"
    if isinstance(options, dict):
        value = options.get("pipeline", "OCR")
    elif options is not None and hasattr(options, "pipeline"):
        value = getattr(options, "pipeline")
    if isinstance(value, Enum):
        value = value.value
    elif hasattr(value, "value"):
        value = getattr(value, "value")
    text = str(value).strip()
    return text or "OCR"


def _send_state_payload(
    worker: Any,
    pipeline_ttls: dict[str, int],
    last_used: dict[str, float],
) -> bool:
    """Send the internal SET_TTL extension with exact restored timestamps."""
    from vibeocr.services.ocr_worker_process import (
        MSG_ACK,
        MSG_ERROR,
        MSG_SET_TTL,
        OCRWorkerProcessError,
    )

    protocol = getattr(worker, "protocol", None)
    if protocol is None:
        raise OCRWorkerProcessError("Worker 通信协议未初始化，无法恢复缓存状态")

    payload = json.dumps(
        {
            "pipeline_ttls": pipeline_ttls,
            "last_used_unix_ms": {
                name: int(timestamp * 1000) for name, timestamp in last_used.items()
            },
        }
    ).encode("utf-8")
    timeout = Constants.Timeout.WORKER_TIMEOUT
    protocol.write_message(MSG_SET_TTL, payload, timeout=timeout, sender="main")
    protocol.wait_for_read(timeout=timeout)
    msg_type, data = protocol.read_message(
        timeout=timeout, expected_sender="worker"
    )
    if msg_type == MSG_ACK:
        return True
    if msg_type == MSG_ERROR:
        detail = data.decode("utf-8", errors="replace")
        raise OCRWorkerProcessError(f"恢复缓存状态失败: {detail}")
    raise OCRWorkerProcessError(f"恢复缓存状态未收到 ACK: {msg_type!r}")


def install_ocr_worker_runtime_state_patch(worker_cls: type[Any]) -> None:
    """Install restart-safe TTL/residency tracking on ``OCRWorkerProcess``.

    The function is deliberately idempotent because test collection and the
    production import graph can import ``worker_manager`` more than once.
    """
    if getattr(worker_cls, _PATCH_MARKER, False):
        return

    original_start = worker_cls.start
    original_set_ttls = worker_cls.set_ttls
    original_cache_status = worker_cls.cache_status
    original_recognize = worker_cls.recognize
    original_recognize_batch = worker_cls.recognize_batch
    original_preload = worker_cls.preload_pipelines
    original_warmup = worker_cls.warmup_pipelines
    original_release = worker_cls.release_pipelines

    def restore_after_start(worker: Any) -> None:
        state = _state_for(worker)
        with state.lock:
            if state.restoring:
                return
            pipeline_ttls, leases = state.restore_snapshot()
            if not pipeline_ttls and not leases:
                return
            state.restoring = True

        try:
            # Only leases still valid under the latest policy are present here.
            # Oldest-first loading means FIFO capacity enforcement naturally
            # leaves the most recently used heavy pipelines resident.
            pipeline_names = list(leases)
            logger.info(
                "[RuntimeState] Worker %s 重启后恢复: ttls=%s, leases=%s",
                getattr(worker, "worker_id", "?"),
                pipeline_ttls,
                pipeline_names,
            )

            preload_ok: list[str] = []
            if pipeline_names:
                preload_result = original_preload(
                    worker,
                    pipeline_names,
                    timeout=Constants.Timeout.PIPELINE_PRELOAD_DEFAULT,
                )
                if isinstance(preload_result, dict):
                    preload_ok = [
                        str(name) for name, ok in preload_result.items() if ok
                    ]
                    failed = [
                        str(name) for name, ok in preload_result.items() if not ok
                    ]
                    if failed:
                        logger.warning(
                            "[RuntimeState] Worker %s 驻留管道重载失败: %s",
                            getattr(worker, "worker_id", "?"),
                            failed,
                        )

            if preload_ok:
                warmup_result = original_warmup(
                    worker,
                    preload_ok,
                    timeout=Constants.Timeout.PIPELINE_PRELOAD_DEFAULT,
                )
                if isinstance(warmup_result, dict):
                    failed_warmup = [
                        str(name) for name, ok in warmup_result.items() if not ok
                    ]
                    if failed_warmup:
                        logger.warning(
                            "[RuntimeState] Worker %s 驻留管道预热失败: %s",
                            getattr(worker, "worker_id", "?"),
                            failed_warmup,
                        )

            # Apply TTLs after loading, then restore the original timestamps.
            # This prevents a restart from resetting a finite TTL back to N
            # whole minutes.  The worker watcher will immediately evict any
            # lease that expired while model reloading was in progress.
            if not _send_state_payload(worker, pipeline_ttls, leases):
                raise RuntimeError("worker rejected restored cache state")

            try:
                state.record_status(original_cache_status(worker))
            except Exception:
                logger.debug(
                    "[RuntimeState] 恢复后读取缓存状态失败",
                    exc_info=True,
                )
            logger.info(
                "[RuntimeState] Worker %s 驻留状态恢复完成",
                getattr(worker, "worker_id", "?"),
            )
        except Exception:
            logger.exception(
                "[RuntimeState] Worker %s 驻留状态恢复失败",
                getattr(worker, "worker_id", "?"),
            )
            # Even if model restoration fails, preserve the TTL policy so the
            # next on-demand load does not silently become persistent.
            try:
                original_set_ttls(worker, pipeline_ttls)
            except Exception:
                logger.debug(
                    "[RuntimeState] 恢复失败后的 TTL 兜底下发也失败",
                    exc_info=True,
                )
        finally:
            with state.lock:
                state.restoring = False

    @functools.wraps(original_start)
    def start(worker: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_start(worker, *args, **kwargs)
        if getattr(worker, "is_ready", False):
            restore_after_start(worker)
        return result

    @functools.wraps(original_set_ttls)
    def set_ttls(
        worker: Any, pipeline_ttls: dict[str, int], *args: Any, **kwargs: Any
    ) -> Any:
        result = original_set_ttls(worker, pipeline_ttls, *args, **kwargs)
        state = _state_for(worker)
        if result and not state.restoring:
            state.apply_ttls(pipeline_ttls)
        return result

    @functools.wraps(original_cache_status)
    def cache_status(worker: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_cache_status(worker, *args, **kwargs)
        state = _state_for(worker)
        if not state.restoring:
            state.record_status(result)
        return result

    @functools.wraps(original_recognize)
    def recognize(worker: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_recognize(worker, *args, **kwargs)
        state = _state_for(worker)
        if not state.restoring:
            options = args[1] if len(args) > 1 else kwargs.get("options_dict")
            state.record_loaded(_pipeline_name_from_options(options))
        return result

    @functools.wraps(original_recognize_batch)
    def recognize_batch(worker: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_recognize_batch(worker, *args, **kwargs)
        state = _state_for(worker)
        if not state.restoring:
            options = args[1] if len(args) > 1 else kwargs.get("options_dict")
            state.record_loaded(_pipeline_name_from_options(options))
        return result

    @functools.wraps(original_preload)
    def preload_pipelines(worker: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_preload(worker, *args, **kwargs)
        state = _state_for(worker)
        if not state.restoring and isinstance(result, dict):
            now = time.time()
            for name, ok in result.items():
                if ok:
                    state.record_loaded(str(name), used_at=now)
        return result

    @functools.wraps(original_warmup)
    def warmup_pipelines(worker: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_warmup(worker, *args, **kwargs)
        state = _state_for(worker)
        if not state.restoring and isinstance(result, dict):
            now = time.time()
            for name, ok in result.items():
                if ok:
                    state.record_loaded(str(name), used_at=now)
        return result

    @functools.wraps(original_release)
    def release_pipelines(worker: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_release(worker, *args, **kwargs)
        state = _state_for(worker)
        if not state.restoring and isinstance(result, (list, tuple, set)):
            state.record_released([str(name) for name in result])
        return result

    worker_cls.start = start
    worker_cls.set_ttls = set_ttls
    worker_cls.cache_status = cache_status
    worker_cls.recognize = recognize
    worker_cls.recognize_batch = recognize_batch
    worker_cls.preload_pipelines = preload_pipelines
    worker_cls.warmup_pipelines = warmup_pipelines
    worker_cls.release_pipelines = release_pipelines
    setattr(worker_cls, _PATCH_MARKER, True)


__all__ = ["install_ocr_worker_runtime_state_patch"]
