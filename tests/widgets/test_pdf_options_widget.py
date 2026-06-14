# tests/widgets/test_pdf_options_widget.py
"""PdfOptionsWidget 组件测试"""

import pytest

from vibeocr.core.pipelines import OCRPipeline
from vibeocr.models.pdf_ocr_options import PdfGlobalSettings
from vibeocr.widgets.pdf_options_widget import PdfOptionsWidget


@pytest.fixture
def widget(qtbot):
    """创建 PdfOptionsWidget。"""
    w = PdfOptionsWidget()
    qtbot.addWidget(w)
    return w


class TestPdfOptionsWidget:
    """PdfOptionsWidget 组件测试"""

    def test_pipeline_locked_to_document(self, widget):
        """初始化后管道应锁定为文档类。"""
        assert widget.pipeline_options.is_pipeline_locked is True
        assert widget.pipeline_options.get_current_pipeline() in (
            OCRPipeline.DOCUMENT_PARSING,
            OCRPipeline.PADDLEOCR_VL,
        )

    def test_default_settings(self, widget):
        """get_settings 默认值应与 PdfGlobalSettings 默认一致。"""
        s = widget.get_settings()
        assert s.render_dpi == 300
        assert s.font_size_ratio == 0.8
        assert s.text_layer_visible is False

    def test_set_settings_round_trip(self, widget):
        """set_settings 后 get_settings 应返回相同值。"""
        custom = PdfGlobalSettings(
            render_dpi=200,
            max_pixels=8_000_000,
            font_size_ratio=0.6,
            text_layer_visible=True,
            font_size_retry_count=3,
            font_size_shrink_factor=0.5,
        )
        widget.set_settings(custom)
        loaded = widget.get_settings()
        assert loaded.render_dpi == 200
        assert loaded.max_pixels == 8_000_000
        assert loaded.font_size_ratio == 0.6
        assert loaded.text_layer_visible is True
        assert loaded.font_size_retry_count == 3
        assert loaded.font_size_shrink_factor == 0.5

    def test_settings_changed_signal(self, widget, qtbot):
        """修改 spinbox 应触发 settings_changed 信号。"""
        with qtbot.waitSignal(widget.settings_changed, timeout=1000) as blocker:
            widget._dpi_spin.setValue(150)
        assert blocker.args[0].render_dpi == 150

    def test_set_settings_does_not_emit(self, widget, qtbot):
        """set_settings 应阻塞信号，不触发 settings_changed。"""
        emitted = []
        widget.settings_changed.connect(lambda s: emitted.append(s))
        widget.set_settings(PdfGlobalSettings(render_dpi=100))
        assert emitted == []
