# tests/test_preprocess_options_widget.py
"""预处理选项组件测试"""

import pytest
from PySide6.QtWidgets import QApplication

from vibeocr.core.pipelines import OCRPipeline
from vibeocr.models.ocr_options import OCROptions
from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget


@pytest.fixture
def app(qtbot):
    """Qt 应用"""
    return QApplication.instance() or QApplication([])


@pytest.fixture
def widget(app, qtbot):
    """创建组件"""
    w = PreprocessOptionsWidget()
    qtbot.addWidget(w)
    return w


class TestPreprocessOptionsWidget:
    """预处理选项组件测试"""

    def test_initial_state(self, widget):
        """测试初始状态"""
        assert widget.get_current_pipeline() == OCRPipeline.OCR

    def test_pipeline_selection(self, widget, qtbot):
        """测试管道选择"""
        # 选择文档解析
        index = widget._pipeline_combo.findData(OCRPipeline.DOCUMENT_PARSING.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        assert widget.get_current_pipeline() == OCRPipeline.DOCUMENT_PARSING

    def test_options_signal(self, widget, qtbot):
        """测试选项变更信号"""
        with qtbot.waitSignal(widget.options_changed, timeout=1000):
            widget._doc_orientation_cb.setChecked(False)

    def test_get_options(self, widget):
        """测试获取选项"""
        options = widget.get_options()
        assert isinstance(options, OCROptions)
        assert options.use_doc_orientation_classify is True

    def test_set_options(self, widget, qtbot):
        """测试设置选项"""
        new_options = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            enable_table=False,
        )
        widget.set_options(new_options)
        qtbot.wait(50)

        assert widget.get_current_pipeline() == OCRPipeline.DOCUMENT_PARSING
        assert widget._enable_table_cb.isChecked() is False

    def test_tab_visibility_ocr(self, widget, qtbot):
        """测试 OCR 管道的选项卡可见性"""
        index = widget._pipeline_combo.findData(OCRPipeline.OCR.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        # OCR 应显示预处理，不显示高级
        assert widget._tab_widget.isTabVisible(0) is True  # 预处理
        assert widget._tab_widget.isTabVisible(1) is False  # 高级（MinerU选项）

    def test_tab_visibility_document_parsing(self, widget, qtbot):
        """测试文档解析的选项卡可见性"""
        index = widget._pipeline_combo.findData(
            OCRPipeline.DOCUMENT_PARSING.value
        )
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        # 文档解析应显示高级选项
        assert widget._tab_widget.isTabVisible(1) is True  # 高级

    def test_all_pipelines_available(self, widget):
        """测试所有管道都可用"""
        combo_count = widget._pipeline_combo.count()
        assert combo_count == 4  # 4 个管道
