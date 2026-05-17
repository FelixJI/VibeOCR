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


class TestLangCombo:
    """语言下拉框测试"""

    def test_lang_combo_exists(self, widget):
        """测试语言下拉框存在"""
        assert hasattr(widget, "_lang_combo")
        assert widget._lang_combo.count() >= 5

    def test_lang_combo_items(self, widget):
        """测试语言下拉框包含常用语言"""
        langs = []
        for i in range(widget._lang_combo.count()):
            langs.append(widget._lang_combo.itemData(i))
        assert "" in langs       # 自动检测
        assert "zh" in langs     # 中文
        assert "en" in langs     # 英文
        assert "zh,en" in langs  # 中英混合
        assert "ja" in langs     # 日文
        assert "ko" in langs     # 韩文

    def test_lang_combo_default(self, widget):
        """测试语言下拉框默认值为自动检测"""
        assert widget._lang_combo.currentIndex() == 0
        assert widget._lang_combo.currentData() == ""

    def test_get_options_lang_list_auto(self, widget, qtbot):
        """测试 get_options - 自动检测时 lang_list 为空"""
        index = widget._pipeline_combo.findData(OCRPipeline.DOCUMENT_PARSING.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        # 默认为自动检测
        options = widget.get_options()
        assert options.lang_list == []

    def test_get_options_lang_list_chinese(self, widget, qtbot):
        """测试 get_options - 选择中文时 lang_list 为 ['zh']"""
        index = widget._pipeline_combo.findData(OCRPipeline.DOCUMENT_PARSING.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        lang_idx = widget._lang_combo.findData("zh")
        widget._lang_combo.setCurrentIndex(lang_idx)
        qtbot.wait(50)

        options = widget.get_options()
        assert options.lang_list == ["zh"]

    def test_get_options_lang_list_mixed(self, widget, qtbot):
        """测试 get_options - 中英混合时 lang_list 为 ['zh', 'en']"""
        index = widget._pipeline_combo.findData(OCRPipeline.DOCUMENT_PARSING.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        lang_idx = widget._lang_combo.findData("zh,en")
        widget._lang_combo.setCurrentIndex(lang_idx)
        qtbot.wait(50)

        options = widget.get_options()
        assert options.lang_list == ["zh", "en"]

    def test_set_options_lang(self, widget, qtbot):
        """测试 set_options 能正确设置语言"""
        new_options = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            lang_list=["ja"],
        )
        widget.set_options(new_options)

        assert widget._lang_combo.currentData() == "ja"

    def test_set_options_lang_empty(self, widget, qtbot):
        """测试 set_options - lang_list 为空时恢复自动检测"""
        # 先设置为日文
        widget._lang_combo.setCurrentIndex(widget._lang_combo.findData("ja"))

        new_options = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            lang_list=[],
        )
        widget.set_options(new_options)

        assert widget._lang_combo.currentIndex() == 0
        assert widget._lang_combo.currentData() == ""


class TestPageRange:
    """页码范围控件测试"""

    def test_page_controls_exist(self, widget):
        """测试页码范围控件存在"""
        assert hasattr(widget, "_start_page_spin")
        assert hasattr(widget, "_end_page_spin")
        assert hasattr(widget, "_end_page_check")

    def test_start_page_default(self, widget):
        """测试起始页默认值"""
        assert widget._start_page_spin.value() == 0

    def test_end_page_default_disabled(self, widget):
        """测试结束页默认禁用"""
        assert widget._end_page_check.isChecked() is False
        assert widget._end_page_spin.isEnabled() is False

    def test_end_page_check_toggles_spin(self, widget, qtbot):
        """测试勾选限制后启用结束页"""
        widget._end_page_check.setChecked(True)
        qtbot.wait(50)

        assert widget._end_page_spin.isEnabled() is True

        widget._end_page_check.setChecked(False)
        qtbot.wait(50)

        assert widget._end_page_spin.isEnabled() is False

    def test_get_options_page_range_unlimited(self, widget, qtbot):
        """测试 get_options - 不限制结束页时 end_page_id 为 None"""
        index = widget._pipeline_combo.findData(OCRPipeline.DOCUMENT_PARSING.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        options = widget.get_options()
        assert options.start_page_id == 0
        assert options.end_page_id is None

    def test_get_options_page_range_limited(self, widget, qtbot):
        """测试 get_options - 限制结束页时返回正确值"""
        index = widget._pipeline_combo.findData(OCRPipeline.DOCUMENT_PARSING.value)
        widget._pipeline_combo.setCurrentIndex(index)
        qtbot.wait(50)

        widget._start_page_spin.setValue(5)
        widget._end_page_check.setChecked(True)
        widget._end_page_spin.setValue(20)
        qtbot.wait(50)

        options = widget.get_options()
        assert options.start_page_id == 5
        assert options.end_page_id == 20

    def test_set_options_page_range_unlimited(self, widget, qtbot):
        """测试 set_options - end_page_id 为 None 时取消限制"""
        new_options = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            start_page_id=3,
            end_page_id=None,
        )
        widget.set_options(new_options)

        assert widget._start_page_spin.value() == 3
        assert widget._end_page_check.isChecked() is False

    def test_set_options_page_range_limited(self, widget, qtbot):
        """测试 set_options - end_page_id 有值时启用限制"""
        new_options = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            start_page_id=10,
            end_page_id=50,
        )
        widget.set_options(new_options)

        assert widget._start_page_spin.value() == 10
        assert widget._end_page_check.isChecked() is True
        assert widget._end_page_spin.value() == 50


class TestBackendDefault:
    """后端默认值测试"""

    def test_backend_default_hybrid(self, widget):
        """测试后端默认值为混合引擎（推荐）"""
        # 混合引擎应在第一个位置
        assert widget._backend_combo.currentData() == "hybrid-auto-engine"
        assert widget._backend_combo.currentIndex() == 0
        # 确认第一项显示文字含 "推荐"
        assert "推荐" in widget._backend_combo.currentText()

    def test_backend_items_order(self, widget):
        """测试后端选项顺序"""
        assert widget._backend_combo.itemData(0) == "hybrid-auto-engine"
        assert widget._backend_combo.itemData(1) == "vlm-auto-engine"
        assert widget._backend_combo.itemData(2) == "pipeline"

    def test_set_options_backend(self, widget, qtbot):
        """测试 set_options 能正确设置后端"""
        new_options = OCROptions(
            pipeline=OCRPipeline.DOCUMENT_PARSING,
            backend="pipeline",
        )
        widget.set_options(new_options)

        assert widget._backend_combo.currentData() == "pipeline"
