"""管道缓存生命周期管理（在 worker 子进程内运行）。

接管 OCRService._pipelines 的生命周期：
- 记录每个重管道的 last_used 时间戳
- FIFO 淘汰（超并存上限时淘汰最久未用的）
- TTL 闲置回收（evict_idle）
- 显式释放（release）
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibeocr.services.ocr_service import OCRService

logger = logging.getLogger(__name__)

#: 默认 TTL（秒）。
DEFAULT_TTL_SECONDS = 300
#: 显存分档阈值（MB）。
VRAM_TIER_6GB = 6144
VRAM_TIER_12GB = 12288
#: pynvml 不可用时的回退并存上限。
FALLBACK_MAX_HEAVY = 2


def compute_max_heavy_by_vram(total_vram_mb: int) -> int:
    """按显存总量计算重管道并存上限。

    Args:
        total_vram_mb: GPU 显存总量（MB），0 表示无法读取。

    Returns:
        并存上限：≤6G=1, ≤12G=2, >12G=3, 未知=2。
    """
    if total_vram_mb <= 0:
        return FALLBACK_MAX_HEAVY
    if total_vram_mb <= VRAM_TIER_6GB:
        return 1
    if total_vram_mb <= VRAM_TIER_12GB:
        return 2
    return 3


class PipelineCacheManager:
    """管道缓存生命周期管理器。

    在 worker 子进程内实例化，由 OCRService 持有。
    """

    def __init__(
        self,
        service: OCRService,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_heavy: int | None = None,
    ) -> None:
        self._service = service
        self._ttl = ttl_seconds
        self._last_used: dict[str, float] = {}
        # max_heavy=None 时按显存自动计算
        self._max_heavy = (
            max_heavy if max_heavy is not None else self._detect_max_heavy()
        )

    def _detect_max_heavy(self) -> int:
        """读 GPU 显存总量算并存上限，失败回退。

        CPU 模式（VIBEOCR_USE_GPU != true）固定返回 1（串行更稳）。
        """
        import os

        if os.environ.get("VIBEOCR_USE_GPU", "").lower() != "true":
            return 1
        try:
            from vibeocr.utils.gpu_memory_monitor import GPUMemoryMonitor

            info = GPUMemoryMonitor().get_status()
            if info.available and info.total > 0:
                return compute_max_heavy_by_vram(info.total)
        except Exception as e:
            logger.warning(
                "[CacheManager] 检测显存失败，回退上限 %d: %s", FALLBACK_MAX_HEAVY, e
            )
        return FALLBACK_MAX_HEAVY

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    @ttl_seconds.setter
    def ttl_seconds(self, value: int) -> None:
        self._ttl = max(0, int(value))

    @property
    def max_heavy(self) -> int:
        return self._max_heavy

    def touch(self, pipeline_name: str, now: float | None = None) -> None:
        """记录管道使用时间。每次 get_or_create_pipeline 后调用。"""
        self._last_used[pipeline_name] = now if now is not None else time.time()

    def get_last_used(self, pipeline_name: str) -> float | None:
        return self._last_used.get(pipeline_name)

    def enforce_capacity(
        self, new_pipeline: str, now: float | None = None
    ) -> list[str]:
        """加载新重管道前，FIFO 淘汰至不超并存上限。

        只淘汰重管道，不动 OCR 等轻管道。不淘汰 new_pipeline 本身。

        Args:
            new_pipeline: 即将加载的管道名（排除在淘汰候选外）。
            now: 当前时间戳（测试注入用）。

        Returns:
            被释放的管道名列表。
        """
        now = now if now is not None else time.time()
        from vibeocr.core.pipelines import get_heavy_pipelines

        heavy_names = {p.value for p in get_heavy_pipelines()}
        # 当前缓存中的重管道（排除 new_pipeline）
        cached_heavy = [
            name
            for name in self._service._pipelines
            if name in heavy_names and name != new_pipeline
        ]
        evicted: list[str] = []
        while len(cached_heavy) >= self._max_heavy:
            # 按 last_used 升序，淘汰最旧的
            cached_heavy.sort(key=lambda n: self._last_used.get(n, 0.0))
            victim = cached_heavy.pop(0)
            self._release_one(victim)
            evicted.append(victim)
        return evicted

    def evict_idle(self, now: float | None = None) -> list[str]:
        """回收闲置超 TTL 的重管道。worker 主循环每次消息处理后调用。

        OCR 等轻管道不受 TTL 回收。

        Args:
            now: 当前时间戳（测试注入用）。

        Returns:
            被释放的管道名列表。
        """
        if self._ttl <= 0:
            return []
        now = now if now is not None else time.time()
        from vibeocr.core.pipelines import get_heavy_pipelines

        heavy_names = {p.value for p in get_heavy_pipelines()}
        evicted: list[str] = []
        for name in list(self._service._pipelines.keys()):
            if name not in heavy_names:
                continue  # 轻管道跳过
            last = self._last_used.get(name, 0.0)
            if last + self._ttl < now:
                self._release_one(name)
                evicted.append(name)
        if evicted:
            logger.info(
                "[CacheManager] TTL 回收 %d 个闲置管道: %s", len(evicted), evicted
            )
        return evicted

    def release(self, heavy_only: bool = True) -> list[str]:
        """显式释放管道。

        Args:
            heavy_only: True 只释放重管道，False 释放全部（含 OCR）。

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
        self._empty_cache()
        logger.info(
            "[CacheManager] release(heavy_only=%s) 释放 %d 个管道: %s",
            heavy_only,
            len(released),
            released,
        )
        return released

    def _release_one(self, pipeline_name: str) -> None:
        """释放单个管道（del + empty_cache），并清理记录。"""
        try:
            del self._service._pipelines[pipeline_name]
        except KeyError:
            pass
        self._last_used.pop(pipeline_name, None)
        self._empty_cache()

    @staticmethod
    def _empty_cache() -> None:
        """GPU 模式下回收显存碎片。"""
        try:
            import os

            if os.environ.get("VIBEOCR_USE_GPU", "").lower() == "true":
                import paddle

                paddle.device.cuda.empty_cache()
        except Exception as e:
            logger.debug("[CacheManager] empty_cache 跳过: %s", e)
