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

import os
import logging

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


def get_ocr_service():
    """
    获取 OCR 服务实例（工厂函数）

    根据环境变量自动选择实现：
    - VIBEOCR_USE_SUBPROCESS=true: 使用子进程模式（默认）
    - VIBEOCR_USE_SUBPROCESS=false: 使用直接模式

    Returns:
        OCRService 或 OCRServiceSubprocess 实例
    """
    if _should_use_subprocess():
        _logger.info("使用子进程 OCR 服务")
        from .ocr_service_subprocess import OCRServiceSubprocess
        return OCRServiceSubprocess()
    else:
        _logger.info("使用直接 OCR 服务")
        if _should_use_portable():
            from .ocr_service_portable import OCRService
        else:
            from .ocr_service import OCRService
        return OCRService()


# 为了向后兼容，保持原有的导入方式
USE_SUBPROCESS = _should_use_subprocess()
USE_PORTABLE_OCR = _should_use_portable()

if USE_SUBPROCESS:
    # 子进程模式
    from .ocr_service_subprocess import OCRServiceSubprocess
    # 别名，方便使用
    OCRService = OCRServiceSubprocess
else:
    # 直接模式
    if USE_PORTABLE_OCR:
        from .ocr_service_portable import OCRService
    else:
        from .ocr_service import OCRService

__all__ = [
    "OCRService",
    "OCRServiceSubprocess",
    "get_ocr_service",
    "USE_SUBPROCESS",
    "USE_PORTABLE_OCR",
]
