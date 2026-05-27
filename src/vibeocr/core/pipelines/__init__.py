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
    TABLE_RECOGNITION = "TABLE_RECOGNITION"
    FORMULA_RECOGNITION = "FORMULA_RECOGNITION"

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
        "short_name": "文字",
        "preloadable": True,
        "description": "识别图片中的文字内容，适用于纯文本场景",
        "supported_options": [
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_textline_orientation",
        ],
    },
    OCRPipeline.PP_STRUCTURE_V3: {
        "display_name": "PP-StructureV3",
        "short_name": "结构",
        "preloadable": True,
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
        "display_name": "文档M（MineRU）",
        "short_name": "文档M",
        "preloadable": False,
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
        "display_name": "文档P（PaddleOCR-VL）",
        "short_name": "文档P",
        "preloadable": True,
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
    OCRPipeline.TABLE_RECOGNITION: {
        "display_name": "表格识别",
        "short_name": "表格",
        "preloadable": True,
        "description": "独立表格结构识别，支持有线和无线表格",
        "supported_options": [
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_wireless_table",
            "use_table_orientation_classify",
            "use_ocr_results_with_table_cells",
            "use_e2e_wired_table_rec_model",
            "use_e2e_wireless_table_rec_model",
            "use_wired_table_cells_trans_to_html",
            "use_wireless_table_cells_trans_to_html",
            "text_det_limit_side_len",
            "text_det_thresh",
            "text_det_box_thresh",
            "text_det_unclip_ratio",
            "text_rec_score_thresh",
        ],
    },
    OCRPipeline.FORMULA_RECOGNITION: {
        "display_name": "公式识别",
        "short_name": "公式",
        "preloadable": True,
        "description": "独立数学公式识别（LaTeX 输出）",
        "supported_options": [
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "formula_recognition_batch_size",
            "formula_recognition_model_name",
            "formula_recognition_model_dir",
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


def get_pipeline_short_name(pipeline: OCRPipeline) -> str:
    """获取管道短名称（用于紧凑 UI 按钮）

    Args:
        pipeline: 管道类型

    Returns:
        管道的短名称
    """
    metadata = _PIPELINE_METADATA.get(pipeline, {})
    return metadata.get("short_name", pipeline.value)


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


def get_preloadable_pipelines() -> list[OCRPipeline]:
    """获取可预加载的管道列表（排除使用独立服务的管道）

    Returns:
        可预加载的管道枚举值列表
    """
    return [
        p
        for p in OCRPipeline
        if _PIPELINE_METADATA.get(p, {}).get("preloadable", False)
    ]


def is_option_supported(pipeline: OCRPipeline, option_name: str) -> bool:
    """检查管道是否支持指定选项

    Args:
        pipeline: 管道类型
        option_name: 选项名称

    Returns:
        是否支持该选项
    """
    return option_name in get_pipeline_supported_options(pipeline)


# ---------------------------------------------------------------------------
# 管道注册表（Pipeline Registry）
# ---------------------------------------------------------------------------
# 注册所有已定义的 PipelineSpec，提供统一的 get_registry() 接口。
# ---------------------------------------------------------------------------

from vibeocr.core.pipelines.pipeline_formula import (  # noqa: E402
    FORMULA_RECOGNITION_SPEC,
)
from vibeocr.core.pipelines.pipeline_mineru import MINERU_SPEC  # noqa: E402
from vibeocr.core.pipelines.pipeline_ocr import OCR_SPEC  # noqa: E402
from vibeocr.core.pipelines.pipeline_paddlocr_vl import PADDLEOCR_VL_SPEC  # noqa: E402
from vibeocr.core.pipelines.pipeline_pp_structure import (  # noqa: E402
    PP_STRUCTURE_V3_SPEC,
)
from vibeocr.core.pipelines.pipeline_table import TABLE_RECOGNITION_SPEC  # noqa: E402
from vibeocr.core.pipelines.registry import PipelineRegistry  # noqa: E402

_registry = PipelineRegistry()
_registry.register(OCR_SPEC)
_registry.register(PP_STRUCTURE_V3_SPEC)
_registry.register(TABLE_RECOGNITION_SPEC)
_registry.register(FORMULA_RECOGNITION_SPEC)
_registry.register(MINERU_SPEC)
_registry.register(PADDLEOCR_VL_SPEC)


def get_registry() -> PipelineRegistry:
    """获取全局管道注册表单例"""
    return _registry
