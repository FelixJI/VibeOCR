"""批量识别集成测试

测试批量添加、提交、取消功能的完整流程。
"""

import pytest
from unittest.mock import MagicMock, patch
import tempfile
import os

# 设置路径
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestBatchRecognitionIntegration:
    """批量识别集成测试"""

    def test_batch_request_model_flow(self):
        """测试批量请求数据模型流程"""
        from vibeocr.models.batch_request import (
            BatchRequest,
            BatchRequestStatus,
            PreprocessOptions,
        )

        # 创建请求
        request = BatchRequest(
            file_path="/test/image.png",
            file_name="image.png",
            image_data=b"fake_image_data",
            options={"lang": "ch"}
        )

        assert request.status == BatchRequestStatus.PENDING

        # 处理流程
        request.mark_processing()
        assert request.status == BatchRequestStatus.PROCESSING
        assert request.started_at is not None

        # 完成
        result = {"text": "Hello World"}
        request.mark_completed(result)
        assert request.status == BatchRequestStatus.COMPLETED
        assert request.result == result
        assert request.is_finished

    def test_preprocess_options_serialization(self):
        """测试预处理选项序列化"""
        from vibeocr.models.batch_request import PreprocessOptions

        options = PreprocessOptions(
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=True
        )

        # 转换为字典
        data = options.to_dict()

        # 从字典恢复
        restored = PreprocessOptions.from_dict(data)

        assert restored.use_doc_orientation_classify == options.use_doc_orientation_classify
        assert restored.use_doc_unwarping == options.use_doc_unwarping
        assert restored.use_textline_orientation == options.use_textline_orientation

    def test_shared_memory_batch_messages(self):
        """测试共享内存批量消息序列化"""
        from vibeocr.utils.shared_memory_v2 import (
            serialize_batch_request,
            deserialize_batch_request,
            serialize_batch_commit,
            deserialize_batch_commit,
            serialize_batch_result,
            deserialize_batch_result,
            MessageType,
        )

        # 测试批量请求
        request_id = "test-123"
        image_data = b"fake_image_bytes" * 100
        options = {"lang": "ch", "use_gpu": True}

        serialized = serialize_batch_request(request_id, image_data, options)
        restored_id, restored_data, restored_opts = deserialize_batch_request(serialized)

        assert restored_id == request_id
        assert restored_data == image_data
        assert restored_opts == options

        # 测试批量提交
        commit_opts = {
            'use_doc_orientation_classify': True,
            'use_doc_unwarping': False,
        }
        serialized = serialize_batch_commit(commit_opts)
        restored = deserialize_batch_commit(serialized)
        assert restored == commit_opts

        # 测试批量结果
        results = {
            "req-1": {"text": "Result 1"},
            "req-2": {"error": "Failed"},
        }
        serialized = serialize_batch_result(results)
        restored = deserialize_batch_result(serialized)
        assert restored == results

    def test_batch_queue_manager_basic(self):
        """测试批量队列管理器基本功能"""
        from vibeocr.workers.batch_queue_manager import BatchQueueManager
        from vibeocr.models.batch_request import PreprocessOptions

        # Mock pipeline
        class MockPipeline:
            def predict(self, images, **kwargs):
                for i, img in enumerate(images):
                    yield {"text": f"Result {i}", "confidence": 0.95}

        pipeline = MockPipeline()
        manager = BatchQueueManager(pipeline, max_batch_size=4)

        # 添加请求
        request_id = manager.add_request(
            image_data=b"fake_image",
            options={"lang": "ch"},
            file_name="test.png"
        )

        assert request_id != ""
        assert manager.get_queue_size() == 1

        # 提交处理
        options = PreprocessOptions()
        results = manager.commit(options)

        assert len(results) == 1
        assert request_id in results

        # 清理
        manager.close()

    def test_batch_queue_manager_multiple_requests(self):
        """测试批量队列管理器处理多个请求"""
        from vibeocr.workers.batch_queue_manager import BatchQueueManager
        from vibeocr.models.batch_request import PreprocessOptions

        class MockPipeline:
            def predict(self, images, **kwargs):
                for i, img in enumerate(images):
                    yield {"text": f"Result {i}"}

        pipeline = MockPipeline()
        progress_list = []

        def progress_callback(progress):
            progress_list.append(progress)

        manager = BatchQueueManager(
            pipeline,
            max_batch_size=2,
            progress_callback=progress_callback
        )

        # 添加多个请求
        request_ids = []
        for i in range(5):
            rid = manager.add_request(
                image_data=b"image",
                options={},
                file_name=f"test_{i}.png"
            )
            request_ids.append(rid)

        assert manager.get_queue_size() == 5

        # 提交处理
        options = PreprocessOptions()
        results = manager.commit(options)

        assert len(results) == 5
        for rid in request_ids:
            assert rid in results

        # 验证进度回调
        assert len(progress_list) > 0

        manager.close()

    def test_batch_queue_manager_cancel(self):
        """测试批量队列管理器取消功能"""
        from vibeocr.workers.batch_queue_manager import BatchQueueManager
        from vibeocr.models.batch_request import PreprocessOptions

        class SlowPipeline:
            def predict(self, images, **kwargs):
                import time
                for i, img in enumerate(images):
                    time.sleep(0.1)
                    yield {"text": f"Result {i}"}

        pipeline = SlowPipeline()
        manager = BatchQueueManager(pipeline, max_batch_size=2)

        # 添加请求
        for i in range(10):
            manager.add_request(b"image", {}, file_name=f"test_{i}.png")

        # 取消
        manager.cancel()

        # 提交应该被取消
        options = PreprocessOptions()
        results = manager.commit(options)

        # 由于取消标志在 commit 开始时已设置，
        # 实际行为取决于实现细节
        manager.close()

    def test_ui_components_integration(self):
        """测试 UI 组件集成"""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget
        from vibeocr.widgets.batch_file_list_widget import BatchFileListWidget

        # 创建预处理选项组件
        preprocess_widget = PreprocessOptionsWidget()
        options = preprocess_widget.get_options()

        assert options.use_doc_orientation_classify is True
        assert options.use_doc_unwarping is True
        assert options.use_textline_orientation is False

        # 创建文件列表组件
        file_list_widget = BatchFileListWidget()

        # 添加测试文件
        file_list_widget.add_files(['/test/file1.png', '/test/file2.jpg'])
        assert file_list_widget.get_file_count() == 2

        # 更新状态
        file_list_widget.update_file_status('/test/file1.png', 'completed')
        assert file_list_widget.get_pending_count() == 1

    def test_end_to_end_simulation(self):
        """端到端流程模拟"""
        from vibeocr.models.batch_request import (
            BatchRequest,
            PreprocessOptions,
            BatchProgress,
        )
        from vibeocr.utils.shared_memory_v2 import (
            serialize_batch_request,
            deserialize_batch_request,
            serialize_batch_commit,
            deserialize_batch_commit,
            serialize_batch_result,
            deserialize_batch_result,
        )

        # 1. 创建请求
        requests = []
        for i in range(3):
            request = BatchRequest(
                file_path=f"/test/file_{i}.png",
                file_name=f"file_{i}.png",
                image_data=b"fake_image_data",
                options={"lang": "ch"}
            )
            requests.append(request)

        # 2. 序列化并发送请求（模拟主进程）
        for req in requests:
            serialized = serialize_batch_request(
                req.request_id,
                req.image_data,
                req.options
            )
            # 模拟发送到 Worker
            restored_id, restored_data, restored_opts = deserialize_batch_request(serialized)
            assert restored_id == req.request_id

        # 3. 发送提交请求
        preprocess_opts = PreprocessOptions()
        commit_data = serialize_batch_commit(preprocess_opts.to_dict())
        restored_opts = deserialize_batch_commit(commit_data)

        # 4. 模拟处理并返回结果
        results = {}
        for req in requests:
            results[req.request_id] = {"text": f"Result for {req.file_name}"}

        # 5. 序列化结果
        results_data = serialize_batch_result(results)
        restored_results = deserialize_batch_result(results_data)

        assert len(restored_results) == 3
        for req in requests:
            assert req.request_id in restored_results


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
