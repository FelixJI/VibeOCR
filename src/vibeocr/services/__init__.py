"""Services module

OCR 服务实现方案：
1. 子进程模式（推荐）: 隔离 GPU 上下文，避免 UI 冻结
2. 直接模式: 在主进程中直接执行 OCR（用于调试）
3. 便携式模式: 使用便携式 Python 环境

环境变量配置：
- VIBEOCR_USE_SUBPROCESS=true: 使用子进程模式（默认）
- VIBEOCR_USE_SUBPROCESS=false: 使用直接模式
- VIBEOCR_OCR_MODE=direct: 强制使用当前 Python 环境（调试用）
"""

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibeocr.services.ocr_service import OCRService as _DirectOCRService
    from vibeocr.services.ocr_service_portable import (
        OCRServicePortable as _PortableOCRService,
    )
    from vibeocr.services.ocr_service_subprocess import (
        OCRServiceSubprocess as _SubprocessOCRService,
    )

_logger = logging.getLogger(__name__)


def _should_use_subprocess() -> bool:
    """
    判断是否使用子进程模式

    默认使用子进程模式，除非明确禁用。
    """
    env_value = os.environ.get("VIBEOCR_USE_SUBPROCESS", "true").lower()
    return env_value in ("true", "1", "yes")


def _should_use_portable() -> bool:
    """
    判断是否使用便携式方案（仅用于直接模式）

    策略：
    1. 检查环境变量 VIBEOCR_OCR_MODE
       - "direct": 直接使用当前 Python 环境（用于特殊调试场景）
       - 其他或未设置：使用便携式方案（默认）
    """
    env_value = os.environ.get("VIBEOCR_OCR_MODE", "").lower()
    return env_value != "direct"


def get_ocr_service(
    skip_auto_start: bool = False,
) -> "_SubprocessOCRService | _DirectOCRService | _PortableOCRService":
    """
    获取 OCR 服务实例（工厂函数）

    根据环境变量自动选择实现：
    - VIBEOCR_USE_SUBPROCESS=true: 使用子进程模式（默认，生产路径）
    - VIBEOCR_USE_SUBPROCESS=false: 使用直连/便携模式（仅调试逃生口）

    ⚠️ 三套实现的关系（勿误删/勿拆分）：
    - **OCRServiceSubprocess**（默认）：子进程隔离 GPU 上下文，避免 UI 冻结，
      是生产环境的唯一推荐路径。
    - **OCRService**（``ocr_service.py``）：在主进程内直接加载 PaddleOCR。
      ⚠️ **仅调试逃生口**（``VIBEOCR_USE_SUBPROCESS=false`` + ``VIBEOCR_OCR_MODE=direct``），
      会阻塞 UI、占用 GPU，**生产不应启用**。同时它也是子进程 worker 内部的
      实际推理内核（``workers/ocr_worker.py`` 实例化它），故类本身必须保留。
    - **OCRServicePortable**：便携式 Python 环境路径（``VIBEOCR_OCR_MODE != direct``
      时的直连分支），用于分发打包环境。

    Args:
        skip_auto_start: 是否跳过自动启动（用于避免重复初始化）

    Returns:
        OCRService 或 OCRServiceSubprocess 实例
    """
    if _should_use_subprocess():
        from .ocr_service_subprocess import OCRServiceSubprocess

        if OCRServiceSubprocess._instance is not None:
            if OCRServiceSubprocess._instance._initialized:
                _logger.debug("[get_ocr_service] 返回现有子进程实例")
                return OCRServiceSubprocess._instance
        _logger.info(f"创建子进程 OCR 服务, auto_start={not skip_auto_start}")
        return OCRServiceSubprocess(auto_start=not skip_auto_start)
    # ⚠️ 以下为调试逃生口：主进程直连，会阻塞 UI，生产勿用
    _logger.warning(
        "[get_ocr_service] 直连模式（VIBEOCR_USE_SUBPROCESS=false）—— "
        "仅用于调试，将在主进程内加载模型，可能冻结 UI"
    )
    _logger.debug("使用直接 OCR 服务")
    if _should_use_portable():
        from .ocr_service_portable import OCRService
    else:
        from .ocr_service import OCRService  # type: ignore[no-redef,assignment]
    return OCRService()


# 为了向后兼容，保持原有的导入方式
# ⚠️ 模块导入期即固定模式：USE_SUBPROCESS 默认 True（子进程，生产路径）
USE_SUBPROCESS = _should_use_subprocess()
USE_PORTABLE_OCR = _should_use_portable()

if USE_SUBPROCESS:
    # 子进程模式（生产路径）
    from .ocr_service_subprocess import OCRServiceSubprocess

    # 别名，方便使用
    OCRService = OCRServiceSubprocess  # type: ignore[assignment,misc]
else:
    # 直连模式（仅调试逃生口，见 get_ocr_service 注释）
    if USE_PORTABLE_OCR:
        from .ocr_service_portable import OCRService  # type: ignore[assignment]
    else:
        from .ocr_service import OCRService  # type: ignore[assignment]

from .mineru_batch_service import MinerUBatchService  # noqa: E402

__all__ = [
    "USE_PORTABLE_OCR",
    "USE_SUBPROCESS",
    "MinerUBatchService",
    "OCRService",
    "OCRServiceSubprocess",
    "get_ocr_service",
]
