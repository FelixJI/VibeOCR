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
# - paddle 模块：paddlepaddle-gpu 与 paddlepaddle(CPU) 均导入为 paddle，故只检 paddle
# - 版本约束不在此处，安装版本来自 pyproject.toml（env_manager._load_dep_specs）
OCR_CHECK_MODULES: dict[str, str] = {
    "paddle": "paddlepaddle",
    "paddleocr": "paddleocr",
    "mineru": "mineru",
    "torch": "torch",
}

# 各模块 import 检测的 timeout（秒）。
# paddle 首次导入需初始化 CUDA 上下文，显著慢于其他模块。
OCR_CHECK_TIMEOUTS: dict[str, int] = {
    "paddle": 60,
    "paddleocr": 30,
    "mineru": 15,
    "torch": 15,
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
    base = PYTORCH_MIRROR_SOURCES.get(name, PYTORCH_MIRROR_SOURCES[DEFAULT_PYTORCH_MIRROR])
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

    判断条件与 env_manager.get_project_root 一致：向上查找含 src/vibeocr 的目录。
    统一条件避免两份实现在非标准布局下返回不同结果。
    """
    current = Path(__file__).resolve()
    while current.parent != current:
        if (current / "src" / "vibeocr").exists():
            return current
        current = current.parent
    return Path(__file__).parent.parent.parent.parent


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
