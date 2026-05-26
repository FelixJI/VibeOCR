"""核心抽象层

包含：
- SingletonMeta: 单例元类
- Constants: 全局常量
- OCRPipeline: OCR 管道枚举（来自 pipelines.py）
"""

from vibeocr.core.base_worker import BaseWorker, BatchWorker
from vibeocr.core.constants import (
    COLOR_BACKGROUND,
    COLOR_BORDER,
    COLOR_ERROR,
    COLOR_HOVER,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_TEXT,
    COLOR_WARNING,
    DEFAULT_BATCH_SIZE,
    DEFAULT_MARGIN,
    DEFAULT_SHM_SIZE,
    DEFAULT_SPACING,
    LONG_DELAY_MS,
    MAX_BATCH_SIZE,
    MEDIUM_DELAY_MS,
    MIN_BATCH_SIZE,
    SHM_TIMEOUT,
    SHORT_DELAY_MS,
    TOAST_DELAY_MS,
    Constants,
    FileType,
    WindowsColors,
)
from vibeocr.core.pipelines import (
    OCRPipeline,
    get_all_pipelines,
    get_pipeline_description,
    get_pipeline_display_name,
    get_pipeline_supported_options,
    is_option_supported,
)
from vibeocr.core.singleton_meta import SingletonMeta
from vibeocr.core.styles import AppStyles

__all__ = [
    "COLOR_BACKGROUND",
    "COLOR_BORDER",
    "COLOR_ERROR",
    "COLOR_HOVER",
    "COLOR_PRIMARY",
    "COLOR_SUCCESS",
    "COLOR_TEXT",
    "COLOR_WARNING",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_MARGIN",
    "DEFAULT_SHM_SIZE",
    "DEFAULT_SPACING",
    "LONG_DELAY_MS",
    "MAX_BATCH_SIZE",
    "MEDIUM_DELAY_MS",
    "MIN_BATCH_SIZE",
    "SHM_TIMEOUT",
    "SHORT_DELAY_MS",
    "TOAST_DELAY_MS",
    "AppStyles",
    "BaseWorker",
    "BatchWorker",
    "Constants",
    "FileType",
    "OCRPipeline",
    "SingletonMeta",
    "WindowsColors",
    "get_all_pipelines",
    "get_pipeline_description",
    "get_pipeline_display_name",
    "get_pipeline_supported_options",
    "is_option_supported",
]
