"""测试 BaseWorker 基类"""

import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtCore import QCoreApplication

from vibeocr.core import BaseWorker, BatchWorker


class SimpleWorker(BaseWorker):
    """简单测试 Worker"""

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self._items = items

    def _get_items(self) -> list:
        return self._items

    def _get_item_id(self, item, index: int) -> str:
        return f"item_{index}"

    def _process_item(self, item, index: int):
        return {"processed": item, "index": index}


class FailingWorker(BaseWorker):
    """会失败的测试 Worker"""

    def __init__(self, fail_at_index=None, parent=None):
        super().__init__(parent)
        self._items = ["a", "b", "c"]
        self._fail_at_index = fail_at_index

    def _get_items(self) -> list:
        return self._items

    def _get_item_id(self, item, index: int) -> str:
        return f"item_{index}"

    def _process_item(self, item, index: int):
        if self._fail_at_index == index:
            raise ValueError(f"故意失败在索引 {index}")
        return {"item": item}


class TestBaseWorker:
    """测试 BaseWorker 基类"""

    def test_worker_initialization(self, qtbot):
        """测试 Worker 初始化"""
        worker = SimpleWorker(["test"])

        assert worker._cancelled is False
        assert worker._results == {}
        assert worker.is_cancelled is False

    def test_worker_cancel(self, qtbot):
        """测试取消功能"""
        worker = SimpleWorker(["test"])

        assert worker.is_cancelled is False
        worker.cancel()
        assert worker.is_cancelled is True

    def test_worker_empty_items(self, qtbot):
        """测试空项目列表"""
        worker = SimpleWorker([])

        finished_signal = MagicMock()
        worker.finished.connect(finished_signal)

        with qtbot.waitSignal(worker.finished, timeout=1000):
            worker.start()
            worker.wait()

        finished_signal.assert_called_once()
        args = finished_signal.call_args[0][0]
        assert args == {}

    def test_worker_processing(self, qtbot):
        """测试正常处理"""
        items = ["a", "b", "c"]
        worker = SimpleWorker(items)

        progress_signals = []
        completed_signals = []

        def on_progress(current, total, message):
            progress_signals.append((current, total, message))

        def on_completed(file_path, status, result):
            completed_signals.append((file_path, status, result))

        worker.progress.connect(on_progress)
        worker.file_completed.connect(on_completed)

        with qtbot.waitSignal(worker.finished, timeout=1000):
            worker.start()
            worker.wait()

        # 应该发出进度信号
        assert len(progress_signals) == 3
        assert progress_signals[0] == (0, 3, "item_0")
        assert progress_signals[1] == (1, 3, "item_1")
        assert progress_signals[2] == (2, 3, "item_2")

        # 应该发出完成信号
        assert len(completed_signals) == 3
        for i, (file_path, status, result) in enumerate(completed_signals):
            assert file_path == f"item_{i}"
            assert status == "completed"
            assert result["index"] == i

    def test_worker_cancellation(self, qtbot):
        """测试中途中断"""
        items = list(range(100))  # 大量项目
        worker = SimpleWorker(items)

        completed_count = [0]

        def on_completed(*args):
            completed_count[0] += 1
            if completed_count[0] >= 2:
                worker.cancel()

        worker.file_completed.connect(on_completed)

        worker.start()
        worker.wait(5000)

        # 应该只处理了部分项目
        assert completed_count[0] < 100

    def test_worker_item_failure(self, qtbot):
        """测试单个项目失败"""
        worker = FailingWorker(fail_at_index=1)

        completed_signals = []

        def on_completed(file_path, status, result):
            completed_signals.append((file_path, status, result))

        worker.file_completed.connect(on_completed)

        with qtbot.waitSignal(worker.finished, timeout=1000):
            worker.start()
            worker.wait()

        # 应该完成所有项目，包括失败的
        assert len(completed_signals) == 3

        # 检查失败的项目
        failed_items = [s for s in completed_signals if s[1] == "failed"]
        assert len(failed_items) == 1
        assert failed_items[0][0] == "item_1"
        assert "error" in failed_items[0][2]

    def test_worker_error_handling(self, qtbot):
        """测试错误处理"""

        class BrokenWorker(BaseWorker):
            def _get_items(self):
                raise RuntimeError("获取项目失败")

            def _get_item_id(self, item, index):
                return str(index)

            def _process_item(self, item, index):
                return item

        worker = BrokenWorker()

        error_signal = MagicMock()
        worker.error.connect(error_signal)

        with qtbot.waitSignal(worker.error, timeout=1000):
            worker.start()
            worker.wait()

        error_signal.assert_called_once()
        assert "获取项目失败" in str(error_signal.call_args[0][0])


class TestBatchWorker:
    """测试 BatchWorker 基类"""

    def test_batch_worker_initialization(self, qtbot):
        """测试批量 Worker 初始化"""
        files = [{"path": "/test/file1.jpg", "name": "file1.jpg"}]
        worker = BatchWorker(files)

        assert worker._files == files

    def test_batch_worker_get_items(self, qtbot):
        """测试获取项目"""
        files = [
            {"path": "/test/file1.jpg", "name": "file1.jpg"},
            {"path": "/test/file2.jpg", "name": "file2.jpg"},
        ]
        worker = BatchWorker(files)

        items = worker._get_items()
        assert items == files

    def test_batch_worker_get_item_id(self, qtbot):
        """测试获取项目 ID"""
        files = [{"path": "/test/file.jpg", "name": "file.jpg"}]
        worker = BatchWorker(files)

        item_id = worker._get_item_id(files[0], 0)
        assert item_id == "/test/file.jpg"

    def test_batch_worker_get_item_id_fallback(self, qtbot):
        """测试获取项目 ID 回退"""
        files = [{"name": "file.jpg"}]  # 没有 path
        worker = BatchWorker(files)

        item_id = worker._get_item_id(files[0], 5)
        assert item_id == "file_5"

    def test_batch_worker_get_file_name(self, qtbot):
        """测试获取文件名"""
        files = [{"path": "/test/file.jpg", "name": "custom_name.jpg"}]
        worker = BatchWorker(files)

        name = worker._get_file_name(files[0])
        assert name == "custom_name.jpg"

    def test_batch_worker_get_file_name_from_path(self, qtbot):
        """测试从路径获取文件名"""
        files = [{"path": "/test/file.jpg"}]
        worker = BatchWorker(files)

        name = worker._get_file_name(files[0])
        assert name == "file.jpg"
