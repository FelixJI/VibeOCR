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

    def test_new_fields_default_values(self):
        """测试新增字段默认值"""
        options = OCROptions()
        assert options.lang_list == []
        assert options.start_page_id == 0
        assert options.end_page_id is None

    def test_new_fields_mineru_options(self):
        """测试 MineRU 选项的新字段"""
        options = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            lang_list=["zh", "en"],
            start_page_id=2,
            end_page_id=10,
        )
        assert options.lang_list == ["zh", "en"]
        assert options.start_page_id == 2
        assert options.end_page_id == 10

    def test_new_fields_round_trip(self):
        """测试新字段序列化往返"""
        original = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            lang_list=["ja"],
            start_page_id=1,
            end_page_id=5,
        )
        data = original.to_dict()
        restored = OCROptions.from_dict(data)
        assert restored.lang_list == ["ja"]
        assert restored.start_page_id == 1
        assert restored.end_page_id == 5

    def test_default_backend_is_hybrid(self):
        """测试默认后端改为 hybrid-auto-engine"""
        options = OCROptions()
        assert options.backend == "hybrid-auto-engine"

    def test_paddlocr_vl_default_values(self):
        """测试 PaddleOCR-VL 选项默认值"""
        options = OCROptions(pipeline=OCRPipeline.PADDLEOCR_VL)
        assert options.vl_use_layout_detection is None
        assert options.vl_use_chart_recognition is None
        assert options.vl_use_seal_recognition is None

    def test_paddlocr_vl_options(self):
        """测试 PaddleOCR-VL 选项设置"""
        options = OCROptions(
            pipeline=OCRPipeline.PADDLEOCR_VL,
            vl_use_layout_detection=False,
            vl_use_chart_recognition=True,
            vl_use_seal_recognition=True,
        )
        assert options.vl_use_layout_detection is False
        assert options.vl_use_chart_recognition is True
        assert options.vl_use_seal_recognition is True

    def test_paddlocr_vl_round_trip(self):
        """测试 PaddleOCR-VL 选项序列化往返"""
        original = OCROptions(
            pipeline=OCRPipeline.PADDLEOCR_VL,
            vl_use_layout_detection=True,
            vl_use_chart_recognition=True,
            vl_use_seal_recognition=False,
        )
        data = original.to_dict()
        assert data["vl_use_layout_detection"] is True
        assert data["vl_use_chart_recognition"] is True
        assert data["vl_use_seal_recognition"] is False
        assert data["pipeline"] == "PaddleOCR-VL"
        restored = OCROptions.from_dict(data)
        assert restored.pipeline == OCRPipeline.PADDLEOCR_VL
        assert restored.vl_use_layout_detection is True
        assert restored.vl_use_chart_recognition is True
        assert restored.vl_use_seal_recognition is False
