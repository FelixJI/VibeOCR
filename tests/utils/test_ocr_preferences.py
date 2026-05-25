# tests/utils/test_ocr_preferences.py
"""OCRPreferences 持久化测试"""

import json
from pathlib import Path

import pytest

from vibeocr.core.pipelines import OCRPipeline
from vibeocr.models.ocr_options import OCROptions
from vibeocr.utils.ocr_preferences import OCRPreferences


@pytest.fixture
def tmp_config_dir(tmp_path):
    """提供临时配置目录"""
    return tmp_path


@pytest.fixture(autouse=True)
def reset_singleton():
    """每个测试前后重置单例"""
    OCRPreferences.reset_instance()
    yield
    OCRPreferences.reset_instance()


class TestOCRPreferencesNewFields:
    """验证新字段（表格识别、公式识别）通过持久化正确保存和加载"""

    def test_table_recognition_round_trip(self, tmp_config_dir):
        """表格识别选项保存再加载应保持一致"""
        prefs = OCRPreferences(tmp_config_dir)

        options = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_wireless_table=False,
            use_table_orientation_classify=False,
            use_ocr_results_with_table_cells=False,
        )
        prefs.set_options(options)

        # 重置单例并重新加载
        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        loaded = prefs2.get_options()

        assert loaded.pipeline == OCRPipeline.TABLE_RECOGNITION
        assert loaded.use_wireless_table is False
        assert loaded.use_table_orientation_classify is False
        assert loaded.use_ocr_results_with_table_cells is False

    def test_formula_recognition_round_trip(self, tmp_config_dir):
        """公式识别选项保存再加载应保持一致"""
        prefs = OCRPreferences(tmp_config_dir)

        options = OCROptions(
            pipeline=OCRPipeline.FORMULA_RECOGNITION,
            formula_recognition_batch_size=8,
        )
        prefs.set_options(options)

        # 重置单例并重新加载
        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        loaded = prefs2.get_options()

        assert loaded.pipeline == OCRPipeline.FORMULA_RECOGNITION
        assert loaded.formula_recognition_batch_size == 8

    def test_batch_options_new_fields(self, tmp_config_dir):
        """批量选项也应正确保存新字段"""
        prefs = OCRPreferences(tmp_config_dir)

        batch_opts = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_wireless_table=True,
        )
        prefs.set_batch_options(batch_opts)

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        loaded = prefs2.get_batch_options()

        assert loaded.pipeline == OCRPipeline.TABLE_RECOGNITION
        assert loaded.use_wireless_table is True

    def test_all_pipelines_persist(self, tmp_config_dir):
        """所有管道类型都应能正确保存和恢复"""
        prefs = OCRPreferences(tmp_config_dir)

        for pipeline in OCRPipeline:
            options = OCROptions(pipeline=pipeline)
            prefs.set_options(options)

            OCRPreferences.reset_instance()
            prefs2 = OCRPreferences(tmp_config_dir)
            loaded = prefs2.get_options()
            assert loaded.pipeline == pipeline, f"管道 {pipeline} 持久化失败"

            # 重置以准备下一次迭代
            OCRPreferences.reset_instance()
            prefs = OCRPreferences(tmp_config_dir)

    def test_json_file_contains_new_fields(self, tmp_config_dir):
        """验证 JSON 文件中确实包含新字段"""
        prefs = OCRPreferences(tmp_config_dir)

        options = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_wireless_table=True,
            use_table_orientation_classify=False,
            use_ocr_results_with_table_cells=True,
        )
        prefs.set_options(options)

        config_path = tmp_config_dir / "ocr_preferences.json"
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["version"] == 2
        main_data = data["main"]["TABLE_RECOGNITION"]
        assert main_data["use_wireless_table"] is True
        assert main_data["use_table_orientation_classify"] is False
        assert main_data["use_ocr_results_with_table_cells"] is True
        assert main_data["formula_recognition_batch_size"] == 1  # default

    def test_missing_new_fields_get_defaults(self, tmp_config_dir):
        """旧格式 JSON（缺少新字段）加载时应使用默认值"""
        config_path = tmp_config_dir / "ocr_preferences.json"
        old_data = {
            "pipeline": "OCR",
            "use_doc_orientation_classify": True,
            "use_doc_unwarping": True,
            "use_textline_orientation": False,
            "batch_options": {"pipeline": "OCR"},
            "version": 1,
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(old_data, f)

        prefs = OCRPreferences(tmp_config_dir)
        loaded = prefs.get_options()

        # 新字段应使用默认值
        assert loaded.use_wireless_table is True
        assert loaded.use_table_orientation_classify is True
        assert loaded.use_ocr_results_with_table_cells is True
        assert loaded.formula_recognition_batch_size == 1

    def test_table_new_fields_round_trip(self, tmp_config_dir):
        """表格识别新增字段持久化往返"""
        prefs = OCRPreferences(tmp_config_dir)

        options = OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_e2e_wired_table_rec_model=True,
            text_det_limit_side_len=960,
            text_det_thresh=0.3,
        )
        prefs.set_options(options)

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        loaded = prefs2.get_options()

        assert loaded.use_e2e_wired_table_rec_model is True
        assert loaded.text_det_limit_side_len == 960
        assert loaded.text_det_thresh == 0.3

    def test_formula_new_fields_round_trip(self, tmp_config_dir):
        """公式识别新增字段持久化往返"""
        prefs = OCRPreferences(tmp_config_dir)

        options = OCROptions(
            pipeline=OCRPipeline.FORMULA_RECOGNITION,
            formula_recognition_model_name="LaTeX-OCR",
            formula_recognition_model_dir="/models/formula",
        )
        prefs.set_options(options)

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)
        loaded = prefs2.get_options()

        assert loaded.formula_recognition_model_name == "LaTeX-OCR"
        assert loaded.formula_recognition_model_dir == "/models/formula"


class TestPerPipelineStorage:
    """Per-pipeline options storage tests"""

    def test_set_and_get_pipeline_options(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)

        options = OCROptions(
            pipeline=OCRPipeline.OCR,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
        )
        prefs.set_pipeline_options("main", OCRPipeline.OCR, options)

        loaded = prefs.get_pipeline_options("main", OCRPipeline.OCR)
        assert loaded.use_doc_orientation_classify is False
        assert loaded.use_doc_unwarping is False

    def test_different_sources_independent(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)

        main_opts = OCROptions(pipeline=OCRPipeline.OCR, use_doc_unwarping=False)
        screenshot_opts = OCROptions(pipeline=OCRPipeline.OCR, use_doc_unwarping=True)

        prefs.set_pipeline_options("main", OCRPipeline.OCR, main_opts)
        prefs.set_pipeline_options("screenshot", OCRPipeline.OCR, screenshot_opts)

        assert prefs.get_pipeline_options("main", OCRPipeline.OCR).use_doc_unwarping is False
        assert prefs.get_pipeline_options("screenshot", OCRPipeline.OCR).use_doc_unwarping is True

    def test_different_pipelines_independent(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)

        ocr_opts = OCROptions(pipeline=OCRPipeline.OCR, use_doc_unwarping=False)
        struct_opts = OCROptions(pipeline=OCRPipeline.PP_STRUCTURE_V3, use_doc_unwarping=True)

        prefs.set_pipeline_options("main", OCRPipeline.OCR, ocr_opts)
        prefs.set_pipeline_options("main", OCRPipeline.PP_STRUCTURE_V3, struct_opts)

        assert prefs.get_pipeline_options("main", OCRPipeline.OCR).use_doc_unwarping is False
        assert prefs.get_pipeline_options("main", OCRPipeline.PP_STRUCTURE_V3).use_doc_unwarping is True

    def test_get_unsaved_pipeline_returns_default(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)

        loaded = prefs.get_pipeline_options("main", OCRPipeline.TABLE_RECOGNITION)
        assert loaded.pipeline == OCRPipeline.TABLE_RECOGNITION
        assert loaded.use_wireless_table is True  # default

    def test_persist_and_reload(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)

        prefs.set_pipeline_options("screenshot", OCRPipeline.OCR, OCROptions(
            pipeline=OCRPipeline.OCR,
            use_doc_unwarping=False,
        ))
        prefs.set_pipeline_options("screenshot", OCRPipeline.TABLE_RECOGNITION, OCROptions(
            pipeline=OCRPipeline.TABLE_RECOGNITION,
            use_wireless_table=False,
        ))

        OCRPreferences.reset_instance()
        prefs2 = OCRPreferences(tmp_config_dir)

        assert prefs2.get_pipeline_options("screenshot", OCRPipeline.OCR).use_doc_unwarping is False
        assert prefs2.get_pipeline_options("screenshot", OCRPipeline.TABLE_RECOGNITION).use_wireless_table is False

    def test_pipeline_options_changed_signal(self, tmp_config_dir):
        prefs = OCRPreferences(tmp_config_dir)
        received = []
        prefs.pipeline_options_changed.connect(lambda s, o: received.append((s, o.pipeline)))

        prefs.set_pipeline_options("main", OCRPipeline.OCR, OCROptions(pipeline=OCRPipeline.OCR))

        assert len(received) == 1
        assert received[0] == ("main", OCRPipeline.OCR)


class TestVersionMigration:
    """v1 → v2 migration tests"""

    def test_v1_migrates_to_v2(self, tmp_config_dir):
        config_path = tmp_config_dir / "ocr_preferences.json"
        old_data = {
            "pipeline": "PP-StructureV3",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": True,
            "batch_options": {"pipeline": "OCR"},
            "version": 1,
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(old_data, f)

        prefs = OCRPreferences(tmp_config_dir)

        # Old options migrated to "main" under PP-StructureV3
        loaded = prefs.get_pipeline_options("main", OCRPipeline.PP_STRUCTURE_V3)
        assert loaded.pipeline == OCRPipeline.PP_STRUCTURE_V3
        assert loaded.use_doc_orientation_classify is False
        assert loaded.use_doc_unwarping is True

        # legacy get_options returns the migrated pipeline
        assert prefs.get_options().pipeline == OCRPipeline.PP_STRUCTURE_V3

    def test_v1_migration_preserves_batch_options(self, tmp_config_dir):
        config_path = tmp_config_dir / "ocr_preferences.json"
        old_data = {
            "pipeline": "OCR",
            "batch_options": {"pipeline": "MinerU", "enable_formula": False},
            "version": 1,
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(old_data, f)

        prefs = OCRPreferences(tmp_config_dir)
        assert prefs.get_batch_options().enable_formula is False

    def test_v2_loads_correctly(self, tmp_config_dir):
        config_path = tmp_config_dir / "ocr_preferences.json"
        v2_data = {
            "version": 2,
            "last_main_pipeline": "TABLE_RECOGNITION",
            "main": {
                "TABLE_RECOGNITION": {
                    "pipeline": "TABLE_RECOGNITION",
                    "use_wireless_table": False,
                },
            },
            "screenshot": {
                "OCR": {
                    "pipeline": "OCR",
                    "use_doc_unwarping": False,
                },
            },
            "batch_options": {"pipeline": "OCR"},
        }
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(v2_data, f)

        prefs = OCRPreferences(tmp_config_dir)

        assert prefs.get_pipeline_options("main", OCRPipeline.TABLE_RECOGNITION).use_wireless_table is False
        assert prefs.get_pipeline_options("screenshot", OCRPipeline.OCR).use_doc_unwarping is False
        # legacy get_options uses last_main_pipeline
        assert prefs.get_options().pipeline == OCRPipeline.TABLE_RECOGNITION
