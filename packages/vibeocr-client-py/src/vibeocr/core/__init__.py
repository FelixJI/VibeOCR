"""核心抽象层

包含：
- SingletonMeta: 单例元类
- Constants: 全局常量
- OCRPipeline: OCR 管道枚举（来自 pipelines.py）
- BaseWorker / BatchWorker: GUI 线程 Worker 基类（依赖 PySide6）

注意：
    ``base_worker`` 依赖 PySide6，而 OCR Worker 子进程（嵌入式 Python 运行时）
    不安装 PySide6。故此处对纯逻辑模块（constants / pipelines / singleton_meta）
    采用 eager import，而 ``base_worker`` 改为懒加载（模块级 ``__getattr__``），
    使得子进程 ``from vibeocr.core.constants import ...`` 时不会触发 PySide6 导入。
"""

from pkgutil import extend_path
from typing import TYPE_CHECKING

__path__ = extend_path(__path__, __name__)

from vibeocr.core.constants import (
    DEFAULT_MARGIN,
    DEFAULT_SHM_SIZE,
    DEFAULT_SPACING,
    LONG_DELAY_MS,
    MEDIUM_DELAY_MS,
    MIN_BATCH_SIZE,
    OCR_BATCH_GPU_SIZE_CAP,
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

if TYPE_CHECKING:
    from vibeocr.core.base_worker import BaseWorker, BatchWorker

__all__ = [
    "DEFAULT_MARGIN",
    "DEFAULT_SHM_SIZE",
    "DEFAULT_SPACING",
    "LONG_DELAY_MS",
    "MEDIUM_DELAY_MS",
    "MIN_BATCH_SIZE",
    "OCR_BATCH_GPU_SIZE_CAP",
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

# BaseWorker / BatchWorker 依赖 PySide6，懒加载以避免子进程（无 PySide6）
# 在 import vibeocr.core 时被强制拉入 GUI 依赖。
_LAZY_NAMES = {
    "BaseWorker": "vibeocr.core.base_worker",
    "BatchWorker": "vibeocr.core.base_worker",
}


def __getattr__(name: str):
    """按需导入 GUI 相关符号，避免子进程启动时加载 PySide6"""
    module_path = _LAZY_NAMES.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value  # 缓存，后续直接命中
    return value
