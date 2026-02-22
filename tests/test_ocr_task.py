"""Tests for OCRTask."""

import pytest
from PySide6.QtCore import QThreadPool

from vibeocr.views.main_window import OCRTask


class TestOCRTask:
    """测试 OCRTask 后台任务。"""

    def test_finished_signal_emitted(self, qapp, sample_image_with_text_bytes):
        """成功识别后发送 finished 信号。"""
        task = OCRTask(sample_image_with_text_bytes)
        results = {"finished": None, "error": None}

        def on_finished(result):
            results["finished"] = result

        def on_error(msg):
            results["error"] = msg

        task.signals.finished.connect(on_finished)
        task.signals.error.connect(on_error)

        # 直接运行（不使用线程池，便于测试）
        task.run()

        assert results["error"] is None
        assert results["finished"] is not None
        assert isinstance(results["finished"], str)

    def test_error_signal_on_invalid_data(self, qapp):
        """无效数据时发送 error 信号。"""
        task = OCRTask(b"invalid image data")
        results = {"finished": None, "error": None}

        def on_finished(result):
            results["finished"] = result

        def on_error(msg):
            results["error"] = msg

        task.signals.finished.connect(on_finished)
        task.signals.error.connect(on_error)

        task.run()

        assert results["error"] is not None
        assert results["finished"] is None

    def test_thread_pool_execution(self, qapp, qtbot, sample_image_with_text_bytes):
        """在 QThreadPool 中执行。"""
        task = OCRTask(sample_image_with_text_bytes)
        results = {"finished": None}

        def on_finished(result):
            results["finished"] = result

        task.signals.finished.connect(on_finished)

        pool = QThreadPool()
        pool.start(task)

        # 等待信号（最多 10 秒）
        with qtbot.waitSignal(task.signals.finished, timeout=10000):
            pass

        assert results["finished"] is not None
