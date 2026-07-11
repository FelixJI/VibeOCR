"""测试 SwitchWorker 协作式取消：不使用 QThread.terminate。

根因：closeEvent 直接 QThread.terminate() + 无限 wait()，强杀 Python 线程
可能发生在 pip/文件修改中间，造成 CPU/GPU 包都不完整；无限 wait 还可能
冻结关闭。修复：复用 InstallWorker 的 cancel_event + kill pip 子进程 +
有界 wait 范式。
"""

import threading
from unittest.mock import MagicMock

import pytest

# 检查模块可用性
try:
    from vibeocr.widgets.switch_dialog import SwitchDialog, SwitchWorker

    HAS_MODULE = True
except ImportError:
    SwitchDialog = None  # type: ignore[assignment,misc]
    SwitchWorker = None  # type: ignore[assignment,misc]
    HAS_MODULE = False


@pytest.mark.skipif(not HAS_MODULE, reason="switch_dialog not available")
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
        """closeEvent 调用 request_cancel + 有界 wait，不调用 terminate"""
        dialog = SwitchDialog.__new__(SwitchDialog)
        dialog._worker = MagicMock()
        dialog._worker.isRunning.return_value = True

        event = MagicMock()
        SwitchDialog.closeEvent(dialog, event)

        # closeEvent 应调用 request_cancel + wait(timeout)，不调用 terminate
        dialog._worker.request_cancel.assert_called_once()
        dialog._worker.terminate.assert_not_called()
        # wait 应有超时参数（有界），不是无限 wait
        dialog._worker.wait.assert_called_once()
        wait_args = dialog._worker.wait.call_args
        # wait() 应传入了超时（位置或关键字）
        has_timeout = bool(wait_args.args) or "timeout" in wait_args.kwargs or "ms" in wait_args.kwargs
        assert has_timeout, "wait() 应有超时参数，不能无限等待"
        event.accept.assert_called_once()

    def test_close_event_when_no_worker(self):
        """无 worker 时 closeEvent 直接 accept"""
        dialog = SwitchDialog.__new__(SwitchDialog)
        dialog._worker = None
        event = MagicMock()
        SwitchDialog.closeEvent(dialog, event)
        event.accept.assert_called_once()

    def test_switch_worker_has_cancel_event_and_proc_tracking(self):
        """SwitchWorker 应有 _cancel_event 和 _current_proc 属性（协作式取消）"""
        from pathlib import Path

        worker = SwitchWorker(Path("/fake"), "gpu")
        assert hasattr(worker, "_cancel_event")
        assert hasattr(worker, "_current_proc")
        assert hasattr(worker, "_proc_lock")
        assert hasattr(worker, "request_cancel")
        assert hasattr(worker, "is_cancelled")
        assert hasattr(worker, "_on_proc")
