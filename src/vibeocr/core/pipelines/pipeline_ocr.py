# src/vibeocr/core/pipelines/pipeline_ocr.py
"""通用 OCR 管道选项"""

from dataclasses import dataclass

from vibeocr.core.pipelines.base_options import BasePipelineOptions


@dataclass
class OCROptions(BasePipelineOptions):
    """通用 OCR 管道选项

    适用于纯文本识别场景。
    """

    pipeline: str = "OCR"
    use_doc_orientation_classify: bool = True
    use_doc_unwarping: bool = True
    use_textline_orientation: bool = False
