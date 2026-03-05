# tests/core/test_pipelines.py
"""管道定义模块测试"""

import pytest

from vibeocr.core.pipelines import (
    OCRPipeline,
    get_pipeline_description,
    get_pipeline_display_name,
    get_pipeline_supported_options,
)


class TestOCRPipeline:
    """管道枚举测试"""

    def test_pipeline_count(self):
        """验证管道数量为 7"""
        assert len(OCRPipeline) == 7

    def test_pipeline_values(self):
        """验证管道值"""
        assert OCRPipeline.OCR.value == "OCR"
        assert OCRPipeline.TABLE_RECOGNITION.value == "table_recognition"
        assert OCRPipeline.FORMULA_RECOGNITION.value == "formula_recognition"
        assert OCRPipeline.PP_STRUCTURE_V3.value == "PP-StructureV3"
        assert OCRPipeline.PADDLEOCR_VL.value == "PaddleOCR-VL"
        assert OCRPipeline.CHATOCRV4.value == "PP-ChatOCRv4"
        assert OCRPipeline.DOC_UNDERSTANDING.value == "doc_understanding"

    def test_get_display_name(self):
        """验证显示名称获取"""
        assert get_pipeline_display_name(OCRPipeline.OCR) == "通用 OCR"
        assert get_pipeline_display_name(OCRPipeline.TABLE_RECOGNITION) == "表格识别"
        assert get_pipeline_display_name(OCRPipeline.PP_STRUCTURE_V3) == "版面解析"

    def test_get_description(self):
        """验证描述获取"""
        desc = get_pipeline_description(OCRPipeline.OCR)
        assert "文字" in desc or "文本" in desc

    def test_get_supported_options(self):
        """验证支持的选项"""
        options = get_pipeline_supported_options(OCRPipeline.OCR)
        assert "use_doc_orientation_classify" in options
        assert "use_doc_unwarping" in options
        assert "use_textline_orientation" in options

    def test_pp_structure_options(self):
        """PP-StructureV3 应支持子产线选项"""
        options = get_pipeline_supported_options(OCRPipeline.PP_STRUCTURE_V3)
        assert "use_table_recognition" in options
        assert "use_formula_recognition" in options
        assert "use_seal_recognition" in options
        assert "use_chart_recognition" in options

    def test_paddleocr_vl_options(self):
        """PaddleOCR-VL 应支持 VL 特有选项"""
        options = get_pipeline_supported_options(OCRPipeline.PADDLEOCR_VL)
        assert "vl_use_layout_detection" in options
        assert "vl_format_block_content" in options

    def test_doc_understanding_options(self):
        """文档理解应支持模型选择"""
        options = get_pipeline_supported_options(OCRPipeline.DOC_UNDERSTANDING)
        assert "doc_understanding_model" in options
