"""环境配置模块

集中管理环境相关的配置常量和工具函数。
"""

import os
import sys
from pathlib import Path
from typing import Literal, Optional

# 环境模式类型
EnvironmentMode = Literal["virtualenv", "portable", "unknown"]

# Python 版本
PYTHON_VERSION = "3.12.8"
PYTHON_VERSION_SHORT = "3.12"

# pip 下载源
PIP_MIRROR_SOURCES = {
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple/",
    "ustc": "https://mirrors.ustc.edu.cn/pypi/web/simple",
    "official": "https://pypi.org/simple",
}

# 默认 pip 源
DEFAULT_PIP_MIRROR = "tsinghua"

# PaddlePaddle 版本
PADDLE_VERSION = "3.3.0"

# CUDA 版本映射
CUDA_VERSION_MAP = {
    "11.8": "cu118",
    "12.0": "cu121",
    "12.1": "cu121",
    "12.2": "cu123",
    "12.3": "cu123",
    "12.4": "cu126",
    "12.5": "cu126",
    "12.6": "cu126",
    "12.7": "cu129",
    "12.8": "cu129",
    "12.9": "cu129",
}

# PaddleX 模型下载源
PADDLEX_MODEL_SOURCES = {
    "bos": "BOS",  # 百度对象存储（国内快）
    "huggingface": "HuggingFace",  # HuggingFace（国际）
}

# 便携式 Python 目录名
PORTABLE_PYTHON_DIR = "python_portable"

# 配置目录名
CONFIG_DIR = "config"

# 依赖包列表
CORE_DEPENDENCIES = [
    "paddlepaddle",
    "paddlex",
]

# OCR 相关依赖
OCR_DEPENDENCIES = [
    "paddlepaddle",
    "paddlex",
    "paddleocr",
]


def get_pip_mirror(name: str = DEFAULT_PIP_MIRROR) -> str:
    """获取 pip 镜像源 URL

    Args:
        name: 镜像源名称

    Returns:
        镜像源 URL
    """
    return PIP_MIRROR_SOURCES.get(name, PIP_MIRROR_SOURCES[DEFAULT_PIP_MIRROR])


def get_paddle_cuda_version(cuda_version: str) -> Optional[str]:
    """获取 PaddlePaddle 对应的 CUDA 版本标识

    Args:
        cuda_version: 系统 CUDA 版本

    Returns:
        PaddlePaddle CUDA 版本标识，如 "cu118"
    """
    return CUDA_VERSION_MAP.get(cuda_version)


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
    """获取项目根目录"""
    # 从当前文件向上查找包含 pyproject.toml 的目录
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    # 如果找不到，返回 src 的父目录
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
