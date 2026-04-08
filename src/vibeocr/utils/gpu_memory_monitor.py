"""GPU 显存监控工具

通过 pynvml 监控 NVIDIA GPU 显存状态。
"""

import contextlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GPUMemoryInfo:
    """GPU 显存信息"""

    total: int  # 总显存 (MB)
    free: int  # 空闲显存 (MB)
    used: int  # 已用显存 (MB)
    available: bool  # 是否可用


class GPUMemoryMonitor:
    """GPU 显存监控器

    使用 pynvml 获取显存信息，供 MineRU 服务判断 GPU 加速能力。
    """

    def __init__(self, device_id: int = 0):
        """初始化显存监控器

        Args:
            device_id: GPU 设备 ID
        """
        self.device_id = device_id
        self._pynvml_available = False

        # 尝试初始化 pynvml
        try:
            import pynvml  # type: ignore[import-untyped]

            pynvml.nvmlInit()
            self._pynvml_available = True
            self._pynvml = pynvml
            logger.info("pynvml 初始化成功，将使用 NVML 监控显存")
        except Exception as e:
            logger.debug(f"pynvml 不可用: {e}")

    def get_status(self) -> GPUMemoryInfo:
        """获取当前 GPU 显存状态

        Returns:
            GPUMemoryInfo: 显存信息
        """
        if self._pynvml_available:
            return self._get_status_pynvml()

        return GPUMemoryInfo(total=0, free=0, used=0, available=False)

    def _get_status_pynvml(self) -> GPUMemoryInfo:
        """使用 pynvml 获取显存状态"""
        try:
            handle = self._pynvml.nvmlDeviceGetHandleByIndex(self.device_id)
            mem_info = self._pynvml.nvmlDeviceGetMemoryInfo(handle)

            total_mb = mem_info.total // (1024 * 1024)
            free_mb = mem_info.free // (1024 * 1024)
            used_mb = mem_info.used // (1024 * 1024)

            return GPUMemoryInfo(
                total=total_mb, free=free_mb, used=used_mb, available=True
            )
        except Exception as e:
            logger.warning(f"pynvml 获取显存失败: {e}")
            return GPUMemoryInfo(total=0, free=0, used=0, available=False)

    def estimate_batch_size(
        self, avg_image_pixels: int, safety_factor: float = 0.7
    ) -> int:
        """根据当前显存估算安全的 batch_size

        Args:
            avg_image_pixels: 平均图片像素数 (width * height)
            safety_factor: 安全系数 (0-1)，预留显存比例

        Returns:
            推荐的 batch_size
        """
        mem_info = self.get_status()

        if not mem_info.available:
            return 4

        pixels_per_million = avg_image_pixels / 1_000_000
        mem_per_image_mb = pixels_per_million * 3

        usable_mem = mem_info.free * safety_factor
        batch_size = int(usable_mem / mem_per_image_mb)

        return max(1, min(batch_size, 16))

    def is_available(self) -> bool:
        """检查显存监控是否可用"""
        return self._pynvml_available

    def close(self):
        """清理资源"""
        if self._pynvml_available:
            with contextlib.suppress(Exception):
                self._pynvml.nvmlShutdown()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
