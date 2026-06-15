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
        """测试默认后端为 hybrid-engine"""
        options = OCROptions()
        assert options.backend == "hybrid-engine"

    def test_default_effort_is_medium(self):
        """测试默认解析强度为 medium"""
        options = OCROptions()
        assert options.effort == "medium"

    def test_paddlocr_vl_default_values(self):
        """测试 PaddleOCR-VL 选项默认值"""
        options = OCROptions(pipeline=OCRPipeline.PADDLEOCR_VL)
        assert options.vl_use_layout_detection is True
        assert options.vl_use_chart_recognition is False
        assert options.vl_use_seal_recognition is False
        assert options.use_ocr_for_image_block is False

    def test_pp_structure_v3_default_values(self):
        """测试 PP-StructureV3 选项默认值"""
        options = OCROptions()
        assert options.use_table_recognition is True
        assert options.use_formula_recognition is True
        assert options.use_seal_recognition is False
        assert options.use_chart_recognition is False

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

    def test_table_recognition_new_fields_defaults(self):
        """测试表格识别新增字段默认值"""
        options = OCROptions()
        assert options.use_e2e_wired_table_rec_model is False
        assert options.use_e2e_wireless_table_rec_model is True
        assert options.wireless_table_model_name == "SLANeXt_wireless"
        assert options.wired_table_model_name == "SLANeXt_wired"
        assert options.use_wired_table_cells_trans_to_html is False
        assert options.use_wireless_table_cells_trans_to_html is False
        assert options.text_det_limit_side_len is None
        assert options.text_det_thresh is None
        assert options.text_det_box_thresh is None
        assert options.text_det_unclip_ratio is None
        assert options.text_rec_score_thresh is None

    def test_formula_recognition_new_fields_defaults(self):
        """测试公式识别新增字段默认值"""
        options = OCROptions()
        assert options.formula_recognition_model_name is None
        assert options.formula_recognition_model_dir is None

    def test_table_new_fields_round_trip(self):
        """测试表格识别新字段序列化往返"""
        original = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_e2e_wired_table_rec_model=True,
            use_e2e_wireless_table_rec_model=False,
            use_wired_table_cells_trans_to_html=True,
            use_wireless_table_cells_trans_to_html=True,
            text_det_limit_side_len=960,
            text_det_thresh=0.3,
            text_det_box_thresh=0.5,
            text_det_unclip_ratio=2.0,
            text_rec_score_thresh=0.5,
        )
        data = original.to_dict()
        restored = OCROptions.from_dict(data)
        assert restored.use_e2e_wired_table_rec_model is True
        assert restored.use_e2e_wireless_table_rec_model is False
        assert restored.use_wired_table_cells_trans_to_html is True
        assert restored.use_wireless_table_cells_trans_to_html is True
        assert restored.text_det_limit_side_len == 960
        assert restored.text_det_thresh == 0.3
        assert restored.text_det_box_thresh == 0.5
        assert restored.text_det_unclip_ratio == 2.0
        assert restored.text_rec_score_thresh == 0.5

    def test_formula_new_fields_round_trip(self):
        """测试公式识别新字段序列化往返"""
        original = OCROptions(
            pipeline=OCRPipeline.FORMULA_RECOGNITION,
            formula_recognition_model_name="LaTeX-OCR",
            formula_recognition_model_dir="/models/formula",
        )
        data = original.to_dict()
        restored = OCROptions.from_dict(data)
        assert restored.formula_recognition_model_name == "LaTeX-OCR"
        assert restored.formula_recognition_model_dir == "/models/formula"

    def test_old_json_new_fields_get_defaults(self):
        """旧格式 JSON（缺少新字段）加载时应使用默认值"""
        data = {
            "pipeline": "OCR",
            "use_doc_orientation_classify": True,
        }
        options = OCROptions.from_dict(data)
        assert options.use_e2e_wired_table_rec_model is False
        assert options.use_e2e_wireless_table_rec_model is True
        assert options.text_det_limit_side_len is None
        assert options.formula_recognition_model_name is None
