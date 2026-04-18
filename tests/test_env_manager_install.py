"""验证 env_manager 安装依赖的规格"""
from unittest.mock import MagicMock, patch
from pathlib import Path

from vibeocr.env_manager import install_embedded_dependencies, install_dependencies, ensure_mineru_models


class TestInstallSpecs:
    """安装规格测试"""

    def test_embedded_deps_uses_pipeline_not_all(self, tmp_path):
        """便携模式安装应使用 mineru[pipeline] 而非 [all]"""
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
            patch("vibeocr.env_manager.get_pip_source", return_value="https://pypi.org/simple"),
            patch("vibeocr.env_manager.get_embedded_python_executable", return_value=python_exe),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            install_embedded_dependencies(tmp_path, progress_callback=lambda s, m: None)

        mineru_cmd = [c for c in calls if "mineru" in " ".join(c)]
        assert len(mineru_cmd) > 0, "应包含 mineru 安装命令"
        joined = " ".join(mineru_cmd[0])
        assert "mineru[pipeline]" in joined, f"应使用 mineru[pipeline]，实际: {joined}"
        assert "mineru[all]" not in joined

    def test_install_deps_uses_pipeline_not_all(self, tmp_path):
        """完整安装应使用 mineru[pipeline] 而非 [all]"""
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
            patch("vibeocr.env_manager.get_pip_source", return_value="https://pypi.org/simple"),
            patch("vibeocr.env_manager.get_embedded_python_executable", return_value=python_exe),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            install_dependencies(tmp_path)

        mineru_cmd = [c for c in calls if "mineru" in " ".join(c)]
        assert len(mineru_cmd) > 0
        joined = " ".join(mineru_cmd[0])
        assert "mineru[pipeline]" in joined
        assert "mineru[all]" not in joined


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
            ok, msg = ensure_mineru_models(tmp_path)

        assert ok
        cmd = mock_run.call_args[0][0]
        assert "mineru.cli.models_download" in " ".join(cmd)

    def test_returns_false_when_no_python(self, tmp_path):
        with patch(
            "vibeocr.env_manager.get_embedded_python_executable",
            return_value=tmp_path / "nonexistent.exe",
        ):
            ok, msg = ensure_mineru_models(tmp_path)
        assert not ok
