"""OCR 数据模型"""

from vibeocr.models.ocr_result import OCRResult
from vibeocr.models.ocr_result_serializer import ocr_result_to_payload

__all__ = [
    "OCRResult",
    "ocr_result_to_payload",
]
