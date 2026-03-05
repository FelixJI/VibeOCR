# src/vibeocr/core/pipelines.py
"""OCR 管道定义模块

此模块是管道定义的单一来源（Single Source of Truth）。
所有管道相关的枚举、元数据和查询函数都在此定义。
"""

from enum import Enum
from typing import TypeVar

T = TypeVar("T", bound="OCRPipeline")


class OCRPipeline(Enum):
    """OCR 管道类型枚举

    定义所有支持的 OCR 管道类型。
    """

    OCR = "OCR"  # 通用 OCR：纯文本识别
    TABLE_RECOGNITION = "table_recognition"  # 表格识别
    FORMULA_RECOGNITION = "formula_recognition"  # 公式识别
    PP_STRUCTURE_V3 = "PP-StructureV3"  # 版面解析（含表格/公式子产线）
    PADDLEOCR_VL = "PaddleOCR-VL"  # 端到端多模态文档解析
    CHATOCRV4 = "PP-ChatOCRv4"  # 文档场景信息抽取 v4
    DOC_UNDERSTANDING = "doc_understanding"  # 文档理解 (VLM)

    @property
    def display_name(self) -> str:
        """获取管道显示名称"""
        return get_pipeline_display_name(self)

    @property
    def description(self) -> str:
        """获取管道描述"""
        return get_pipeline_description(self)


# 管道元数据
_PIPELINE_METADATA: dict[OCRPipeline, dict] = {
    OCRPipeline.OCR: {
        "display_name": "通用 OCR",
        "description": "识别图片中的文字内容，适用于纯文本场景",
        "supported_options": [
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_textline_orientation",
        ],
    },
    OCRPipeline.TABLE_RECOGNITION: {
        "display_name": "表格识别",
        "description": "识别表格结构，输出 HTML/Excel 格式",
        "supported_options": [
            "use_doc_orientation_classify",
            "use_doc_unwarping",
        ],
    },
    OCRPipeline.FORMULA_RECOGNITION: {
        "display_name": "公式识别",
        "description": "识别数学公式，输出 LaTeX 格式",
        "supported_options": [
            "use_doc_orientation_classify",
            "use_doc_unwarping",
        ],
    },
    OCRPipeline.PP_STRUCTURE_V3: {
        "display_name": "版面解析",
        "description": "解析文档版面，支持表格、公式、印章、图表等子产线",
        "supported_options": [
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_table_recognition",
            "use_formula_recognition",
            "use_seal_recognition",
            "use_chart_recognition",
        ],
    },
    OCRPipeline.PADDLEOCR_VL: {
        "display_name": "PaddleOCR-VL",
        "description": "端到端多模态文档解析，支持表格、公式、印章、图表等",
        "supported_options": [
            "vl_use_layout_detection",
            "vl_format_block_content",
            "vl_use_seal_recognition",
            "vl_use_ocr_for_image_block",
            "vl_temperature",
            "vl_top_p",
            "vl_max_pixels",
            "vl_min_pixels",
        ],
    },
    OCRPipeline.CHATOCRV4: {
        "display_name": "PP-ChatOCRv4",
        "description": "文档场景信息抽取，结合 LLM 和 OCR 技术",
        "supported_options": [
            "use_doc_orientation_classify",
            "use_doc_unwarping",
        ],
    },
    OCRPipeline.DOC_UNDERSTANDING: {
        "display_name": "文档理解",
        "description": "基于视觉-语言模型（VLM）的文档问答",
        "supported_options": [
            "doc_understanding_model",
            "vl_temperature",
            "vl_top_p",
        ],
    },
}

# 文档理解支持的模型列表
DOC_UNDERSTANDING_MODELS = [
    "PP-DocBee-2B",
    "PP-DocBee-7B",
    "PP-DocBee2-3B",
]

DEFAULT_DOC_UNDERSTANDING_MODEL = "PP-DocBee2-3B"


def get_pipeline_display_name(pipeline: OCRPipeline) -> str:
    """获取管道显示名称

    Args:
        pipeline: 管道类型

    Returns:
        管道的中文显示名称
    """
    metadata = _PIPELINE_METADATA.get(pipeline, {})
    return metadata.get("display_name", pipeline.value)


def get_pipeline_description(pipeline: OCRPipeline) -> str:
    """获取管道描述

    Args:
        pipeline: 管道类型

    Returns:
        管道的描述文本
    """
    metadata = _PIPELINE_METADATA.get(pipeline, {})
    return metadata.get("description", "")


def get_pipeline_supported_options(pipeline: OCRPipeline) -> list[str]:
    """获取管道支持的选项列表

    Args:
        pipeline: 管道类型

    Returns:
        支持的选项名称列表
    """
    metadata = _PIPELINE_METADATA.get(pipeline, {})
    return metadata.get("supported_options", [])


def get_all_pipelines() -> list[OCRPipeline]:
    """获取所有管道列表

    Returns:
        所有管道枚举值列表
    """
    return list(OCRPipeline)


def is_option_supported(pipeline: OCRPipeline, option_name: str) -> bool:
    """检查管道是否支持指定选项

    Args:
        pipeline: 管道类型
        option_name: 选项名称

    Returns:
        是否支持该选项
    """
    return option_name in get_pipeline_supported_options(pipeline)
