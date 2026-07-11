"""env_config 模块测试"""

from pathlib import Path
from unittest.mock import patch

from vibeocr.services.env_config import (
    CONFIG_DIR,
    GITEE_REPO_BASE,
    GITHUB_API_LATEST,
    GITHUB_DOWNLOAD_BASE,
    GITHUB_OWNER,
    GITHUB_PROXY_PREFIXES,
    GITHUB_RELEASES_BASE,
    GITHUB_REPO,
    GITHUB_REPO_BASE,
    LEAF_TO_TOPLEVEL,
    OCR_CHECK_LEAF_MODULES,
    OCR_CHECK_MODULES,
    PORTABLE_PYTHON_DIR,
    PYTHON_BUILD_STANDALONE_ASSET,
    PYTHON_BUILD_STANDALONE_BASE,
    PYTHON_BUILD_STANDALONE_MIRRORS,
    PYTHON_BUILD_STANDALONE_TAG,
    PYTHON_VERSION_SHORT,
    build_asset_url_pairs,
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


class TestOcrCheckLeafModules:
    """OCR_CHECK_LEAF_MODULES（paddlex[ocr] leaf 包）测试。

    这些 leaf 包是表格识别管道强制要求的，但顶层 paddleocr import 不触发其检查，
    需单独探测以暴露便携安装中途失败导致的漏装。
    """

    def test_is_mapping_of_module_to_package(self):
        """应是 {import 模块名: pip 包名} 映射"""
        assert isinstance(OCR_CHECK_LEAF_MODULES, dict)
        assert len(OCR_CHECK_LEAF_MODULES) > 0
        for module, package in OCR_CHECK_LEAF_MODULES.items():
            assert isinstance(module, str) and module
            assert isinstance(package, str) and package

    def test_covers_expected_leaf_packages(self):
        """应覆盖 paddlex[ocr] 关键 leaf 包（表格识别爆炸的根因包）"""
        pkgs = set(OCR_CHECK_LEAF_MODULES.values())
        expected = {
            "einops",
            "ftfy",
            "latex2mathml",
            "premailer",
            "regex",
            "scikit-learn",
            "scipy",
            "sentencepiece",
            "tiktoken",
            "tokenizers",
        }
        missing = expected - pkgs
        assert not missing, f"缺少 leaf 包: {missing}"

    def test_sklearn_import_name_differs_from_package(self):
        """scikit-learn 的 import 名是 sklearn（与 pip 包名不同）"""
        assert OCR_CHECK_LEAF_MODULES["sklearn"] == "scikit-learn"

    def test_no_version_constraints(self):
        """包名不应含版本约束（清单只表达'检测哪些'）"""
        for package in OCR_CHECK_LEAF_MODULES.values():
            for op in (">", "=", "<", "~"):
                assert op not in package, f"{package} 不应含版本约束"

    def test_leaf_modules_disjoint_from_top_level(self):
        """leaf 模块不应与顶层 OCR_CHECK_MODULES 重复（避免双重探测）"""
        top_imports = set(OCR_CHECK_MODULES.keys())
        leaf_imports = set(OCR_CHECK_LEAF_MODULES.keys())
        overlap = top_imports & leaf_imports
        assert not overlap, f"leaf 与顶层模块重复: {overlap}"

    def test_leaf_to_toplevel_all_map_to_paddleocr(self):
        """所有 leaf 的承载顶层包应是 paddleocr（paddleocr[doc-parser]→paddlex[ocr]）"""
        assert isinstance(LEAF_TO_TOPLEVEL, dict)
        for leaf_pkg, toplevel in LEAF_TO_TOPLEVEL.items():
            assert toplevel == "paddleocr", (
                f"{leaf_pkg} 的承载顶层包应为 paddleocr，实际 {toplevel}"
            )
            assert leaf_pkg in OCR_CHECK_LEAF_MODULES.values(), (
                f"LEAF_TO_TOPLEVEL 的 key {leaf_pkg} 应在 OCR_CHECK_LEAF_MODULES.values()"
            )


class TestReleaseRepoConstants:
    """发布仓库标识 SSOT 常量测试"""

    def test_repo_constants_nonempty(self):
        assert GITHUB_OWNER and GITHUB_REPO

    def test_releases_base_format(self):
        assert GITHUB_RELEASES_BASE.startswith("https://github.com/")
        assert GITHUB_RELEASES_BASE.endswith("/releases")

    def test_repo_base_is_repo_root(self):
        """repo 基址应指向仓库根（无 /releases 后缀），供关于页主页链接用"""
        assert GITHUB_REPO_BASE == "https://github.com/FelixJI/VibeOCR"
        # Gitee 仅保留仓库主页链接（关于页展示用），不派生 releases/download 基址
        assert GITEE_REPO_BASE == "https://gitee.com/felixjii/vibeocr"
        # releases 基址 = repo 基址 + /releases
        assert f"{GITHUB_REPO_BASE}/releases" == GITHUB_RELEASES_BASE

    def test_download_base_ends_with_download(self):
        assert GITHUB_DOWNLOAD_BASE.endswith("/download")
        # download 基址应为 releases 基址 + /download
        assert f"{GITHUB_RELEASES_BASE}/download" == GITHUB_DOWNLOAD_BASE

    def test_api_latest_format(self):
        assert "api.github.com" in GITHUB_API_LATEST
        assert "releases/latest" in GITHUB_API_LATEST

    def test_proxy_prefixes_include_ghproxy(self):
        joined = " ".join(GITHUB_PROXY_PREFIXES)
        assert "gh-proxy" in joined or "ghproxy" in joined
        assert len(GITHUB_PROXY_PREFIXES) >= 2


class TestBuildGithubAssetUrls:
    """build_github_asset_urls 工厂函数测试"""

    def test_domestic_order_three_candidates(self):
        """国内：gh-proxy → ghproxy → GitHub 裸连（3 候选）"""
        urls = build_github_asset_urls("domestic", "0.3.1", "VibeOCR-v0.3.1-win64.zip")
        assert len(urls) == 3
        assert "gh-proxy.com" in urls[0]
        assert "ghproxy.com" in urls[1]
        assert "github.com" in urls[2] and "gh-proxy" not in urls[2]

    def test_international_order_one_candidate(self):
        """海外：GitHub 直连（1 候选）"""
        urls = build_github_asset_urls(
            "international", "0.3.1", "VibeOCR-v0.3.1-win64.zip"
        )
        assert len(urls) == 1
        assert "github.com" in urls[0]

    def test_unknown_network_falls_back_to_international(self):
        """未知 network_type 走 international 分支（1 候选）"""
        urls = build_github_asset_urls("unknown", "0.3.1", "x.zip")
        assert len(urls) == 1
        assert "github.com" in urls[0]

    def test_version_prefix_in_url(self):
        """URL 中含 /v0.3.1/（带 v 前缀）"""
        urls = build_github_asset_urls("international", "0.3.1", "x.zip")
        assert all("/v0.3.1/x.zip" in u for u in urls)

    def test_domestic_proxied_urls_prefix_github_direct(self):
        """gh 代理候选是 前缀 + GitHub 直链"""
        urls = build_github_asset_urls("domestic", "0.3.1", "x.zip")
        github_direct = f"{GITHUB_DOWNLOAD_BASE}/v0.3.1/x.zip"
        assert urls[0] == "https://gh-proxy.com/" + github_direct
        assert urls[1] == "https://ghproxy.com/" + github_direct
        # 最后一个是裸 GitHub 直连
        assert urls[2] == github_direct


class TestBuildAssetUrlPairs:
    """build_asset_url_pairs：zip + sha256 成对候选，同源序"""

    def test_domestic_three_pairs_same_source_order(self):
        """国内 3 对候选，每对的 zip 与 sha 同源（host 一致）"""
        pairs = build_asset_url_pairs(
            "domestic", "0.3.1", "VibeOCR-v0.3.1-win64.zip", "VibeOCR-v0.3.1-win64.zip.sha256"
        )
        assert len(pairs) == 3
        host_order = ["gh-proxy.com", "ghproxy.com", "github.com"]
        for (zip_url, sha_url), host in zip(pairs, host_order):
            assert host in zip_url
            assert host in sha_url
            # sha 文件名正确
            assert sha_url.endswith("VibeOCR-v0.3.1-win64.zip.sha256")
            # zip 与 sha 共享同一源前缀（同 tag 目录）
            assert sha_url.replace(".sha256", "") == zip_url

    def test_international_one_pair(self):
        """海外 1 对候选：GitHub 直连"""
        pairs = build_asset_url_pairs(
            "international", "0.3.1", "x.zip", "x.zip.sha256"
        )
        assert len(pairs) == 1
        assert "github.com" in pairs[0][0]

    def test_pairs_share_source_order_with_single_version(self):
        """配对版的源序与单文件 build_github_asset_urls 完全一致"""
        net, ver, name = "domestic", "0.3.1", "x.zip"
        single = build_github_asset_urls(net, ver, name)
        pairs = build_asset_url_pairs(net, ver, name, f"{name}.sha256")
        assert [z for z, _ in pairs] == single
