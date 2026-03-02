"""核心抽象层

包含：
- BaseWorker: Worker 基类
- SingletonMeta: 单例元类
- Constants: 全局常量
- AppStyles: 应用程序样式
"""

from vibeocr.core.singleton_meta import SingletonMeta
from vibeocr.core.constants import (
    Constants,
    OCRPipeline,
    FileType,
    DEFAULT_SHM_SIZE,
    SHM_TIMEOUT,
    SHORT_DELAY_MS,
    MEDIUM_DELAY_MS,
    LONG_DELAY_MS,
    TOAST_DELAY_MS,
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_SIZE,
    MIN_BATCH_SIZE,
    DEFAULT_SPACING,
    DEFAULT_MARGIN,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_ERROR,
    COLOR_TEXT,
    COLOR_BORDER,
    COLOR_BACKGROUND,
    COLOR_HOVER,
)
from vibeocr.core.styles import AppStyles
from vibeocr.core.base_worker import BaseWorker, BatchWorker

__all__ = [
    # 核心类
    "SingletonMeta",
    "Constants",
    "AppStyles",
    "BaseWorker",
    "BatchWorker",
    # 枚举
    "OCRPipeline",
    "FileType",
    # 常量（向后兼容）
    "DEFAULT_SHM_SIZE",
    "SHM_TIMEOUT",
    "SHORT_DELAY_MS",
    "MEDIUM_DELAY_MS",
    "LONG_DELAY_MS",
    "TOAST_DELAY_MS",
    "DEFAULT_BATCH_SIZE",
    "MAX_BATCH_SIZE",
    "MIN_BATCH_SIZE",
    "DEFAULT_SPACING",
    "DEFAULT_MARGIN",
    # 颜色（向后兼容）
    "COLOR_PRIMARY",
    "COLOR_SUCCESS",
    "COLOR_WARNING",
    "COLOR_ERROR",
    "COLOR_TEXT",
    "COLOR_BORDER",
    "COLOR_BACKGROUND",
    "COLOR_HOVER",
]
