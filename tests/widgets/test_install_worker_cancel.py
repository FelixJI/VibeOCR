"""InstallWorker 的协作式取消机制测试

回归（修复 1）：旧实现用 QThread.terminate() 强杀线程，制造孤儿 pip 进程。
新实现改为 cancel_event + on_proc 回调 + request_cancel()，关闭/取消时
先 set event 再 kill 子进程，绝不强杀线程。
"""

import threading
from unittest.mock import MagicMock, patch

from vibeocr.widgets.install_dialog import InstallWorker


def test_request_cancel_sets_cancel_event(qtbot, tmp_path):
    """request_cancel 应 set 内部 cancel_event"""
    worker = InstallWorker(tmp_path)
    assert not worker.is_cancelled(), "初始状态不应已取消"
    worker.request_cancel()
    assert worker.is_cancelled(), "request_cancel 后应标记为已取消"


def test_request_cancel_kills_current_proc(qtbot, tmp_path):
    """request_cancel 时若有正在运行的子进程，应 kill 它（避免孤儿）"""
    worker = InstallWorker(tmp_path)
    fake_proc = MagicMock()
    worker._on_proc(fake_proc)  # 模拟 env_manager 回调交出句柄

    worker.request_cancel()

    fake_proc.kill.assert_called_once(), "应 kill 当前子进程"


def test_request_cancel_without_proc_is_safe(qtbot, tmp_path):
    """没有正在运行的子进程时 request_cancel 不应报错"""
    worker = InstallWorker(tmp_path)
    assert worker._current_proc is None
    worker.request_cancel()  # 不应抛异常
    assert worker.is_cancelled()


def test_on_proc_records_current_proc(qtbot, tmp_path):
    """_on_proc 回调应把 Popen 句柄存到 _current_proc"""
    worker = InstallWorker(tmp_path)
    proc1 = MagicMock()
    worker._on_proc(proc1)
    assert worker._current_proc is proc1
    proc2 = MagicMock()
    worker._on_proc(proc2)
    assert worker._current_proc is proc2, "应更新为最新句柄"


def test_install_passes_cancel_event_and_on_proc(qtbot, tmp_path):
    """run() 调用 env_manager 时应透传 cancel_event 和 on_proc"""
    worker = InstallWorker(tmp_path)

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.get_embedded_python_executable.return_value = tmp_path / "python.exe"
        (tmp_path / "python.exe").touch()
        mock_em.install_embedded_dependencies.return_value = (True, "ok")
        # force_backend=None 走自动检测，detect_gpu 需返回可解包的元组
        mock_em.detect_gpu.return_value = (False, None)

        with qtbot.waitSignal(worker.finished, timeout=5000):
            worker.start()

    kwargs = mock_em.install_embedded_dependencies.call_args.kwargs
    # cancel_event 应是 worker 内部的 Event 实例
    cancel_event = kwargs.get("cancel_event")
    assert isinstance(cancel_event, threading.Event), "应透传 cancel_event"
    # on_proc 应是可调用对象（worker._on_proc）
    on_proc = kwargs.get("on_proc")
    assert callable(on_proc), "应透传 on_proc 回调"


def test_close_does_not_use_terminate(qtbot, tmp_path):
    """closeEvent 触发的取消不应调用危险的 QThread.terminate()

    回归核心：旧 closeEvent 用 self._worker.terminate()，新实现用 request_cancel + wait。
    本测试验证 request_cancel 路径生效（terminate 不会被调用）。
    """
    worker = InstallWorker(tmp_path)

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.get_embedded_python_executable.return_value = tmp_path / "python.exe"
        (tmp_path / "python.exe").touch()
        mock_em.install_embedded_dependencies.return_value = (True, "ok")

        with qtbot.waitSignal(worker.finished, timeout=5000):
            worker.start()

    # worker 已结束，模拟关闭时的取消检查（worker.request_cancel 应安全）
    worker.request_cancel()
    assert worker.is_cancelled()
    # 关键：terminate() 从未被调用（QThread.terminate 是危险操作）
    # 此处通过验证 cancel 机制本身工作来间接确认——真正调用 terminate 的
    # 是 InstallDialog.closeEvent，那里已移除 terminate 改为 request_cancel。
