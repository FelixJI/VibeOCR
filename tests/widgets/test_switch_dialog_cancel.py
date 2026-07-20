"""测试 SwitchWorker 协作式取消：不使用 QThread.terminate。

根因：closeEvent 直接 QThread.terminate() + 无限 wait()，强杀 Python 线程
可能发生在 pip/文件修改中间，造成 CPU/GPU 包都不完整；无限 wait 还可能
冻结关闭。修复：复用 InstallWorker 的 cancel_event + kill pip 子进程 +
关闭立即返回，由应用级生命周期注册表保活到原生线程结束。
"""

import threading
from unittest.mock import MagicMock

from vibeocr.widgets.switch_dialog import SwitchDialog, SwitchWorker


class TestSwitchWorkerCancel:
    """SwitchWorker 协作式取消（复用 InstallWorker 范式）。"""

    def test_request_cancel_sets_event_and_kills_proc(self):
        """request_cancel 设置 cancel_event 并 kill 当前子进程"""
        worker = SwitchWorker.__new__(SwitchWorker)
        worker._cancel_event = threading.Event()
        worker._current_proc = None
        worker._proc_lock = threading.Lock()

        mock_proc = MagicMock()
        worker._current_proc = mock_proc

        worker.request_cancel()

        assert worker._cancel_event.is_set()
        mock_proc.kill.assert_called_once()

    def test_request_cancel_no_proc_does_not_raise(self):
        """无子进程时 request_cancel 不抛异常"""
        worker = SwitchWorker.__new__(SwitchWorker)
        worker._cancel_event = threading.Event()
        worker._current_proc = None
        worker._proc_lock = threading.Lock()

        worker.request_cancel()
        assert worker._cancel_event.is_set()

    def test_is_cancelled_reflects_event(self):
        """is_cancelled 反映 cancel_event 状态"""
        worker = SwitchWorker.__new__(SwitchWorker)
        worker._cancel_event = threading.Event()
        assert not worker.is_cancelled()
        worker._cancel_event.set()
        assert worker.is_cancelled()

    def test_close_event_uses_request_cancel_not_terminate(self):
        """closeEvent 只请求取消并立即返回，不阻塞 GUI。"""
        dialog = SwitchDialog.__new__(SwitchDialog)
        dialog._worker = MagicMock()
        dialog._worker.isRunning.return_value = True

        event = MagicMock()
        SwitchDialog.closeEvent(dialog, event)

        # closeEvent 应调用 request_cancel，但不能 terminate 或 wait 阻塞 GUI。
        dialog._worker.request_cancel.assert_called_once()
        dialog._worker.terminate.assert_not_called()
        dialog._worker.wait.assert_not_called()
        event.accept.assert_called_once()

    def test_close_event_when_no_worker(self):
        """无 worker 时 closeEvent 直接 accept"""
        dialog = SwitchDialog.__new__(SwitchDialog)
        dialog._worker = None
        event = MagicMock()
        SwitchDialog.closeEvent(dialog, event)
        event.accept.assert_called_once()
