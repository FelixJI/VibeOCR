# tests/models/test_ocr_options.py
"""OCROptions 测试"""

import pytest

from vibeocr.core.pipelines import OCRPipeline
from vibeocr.models.ocr_options import OCROptions


class TestOCROptions:
    """OCROptions 测试"""

    def test_default_values(self):
        """测试默认值"""
        options = OCROptions()
        assert options.pipeline == OCRPipeline.OCR
        assert options.use_doc_orientation_classify is True
        assert options.use_doc_unwarping is True
        assert options.use_textline_orientation is False

    def test_to_dict(self):
        """测试转换为字典"""
        options = OCROptions(pipeline=OCRPipeline.PP_STRUCTURE_V3)
        data = options.to_dict()
        assert data["pipeline"] == "PP-StructureV3"
        assert data["use_doc_orientation_classify"] is True
        assert "use_table_recognition" in data

    def test_from_dict(self):
        """测试从字典创建"""
        data = {
            "pipeline": "PP-StructureV3",
            "use_doc_orientation_classify": False,
            "use_seal_recognition": True,
        }
        options = OCROptions.from_dict(data)
        assert options.pipeline == OCRPipeline.PP_STRUCTURE_V3
        assert options.use_doc_orientation_classify is False
        assert options.use_seal_recognition is True

    def test_pp_structure_v3_options(self):
        """测试 PP-StructureV3 特有选项"""
        options = OCROptions(
            pipeline=OCRPipeline.PP_STRUCTURE_V3,
            use_table_recognition=True,
            use_formula_recognition=True,
            use_seal_recognition=True,
            use_chart_recognition=False,
        )
        assert options.use_table_recognition is True
        assert options.use_chart_recognition is False

    def test_paddleocr_vl_options(self):
        """测试 PaddleOCR-VL 特有选项"""
        options = OCROptions(
            pipeline=OCRPipeline.PADDLEOCR_VL,
            vl_use_layout_detection=True,
            vl_format_block_content=True,
            vl_temperature=0.5,
        )
        assert options.vl_use_layout_detection is True
        assert options.vl_temperature == 0.5

    def test_doc_understanding_model(self):
        """测试文档理解模型选择"""
        options = OCROptions(
            pipeline=OCRPipeline.DOC_UNDERSTANDING,
            doc_understanding_model="PP-DocBee-7B",
        )
        assert options.doc_understanding_model == "PP-DocBee-7B"

    def test_vlm_sampling_params(self):
        """测试 VLM 采样参数"""
        options = OCROptions(
            vl_temperature=0.7,
            vl_top_p=0.9,
            vl_max_pixels=1000000,
            vl_min_pixels=10000,
        )
        assert options.vl_temperature == 0.7
        assert options.vl_top_p == 0.9
        assert options.vl_max_pixels == 1000000

    def test_round_trip_serialization(self):
        """测试序列化往返"""
        original = OCROptions(
            pipeline=OCRPipeline.CHATOCRV4,
            use_doc_orientation_classify=False,
            use_seal_recognition=True,
        )
        data = original.to_dict()
        restored = OCROptions.from_dict(data)
        assert restored.pipeline == original.pipeline
        assert (
            restored.use_doc_orientation_classify
            == original.use_doc_orientation_classify
        )
        assert restored.use_seal_recognition == original.use_seal_recognition
