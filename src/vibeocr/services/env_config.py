"""环境配置模块

集中管理环境相关的配置常量和工具函数。
"""

import sys
from pathlib import Path

# Python 版本（仅保留短版本；完整版本由 PYTHON_VERSION_SHORT + PATCH 拼出）
PYTHON_VERSION_SHORT = "3.13"

# ---------------------------------------------------------------------------
# python-build-standalone 运行时（替代 embeddable 发行版）
# ---------------------------------------------------------------------------
# 上游：https://github.com/astral-sh/python-build-standalone
# 升级时仅改这两个常量：BUILD_TAG（astral release tag）与 PATCH（对应 cpython 补丁号）
PYTHON_BUILD_STANDALONE_TAG = "20260325"  # astral release tag
PYTHON_BUILD_STANDALONE_PATCH = "12"  # cpython 3.13 补丁号 → 3.13.12
# Windows install_only 资产（上游仅发布 .tar.gz，无 .zip）
PYTHON_BUILD_STANDALONE_ASSET = (
    f"cpython-{PYTHON_VERSION_SHORT}.{PYTHON_BUILD_STANDALONE_PATCH}"
    f"+{PYTHON_BUILD_STANDALONE_TAG}"
    "-x86_64-pc-windows-msvc-install_only.tar.gz"
)
# GitHub 直链
PYTHON_BUILD_STANDALONE_BASE = (
    "https://github.com/astral-sh/python-build-standalone/releases/download"
    f"/{PYTHON_BUILD_STANDALONE_TAG}/{PYTHON_BUILD_STANDALONE_ASSET}"
)
# 国内镜像与加速前缀（按优先级顺序尝试）
PYTHON_BUILD_STANDALONE_MIRRORS = [
    # 南大镜像：与上游 release 同步，最稳
    f"https://mirror.nju.edu.cn/github-release/astral-sh/python-build-standalone/"
    f"{PYTHON_BUILD_STANDALONE_TAG}/{PYTHON_BUILD_STANDALONE_ASSET}",
    # ghproxy 公共加速前缀（拼接 GitHub 直链）
    "https://gh-proxy.com/" + PYTHON_BUILD_STANDALONE_BASE,
    "https://ghproxy.com/" + PYTHON_BUILD_STANDALONE_BASE,
]

# ---------------------------------------------------------------------------
# 发布仓库标识（SSOT）—— update_service / about_tab 共享
# ---------------------------------------------------------------------------
# 发布渠道：CNB 仅镜像代码；产物唯一源 GitHub（国内走 gh 代理加速）。
# Gitee 不再作为下载/发版源，仅保留仓库主页链接供关于页展示。
GITHUB_OWNER = "FelixJI"
GITHUB_REPO = "VibeOCR"

# repo 根：仓库主页（关于页"项目主页"链接用）
GITHUB_REPO_BASE = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
# Gitee 仓库主页：仅作代码仓库展示（关于页），不参与下载
GITEE_REPO_BASE = "https://gitee.com/felixjii/vibeocr"
# releases 页：发布列表（手动下载兜底链接用）
GITHUB_RELEASES_BASE = f"{GITHUB_REPO_BASE}/releases"
GITHUB_DOWNLOAD_BASE = f"{GITHUB_RELEASES_BASE}/download"  # .../download/v{ver}/{asset}

# GitHub Release API（latest）
GITHUB_API_LATEST = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)

# GitHub 加速代理前缀（拼接在 GitHub 直链之前，按优先级）
# 与 PYTHON_BUILD_STANDALONE_MIRRORS 一致的加速策略
GITHUB_PROXY_PREFIXES = ["https://gh-proxy.com/", "https://ghproxy.com/"]


def _ordered_download_prefixes(network_type: str) -> list[str]:
    """返回各下载源的「直链前缀」候选，按 network_type 决定优先级顺序。

    每个前缀与 asset 名拼接即得完整下载 URL。约定：
    - 空串 ""：GitHub 裸连（GITHUB_DOWNLOAD_BASE 由调用方拼）
    - 代理前缀：拼在 GitHub 直链之前

    这里改用「前缀」表达，是因为同一源的 zip 与 sha256 需要分别拼 URL，
    但共享同一源序——用前缀列表配对最清晰。
    国内(domestic)：gh-proxy → ghproxy → GitHub 裸连（3 候选）
    海外(international)：GitHub 直连（1 候选）
    未知 network_type 按国际（直连优先）处理。
    """
    if network_type == "domestic":
        return [*GITHUB_PROXY_PREFIXES, GITHUB_DOWNLOAD_BASE]
    return [GITHUB_DOWNLOAD_BASE]


def _asset_url(prefix: str, version: str, asset_name: str) -> str:
    """按源前缀拼单个 asset 的完整下载 URL。

    代理前缀（gh-proxy / ghproxy）需拼在 GitHub 直链之前，GitHub 直连前缀
    自身已是完整基址。
    """
    github_url = f"{GITHUB_DOWNLOAD_BASE}/v{version}/{asset_name}"
    if prefix in GITHUB_PROXY_PREFIXES:
        return prefix + github_url
    return f"{prefix}/v{version}/{asset_name}"


def build_github_asset_urls(
    network_type: str, version: str, asset_name: str
) -> list[str]:
    """构造某个 GitHub release asset 的有序下载候选 URL 列表。

    国内(domestic)：gh-proxy → ghproxy → GitHub 裸连（3 候选）
    海外(international)：GitHub 直连（1 候选）
    未知 network_type 按国际（直连优先）处理。

    Args:
        network_type: "domestic" 或 "international"
        version: 版本号（不含 v 前缀，如 "0.3.1"）
        asset_name: 资产文件名，如 "VibeOCR-v0.3.1-win64.zip"

    Returns:
        有序 URL 候选列表，调用方逐个尝试直至下载成功
    """
    return [
        _asset_url(p, version, asset_name)
        for p in _ordered_download_prefixes(network_type)
    ]


def build_asset_url_pairs(
    network_type: str, version: str, zip_name: str, sha_name: str
) -> list[tuple[str, str]]:
    """构造 zip + 校验文件的成对下载候选（同源序，源序与 build_github_asset_urls 一致）。

    与单文件版本不同：每个候选源同时给出 zip_url 与 sha_url，二者来自同一源、
    同一 tag 目录，确保校验文件和被校验文件确实同源同版——避免此前用
    ``f"{zip_url}.sha256"`` 盲拼、可能下到无关/404 内容的问题。

    Args:
        network_type: "domestic" 或 "international"
        version: 版本号（不含 v 前缀）
        zip_name: zip 资产文件名
        sha_name: 对应 sha256 资产文件名

    Returns:
        有序 (zip_url, sha_url) 候选对列表
    """
    return [
        (
            _asset_url(p, version, zip_name),
            _asset_url(p, version, sha_name),
        )
        for p in _ordered_download_prefixes(network_type)
    ]

# PyTorch CUDA 镜像源
PYTORCH_MIRROR_SOURCES = {
    "nju": "https://mirrors.nju.edu.cn/pytorch/whl",
    "sjtu": "https://mirror.sjtu.edu.cn/pytorch-wheels",
    "official": "https://download.pytorch.org/whl",
}

# 默认 PyTorch 镜像源（国内）
DEFAULT_PYTORCH_MIRROR = "nju"

# 便携式 Python 目录名（与运行时实际使用的 project_root/python/ 一致）
PORTABLE_PYTHON_DIR = "python"

# 配置目录名
CONFIG_DIR = "config"

# ---------------------------------------------------------------------------
# OCR 依赖检测单一清单源（SSOT）
# ---------------------------------------------------------------------------
# {import 模块名: pip 包名} —— 检测环境时 import 模块名，结果/缓存用包名做 key。
# - paddle 模块：paddlepaddle-gpu / paddlepaddle-cpu / paddlepaddle 均导入为 paddle，
#   故只检 paddle；但它们的发行版名各异，额外候选见 OCR_DIST_NAME_ALIASES。
# - 版本约束不在此处，安装版本来自 pyproject.toml（env_manager._load_dep_specs）
OCR_CHECK_MODULES: dict[str, str] = {
    "paddle": "paddlepaddle",
    "paddleocr": "paddleocr",
    "mineru": "mineru",
    "torch": "torch",
    # markdown 已从 exe 包排除，由便携 Python 安装供 OCR/MinerU worker 用，
    # 故纳入便携环境就绪检测，避免装漏导致 worker 子进程崩溃。
    "markdown": "markdown",
}

# 同一 import 模块可能来自不同发行版名的额外候选。
# paddle 模块：paddlepaddle-gpu / paddlepaddle-cpu / paddlepaddle 均导入为 paddle，
# 但它们的 PyPI/分发发行版名各异。metadata 第一层探测只查 OCR_CHECK_MODULES
# 的归一 key（"paddlepaddle"）会漏掉 GPU/CPU 专用包（其发行版名不是 paddlepaddle），
# 导致"装了 paddlepaddle-gpu 却误报缺失"。此处补全候选，探测时任一命中即视为已安装；
# 结果 dict 仍用归一 key（"paddlepaddle"），下游（required_deps/缓存/设置页）不受影响。
OCR_DIST_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "paddlepaddle": ("paddlepaddle-gpu", "paddlepaddle-cpu"),
}

# 各模块 import 检测的 timeout（秒）。
# paddle 首次导入需初始化 CUDA 上下文，显著慢于其他模块。
OCR_CHECK_TIMEOUTS: dict[str, int] = {
    "paddle": 60,
    "paddleocr": 30,
    "mineru": 15,
    "torch": 15,
    "markdown": 10,
}


def get_pytorch_mirror(
    name: str = DEFAULT_PYTORCH_MIRROR,
    cuda_tag: str = "",
) -> str:
    """获取 PyTorch CUDA 镜像源 URL

    Args:
        name: 镜像源名称
        cuda_tag: CUDA 版本标签，如 "cu126"

    Returns:
        镜像源完整 URL，如 "https://mirrors.nju.edu.cn/pytorch/whl/cu126"
    """
    base = PYTORCH_MIRROR_SOURCES.get(
        name, PYTORCH_MIRROR_SOURCES[DEFAULT_PYTORCH_MIRROR]
    )
    if cuda_tag:
        return f"{base}/{cuda_tag}"
    return base


def is_windows() -> bool:
    """检查是否在 Windows 系统上运行"""
    return sys.platform == "win32"


def is_linux() -> bool:
    """检查是否在 Linux 系统上运行"""
    return sys.platform.startswith("linux")


def is_macos() -> bool:
    """检查是否在 macOS 系统上运行"""
    return sys.platform == "darwin"


def get_project_root() -> Path:
    """获取项目根目录

    委托 env_manager.get_project_root()，保持单一实现源（SSOT）。
    判断逻辑：打包态锚定 exe 所在目录；开发态向上查找含 src/vibeocr 的目录。
    统一调用避免两份实现在非标准布局下返回不同结果。
    """
    # 延迟导入打破循环依赖（env_manager 反向依赖本模块的常量）
    from vibeocr.env_manager import get_project_root as _get_root

    return _get_root()


def get_config_dir() -> Path:
    """获取配置目录"""
    return get_project_root() / CONFIG_DIR


def get_portable_python_dir() -> Path:
    """获取便携式 Python 目录"""
    return get_project_root() / PORTABLE_PYTHON_DIR


def ensure_config_dir() -> Path:
    """确保配置目录存在"""
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


# data 目录名
DATA_DIR = "data"


def get_data_dir() -> Path:
    """获取用户数据目录"""
    return get_project_root() / DATA_DIR


def get_update_cache_dir() -> Path:
    """获取更新下载缓存目录"""
    d = get_data_dir() / "cache" / "update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_update_settings_path() -> Path:
    """获取更新设置文件路径（skip_version 等）"""
    d = get_data_dir() / "settings"
    d.mkdir(parents=True, exist_ok=True)
    return d / "update_settings.json"


def get_pending_sync_path() -> Path:
    """获取依赖版本待同步标记文件路径

    updater 在替换应用文件后写入此文件（含变更的 dep_versions），
    新版 VibeOCR 启动时读取并据此用 install_embedded_dependencies 升级 python/，
    升级成功后删除。与 updater_main.py 的写入路径保持一致。
    """
    d = get_data_dir() / "settings"
    d.mkdir(parents=True, exist_ok=True)
    return d / "pending_sync.json"
