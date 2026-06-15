"""验证 env_manager 安装依赖的规格"""

import io
import tarfile
from unittest.mock import MagicMock, patch

from vibeocr.env_manager import (
    ensure_mineru_models,
    install_dependencies,
    install_embedded_dependencies,
    install_embedded_python,
)


class TestInstallStandalonePython:
    """python-build-standalone 安装测试（验证从 embeddable 迁移）"""

    @staticmethod
    def _make_standalone_tar_bytes() -> bytes:
        """构造一个最小 install_only 布局的 tar.gz：
        顶层为 install_only/python/，含 python.exe 和 Lib/site-packages/pip/__init__.py
        """
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            # 目录
            for d in ("install_only/python", "install_only/python/Lib",
                      "install_only/python/Lib/site-packages",
                      "install_only/python/Lib/site-packages/pip"):
                info = tarfile.TarInfo(name=d)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            # python.exe 占位文件
            exe_data = b"fake"
            info = tarfile.TarInfo(name="install_only/python/python.exe")
            info.size = len(exe_data)
            tar.addfile(info, io.BytesIO(exe_data))
            # pip __init__.py（证明自带 pip）
            pip_data = b"pip"
            info = tarfile.TarInfo(
                name="install_only/python/Lib/site-packages/pip/__init__.py"
            )
            info.size = len(pip_data)
            tar.addfile(info, io.BytesIO(pip_data))
        return buf.getvalue()

    def test_downloads_standalone_tar_and_extracts_no_pth_no_getpip(self, tmp_path):
        """安装应下载 .tar.gz、用 tarfile 解压，且不再写 ._pth 或下载 get-pip.py"""
        # install_embedded_python 先检查 get_environment_mode，需返回非 venv
        with (
            patch("vibeocr.env_manager.get_environment_mode", return_value="none"),
            patch(
                "vibeocr.env_manager.download_file_with_progress",
                return_value=True,
            ) as mock_dl,
            patch("tarfile.open", wraps=tarfile.open) as _mock_tar,
            patch("vibeocr.env_manager.get_embedded_python_executable",
                  return_value=tmp_path / "python" / "python.exe"),
            patch("vibeocr.env_manager.subprocess.run"),
        ):
            # 让下载函数把 tar 写到期望路径
            def _fake_dl(url, dest, *a, **kw):
                dest.write_bytes(self._make_standalone_tar_bytes())
                return True
            mock_dl.side_effect = _fake_dl

            ok, msg = install_embedded_python(tmp_path)

        assert ok, f"安装应成功: {msg}"
        # 下载被调用（至少一次），且 URL 指向 python-build-standalone
        assert mock_dl.called
        first_url = mock_dl.call_args[0][0]
        assert "python-build-standalone" in first_url
        assert first_url.endswith(".tar.gz")
        # 关键文件落盘（证明 tarfile 解压 + flatten 首层目录）
        assert (tmp_path / "python" / "python.exe").exists()
        assert (tmp_path / "python" / "Lib" / "site-packages" / "pip" / "__init__.py").exists()
        # 不应再写 ._pth 文件
        assert not any((tmp_path / "python").glob("._pth"))
        assert not any((tmp_path / "python").glob("*._pth"))

    def test_no_get_pip_download(self, tmp_path):
        """不应再下载 get-pip.py（build-standalone 自带 pip）"""
        with (
            patch("vibeocr.env_manager.get_environment_mode", return_value="none"),
            patch(
                "vibeocr.env_manager.download_file_with_progress",
                return_value=True,
            ) as mock_dl,
            patch("vibeocr.env_manager.get_embedded_python_executable",
                  return_value=tmp_path / "python" / "python.exe"),
            patch("vibeocr.env_manager.subprocess.run"),
        ):
            def _fake_dl(url, dest, *a, **kw):
                dest.write_bytes(self._make_standalone_tar_bytes())
                return True
            mock_dl.side_effect = _fake_dl
            install_embedded_python(tmp_path)

        # 所有下载 URL 都不得引用 get-pip.py
        for call in mock_dl.call_args_list:
            url = call[0][0]
            assert "get-pip.py" not in url, f"不应下载 get-pip.py: {url}"

    def test_download_failure_returns_false(self, tmp_path):
        """所有下载源都失败时应返回 False"""
        with (
            patch("vibeocr.env_manager.get_environment_mode", return_value="none"),
            patch("vibeocr.env_manager.download_file_with_progress", return_value=False),
            patch("vibeocr.env_manager.get_embedded_python_executable",
                  return_value=tmp_path / "python" / "python.exe"),
        ):
            ok, _msg = install_embedded_python(tmp_path)
        assert not ok


class TestInstallSpecs:
    """安装规格测试"""

    def test_embedded_deps_uses_core_not_all(self, tmp_path):
        """便携模式安装应使用 mineru[core] 而非 [all]"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            install_embedded_dependencies(tmp_path, progress_callback=lambda s, m: None)

        mineru_cmd = [c for c in calls if "mineru" in " ".join(c)]
        assert len(mineru_cmd) > 0, "应包含 mineru 安装命令"
        joined = " ".join(mineru_cmd[0])
        assert "mineru[core]" in joined, f"应使用 mineru[core]，实际: {joined}"
        assert "mineru[all]" not in joined

    def test_install_deps_uses_core_not_all(self, tmp_path):
        """完整安装应使用 mineru[core] 而非 [all]"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            install_dependencies(tmp_path)

        mineru_cmd = [c for c in calls if "mineru" in " ".join(c)]
        assert len(mineru_cmd) > 0
        joined = " ".join(mineru_cmd[0])
        assert "mineru[core]" in joined
        assert "mineru[all]" not in joined

    def test_embedded_deps_gpu_with_cuda_version(self, tmp_path):
        """便携模式 GPU 安装应使用 paddlepaddle-gpu"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            install_embedded_dependencies(
                tmp_path,
                use_gpu=True,
                cuda_version="12.1",
                progress_callback=lambda s, m: None,
            )

        paddle_cmd = [c for c in calls if "paddlepaddle" in " ".join(c)]
        assert len(paddle_cmd) > 0
        joined = " ".join(paddle_cmd[0])
        assert "paddlepaddle-gpu" in joined, f"应使用 paddlepaddle-gpu，实际: {joined}"
        assert "cu121" in joined

    def test_embedded_deps_gpu_without_cuda_falls_back_to_default(self, tmp_path):
        """便携模式 GPU 无 CUDA 版本时应使用默认 cu129"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            install_embedded_dependencies(
                tmp_path, use_gpu=True, progress_callback=lambda s, m: None
            )

        paddle_cmd = [c for c in calls if "paddlepaddle" in " ".join(c)]
        assert len(paddle_cmd) > 0
        joined = " ".join(paddle_cmd[0])
        assert "paddlepaddle-gpu" in joined
        assert "cu129" in joined

    def test_install_deps_gpu_with_cuda_version(self, tmp_path):
        """完整安装 GPU 应使用 paddlepaddle-gpu"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            install_dependencies(tmp_path, use_gpu=True, cuda_version="12.6")

        paddle_cmd = [c for c in calls if "paddlepaddle" in " ".join(c)]
        assert len(paddle_cmd) > 0
        joined = " ".join(paddle_cmd[0])
        assert "paddlepaddle-gpu" in joined
        assert "cu126" in joined


class TestEnsureMineruModels:
    """MinerU 模型下载测试"""

    def test_calls_models_download(self, tmp_path):
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            ok, _msg = ensure_mineru_models(tmp_path)

        assert ok
        cmd = mock_run.call_args[0][0]
        assert "mineru.cli.models_download" in " ".join(cmd)

    def test_returns_false_when_no_python(self, tmp_path):
        with patch(
            "vibeocr.env_manager.get_embedded_python_executable",
            return_value=tmp_path / "nonexistent.exe",
        ):
            ok, _msg = ensure_mineru_models(tmp_path)
        assert not ok
