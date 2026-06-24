"""核心抽象层

包含：
- SingletonMeta: 单例元类
- Constants: 全局常量
- OCRPipeline: OCR 管道枚举（来自 pipelines.py）
"""

from vibeocr.core.base_worker import BaseWorker, BatchWorker
from vibeocr.core.constants import (
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

__all__ = [
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
    "BaseWorker",
    "BatchWorker",
    "Constants",
    "FileType",
    "OCRPipeline",
    "SingletonMeta",
    "get_all_pipelines",
    "get_pipeline_description",
    "get_pipeline_display_name",
    "get_pipeline_supported_options",
    "is_option_supported",
]
