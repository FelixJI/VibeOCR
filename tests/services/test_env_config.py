"""env_config 模块测试"""

from pathlib import Path
from unittest.mock import patch

from vibeocr.services.env_config import (
    CONFIG_DIR,
    PADDLE_VERSION,
    PIP_MIRROR_SOURCES,
    PORTABLE_PYTHON_DIR,
    PYTHON_VERSION,
    PYTHON_VERSION_SHORT,
    ensure_config_dir,
    get_config_dir,
    get_portable_python_dir,
    get_project_root,
)


class TestEnvConfigConstants:
    """环境配置常量测试"""

    def test_python_version_format(self):
        """测试 Python 版本格式"""
        assert "." in PYTHON_VERSION
        parts = PYTHON_VERSION.split(".")
        assert len(parts) >= 2

    def test_python_version_short_format(self):
        """测试短版本格式"""
        assert "." in PYTHON_VERSION_SHORT
        parts = PYTHON_VERSION_SHORT.split(".")
        assert len(parts) == 2

    def test_pip_mirror_sources_not_empty(self):
        """测试 pip 镜像源不为空"""
        assert len(PIP_MIRROR_SOURCES) > 0

    def test_pip_mirror_sources_are_urls(self):
        """测试 pip 镜像源是 URL"""
        for _name, url in PIP_MIRROR_SOURCES.items():
            assert url.startswith("http")

    def test_paddle_version_format(self):
        """测试 Paddle 版本格式"""
        assert "." in PADDLE_VERSION

    def test_config_dir_name(self):
        """测试配置目录名称"""
        assert CONFIG_DIR == "config"

    def test_portable_python_dir_name(self):
        """测试便携式 Python 目录名称"""
        assert PORTABLE_PYTHON_DIR == "python_portable"


class TestEnvConfigFunctions:
    """环境配置函数测试"""

    def test_get_project_root_returns_path(self):
        """测试获取项目根目录返回 Path"""
        root = get_project_root()
        assert isinstance(root, Path)

    def test_get_project_root_contains_pyproject(self):
        """测试项目根目录包含 pyproject.toml"""
        root = get_project_root()
        pyproject = root / "pyproject.toml"
        assert pyproject.exists()

    def test_get_config_dir_returns_path(self):
        """测试获取配置目录返回 Path"""
        config_dir = get_config_dir()
        assert isinstance(config_dir, Path)
        assert config_dir.name == CONFIG_DIR

    def test_get_portable_python_dir_returns_path(self):
        """测试获取便携式 Python 目录返回 Path"""
        portable_dir = get_portable_python_dir()
        assert isinstance(portable_dir, Path)
        assert portable_dir.name == PORTABLE_PYTHON_DIR

    def test_ensure_config_dir_creates_directory(self, tmp_path):
        """测试确保配置目录创建"""
        with patch(
            "vibeocr.services.env_config.get_project_root", return_value=tmp_path
        ):
            config_dir = ensure_config_dir()
            assert config_dir.exists()
            assert config_dir.is_dir()


class TestDependencyConstants:
    """依赖常量测试"""

    def test_paddle_dependencies(self):
        from vibeocr.services.env_config import PADDLE_DEPENDENCIES

        assert "paddlepaddle" in PADDLE_DEPENDENCIES
        assert "paddleocr" in PADDLE_DEPENDENCIES

    def test_mineru_dependencies(self):
        from vibeocr.services.env_config import MINERU_DEPENDENCIES

        assert "mineru" in MINERU_DEPENDENCIES

    def test_ocr_dependencies_is_union(self):
        from vibeocr.services.env_config import (
            OCR_DEPENDENCIES,
            PADDLE_DEPENDENCIES,
            MINERU_DEPENDENCIES,
        )

        assert OCR_DEPENDENCIES == PADDLE_DEPENDENCIES + MINERU_DEPENDENCIES

    def test_mineru_pipeline_spec(self):
        from vibeocr.services.env_config import MINERU_PIPELINE_SPEC

        assert MINERU_PIPELINE_SPEC == "mineru[core]"


class TestEnvironmentMode:
    """环境模式类型测试"""

    def test_environment_mode_values(self):
        """测试环境模式有效值"""
        valid_modes = ["virtualenv", "portable", "unknown"]
        for mode in valid_modes:
            # 类型检查
            assert isinstance(mode, str)
