# tests/workers/test_extraction_worker.py
import pytest
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication

from vibeocr.workers.extraction_worker import ExtractionWorker
from vibeocr.models.extraction_options import ExtractionOptions


class TestExtractionWorker:
    def test_create_worker(self, qtbot):
        """测试创建 Worker"""
        options = ExtractionOptions()
        worker = ExtractionWorker(
            service=Mock(),
            files=[{"path": "/tmp/test.png", "name": "test.png"}],
            keys=["姓名", "日期"],
            options=options
        )
        assert worker is not None
        assert not worker._cancelled

    def test_cancel_worker(self, qtbot):
        """测试取消 Worker"""
        worker = ExtractionWorker(
            service=Mock(),
            files=[],
            keys=[],
            options=ExtractionOptions()
        )
        worker.cancel()
        assert worker._cancelled is True

    def test_worker_signals(self, qtbot):
        """测试 Worker 信号定义"""
        worker = ExtractionWorker(
            service=Mock(),
            files=[],
            keys=[],
            options=ExtractionOptions()
        )
        # 验证信号存在
        assert hasattr(worker, 'progress')
        assert hasattr(worker, 'file_completed')
        assert hasattr(worker, 'finished')
        assert hasattr(worker, 'error')
