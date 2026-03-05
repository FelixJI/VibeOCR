# tests/integration/test_pipelines_integration.py
"""管道集成测试"""

import pytest

from vibeocr.core.pipelines import (
    DEFAULT_DOC_UNDERSTANDING_MODEL,
    DOC_UNDERSTANDING_MODELS,
    OCRPipeline,
    get_all_pipelines,
    get_pipeline_description,
    get_pipeline_display_name,
    get_pipeline_supported_options,
    is_option_supported,
)
from vibeocr.models.ocr_options import OCROptions


class TestPipelineIntegration:
    """管道集成测试"""

    @pytest.mark.parametrize("pipeline", list(OCRPipeline))
    def test_all_pipelines_have_options(self, pipeline):
        """所有管道都应有支持的选项"""
        options = get_pipeline_supported_options(pipeline)
        assert len(options) > 0, f"{pipeline} 没有定义支持的选项"

    @pytest.mark.parametrize("pipeline", list(OCRPipeline))
    def test_all_pipelines_have_display_name(self, pipeline):
        """所有管道都应有显示名称"""
        name = get_pipeline_display_name(pipeline)
        assert len(name) > 0, f"{pipeline} 没有定义显示名称"

    @pytest.mark.parametrize("pipeline", list(OCRPipeline))
    def test_all_pipelines_have_description(self, pipeline):
        """所有管道都应有描述"""
        desc = get_pipeline_description(pipeline)
        assert len(desc) > 0, f"{pipeline} 没有定义描述"

    @pytest.mark.parametrize("pipeline", list(OCRPipeline))
    def test_options_can_be_created_for_all_pipelines(self, pipeline):
        """所有管道都能创建选项"""
        options = OCROptions(pipeline=pipeline)
        assert options.pipeline == pipeline

    def test_options_round_trip_all_pipelines(self):
        """所有管道的选项都能序列化往返"""
        for pipeline in OCRPipeline:
            original = OCROptions(pipeline=pipeline)
            data = original.to_dict()
            restored = OCROptions.from_dict(data)
            assert restored.pipeline == pipeline

    def test_pipeline_count(self):
        """验证管道数量"""
        assert len(OCRPipeline) == 7
        assert len(get_all_pipelines()) == 7

    def test_doc_understanding_models(self):
        """测试文档理解模型列表"""
        assert len(DOC_UNDERSTANDING_MODELS) > 0
        assert DEFAULT_DOC_UNDERSTANDING_MODEL in DOC_UNDERSTANDING_MODELS

    def test_is_option_supported(self):
        """测试选项支持检查"""
        # OCR 管道支持预处理选项
        assert is_option_supported(OCRPipeline.OCR, "use_doc_orientation_classify")
        assert is_option_supported(OCRPipeline.OCR, "use_textline_orientation")

        # PP-StructureV3 支持子产线选项
        assert is_option_supported(OCRPipeline.PP_STRUCTURE_V3, "use_table_recognition")
        assert is_option_supported(
            OCRPipeline.PP_STRUCTURE_V3, "use_formula_recognition"
        )

        # PaddleOCR-VL 支持 VL 特有选项
        assert is_option_supported(OCRPipeline.PADDLEOCR_VL, "vl_use_layout_detection")

        # 文档理解支持模型选择
        assert is_option_supported(
            OCRPipeline.DOC_UNDERSTANDING, "doc_understanding_model"
        )

    def test_options_default_values(self):
        """测试选项默认值"""
        options = OCROptions()
        assert options.pipeline == OCRPipeline.OCR
        assert options.use_doc_orientation_classify is True
        assert options.use_doc_unwarping is True
        assert options.use_textline_orientation is False

    def test_options_copy(self):
        """测试选项复制"""
        original = OCROptions(pipeline=OCRPipeline.PP_STRUCTURE_V3)
        copied = original.copy(use_table_recognition=False)
        assert copied.pipeline == OCRPipeline.PP_STRUCTURE_V3
        assert copied.use_table_recognition is False
        # 原始不变
        assert original.use_table_recognition is True
