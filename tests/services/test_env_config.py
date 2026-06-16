"""env_config 模块测试"""

from pathlib import Path
from unittest.mock import patch

from vibeocr.services.env_config import (
    CONFIG_DIR,
    OCR_CHECK_MODULES,
    PORTABLE_PYTHON_DIR,
    PYTHON_BUILD_STANDALONE_ASSET,
    PYTHON_BUILD_STANDALONE_BASE,
    PYTHON_BUILD_STANDALONE_MIRRORS,
    PYTHON_BUILD_STANDALONE_TAG,
    PYTHON_VERSION_SHORT,
    ensure_config_dir,
    get_config_dir,
    get_portable_python_dir,
    get_project_root,
)


class TestEnvConfigConstants:
    """环境配置常量测试"""

    def test_python_version_short_format(self):
        """测试短版本格式"""
        assert "." in PYTHON_VERSION_SHORT
        parts = PYTHON_VERSION_SHORT.split(".")
        assert len(parts) == 2

    def test_config_dir_name(self):
        """测试配置目录名称"""
        assert CONFIG_DIR == "config"

    def test_portable_python_dir_name(self):
        """便携式 Python 目录名应与运行时实际使用的 python/ 一致"""
        assert PORTABLE_PYTHON_DIR == "python"


class TestBuildStandaloneConstants:
    """python-build-standalone 运行时常量测试"""

    def test_tag_nonempty(self):
        assert PYTHON_BUILD_STANDALONE_TAG
        assert PYTHON_BUILD_STANDALONE_TAG.isdigit(), "tag 应为纯数字日期"

    def test_asset_is_windows_msvc_install_only_targz(self):
        """资产名应为 Windows install_only tar.gz（上游无 .zip）"""
        assert PYTHON_BUILD_STANDALONE_ASSET.startswith("cpython-")
        assert "x86_64-pc-windows-msvc" in PYTHON_BUILD_STANDALONE_ASSET
        assert PYTHON_BUILD_STANDALONE_ASSET.endswith("install_only.tar.gz")
        # 版本对齐
        assert PYTHON_VERSION_SHORT in PYTHON_BUILD_STANDALONE_ASSET
        assert PYTHON_BUILD_STANDALONE_TAG in PYTHON_BUILD_STANDALONE_ASSET

    def test_base_url_points_to_astral_release(self):
        assert PYTHON_BUILD_STANDALONE_BASE.startswith(
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
        )
        assert PYTHON_BUILD_STANDALONE_ASSET in PYTHON_BUILD_STANDALONE_BASE
        assert PYTHON_BUILD_STANDALONE_TAG in PYTHON_BUILD_STANDALONE_BASE

    def test_mirrors_nonempty_and_include_nju(self):
        """国内镜像列表非空，且包含南大镜像（最稳）"""
        assert len(PYTHON_BUILD_STANDALONE_MIRRORS) >= 1
        nju = [m for m in PYTHON_BUILD_STANDALONE_MIRRORS if "nju.edu.cn" in m]
        assert nju, "应包含南大镜像"

    def test_mirrors_include_ghproxy_fallback(self):
        """镜像列表应包含 ghproxy 公共加速前缀作为回退"""
        joined = " ".join(PYTHON_BUILD_STANDALONE_MIRRORS)
        assert "ghproxy" in joined or "gh-proxy" in joined, "应包含 ghproxy 回退"


class TestEnvConfigFunctions:
    """环境配置函数测试"""

    def test_get_project_root_returns_path(self):
        """测试获取项目根目录返回 Path"""
        root = get_project_root()
        assert isinstance(root, Path)

    def test_get_project_root_contains_src_vibeocr(self):
        """项目根目录应含 src/vibeocr（判断条件与 env_manager.get_project_root 一致）"""
        root = get_project_root()
        assert (root / "src" / "vibeocr").exists()

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


class TestOcrCheckModules:
    """OCR_CHECK_MODULES 单一依赖检测清单源测试"""

    def test_is_mapping_of_module_to_package(self):
        """OCR_CHECK_MODULES 应是 {import 模块名: 包名} 映射"""
        assert isinstance(OCR_CHECK_MODULES, dict)
        assert len(OCR_CHECK_MODULES) > 0
        for module, package in OCR_CHECK_MODULES.items():
            assert isinstance(module, str) and module
            assert isinstance(package, str) and package

    def test_covers_all_required_ocr_modules(self):
        """应覆盖全部 OCR 核心模块：paddle/paddleocr/mineru/torch"""
        keys = OCR_CHECK_MODULES.keys()
        assert "paddle" in keys, "应检测 paddle（GPU/CPU 均导入为 paddle）"
        assert "paddleocr" in keys
        assert "mineru" in keys
        assert "torch" in keys, "应检测 torch（MinerU pipeline 依赖）"

    def test_paddle_maps_to_paddlepaddle_package(self):
        """paddle 模块应对应 paddlepaddle 包（GPU/CPU 二选一）"""
        assert OCR_CHECK_MODULES["paddle"] == "paddlepaddle"

    def test_all_package_names_are_install_names(self):
        """每个 value 应是可识别的 pip 包名（小写、无版本约束）"""
        for package in OCR_CHECK_MODULES.values():
            # 包名不应包含版本操作符（清单只表达"检测哪些"，版本来自 pyproject）
            for op in (">", "=", "<", "~"):
                assert op not in package, f"{package} 不应含版本约束"
