"""OCR 数据模型"""

from vibeocr.models.ocr_result import OCRResult
from vibeocr.models.extraction_options import ExtractionOptions
from vibeocr.models.extraction_template import ExtractionTemplate, DEFAULT_TEMPLATES
from vibeocr.models.llm_config import LLMConfig, APIType

__all__ = [
    "OCRResult",
    "ExtractionOptions",
    "ExtractionTemplate",
    "DEFAULT_TEMPLATES",
    "LLMConfig",
    "APIType",
]
