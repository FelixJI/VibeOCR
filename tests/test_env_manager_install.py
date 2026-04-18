"""验证 env_manager 安装依赖的规格"""
import subprocess
from unittest.mock import MagicMock, patch

from vibeocr.env_manager import install_embedded_dependencies, install_dependencies


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
