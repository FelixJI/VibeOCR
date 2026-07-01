"""InstallWorker 的 WebEngine 阶段测试

验证 InstallWorker.run() 在 OCR 依赖安装成功后，按 ``webengine_manager.needs_install()``
条件触发 WebEngine 资源包下载+解压阶段，并复用既有 finished 信号语义：

- needs_install() 为真 → 调 download_and_install；成功→finished(True)；失败→finished(False)
- needs_install() 为假 → 不调 download_and_install
- report_fn 三参签名适配为 _emit_progress(stage, msg)（丢弃 dl/total）

worker.run() 内部 ``from vibeocr.services import webengine_manager as wm`` 取得的是
本模块顶部导入的同一模块对象，故直接 patch 其 needs_install / download_and_install 即可。
"""

from unittest.mock import patch

from vibeocr.services import webengine_manager as wm
from vibeocr.widgets.install_dialog import InstallWorker


def _setup_common_mocks(mock_nd, mock_em):
    """复用：NetworkDetector + env_manager 的最小桩，使流程走到 WebEngine 阶段。"""
    mock_nd.return_value.network_type = "domestic"
    # python.exe 存在 → 跳过嵌入式 Python 安装
    mock_em.get_embedded_python_executable.return_value.exists.return_value = True
    # 依赖安装成功
    mock_em.install_embedded_dependencies.return_value = (True, "ok")
    # force_backend=None 走自动检测分支
    mock_em.detect_gpu.return_value = (False, None)


def test_webengine_stage_runs_when_needs_install(qtbot, tmp_path):
    """needs_install() 为真 → 调 download_and_install；成功→finished(True)"""
    worker = InstallWorker(tmp_path, force_backend="cpu")

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
        patch.object(wm, "needs_install", return_value=True) as mock_needs,
        patch.object(wm, "download_and_install", return_value=True) as mock_dl,
    ):
        _setup_common_mocks(mock_nd, mock_em)

        with qtbot.waitSignal(worker.finished, timeout=5000) as blocker:
            worker.start()

    ok, _msg = blocker.args
    assert ok is True
    mock_needs.assert_called_once()
    mock_dl.assert_called_once()
    # detector 由 run() 顶部构造并透传
    assert mock_dl.call_args.kwargs.get("detector") is not None
    # report_fn 必须存在（三参签名适配为 _emit_progress）
    assert callable(mock_dl.call_args.kwargs.get("report_fn"))


def test_webengine_stage_failure_emits_finished_false(qtbot, tmp_path):
    """needs_install() 为真但 download_and_install 返回 False → finished(False)"""
    worker = InstallWorker(tmp_path, force_backend="cpu")

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
        patch.object(wm, "needs_install", return_value=True),
        patch.object(wm, "download_and_install", return_value=False),
    ):
        _setup_common_mocks(mock_nd, mock_em)

        with qtbot.waitSignal(worker.finished, timeout=5000) as blocker:
            worker.start()

    ok, msg = blocker.args
    assert ok is False
    assert "渲染组件" in msg


def test_webengine_stage_skipped_when_not_needed(qtbot, tmp_path):
    """needs_install() 为假 → 不调 download_and_install；finished(True)"""
    worker = InstallWorker(tmp_path, force_backend="cpu")

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
        patch.object(wm, "needs_install", return_value=False) as mock_needs,
        patch.object(wm, "download_and_install") as mock_dl,
    ):
        _setup_common_mocks(mock_nd, mock_em)

        with qtbot.waitSignal(worker.finished, timeout=5000) as blocker:
            worker.start()

    ok, _msg = blocker.args
    assert ok is True
    mock_needs.assert_called_once()
    mock_dl.assert_not_called()


def test_dependency_failure_skips_webengine_stage(qtbot, tmp_path):
    """OCR 依赖安装失败 → 不进入 WebEngine 阶段，finished(False)"""
    worker = InstallWorker(tmp_path, force_backend="cpu")

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
        patch.object(wm, "needs_install") as mock_needs,
        patch.object(wm, "download_and_install") as mock_dl,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.get_embedded_python_executable.return_value.exists.return_value = True
        mock_em.detect_gpu.return_value = (False, None)
        # 依赖安装失败
        mock_em.install_embedded_dependencies.return_value = (False, "pip 失败")

        with qtbot.waitSignal(worker.finished, timeout=5000) as blocker:
            worker.start()

    ok, msg = blocker.args
    assert ok is False
    assert "pip 失败" in msg
    # 依赖失败时不应触发 WebEngine 阶段
    mock_needs.assert_not_called()
    mock_dl.assert_not_called()
