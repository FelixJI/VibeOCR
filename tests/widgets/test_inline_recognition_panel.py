"""InlineRecognitionPanel tests"""

from vibeocr.core.pipelines import OCRPipeline
from vibeocr.models.ocr_options import OCROptions
from vibeocr.widgets.inline_recognition_panel import InlineRecognitionPanel


class TestInlineRecognitionPanel:
    def test_initial_pipeline_is_ocr(self, qapp):
        panel = InlineRecognitionPanel()
        options = panel.get_options()
        assert options.pipeline == OCRPipeline.OCR

    def test_pipeline_buttons_exist(self, qapp):
        panel = InlineRecognitionPanel()
        assert len(panel._pipeline_buttons) == len(OCRPipeline)

    def test_get_options_uses_persisted(self, qapp, tmp_path):
        """get_options 返回持久化的选项而非默认值"""
        from vibeocr.utils.ocr_preferences import OCRPreferences

        OCRPreferences.reset_instance()
        try:
            prefs = OCRPreferences.instance(tmp_path)
            custom_opts = OCROptions(
                pipeline=OCRPipeline.OCR,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
            )
            prefs.set_pipeline_options("screenshot", OCRPipeline.OCR, custom_opts)

            panel = InlineRecognitionPanel()
            options = panel.get_options()
            assert options.use_doc_orientation_classify is False
            assert options.use_doc_unwarping is False
        finally:
            OCRPreferences.reset_instance()

    def test_click_button_loads_persisted_options(self, qapp, tmp_path):
        """点击按钮加载该管道的持久化选项"""
        from vibeocr.utils.ocr_preferences import OCRPreferences

        OCRPreferences.reset_instance()
        try:
            prefs = OCRPreferences.instance(tmp_path)
            custom_opts = OCROptions(
                pipeline=OCRPipeline.PP_STRUCTURE_V3,
                use_table_recognition=False,
            )
            prefs.set_pipeline_options(
                "screenshot", OCRPipeline.PP_STRUCTURE_V3, custom_opts
            )

            panel = InlineRecognitionPanel()
            panel._pipeline_buttons[OCRPipeline.PP_STRUCTURE_V3].click()
            options = panel.get_options()
            assert options.pipeline == OCRPipeline.PP_STRUCTURE_V3
            assert options.use_table_recognition is False
        finally:
            OCRPreferences.reset_instance()

    def test_tooltip_shows_option_state(self, qapp, tmp_path):
        """按钮 tooltip 显示关键选项状态"""
        from vibeocr.utils.ocr_preferences import OCRPreferences

        OCRPreferences.reset_instance()
        try:
            prefs = OCRPreferences.instance(tmp_path)
            prefs.set_pipeline_options(
                "screenshot",
                OCRPipeline.OCR,
                OCROptions(
                    pipeline=OCRPipeline.OCR,
                    use_doc_orientation_classify=False,
                ),
            )

            panel = InlineRecognitionPanel()
            tooltip = panel._pipeline_buttons[OCRPipeline.OCR].toolTip()
            assert "方向分类: 关" in tooltip
        finally:
            OCRPreferences.reset_instance()

    def test_set_options(self, qapp):
        panel = InlineRecognitionPanel()
        options = OCROptions(pipeline=OCRPipeline.PADDLEOCR_VL)
        panel.set_options(options)
        assert panel.get_options().pipeline == OCRPipeline.PADDLEOCR_VL
