# src/vibeocr/core/pipelines/__init__.py
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

    OCR = "OCR"
    PP_STRUCTURE_V3 = "PP-StructureV3"
    DOCUMENT_PARSING = "MinerU"
    PADDLEOCR_VL = "PaddleOCR-VL"

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
    OCRPipeline.PP_STRUCTURE_V3: {
        "display_name": "PP-StructureV3",
        "description": "文档结构分析，支持表格、公式、印章、图表识别",
        "supported_options": [
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_textline_orientation",
            "use_table_recognition",
            "use_formula_recognition",
            "use_seal_recognition",
            "use_chart_recognition",
        ],
    },
    OCRPipeline.DOCUMENT_PARSING: {
        "display_name": "MineRU（文档）",
        "description": "使用 MineRU 解析文档，支持 PDF/图片，提取文本、表格、公式等",
        "supported_options": [
            "parse_method",
            "backend",
            "enable_formula",
            "enable_table",
            "lang_list",
            "start_page_id",
            "end_page_id",
        ],
    },
    OCRPipeline.PADDLEOCR_VL: {
        "display_name": "PaddleOCR-VL（文档）",
        "description": "使用 PaddleOCR-VL-1.5 解析文档，支持图片/PDF，提取文本、表格、公式、图表等",
        "supported_options": [
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "vl_use_layout_detection",
            "vl_use_chart_recognition",
            "vl_use_seal_recognition",
            "use_ocr_for_image_block",
        ],
    },
}


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
