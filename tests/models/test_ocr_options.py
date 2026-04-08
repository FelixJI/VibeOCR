# tests/models/test_ocr_options.py
"""OCROptions 测试"""

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
        options = OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING)
        data = options.to_dict()
        assert data["pipeline"] == "MinerU"
        assert data["use_doc_orientation_classify"] is True
        assert "parse_method" in data

    def test_from_dict(self):
        """测试从字典创建"""
        data = {
            "pipeline": "MinerU",
            "use_doc_orientation_classify": False,
            "enable_formula": False,
        }
        options = OCROptions.from_dict(data)
        assert options.pipeline == OCRPipeline.DOCUMENT_PARSING
        assert options.use_doc_orientation_classify is False
        assert options.enable_formula is False

    def test_mineru_options(self):
        """测试 MineRU 文档解析选项"""
        options = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            parse_method="ocr",
            enable_formula=False,
            enable_table=False,
        )
        assert options.parse_method == "ocr"
        assert options.enable_formula is False
        assert options.enable_table is False

    def test_mineru_default_values(self):
        """测试 MineRU 选项默认值"""
        options = OCROptions()
        assert options.parse_method == "auto"
        assert options.enable_formula is True
        assert options.enable_table is True

    def test_round_trip_serialization(self):
        """测试序列化往返"""
        original = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            use_doc_orientation_classify=False,
            parse_method="txt",
            enable_formula=False,
        )
        data = original.to_dict()
        restored = OCROptions.from_dict(data)
        assert restored.pipeline == original.pipeline
        assert (
            restored.use_doc_orientation_classify
            == original.use_doc_orientation_classify
        )
        assert restored.parse_method == "txt"
        assert restored.enable_formula is False
