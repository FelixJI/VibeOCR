"""Services module

OCR 服务实现方案：
- 开发环境：使用 .venv 虚拟环境中的 PaddleX
- 生产环境：使用便携式 python/ 目录中的 PaddleX

两种环境都通过 PythonPathManager 在主进程中直接导入 PaddleX，
无需子进程通信，提供更好的性能和调试体验。

环境变量配置：
- VIBEOCR_OCR_MODE=direct: 强制使用当前 Python 环境（用于特殊调试场景）
- 未设置或=portable: 使用便携式方案（默认）
"""

import os


def _should_use_portable() -> bool:
    """
    判断是否使用便携式方案

    策略：
    1. 检查环境变量 VIBEOCR_OCR_MODE
       - "direct": 直接使用当前 Python 环境（用于特殊调试场景）
       - 其他或未设置：使用便携式方案（默认）
    """
    env_value = os.environ.get("VIBEOCR_OCR_MODE", "").lower()
    return env_value != "direct"


# 根据环境选择使用哪种 OCR 服务实现
USE_PORTABLE_OCR = _should_use_portable()

if USE_PORTABLE_OCR:
    from .ocr_service_portable import OCRService
else:
    # 直接使用当前 Python 环境（用于特殊调试场景）
    from .ocr_service import OCRService

__all__ = ["OCRService"]
