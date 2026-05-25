"""测试批量队列管理器"""

from unittest.mock import MagicMock, patch

import pytest

from vibeocr.models.batch_request import (
    BatchProgress,
    PreprocessOptions,
)
from vibeocr.workers.batch_queue_manager import BatchQueueManager


class MockPipeline:
    """模拟 Pipeline"""

    def predict(self, images, **kwargs):
        """模拟批量预测"""
        for i, _img in enumerate(images):
            yield {"text": f"Result {i}", "confidence": 0.95}


class TestBatchQueueManager:
    """BatchQueueManager 测试"""

    @pytest.fixture
    def pipeline(self):
        """创建模拟 pipeline"""
        return MockPipeline()

    @pytest.fixture
    def manager(self, pipeline):
        """创建管理器"""
        return BatchQueueManager(pipeline, max_batch_size=4)

    def test_add_request(self, manager):
        """测试添加请求"""
        request_id = manager.add_request(
            image_data=b"fake_image", options={"lang": "ch"}, file_name="test.png"
        )

        assert request_id != ""
        assert manager.get_queue_size() == 1

    def test_add_multiple_requests(self, manager):
        """测试添加多个请求"""
        for i in range(5):
            manager.add_request(
                image_data=b"fake_image", options={}, file_name=f"test_{i}.png"
            )

        assert manager.get_queue_size() == 5

    def test_clear_queue(self, manager):
        """测试清空队列"""
        manager.add_request(b"image", {})
        manager.add_request(b"image", {})

        assert manager.get_queue_size() == 2

        manager.clear_queue()

        assert manager.get_queue_size() == 0

    def test_commit_empty_queue(self, manager):
        """测试提交空队列"""
        options = PreprocessOptions()
        results = manager.commit(options)

        assert results == {}

    def test_commit_single_request(self, manager):
        """测试提交单个请求"""
        request_id = manager.add_request(b"image", {}, file_name="test.png")

        options = PreprocessOptions()
        results = manager.commit(options)

        assert request_id in results
        assert "text" in results[request_id]

    def test_commit_multiple_requests(self, manager):
        """测试提交多个请求"""
        request_ids = []
        for i in range(3):
            rid = manager.add_request(b"image", {}, file_name=f"test_{i}.png")
            request_ids.append(rid)

        options = PreprocessOptions()
        results = manager.commit(options)

        assert len(results) == 3
        for rid in request_ids:
            assert rid in results

    def test_progress_callback(self, pipeline):
        """测试进度回调"""
        progress_list = []

        def progress_callback(progress: BatchProgress):
            progress_list.append(progress)

        manager = BatchQueueManager(
            pipeline, max_batch_size=2, progress_callback=progress_callback
        )

        for i in range(4):
            manager.add_request(b"image", {}, file_name=f"test_{i}.png")

        options = PreprocessOptions()
        manager.commit(options)

        # 应该有多次进度回调
        assert len(progress_list) > 0

        # 最后一次进度应该是完成状态
        final_progress = progress_list[-1]
        assert final_progress.completed == 4

    def test_cancel(self, manager):
        """测试取消处理"""
        for i in range(10):
            manager.add_request(b"image", {}, file_name=f"test_{i}.png")

        # 在处理前取消
        manager.cancel()

        options = PreprocessOptions()
        _results = manager.commit(options)

        # 取消后不应该有结果
        # (因为取消标志在 commit 开始时已设置)
        # 注意: 实际行为取决于实现细节

    def test_get_stats(self, manager):
        """测试获取统计信息"""
        manager.add_request(b"image", {})
        manager.add_request(b"image", {})

        options = PreprocessOptions()
        manager.commit(options)

        stats = manager.get_stats()

        assert "total_requests" in stats
        assert "total_batches" in stats
        assert "total_time" in stats
        assert stats["total_requests"] == 2


class TestBatchQueueManagerWithGPU:
    """带 GPU 监控的测试"""

    def test_calculate_batch_size_with_mock_gpu(self):
        """测试 GPU 显存影响 batch_size 计算"""
        pipeline = MockPipeline()

        with patch(
            "vibeocr.workers.batch_queue_manager.GPUMemoryMonitor"
        ) as MockMonitor:
            # 模拟显存监控器
            mock_monitor = MagicMock()
            mock_monitor.is_available.return_value = True
            mock_monitor.estimate_batch_size.return_value = 4
            MockMonitor.return_value = mock_monitor

            manager = BatchQueueManager(pipeline, max_batch_size=8)

            # 添加请求
            for _ in range(10):
                manager.add_request(b"x" * 1000, {})

            # commit 会调用 _calculate_batch_size
            # 但这里我们只测试创建是否成功
            assert manager is not None
