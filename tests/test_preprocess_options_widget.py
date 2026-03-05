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
        # 选择 PP-StructureV3
        index = widget._pipeline_combo.findData(OCRPipeline.PP_STRUCTURE_V3.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        assert widget.get_current_pipeline() == OCRPipeline.PP_STRUCTURE_V3

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
            pipeline=OCRPipeline.PP_STRUCTURE_V3,
            use_table_recognition=False,
        )
        widget.set_options(new_options)
        qtbot.wait(50)

        assert widget.get_current_pipeline() == OCRPipeline.PP_STRUCTURE_V3
        assert widget._table_recognition_cb.isChecked() is False

    def test_tab_visibility_ocr(self, widget, qtbot):
        """测试 OCR 管道的选项卡可见性"""
        index = widget._pipeline_combo.findData(OCRPipeline.OCR.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        # OCR 应显示预处理，不显示模型
        assert widget._tab_widget.isTabVisible(0) is True  # 预处理
        assert widget._tab_widget.isTabVisible(2) is False  # 模型

    def test_tab_visibility_doc_understanding(self, widget, qtbot):
        """测试文档理解的选项卡可见性"""
        index = widget._pipeline_combo.findData(OCRPipeline.DOC_UNDERSTANDING.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        # 文档理解应显示模型，不显示预处理
        assert widget._tab_widget.isTabVisible(0) is False  # 预处理
        assert widget._tab_widget.isTabVisible(2) is True  # 模型

    def test_all_pipelines_available(self, widget):
        """测试所有管道都可用"""
        combo_count = widget._pipeline_combo.count()
        assert combo_count == 7  # 7 个管道

    def test_pp_structure_options_visibility(self, widget, qtbot):
        """测试 PP-StructureV3 选项可见性"""
        index = widget._pipeline_combo.findData(OCRPipeline.PP_STRUCTURE_V3.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        # PP-StructureV3 应显示子产线选项组（使用 isHidden 而非 isVisible）
        assert widget._pp_structure_group.isHidden() is False
        assert widget._vl_group.isHidden() is True

    def test_vl_options_visibility(self, widget, qtbot):
        """测试 PaddleOCR-VL 选项可见性"""
        index = widget._pipeline_combo.findData(OCRPipeline.PADDLEOCR_VL.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        # VL 应显示 VL 选项组
        assert widget._vl_group.isHidden() is False
        assert widget._pp_structure_group.isHidden() is True

    def test_doc_understanding_model_selection(self, widget, qtbot):
        """测试文档理解模型选择"""
        index = widget._pipeline_combo.findData(OCRPipeline.DOC_UNDERSTANDING.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        # 选择不同的模型
        model_index = widget._doc_model_combo.findText("PP-DocBee-7B")
        if model_index >= 0:
            widget._doc_model_combo.setCurrentIndex(model_index)
            qtbot.wait(50)

            options = widget.get_options()
            assert options.doc_understanding_model == "PP-DocBee-7B"
