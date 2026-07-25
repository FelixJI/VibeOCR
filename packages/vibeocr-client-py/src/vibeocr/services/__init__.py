"""可扩展的服务命名空间（v2 重写后精简）。

历史版本在此提供 OCRServiceSubprocess / MinerUBatchService 等 worker_host
时代的工厂；统一 inference supervisor 重写后这些已删除。本命名空间只保留
对直接 OCRService 的按需访问，供仍需要它的代码使用；推理编排在 supervisor 内。
"""

from __future__ import annotations

import importlib
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)


def get_ocr_service():
    """返回直接 OCRService（backend wheel 提供）。

    v2 重写后不再有 subprocess/portable 模式切换：OCRService 由 supervisor
    进程持有，前端不经由此处取用。本函数仅为兼容仍在 import 它的旧代码保留。
    """
    cls = importlib.import_module("vibeocr.services.ocr_service").OCRService
    return cls()


def __getattr__(name: str):
    # 历史导出 OCRServiceSubprocess / MinerUBatchService 已随 worker_host 删除。
    # 仍请求 OCRService 时转发到直接实现。
    if name == "OCRService":
        value = importlib.import_module("vibeocr.services.ocr_service").OCRService
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["OCRService", "get_ocr_service"]
