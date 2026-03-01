"""GPU 显存监控工具

支持 NVIDIA GPU（通过 pynvml）和通用 GPU（通过 paddle.device）
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GPUMemoryInfo:
    """GPU 显存信息"""
    total: int  # 总显存 (MB)
    free: int   # 空闲显存 (MB)
    used: int   # 已用显存 (MB)
    available: bool  # 是否可用


class GPUMemoryMonitor:
    """GPU 显存监控器

    优先使用 pynvml 获取准确的显存信息，
    如果不可用则回退到 paddle.device 获取估计值。
    """

    def __init__(self, device_id: int = 0):
        """初始化显存监控器

        Args:
            device_id: GPU 设备 ID
        """
        self.device_id = device_id
        self._pynvml_available = False
        self._paddle_available = False

        # 尝试初始化 pynvml
        try:
            import pynvml
            pynvml.nvmlInit()
            self._pynvml_available = True
            self._pynvml = pynvml
            logger.info(f"pynvml 初始化成功，将使用 NVML 监控显存")
        except Exception as e:
            logger.debug(f"pynvml 不可用: {e}")

        # 检查 paddle 是否可用
        try:
            import paddle
            if paddle.is_compiled_with_cuda():
                self._paddle_available = True
                logger.info("Paddle CUDA 可用，可作为显存监控备选方案")
        except Exception as e:
            logger.debug(f"Paddle CUDA 不可用: {e}")

    def get_status(self) -> GPUMemoryInfo:
        """获取当前 GPU 显存状态

        Returns:
            GPUMemoryInfo: 显存信息
        """
        # 优先使用 pynvml
        if self._pynvml_available:
            return self._get_status_pynvml()

        # 回退到 paddle
        if self._paddle_available:
            return self._get_status_paddle()

        # 都不可用，返回默认值
        return GPUMemoryInfo(
            total=0,
            free=0,
            used=0,
            available=False
        )

    def _get_status_pynvml(self) -> GPUMemoryInfo:
        """使用 pynvml 获取显存状态"""
        try:
            handle = self._pynvml.nvmlDeviceGetHandleByIndex(self.device_id)
            mem_info = self._pynvml.nvmlDeviceGetMemoryInfo(handle)

            total_mb = mem_info.total // (1024 * 1024)
            free_mb = mem_info.free // (1024 * 1024)
            used_mb = mem_info.used // (1024 * 1024)

            return GPUMemoryInfo(
                total=total_mb,
                free=free_mb,
                used=used_mb,
                available=True
            )
        except Exception as e:
            logger.warning(f"pynvml 获取显存失败: {e}")
            return GPUMemoryInfo(total=0, free=0, used=0, available=False)

    def _get_status_paddle(self) -> GPUMemoryInfo:
        """使用 paddle 获取显存状态（估计值）"""
        try:
            import paddle

            # 获取已分配的显存
            allocated = paddle.device.cuda.memory_allocated(self.device_id)
            allocated_mb = allocated // (1024 * 1024)

            # Paddle 不直接提供总显存，使用估计值
            # 常见 GPU 显存大小
            estimated_total = 8000  # 默认假设 8GB

            return GPUMemoryInfo(
                total=estimated_total,
                free=max(0, estimated_total - allocated_mb),
                used=allocated_mb,
                available=True
            )
        except Exception as e:
            logger.warning(f"paddle 获取显存失败: {e}")
            return GPUMemoryInfo(total=0, free=0, used=0, available=False)

    def estimate_batch_size(
        self,
        avg_image_pixels: int,
        safety_factor: float = 0.7
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
            # 显存信息不可用，返回保守值
            return 4

        # 经验公式：每百万像素约需 3MB 显存
        # (基于 PP-OCR 模型的经验值)
        pixels_per_million = avg_image_pixels / 1_000_000
        mem_per_image_mb = pixels_per_million * 3

        # 计算安全 batch_size
        usable_mem = mem_info.free * safety_factor
        batch_size = int(usable_mem / mem_per_image_mb)

        # 限制在合理范围内
        return max(1, min(batch_size, 16))

    def is_available(self) -> bool:
        """检查显存监控是否可用"""
        return self._pynvml_available or self._paddle_available

    def close(self):
        """清理资源"""
        if self._pynvml_available:
            try:
                self._pynvml.nvmlShutdown()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
