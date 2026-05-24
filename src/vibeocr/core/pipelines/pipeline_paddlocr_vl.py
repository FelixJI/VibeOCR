# src/vibeocr/core/pipelines/pipeline_paddlocr_vl.py
"""PaddleOCR-VL 管道选项"""

from dataclasses import dataclass

from vibeocr.core.pipelines.base_options import BasePipelineOptions


@dataclass
class PaddleOCRVLOptions(BasePipelineOptions):
    """PaddleOCR-VL 管道选项

    使用 PaddleOCR-VL 解析文档，支持图片/PDF，提取文本、表格、公式、图表等。
    """

    pipeline: str = "PaddleOCR-VL"
    use_doc_orientation_classify: bool = True
    use_doc_unwarping: bool = True
    vl_use_layout_detection: bool = True
    vl_use_chart_recognition: bool = False
    vl_use_seal_recognition: bool = False
    use_ocr_for_image_block: bool = False
