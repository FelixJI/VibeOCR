"""InstallWorker 的 force_backend 参数测试"""

from unittest.mock import patch

from vibeocr.widgets.install_dialog import InstallWorker


def test_force_backend_gpu_skips_detect_and_passes_force(qtbot, tmp_path):
    """force_backend='gpu' 时应跳过 detect_gpu，并透传 force_backend"""
    worker = InstallWorker(tmp_path, force_backend="gpu")

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.get_embedded_python_executable.return_value = tmp_path / "python.exe"
        # python.exe 存在，跳过嵌入式安装
        (tmp_path / "python.exe").touch()
        mock_em.install_embedded_dependencies.return_value = (True, "ok")
        # GPU 分支会调一次 detect_gpu 取 cuda_version
        mock_em.detect_gpu.return_value = (True, "cu126")

        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()

    mock_em.install_embedded_dependencies.assert_called_once()
    kwargs = mock_em.install_embedded_dependencies.call_args.kwargs
    assert kwargs.get("force_backend") == "gpu"


def test_force_backend_cpu_skips_detect(qtbot, tmp_path):
    """force_backend='cpu' 时应完全跳过 detect_gpu"""
    worker = InstallWorker(tmp_path, force_backend="cpu")

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.get_embedded_python_executable.return_value = tmp_path / "python.exe"
        (tmp_path / "python.exe").touch()
        mock_em.install_embedded_dependencies.return_value = (True, "ok")
        mock_em.detect_gpu.side_effect = AssertionError("cpu 不应调用 detect_gpu")

        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()

    kwargs = mock_em.install_embedded_dependencies.call_args.kwargs
    assert kwargs.get("force_backend") == "cpu"


def test_force_backend_none_keeps_auto_detect(qtbot, tmp_path):
    """force_backend=None 时保持自动检测（向后兼容）"""
    worker = InstallWorker(tmp_path)  # 不传 force_backend

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.get_embedded_python_executable.return_value = tmp_path / "python.exe"
        (tmp_path / "python.exe").touch()
        mock_em.detect_gpu.return_value = (True, "cu126")
        mock_em.install_embedded_dependencies.return_value = (True, "ok")

        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()

    mock_em.detect_gpu.assert_called_once()
    kwargs = mock_em.install_embedded_dependencies.call_args.kwargs
    assert "force_backend" not in kwargs or kwargs["force_backend"] is None


def test_missing_only_calls_install_missing_dependencies(qtbot, tmp_path):
    """missing_only=True 时 worker 应调 install_missing_dependencies 而非全量"""
    worker = InstallWorker(tmp_path, missing_only=True)

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.get_embedded_python_executable.return_value = tmp_path / "python.exe"
        (tmp_path / "python.exe").touch()
        mock_em.detect_gpu.return_value = (False, None)
        mock_em.install_missing_dependencies.return_value = (True, "ok")

        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()

    mock_em.install_missing_dependencies.assert_called_once()
    mock_em.install_embedded_dependencies.assert_not_called()


def test_missing_only_false_calls_install_embedded_dependencies(qtbot, tmp_path):
    """missing_only=False（缺省）时 worker 应调全量安装（向后兼容）"""
    worker = InstallWorker(tmp_path)  # 缺省 missing_only=False

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.get_embedded_python_executable.return_value = tmp_path / "python.exe"
        (tmp_path / "python.exe").touch()
        mock_em.detect_gpu.return_value = (False, None)
        mock_em.install_embedded_dependencies.return_value = (True, "ok")

        with qtbot.waitSignal(worker.completed, timeout=5000):
            worker.start()

    mock_em.install_embedded_dependencies.assert_called_once()
    mock_em.install_missing_dependencies.assert_not_called()
