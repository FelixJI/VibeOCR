"""QrcodeTab UI 测试"""

import pytest
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
)

from vibeocr.services.qrcode_service import QrcodeService


@pytest.fixture
def qrcode_tab(qtbot):
    from vibeocr.views.tabs.qrcode_tab import QrcodeTab

    tab = QrcodeTab()
    qtbot.addWidget(tab)
    return tab


class TestQrcodeTabStructure:
    def test_tab_has_splitter(self, qrcode_tab):
        splitter = qrcode_tab.findChild(QSplitter)
        assert splitter is not None

    def test_tab_has_format_combo(self, qrcode_tab):
        combo = qrcode_tab.findChild(QComboBox)
        assert combo is not None

    def test_tab_has_text_input(self, qrcode_tab):
        text_edit = qrcode_tab.findChild(QPlainTextEdit)
        assert text_edit is not None

    def test_tab_has_preview_label(self, qrcode_tab):
        label = qrcode_tab.findChild(QLabel, "previewLabel")
        assert label is not None

    def test_tab_has_save_button(self, qrcode_tab):
        btn = qrcode_tab.findChild(QPushButton, "btnSave")
        assert btn is not None

    def test_tab_has_copy_button(self, qrcode_tab):
        btn = qrcode_tab.findChild(QPushButton, "btnCopy")
        assert btn is not None

    def test_tab_has_service(self, qrcode_tab):
        assert isinstance(qrcode_tab._service, QrcodeService)

    def test_format_combo_contains_qr(self, qrcode_tab):
        combo = qrcode_tab.findChild(QComboBox)
        texts = [combo.itemText(i) for i in range(combo.count())]
        assert "QR Code" in texts

    def test_format_combo_contains_code128(self, qrcode_tab):
        combo = qrcode_tab.findChild(QComboBox)
        texts = [combo.itemText(i) for i in range(combo.count())]
        assert "Code 128" in texts


class TestQrcodeTabBehavior:
    def test_qr_code_selected_shows_ec_buttons(self, qrcode_tab):
        qrcode_tab.show()
        combo = qrcode_tab.findChild(QComboBox)
        combo.setCurrentIndex(0)  # QR Code
        for btn in qrcode_tab._ec_group.buttons():
            assert not btn.isHidden()

    def test_barcode_selected_hides_ec_buttons(self, qrcode_tab):
        qrcode_tab.show()
        combo = qrcode_tab.findChild(QComboBox)
        combo.setCurrentIndex(1)  # Code 128
        for btn in qrcode_tab._ec_group.buttons():
            assert btn.isHidden()

    def test_qr_code_selected_shows_logo_section(self, qrcode_tab):
        qrcode_tab.show()
        combo = qrcode_tab.findChild(QComboBox)
        combo.setCurrentIndex(0)
        assert not qrcode_tab._logo_check.isHidden()

    def test_barcode_selected_hides_logo_section(self, qrcode_tab):
        qrcode_tab.show()
        combo = qrcode_tab.findChild(QComboBox)
        combo.setCurrentIndex(1)
        assert qrcode_tab._logo_check.isHidden()

    def test_logo_check_disables_select_when_unchecked(self, qrcode_tab):
        qrcode_tab._logo_check.setChecked(False)
        assert not qrcode_tab._logo_select_btn.isEnabled()

    def test_logo_check_enables_select_when_checked(self, qrcode_tab):
        qrcode_tab._logo_check.setChecked(True)
        assert qrcode_tab._logo_select_btn.isEnabled()

    def test_text_input_triggers_preview(self, qrcode_tab, qtbot):
        qrcode_tab._text_input.setPlainText("Hello World")
        qtbot.wait(400)
        assert qrcode_tab._current_image is not None

    def test_empty_text_shows_placeholder(self, qrcode_tab, qtbot):
        qrcode_tab._text_input.setPlainText("Hello")
        qtbot.wait(400)
        qrcode_tab._text_input.setPlainText("")
        qtbot.wait(400)
        assert qrcode_tab._current_image is None

    def test_paste_from_clipboard(self, qrcode_tab, qtbot):
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText("Pasted text")
        qrcode_tab._btn_paste.click()
        assert qrcode_tab._text_input.toPlainText() == "Pasted text"

    def test_copy_creates_clipboard_content(self, qrcode_tab, qtbot):
        from PySide6.QtGui import QGuiApplication

        qrcode_tab._text_input.setPlainText("Copy test")
        qtbot.wait(400)
        if qrcode_tab._current_image is not None:
            qrcode_tab._btn_copy.click()
            clipboard_pixmap = QGuiApplication.clipboard().pixmap()
            assert not clipboard_pixmap.isNull()
