"""可扩展的服务命名空间。

client wheel 只持有环境配置与更新等轻量服务；推理实现由 backend wheel
贡献。这里保持兼容导出，但全部按需导入，避免轻量客户端导入时加载后端。
"""

from __future__ import annotations

import importlib
import os
from pkgutil import extend_path
from typing import TYPE_CHECKING

__path__ = extend_path(__path__, __name__)

if TYPE_CHECKING:
    from vibeocr.services.mineru_batch_service import MinerUBatchService
    from vibeocr.services.ocr_service import OCRService as DirectOCRService
    from vibeocr.services.ocr_service_portable import OCRServicePortable
    from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess


def _should_use_subprocess() -> bool:
    return os.environ.get("VIBEOCR_USE_SUBPROCESS", "true").lower() in {
        "true",
        "1",
        "yes",
    }


def _should_use_portable() -> bool:
    return os.environ.get("VIBEOCR_OCR_MODE", "").lower() != "direct"


USE_SUBPROCESS = _should_use_subprocess()
USE_PORTABLE_OCR = _should_use_portable()


def get_ocr_service(skip_auto_start: bool = False):
    """按运行配置创建后端 OCR 服务；调用时才要求安装 backend wheel。"""
    if USE_SUBPROCESS:
        cls = __getattr__("OCRServiceSubprocess")
        if cls._instance is not None and cls._instance._initialized:
            return cls._instance
        return cls(auto_start=not skip_auto_start)
    if USE_PORTABLE_OCR:
        cls = importlib.import_module(
            "vibeocr.services.ocr_service_portable"
        ).OCRService
    else:
        cls = importlib.import_module("vibeocr.services.ocr_service").OCRService
    return cls()


def __getattr__(name: str):
    module_and_attr = {
        "MinerUBatchService": (
            "vibeocr.services.mineru_batch_service",
            "MinerUBatchService",
        ),
        "OCRServiceSubprocess": (
            "vibeocr.services.ocr_service_subprocess",
            "OCRServiceSubprocess",
        ),
    }
    if name == "OCRService":
        if USE_SUBPROCESS:
            module_and_attr[name] = module_and_attr["OCRServiceSubprocess"]
        elif USE_PORTABLE_OCR:
            module_and_attr[name] = (
                "vibeocr.services.ocr_service_portable",
                "OCRService",
            )
        else:
            module_and_attr[name] = ("vibeocr.services.ocr_service", "OCRService")
    target = module_and_attr.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(target[0]), target[1])
    globals()[name] = value
    return value


__all__ = [
    "USE_PORTABLE_OCR",
    "USE_SUBPROCESS",
    "MinerUBatchService",
    "OCRService",
    "OCRServiceSubprocess",
    "get_ocr_service",
]
