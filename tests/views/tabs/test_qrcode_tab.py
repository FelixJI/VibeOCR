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

    def test_tab_has_sub_tabs(self, qrcode_tab):
        from PySide6.QtWidgets import QTabWidget

        sub = qrcode_tab.findChild(QTabWidget, "subTabs")
        assert sub is not None
        assert sub.count() == 2
        assert sub.tabText(0) == "生成"
        assert sub.tabText(1) == "识别"

    def test_tab_has_decode_button(self, qrcode_tab):
        btn = qrcode_tab.findChild(QPushButton, "btnDecode")
        assert btn is not None
        assert not btn.isEnabled()  # 无图时禁用

    def test_tab_has_decode_service(self, qrcode_tab):
        from vibeocr.services.qrcode_decode_service import QrcodeDecodeService

        assert isinstance(qrcode_tab._decode_service, QrcodeDecodeService)


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


class TestQrcodeDecodeBehavior:
    def test_switch_to_decode_enables_drops(self, qrcode_tab):
        qrcode_tab.show()
        qrcode_tab._sub_tabs.setCurrentIndex(1)
        assert qrcode_tab._preview_label.acceptDrops() is True

    def test_switch_to_generate_disables_drops(self, qrcode_tab):
        qrcode_tab.show()
        qrcode_tab._sub_tabs.setCurrentIndex(1)
        qrcode_tab._sub_tabs.setCurrentIndex(0)
        assert qrcode_tab._preview_label.acceptDrops() is False

    def test_image_input_enables_decode_btn(self, qrcode_tab):
        from PySide6.QtGui import QPixmap

        pm = QPixmap(10, 10)
        pm.fill()
        qrcode_tab._on_image_input(pm)
        assert qrcode_tab._btn_decode.isEnabled()

    def test_clear_disables_decode_btn(self, qrcode_tab):
        from PySide6.QtGui import QPixmap

        pm = QPixmap(10, 10)
        pm.fill()
        qrcode_tab._on_image_input(pm)
        qrcode_tab._on_clear_decode()
        assert not qrcode_tab._btn_decode.isEnabled()

    def test_decode_qr_shows_result(self, qrcode_tab, qtbot):
        from vibeocr.services.qrcode_service import QrcodeService

        gen = QrcodeService()
        opts = gen.default_options()
        opts["format"] = "qr"
        pil_img = gen.generate("https://decode-test.example", opts)

        from vibeocr.views.tabs.qrcode_tab import _pil_to_qpixmap

        pm = _pil_to_qpixmap(pil_img)
        qrcode_tab._on_image_input(pm)
        qtbot.waitUntil(lambda: qrcode_tab._btn_decode.isEnabled())
        qrcode_tab._btn_decode.click()
        # 同步解码，结果立即可用
        assert qrcode_tab._decode_result_list.count() == 1
        assert "1" in qrcode_tab._result_count_label.text()

    def test_open_url_calls_desktop_services(self, qrcode_tab, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            "vibeocr.views.tabs.qrcode_tab.QDesktopServices.openUrl",
            lambda url: recorded.append(url.toString()),
        )
        qrcode_tab._on_open_url("https://example.com/x")
        assert recorded == ["https://example.com/x"]

    def test_copy_all_joins_results(self, qrcode_tab, qtbot):
        from PySide6.QtGui import QGuiApplication

        # 手动塞两条结果到 _decode_results 以测复制逻辑
        from vibeocr.services.qrcode_decode_service import DecodedItem

        qrcode_tab._decode_results = [
            DecodedItem("a", "QRCODE", False),
            DecodedItem("b", "QRCODE", False),
        ]
        qrcode_tab._on_copy_all()
        assert QGuiApplication.clipboard().text() == "a\nb"

    def test_blank_image_shows_zero_hint(self, qrcode_tab, qtbot):
        from PIL import Image

        from vibeocr.views.tabs.qrcode_tab import _pil_to_qpixmap

        blank = Image.new("RGB", (100, 100), "white")
        pm = _pil_to_qpixmap(blank)
        qrcode_tab._on_image_input(pm)
        qrcode_tab._btn_decode.click()
        # 空结果时 _decode_results 为空，但列表显示一条提示项
        assert qrcode_tab._decode_results == []
        assert qrcode_tab._decode_result_list.count() == 1  # 提示项
        assert "0" in qrcode_tab._result_count_label.text()

    def test_drop_label_emits_image_dropped(self, qrcode_tab, qtbot):
        """验证 DropLabel 信号能触发 _on_image_input。"""
        from PySide6.QtGui import QPixmap

        qrcode_tab.show()
        qrcode_tab._sub_tabs.setCurrentIndex(1)

        pm = QPixmap(20, 20)
        pm.fill()

        # 信号连接应触发 _on_image_input（通过 btnDecode 启用间接验证）
        qrcode_tab._preview_label.imageDropped.emit(pm)
        assert qrcode_tab._btn_decode.isEnabled()

    def test_generate_subtab_ignores_drops(self, qrcode_tab):
        """生成子页激活时，预览区不接受拖入。"""
        qrcode_tab.show()
        qrcode_tab._sub_tabs.setCurrentIndex(0)
        assert qrcode_tab._preview_label.acceptDrops() is False
