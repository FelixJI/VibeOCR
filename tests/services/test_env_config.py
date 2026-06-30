"""env_config 模块测试"""

from pathlib import Path
from unittest.mock import patch

from vibeocr.services.env_config import (
    CONFIG_DIR,
    GITHUB_API_LATEST,
    GITHUB_DOWNLOAD_BASE,
    GITHUB_OWNER,
    GITHUB_PROXY_PREFIXES,
    GITHUB_RELEASES_BASE,
    GITHUB_REPO,
    GITHUB_REPO_BASE,
    GITEE_API_LATEST,
    GITEE_DOWNLOAD_BASE,
    GITEE_OWNER,
    GITEE_RELEASES_BASE,
    GITEE_REPO,
    GITEE_REPO_BASE,
    OCR_CHECK_MODULES,
    PORTABLE_PYTHON_DIR,
    PYTHON_BUILD_STANDALONE_ASSET,
    PYTHON_BUILD_STANDALONE_BASE,
    PYTHON_BUILD_STANDALONE_MIRRORS,
    PYTHON_BUILD_STANDALONE_TAG,
    PYTHON_VERSION_SHORT,
    build_github_asset_urls,
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


class TestReleaseRepoConstants:
    """发布仓库标识 SSOT 常量测试"""

    def test_repo_constants_nonempty(self):
        assert GITHUB_OWNER and GITHUB_REPO
        assert GITEE_OWNER and GITEE_REPO

    def test_releases_base_format(self):
        assert GITHUB_RELEASES_BASE.startswith("https://github.com/")
        assert GITHUB_RELEASES_BASE.endswith("/releases")
        assert GITEE_RELEASES_BASE.startswith("https://gitee.com/")
        assert GITEE_RELEASES_BASE.endswith("/releases")

    def test_repo_base_is_repo_root(self):
        """repo 基址应指向仓库根（无 /releases 后缀），供关于页主页链接用"""
        assert GITHUB_REPO_BASE == "https://github.com/FelixJI/VibeOCR"
        assert GITEE_REPO_BASE == "https://gitee.com/felixjii/vibeocr"
        # releases 基址 = repo 基址 + /releases
        assert GITHUB_RELEASES_BASE == f"{GITHUB_REPO_BASE}/releases"
        assert GITEE_RELEASES_BASE == f"{GITEE_REPO_BASE}/releases"

    def test_download_base_ends_with_download(self):
        assert GITHUB_DOWNLOAD_BASE.endswith("/download")
        assert GITEE_DOWNLOAD_BASE.endswith("/download")
        # download 基址应为 releases 基址 + /download
        assert GITHUB_DOWNLOAD_BASE == f"{GITHUB_RELEASES_BASE}/download"
        assert GITEE_DOWNLOAD_BASE == f"{GITEE_RELEASES_BASE}/download"

    def test_api_latest_format(self):
        assert "api.github.com" in GITHUB_API_LATEST
        assert "releases/latest" in GITHUB_API_LATEST
        assert "gitee.com/api/v5" in GITEE_API_LATEST
        assert "releases/latest" in GITEE_API_LATEST

    def test_proxy_prefixes_include_ghproxy(self):
        joined = " ".join(GITHUB_PROXY_PREFIXES)
        assert "gh-proxy" in joined or "ghproxy" in joined
        assert len(GITHUB_PROXY_PREFIXES) >= 2


class TestBuildGithubAssetUrls:
    """build_github_asset_urls 工厂函数测试"""

    def test_domestic_order_four_candidates(self):
        """国内：Gitee → gh-proxy → ghproxy → GitHub 裸连（4 候选）"""
        urls = build_github_asset_urls("domestic", "0.3.1", "VibeOCR-v0.3.1-win64.zip")
        assert len(urls) == 4
        assert "gitee.com" in urls[0]
        assert "gh-proxy.com" in urls[1]
        assert "ghproxy.com" in urls[2]
        assert "github.com" in urls[3] and "gh-proxy" not in urls[3]

    def test_international_order_two_candidates(self):
        """海外：GitHub 直连 → Gitee（2 候选）"""
        urls = build_github_asset_urls(
            "international", "0.3.1", "VibeOCR-v0.3.1-win64.zip"
        )
        assert len(urls) == 2
        assert "github.com" in urls[0]
        assert "gitee.com" in urls[1]

    def test_unknown_network_falls_back_to_international(self):
        """未知 network_type 走 international 分支（2 候选）"""
        urls = build_github_asset_urls("unknown", "0.3.1", "x.zip")
        assert len(urls) == 2
        assert "github.com" in urls[0]

    def test_version_prefix_in_url(self):
        """URL 中含 /v0.3.1/（带 v 前缀）"""
        urls = build_github_asset_urls("international", "0.3.1", "x.zip")
        assert all("/v0.3.1/x.zip" in u for u in urls)

    def test_domestic_proxied_urls_prefix_github_direct(self):
        """gh 代理候选是 前缀 + GitHub 直链"""
        urls = build_github_asset_urls("domestic", "0.3.1", "x.zip")
        github_direct = f"{GITHUB_DOWNLOAD_BASE}/v0.3.1/x.zip"
        assert urls[1] == "https://gh-proxy.com/" + github_direct
        assert urls[2] == "https://ghproxy.com/" + github_direct
        # 最后一个是裸 GitHub 直连
        assert urls[3] == github_direct
