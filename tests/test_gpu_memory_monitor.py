"""测试 GPU 显存监控"""

from unittest.mock import patch

from vibeocr.utils.gpu_memory_monitor import (
    GPUMemoryInfo,
    GPUMemoryMonitor,
)


class TestGPUMemoryMonitor:
    """GPUMemoryMonitor 测试"""

    def test_init_without_gpu(self):
        """测试无 GPU 环境下的初始化"""
        with patch("pynvml.nvmlInit", side_effect=ImportError):
            with patch("paddle.is_compiled_with_cuda", return_value=False):
                monitor = GPUMemoryMonitor()
                assert not monitor.is_available()

    def test_get_status_unavailable(self):
        """测试 GPU 不可用时返回默认值"""
        with patch("pynvml.nvmlInit", side_effect=ImportError):
            with patch("paddle.is_compiled_with_cuda", return_value=False):
                monitor = GPUMemoryMonitor()
                status = monitor.get_status()

                assert status.available is False
                assert status.total == 0
                assert status.free == 0

    def test_estimate_batch_size_no_gpu(self):
        """测试无 GPU 时返回保守 batch_size"""
        with patch("pynvml.nvmlInit", side_effect=ImportError):
            with patch("paddle.is_compiled_with_cuda", return_value=False):
                monitor = GPUMemoryMonitor()
                # 1920x1080 图片
                batch_size = monitor.estimate_batch_size(1920 * 1080)

                # 应该返回默认保守值 4
                assert batch_size == 4

    def test_estimate_batch_size_with_mock_gpu(self):
        """测试模拟 GPU 环境下的 batch_size 估算"""
        monitor = GPUMemoryMonitor()

        # 模拟 get_status 返回
        mock_status = GPUMemoryInfo(total=8192, free=6000, used=2192, available=True)

        with patch.object(monitor, "get_status", return_value=mock_status):
            # 1920x1080 图片约 2M 像素
            batch_size = monitor.estimate_batch_size(1920 * 1080)

            # 6000MB * 0.7 / (2 * 3) ≈ 700
            # 但最大限制为 16
            assert 1 <= batch_size <= 16

    def test_estimate_batch_size_small_image(self):
        """测试小图片的 batch_size 估算"""
        monitor = GPUMemoryMonitor()

        mock_status = GPUMemoryInfo(total=8192, free=4000, used=4192, available=True)

        with patch.object(monitor, "get_status", return_value=mock_status):
            # 640x480 图片约 0.3M 像素
            batch_size = monitor.estimate_batch_size(640 * 480)

            assert batch_size >= 1

    def test_context_manager(self):
        """测试上下文管理器"""
        with GPUMemoryMonitor() as monitor:
            # 应该能正常使用
            status = monitor.get_status()
            assert isinstance(status, GPUMemoryInfo)
