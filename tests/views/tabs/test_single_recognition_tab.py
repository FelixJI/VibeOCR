"""SingleRecognitionTab 测试"""

from PySide6.QtWidgets import QWidget

from vibeocr.views.tabs.base_tab import BaseOcrTab
from vibeocr.views.tabs.single_recognition_tab import SingleRecognitionTab


class TestSingleRecognitionTab:
    def test_creation(self, qapp):
        tab = SingleRecognitionTab()
        assert isinstance(tab, QWidget)

    def test_is_base_ocr_tab(self, qapp):
        tab = SingleRecognitionTab()
        assert isinstance(tab, BaseOcrTab)

    def test_has_preview_widget(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._preview_widget is not None

    def test_has_result_widget(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._result_widget is not None

    def test_has_preprocess_options(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._preprocess_options is not None
