# tests/test_inline_recognition_panel.py
"""Tests for InlineRecognitionPanel."""

from vibeocr.widgets.inline_recognition_panel import InlineRecognitionPanel
from vibeocr.core.pipelines import OCRPipeline
from vibeocr.models.ocr_options import OCROptions


class TestInlineRecognitionPanel:
    def test_initial_pipeline_is_ocr(self, qapp):
        panel = InlineRecognitionPanel()
        options = panel.get_options()
        assert options.pipeline == OCRPipeline.OCR

    def test_pipeline_buttons_exist(self, qapp):
        panel = InlineRecognitionPanel()
        assert len(panel._pipeline_buttons) == 4

    def test_click_table_recognition(self, qapp):
        panel = InlineRecognitionPanel()
        panel._pipeline_buttons[OCRPipeline.TABLE_RECOGNITION].click()
        options = panel.get_options()
        assert options.pipeline == OCRPipeline.TABLE_RECOGNITION

    def test_set_options(self, qapp):
        panel = InlineRecognitionPanel()
        options = OCROptions()
        options.pipeline = OCRPipeline.FORMULA_RECOGNITION
        panel.set_options(options)
        result = panel.get_options()
        assert result.pipeline == OCRPipeline.FORMULA_RECOGNITION

    def test_more_settings_toggle(self, qapp):
        panel = InlineRecognitionPanel()
        assert not panel._settings_expanded
        panel._btn_more.click()
        assert panel._settings_expanded
