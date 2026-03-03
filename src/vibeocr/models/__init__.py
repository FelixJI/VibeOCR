"""OCR 数据模型"""

from vibeocr.models.extraction_options import ExtractionOptions
from vibeocr.models.extraction_template import DEFAULT_TEMPLATES, ExtractionTemplate
from vibeocr.models.llm_config import APIType, LLMConfig
from vibeocr.models.ocr_result import OCRResult

__all__ = [
    "DEFAULT_TEMPLATES",
    "APIType",
    "ExtractionOptions",
    "ExtractionTemplate",
    "LLMConfig",
    "OCRResult",
]
