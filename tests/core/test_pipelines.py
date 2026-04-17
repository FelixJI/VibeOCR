# tests/core/test_pipelines.py
"""管道定义模块测试"""

from vibeocr.core.pipelines import (
    OCRPipeline,
    get_pipeline_description,
    get_pipeline_display_name,
    get_pipeline_supported_options,
)


class TestOCRPipeline:
    """管道枚举测试"""

    def test_pipeline_count(self):
        """验证管道数量为 4"""
        assert len(OCRPipeline) == 4

    def test_pipeline_values(self):
        """验证管道值"""
        assert OCRPipeline.OCR.value == "OCR"
        assert OCRPipeline.TABLE_RECOGNITION.value == "table_recognition"
        assert OCRPipeline.FORMULA_RECOGNITION.value == "formula_recognition"
        assert OCRPipeline.DOCUMENT_PARSING.value == "MinerU"

    def test_get_display_name(self):
        """验证显示名称获取"""
        assert get_pipeline_display_name(OCRPipeline.OCR) == "通用 OCR"
        assert get_pipeline_display_name(OCRPipeline.TABLE_RECOGNITION) == "表格识别"
        assert get_pipeline_display_name(OCRPipeline.DOCUMENT_PARSING) == "文档解析"

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

    def test_document_parsing_options(self):
        """文档解析应支持 MinerU 选项"""
        options = get_pipeline_supported_options(OCRPipeline.DOCUMENT_PARSING)
        assert "parse_method" in options
        assert "enable_formula" in options
        assert "enable_table" in options
