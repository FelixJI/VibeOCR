"""DocUnderstandingWorker 单元测试"""


class TestDocUnderstandingWorker:
    """测试 DocUnderstandingWorker 组件。"""

    def test_worker_creation(self, qapp):
        """测试 Worker 可以被创建"""
        from vibeocr.workers.doc_understanding_worker import DocUnderstandingWorker

        worker = DocUnderstandingWorker(
            image_path="test.png",
            query="测试问题"
        )
        assert worker is not None

    def test_worker_signals(self, qapp):
        """测试 Worker 信号定义"""
        from vibeocr.workers.doc_understanding_worker import DocUnderstandingWorker

        worker = DocUnderstandingWorker(
            image_path="test.png",
            query="测试问题"
        )

        # 验证信号存在
        assert hasattr(worker, 'finished')
        assert hasattr(worker, 'error')

    def test_worker_cancel(self, qapp):
        """测试 Worker 取消功能"""
        from vibeocr.workers.doc_understanding_worker import DocUnderstandingWorker

        worker = DocUnderstandingWorker(
            image_path="test.png",
            query="测试问题"
        )

        worker.cancel()
        assert worker._cancelled is True

    def test_worker_available_models(self, qapp):
        """测试 Worker 可用模型列表"""
        from vibeocr.workers.doc_understanding_worker import DocUnderstandingWorker

        assert hasattr(DocUnderstandingWorker, 'AVAILABLE_MODELS')
        assert "PP-DocBee2-3B" in DocUnderstandingWorker.AVAILABLE_MODELS
