"""InstallWorker 的 reinstall_python 参数测试"""

import logging
from unittest.mock import patch

from vibeocr.widgets.install_dialog import InstallWorker


def test_reinstall_python_calls_reinstall_then_install(qtbot, tmp_path):
    """reinstall_python=True 应先调 reinstall_embedded_python 再调 install_embedded_dependencies"""
    worker = InstallWorker(tmp_path, reinstall_python=True)

    call_order = []

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.reinstall_embedded_python.side_effect = lambda *a, **kw: (
            call_order.append("reinstall"),
            (True, "ok"),
        )[1]
        mock_em.install_embedded_dependencies.return_value = (True, "ok")
        # force_backend=None 走自动检测分支，需 mock detect_gpu
        mock_em.detect_gpu.return_value = (False, None)

        with qtbot.waitSignal(worker.finished, timeout=5000):
            worker.start()

    assert call_order == ["reinstall"], "应先调 reinstall_embedded_python"
    mock_em.install_embedded_dependencies.assert_called_once()
    # reinstall_embedded_python 应在 install_embedded_dependencies 之前调用
    method_names = [m[0] for m in mock_em.method_calls]
    assert "reinstall_embedded_python" in method_names
    assert method_names.index("reinstall_embedded_python") < method_names.index(
        "install_embedded_dependencies"
    )


def test_reinstall_python_aborts_when_reinstall_fails(qtbot, tmp_path):
    """reinstall_python=True 但 reinstall 失败时应终止，不装依赖"""
    worker = InstallWorker(tmp_path, reinstall_python=True)

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.reinstall_embedded_python.return_value = (False, "下载失败")
        # force_backend=None 走自动检测分支，需 mock detect_gpu
        mock_em.detect_gpu.return_value = (False, None)

        with qtbot.waitSignal(worker.finished, timeout=5000) as blocker:
            worker.start()

    ok, msg = blocker.args
    assert not ok
    assert "下载失败" in msg
    mock_em.install_embedded_dependencies.assert_not_called()


def test_progress_signal_also_logged(qtbot, tmp_path, caplog):
    """progress 信号触发时应同时 logger.info 一份（确保 UI 进度落盘）"""
    worker = InstallWorker(tmp_path, force_backend="cpu")

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.get_embedded_python_executable.return_value = tmp_path / "python.exe"
        (tmp_path / "python.exe").touch()
        mock_em.install_embedded_dependencies.return_value = (True, "ok")

        with caplog.at_level(logging.INFO, logger="vibeocr.widgets.install_dialog"):
            with qtbot.waitSignal(worker.finished, timeout=5000):
                worker.start()

    info_msgs = " ".join(r.message for r in caplog.records)
    assert "依赖安装" in info_msgs or "安装" in info_msgs, (
        "progress 回调应同时写入 logger，便于落盘"
    )
