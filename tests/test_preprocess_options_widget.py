"""测试预处理选项组件"""

import pytest
from PySide6.QtWidgets import QApplication

from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget
from vibeocr.models.batch_request import PreprocessOptions


@pytest.fixture
def app(qtbot):
    """QApplication fixture"""
    return QApplication.instance() or QApplication([])


@pytest.fixture
def widget(app, qtbot):
    """创建组件"""
    widget = PreprocessOptionsWidget()
    qtbot.addWidget(widget)
    return widget


class TestPreprocessOptionsWidget:
    """PreprocessOptionsWidget 测试"""

    def test_initial_state(self, widget):
        """测试初始状态"""
        options = widget.get_options()

        assert options.use_doc_orientation_classify is True
        assert options.use_doc_unwarping is True
        assert options.use_textline_orientation is False

    def test_set_options(self, widget):
        """测试设置选项"""
        new_options = PreprocessOptions(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True
        )

        widget.set_options(new_options)
        options = widget.get_options()

        assert options.use_doc_orientation_classify is False
        assert options.use_doc_unwarping is False
        assert options.use_textline_orientation is True

    def test_toggle_option(self, widget, qtbot):
        """测试切换选项"""
        # 切换文档方向分类
        widget._doc_orientation_cb.setChecked(False)

        options = widget.get_options()
        assert options.use_doc_orientation_classify is False

    def test_options_changed_signal(self, widget, qtbot):
        """测试选项变更信号"""
        received_options = []

        def on_options_changed(options):
            received_options.append(options)

        widget.options_changed.connect(on_options_changed)

        # 切换选项
        widget._textline_orientation_cb.setChecked(True)

        # 等待信号处理
        qtbot.wait(100)

        assert len(received_options) == 1
        assert received_options[0].use_textline_orientation is True
