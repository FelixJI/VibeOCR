"""Preserve OCR cache policy and eligible model residency across worker restarts.

The actual Paddle pipeline objects and CUDA context live in ``ocr_worker``.  A
child-process restart necessarily destroys them, while the parent
``OCRWorkerProcess`` object survives.  This module installs an idempotent
wrapper on that parent object and remembers:

- the latest per-pipeline TTL policy;
- which Paddle pipelines were loaded;
- the wall-clock time of their most recent completed use.

After a restart, ``TTL=0`` pipelines are restored.  Finite-TTL pipelines are
restored only while their original idle lease remains valid, and the original
``last_used`` timestamp is written back after warm-up so a restart never resets
or extends their TTL.
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
# Private SHM-only extension embedded inside pipeline_ttls.  The public
# WorkerHost schema remains unchanged; PipelineCacheManager consumes this key
# before validating real pipeline names.
_RESTORE_LAST_USED_KEY = "__vibeocr_restore_last_used_unix_ms__"


@dataclass
class _RuntimeResidencyState:
    """Parent-side control-plane snapshot for one Paddle worker object."""

    pipeline_ttls: dict[str, int] = field(default_factory=dict)
    last_used: dict[str, float] = field(default_factory=dict)
    restoring: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)

    @staticmethod
    def _normalize_ttls(raw: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for name, value in raw.items():
            if name == _RESTORE_LAST_USED_KEY:
                continue
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
            if name != "MinerU"
            and (ttl := self.pipeline_ttls.get(name)) is not None
            and ttl > 0
            and last + ttl <= now
        ]
        for name in expired:
            self.last_used.pop(name, None)

    def apply_ttls(self, raw: dict[str, int]) -> None:
        """Record a successful TTL update without reviving expired leases."""
        with self.lock:
            now = time.time()
            self._prune_expired_locked(now)
            self.pipeline_ttls = self._normalize_ttls(raw)
            # A shorter new TTL can expire an existing lease immediately.
            self._prune_expired_locked(now)

    def record_loaded(self, pipeline_name: str, *, used_at: float | None = None) -> None:
        name = str(pipeline_name)
        if name == "MinerU":
            return
        with self.lock:
            self.last_used[name] = time.time() if used_at is None else float(used_at)

    def record_released(self, pipeline_names: list[str]) -> None:
        with self.lock:
            for name in pipeline_names:
                self.last_used.pop(str(name), None)

    def record_status(self, status: object) -> None:
        """Replace the Paddle loaded set from a real worker cache snapshot."""
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
                if name == "MinerU":
                    continue
                raw_ms = timestamps.get(name)
                if isinstance(raw_ms, bool):
                    raw_ms = None
                if isinstance(raw_ms, (int, float)):
                    rebuilt[name] = min(now, max(0.0, float(raw_ms) / 1000.0))
                else:
                    rebuilt[name] = previous.get(name, now)
            self.last_used = rebuilt

    def restore_snapshot(self) -> tuple[dict[str, int], dict[str, float]]:
        """Return TTLs and still-valid Paddle leases, oldest first."""
        with self.lock:
            self._prune_expired_locked(time.time())
            ttls = dict(self.pipeline_ttls)
            ordered = dict(
                sorted(
                    (
                        (name, timestamp)
                        for name, timestamp in self.last_used.items()
                        if name != "MinerU"
                    ),
                    key=lambda item: item[1],
                )
            )
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
    """Send TTLs plus exact restored timestamps over the private SHM RPC."""
    from vibeocr.services.ocr_worker_process import (
        MSG_ACK,
        MSG_ERROR,
        MSG_SET_TTL,
        OCRWorkerProcessError,
    )

    protocol = getattr(worker, "protocol", None)
    if protocol is None:
        raise OCRWorkerProcessError("Worker 通信协议未初始化，无法恢复缓存状态")

    extended_ttls: dict[str, object] = dict(pipeline_ttls)
    extended_ttls[_RESTORE_LAST_USED_KEY] = {
        name: int(timestamp * 1000) for name, timestamp in last_used.items()
    }
    payload = json.dumps({"pipeline_ttls": extended_ttls}).encode("utf-8")
    timeout = Constants.Timeout.WORKER_TIMEOUT
    protocol.write_message(MSG_SET_TTL, payload, timeout=timeout, sender="main")
    protocol.wait_for_read(timeout=timeout)
    msg_type, data = protocol.read_message(timeout=timeout, expected_sender="worker")
    if msg_type == MSG_ACK:
        return True
    if msg_type == MSG_ERROR:
        detail = data.decode("utf-8", errors="replace")
        raise OCRWorkerProcessError(f"恢复缓存状态失败: {detail}")
    raise OCRWorkerProcessError(f"恢复缓存状态未收到 ACK: {msg_type!r}")


def _merge_mineru_status(status: object) -> object:
    """Merge MinerU's separate process state into the existing status shape."""
    if not isinstance(status, dict):
        return status
    from vibeocr.services.mineru_runtime_cache import get_mineru_runtime_cache

    merged = dict(status)
    mineru = get_mineru_runtime_cache().status()

    ttls_raw = merged.get("pipeline_ttls")
    ttls = dict(ttls_raw) if isinstance(ttls_raw, dict) else {}
    ttls["MinerU"] = int(mineru.get("ttl_seconds", 0))
    merged["pipeline_ttls"] = ttls

    loaded_raw = merged.get("loaded_pipelines")
    loaded = [str(name) for name in loaded_raw] if isinstance(loaded_raw, list) else []
    if bool(mineru.get("loaded")):
        if "MinerU" not in loaded:
            loaded.append("MinerU")
    else:
        loaded = [name for name in loaded if name != "MinerU"]
    merged["loaded_pipelines"] = sorted(loaded)

    last_raw = merged.get("last_used_unix_ms")
    last_used = dict(last_raw) if isinstance(last_raw, dict) else {}
    mineru_last = mineru.get("last_used_unix_ms")
    if isinstance(mineru_last, int):
        last_used["MinerU"] = mineru_last
    else:
        last_used.pop("MinerU", None)
    merged["last_used_unix_ms"] = last_used
    return merged


def install_ocr_worker_runtime_state_patch(worker_cls: type[Any]) -> None:
    """Install restart-safe cache tracking on ``OCRWorkerProcess``."""
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
            # Oldest-first loading makes the existing FIFO capacity rule retain
            # the most recently used heavy models when every lease cannot fit.
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
                    preload_ok = [str(name) for name, ok in preload_result.items() if ok]
                    failed = [str(name) for name, ok in preload_result.items() if not ok]
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

            # Apply TTLs after load and restore the original timestamps.  A
            # finite lease that expires during reload is therefore evicted as
            # soon as the worker watcher next runs instead of receiving a fresh
            # full TTL window.
            if not _send_state_payload(worker, pipeline_ttls, leases):
                raise RuntimeError("worker rejected restored cache state")

            try:
                state.record_status(original_cache_status(worker))
            except Exception:
                logger.debug("[RuntimeState] 恢复后读取缓存状态失败", exc_info=True)
            logger.info(
                "[RuntimeState] Worker %s 驻留状态恢复完成",
                getattr(worker, "worker_id", "?"),
            )
        except Exception:
            logger.exception(
                "[RuntimeState] Worker %s 驻留状态恢复失败",
                getattr(worker, "worker_id", "?"),
            )
            # Preserve at least the TTL policy, so the next on-demand load never
            # silently falls back to an infinite lease.
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
            # Capture models already loaded when the user changes their policy,
            # especially finite -> persistent and persistent -> finite.
            try:
                state.record_status(original_cache_status(worker))
            except Exception:
                logger.debug("[RuntimeState] TTL 更新后状态读取失败", exc_info=True)
            from vibeocr.services.mineru_runtime_cache import get_mineru_runtime_cache

            get_mineru_runtime_cache().set_ttl(int(pipeline_ttls.get("MinerU", 0)))
        return result

    @functools.wraps(original_cache_status)
    def cache_status(worker: Any, *args: Any, **kwargs: Any) -> Any:
        paddle_status = original_cache_status(worker, *args, **kwargs)
        state = _state_for(worker)
        if not state.restoring:
            state.record_status(paddle_status)
        return _merge_mineru_status(paddle_status)

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
        released = [str(name) for name in result] if isinstance(result, list) else []
        if not state.restoring:
            state.record_released(released)

        # MinerU is classified as a heavy pipeline and therefore participates
        # in both "release heavy" and "release all", despite living outside the
        # Paddle worker process.
        heavy_only = kwargs.get("heavy_only", args[0] if args else True)
        if bool(heavy_only) or heavy_only is False:
            from vibeocr.services.mineru_runtime_cache import get_mineru_runtime_cache

            if get_mineru_runtime_cache().release() and "MinerU" not in released:
                released.append("MinerU")
        return released if isinstance(result, list) else result

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
