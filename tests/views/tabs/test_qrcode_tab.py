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
