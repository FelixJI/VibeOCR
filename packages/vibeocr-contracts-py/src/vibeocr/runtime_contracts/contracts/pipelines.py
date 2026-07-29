"""Pure OCR pipeline identifiers and presentation metadata.

This module is intentionally stdlib-only.  Frontends may import it without
loading pipeline implementations, model runtimes, Qt, or WorkerHost services.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class OCRPipeline(Enum):
    OCR = "OCR"
    PP_STRUCTURE_V3 = "PP-StructureV3"
    DOCUMENT_PARSING = "MinerU"
    PADDLEOCR_VL = "PaddleOCR-VL"
    TABLE_RECOGNITION = "TABLE_RECOGNITION"
    FORMULA_RECOGNITION = "FORMULA_RECOGNITION"

    @property
    def display_name(self) -> str:
        return get_pipeline_display_name(self)

    @property
    def description(self) -> str:
        return get_pipeline_description(self)


_PIPELINE_METADATA: dict[OCRPipeline, dict[str, Any]] = {
    OCRPipeline.OCR: {
        "display_name": "通用 OCR",
        "short_name": "文字",
        "preloadable": True,
        "heavy": False,
        "cache_kind": "paddle",
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
        "heavy": True,
        "cache_kind": "paddle",
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
        "heavy": True,
        "cache_kind": "mineru",
        "description": "使用 MineRU 解析文档，支持 PDF/图片，提取文本、表格、公式等",
        "supported_options": [
            "parse_method",
            "backend",
            "effort",
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
        "heavy": True,
        "cache_kind": "paddle",
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
        "heavy": False,
        "cache_kind": "paddle",
        "description": "独立表格结构识别，支持有线和无线表格",
        "supported_options": [
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_table_orientation_classify",
            "use_ocr_results_with_table_cells",
        ],
    },
    OCRPipeline.FORMULA_RECOGNITION: {
        "display_name": "公式识别",
        "short_name": "公式",
        "preloadable": True,
        "heavy": False,
        "cache_kind": "paddle",
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


def _metadata(pipeline: OCRPipeline) -> dict[str, Any]:
    return _PIPELINE_METADATA.get(pipeline, {})


def get_pipeline_display_name(pipeline: OCRPipeline) -> str:
    return str(_metadata(pipeline).get("display_name", pipeline.value))


def get_pipeline_short_name(pipeline: OCRPipeline) -> str:
    return str(_metadata(pipeline).get("short_name", pipeline.value))


def get_pipeline_description(pipeline: OCRPipeline) -> str:
    return str(_metadata(pipeline).get("description", ""))


def get_pipeline_supported_options(pipeline: OCRPipeline) -> list[str]:
    return list(_metadata(pipeline).get("supported_options", []))


def get_all_pipelines() -> list[OCRPipeline]:
    return list(OCRPipeline)


def get_preloadable_pipelines() -> list[OCRPipeline]:
    return [p for p in OCRPipeline if _metadata(p).get("preloadable", False)]


def get_heavy_pipelines() -> list[OCRPipeline]:
    return [p for p in OCRPipeline if _metadata(p).get("heavy", False)]


def get_paddle_pipelines() -> list[OCRPipeline]:
    """走 paddle 回收路径的管道（del + paddle.device.cuda.empty_cache）。"""
    return [p for p in OCRPipeline if _metadata(p).get("cache_kind") == "paddle"]


def get_mineru_pipelines() -> list[OCRPipeline]:
    """走 mineru 回收路径的管道（仅移除 httpx 代理，不调 empty_cache）。"""
    return [p for p in OCRPipeline if _metadata(p).get("cache_kind") == "mineru"]


def is_option_supported(pipeline: OCRPipeline, option_name: str) -> bool:
    return option_name in get_pipeline_supported_options(pipeline)


__all__ = [
    "OCRPipeline",
    "get_all_pipelines",
    "get_heavy_pipelines",
    "get_mineru_pipelines",
    "get_paddle_pipelines",
    "get_pipeline_description",
    "get_pipeline_display_name",
    "get_pipeline_short_name",
    "get_pipeline_supported_options",
    "get_preloadable_pipelines",
    "is_option_supported",
]
