"""管道缓存生命周期管理（在 worker 子进程内运行）。

接管 OCRService._pipelines 的生命周期：
- 记录每个管道的 last_used 时间戳
- FIFO 淘汰（超并存上限时淘汰最久未用的 paddle 重管道；MinerU 不计入）
- TTL 闲置回收（后台线程每 30s tick，空缓存阻塞唤醒）
- 显式释放（release）
- 按 cache_kind 分流回收：paddle 调 paddle.device.cuda.empty_cache()，mineru 不调
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibeocr.services.ocr_service import OCRService

logger = logging.getLogger(__name__)

#: 显存分档阈值（MB）。≤8GB=1 并存，>8GB=2 并存。
VRAM_TIER_8GB = 8192
#: pynvml 不可用时的回退并存上限（保守，防 OOM）。
FALLBACK_MAX_HEAVY = 1


def compute_max_heavy_by_vram(total_vram_mb: int) -> int:
    """按显存计算 paddle 重管道并存上限。

    Args:
        total_vram_mb: GPU 显存总量（MB），0 表示无法读取。

    Returns:
        并存上限：≤8G=1, >8G=2, 未知=1。
    """
    if total_vram_mb <= 0:
        return FALLBACK_MAX_HEAVY
    if total_vram_mb <= VRAM_TIER_8GB:
        return 1
    return 2


class PipelineCacheManager:
    """管道缓存生命周期管理器。

    在 worker 子进程内实例化，由 OCRService 持有。
    """

    def __init__(
        self,
        service: OCRService,
        ttls: dict[str, int],
        max_heavy: int | None = None,
        tick_interval: float = 30.0,
    ) -> None:
        self._service = service
        self._ttls = dict(ttls)
        self._last_used: dict[str, float] = {}
        self._max_heavy = (
            max_heavy if max_heavy is not None else self._detect_max_heavy()
        )
        self._tick_interval = tick_interval
        self._stop_event = threading.Event()
        self._wakeup_event = threading.Event()
        self._thread = threading.Thread(
            target=self._tick_loop,
            name="PipelineTTLWatcher",
            daemon=True,
        )
        self._thread.start()

    def _detect_max_heavy(self) -> int:
        """读 GPU 显存总量算并存上限，失败回退。

        CPU 模式（VIBEOCR_USE_GPU != true）固定返回 1（串行更稳）。
        """
        if os.environ.get("VIBEOCR_USE_GPU", "").lower() != "true":
            return 1
        try:
            from vibeocr.utils.gpu_memory_monitor import GPUMemoryMonitor

            info = GPUMemoryMonitor().get_status()
            if info.available and info.total > 0:
                return compute_max_heavy_by_vram(info.total)
        except Exception as e:
            logger.warning(
                "[CacheManager] 检测显存失败，回退上限 %d: %s",
                FALLBACK_MAX_HEAVY,
                e,
            )
        return FALLBACK_MAX_HEAVY

    # ------------------------------------------------------------------
    # 公共属性
    # ------------------------------------------------------------------
    @property
    def ttls(self) -> dict[str, int]:
        return dict(self._ttls)

    @ttls.setter
    def ttls(self, value: dict[str, int]) -> None:
        from vibeocr.core.pipelines import get_all_pipelines

        valid_names = {p.value for p in get_all_pipelines()}
        validated: dict[str, int] = {}
        for name, ttl in value.items():
            if name not in valid_names:
                logger.warning("[CacheManager] 忽略未知管道 TTL: %s", name)
                continue
            validated[name] = max(0, int(ttl))
        self._ttls = validated

    @property
    def max_heavy(self) -> int:
        return self._max_heavy

    # ------------------------------------------------------------------
    # 时间戳 / 容量管理
    # ------------------------------------------------------------------
    def touch(self, pipeline_name: str, now: float | None = None) -> None:
        """记录管道使用时间。每次 get_or_create_pipeline 后调用。"""
        self._last_used[pipeline_name] = now if now is not None else time.time()
        self._wakeup_event.set()

    def get_last_used(self, pipeline_name: str) -> float | None:
        return self._last_used.get(pipeline_name)

    def enforce_capacity(
        self, new_pipeline: str, now: float | None = None
    ) -> list[str]:
        """加载新 paddle 重管道前，FIFO 淘汰至不超并存上限。

        只淘汰 paddle 重管道，不动 OCR/表格/公式（轻）和 MinerU（不计名额）。
        不淘汰 new_pipeline 本身。

        Args:
            new_pipeline: 即将加载的管道名（排除在淘汰候选外）。
            now: 当前时间戳（测试注入用）。

        Returns:
            被释放的管道名列表。
        """
        now = now if now is not None else time.time()
        from vibeocr.core.pipelines import get_paddle_pipelines

        paddle_names = {p.value for p in get_paddle_pipelines()}
        from vibeocr.core.pipelines import get_heavy_pipelines

        heavy_paddle_names = paddle_names & {p.value for p in get_heavy_pipelines()}
        cached_heavy = [
            name
            for name in self._service._pipelines
            if name in heavy_paddle_names and name != new_pipeline
        ]
        evicted: list[str] = []
        while len(cached_heavy) >= self._max_heavy:
            cached_heavy.sort(key=lambda n: self._last_used.get(n, 0.0))
            victim = cached_heavy.pop(0)
            self._release_one(victim)
            evicted.append(victim)
        return evicted

    def evict_idle(self, now: float | None = None) -> list[str]:
        """回收闲置超 TTL 的管道。

        ttl<=0 的管道（含所有持久管道、所有 MinerU 默认配置）不回收。
        回收动作按 cache_kind 分流：paddle 调 empty_cache，mineru 不调。

        Args:
            now: 当前时间戳（测试注入用）。

        Returns:
            被释放的管道名列表。
        """
        now = now if now is not None else time.time()
        evicted: list[str] = []
        for name in list(self._service._pipelines.keys()):
            ttl = self._ttls.get(name, 0)
            if ttl <= 0:
                continue
            last = self._last_used.get(name, 0.0)
            if last + ttl < now:
                self._release_one(name)
                evicted.append(name)
        if evicted:
            logger.info(
                "[CacheManager] TTL 回收 %d 个闲置管道: %s",
                len(evicted),
                evicted,
            )
        return evicted

    def release(self, heavy_only: bool = True) -> list[str]:
        """显式释放管道。

        Args:
            heavy_only: True 只释放重管道，False 释放全部。

        Returns:
            被释放的管道名列表。
        """
        from vibeocr.core.pipelines import get_heavy_pipelines

        heavy_names = {p.value for p in get_heavy_pipelines()}
        released: list[str] = []
        for name in list(self._service._pipelines.keys()):
            if heavy_only and name not in heavy_names:
                continue
            self._release_one(name)
            released.append(name)
        logger.info(
            "[CacheManager] release(heavy_only=%s) 释放 %d 个管道: %s",
            heavy_only,
            len(released),
            released,
        )
        return released

    def release_one(self, pipeline_name: str) -> bool:
        """显式释放单个管道并清理其使用记录。

        供运行时兼容回退使用：只丢弃发生错误的管道，不影响其他已加载模型。

        Returns:
            管道原本存在并已释放时返回 True；不存在时返回 False。
        """
        existed = pipeline_name in self._service._pipelines
        self._release_one(pipeline_name)
        if existed:
            logger.info("[CacheManager] 释放单个管道: %s", pipeline_name)
        return existed

    def status(self) -> dict[str, object]:
        """Return an immutable wire-friendly snapshot of the real worker cache."""
        loaded = sorted(str(name) for name in self._service._pipelines)
        return {
            "pipeline_ttls": dict(self._ttls),
            "max_heavy": self._max_heavy,
            "loaded_pipelines": loaded,
            "last_used_unix_ms": {
                name: int(self._last_used[name] * 1000)
                for name in loaded
                if name in self._last_used
            },
        }

    def shutdown(self) -> None:
        """停止后台 tick 线程，等待最多 2 秒退出。"""
        self._stop_event.set()
        self._wakeup_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # 后台线程
    # ------------------------------------------------------------------
    def _tick_loop(self) -> None:
        """每 tick_interval 秒做一次 evict_idle；空缓存阻塞唤醒。"""
        while not self._stop_event.is_set():
            if not self._service._pipelines:
                # 空缓存：阻塞等新管道加载，避免周期空转
                self._wakeup_event.wait(timeout=60.0)
                self._wakeup_event.clear()
                continue
            try:
                self.evict_idle()
            except Exception as e:
                logger.warning("[CacheManager] tick evict_idle 失败: %s", e)
            self._stop_event.wait(self._tick_interval)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _release_one(self, pipeline_name: str) -> None:
        """释放单个管道，按 cache_kind 决定是否调 empty_cache。"""
        self._service._pipelines.pop(pipeline_name, None)
        self._last_used.pop(pipeline_name, None)
        if self._is_paddle(pipeline_name):
            self._empty_cache()

    @staticmethod
    def _is_paddle(pipeline_name: str) -> bool:
        from vibeocr.core.pipelines import get_paddle_pipelines

        return pipeline_name in {p.value for p in get_paddle_pipelines()}

    @staticmethod
    def _empty_cache() -> None:
        """GPU 模式下回收显存碎片。"""
        try:
            if os.environ.get("VIBEOCR_USE_GPU", "").lower() == "true":
                import paddle

                paddle.device.cuda.empty_cache()
        except Exception as e:
            logger.debug("[CacheManager] empty_cache 跳过: %s", e)
