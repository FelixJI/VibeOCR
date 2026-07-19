"""QrcodeTab UI 测试

Phase 3 第一切片：二维码生成/识别已迁移到 RPC 后端（SyncBackendClient）。
测试注入一个 FakeBackend（duck-typed），不启动真实 WorkerHost。
"""

import io

import pytest
from PIL import Image
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
)

from vibeocr.worker_host.backend_client import DecodedCode


class _FakeBackend:
    """Duck-typed backend: returns deterministic PNG / decode results.

    Implements the same surface QrcodeTab calls: generate_qrcode_sync,
    generate_qrcode_svg_sync, decode_qrcode_sync. No WorkerHost subprocess.
    """

    def __init__(self) -> None:
        self.generate_calls: list[tuple[str, dict]] = []
        self.decode_calls: list[bytes] = []
        # When set, decode_qrcode_sync returns these codes.
        self.decode_result: list[list[DecodedCode]] | None = None

    def generate_qrcode_sync(self, data: str, *, options: dict | None = None) -> bytes:
        self.generate_calls.append((data, options or {}))
        buf = io.BytesIO()
        Image.new("RGB", (10, 10), "black").save(buf, format="PNG")
        return buf.getvalue()

    def generate_qrcode_svg_sync(self, data: str, *, options: dict | None = None) -> str:
        return f"<svg>{data}</svg>"

    def decode_qrcode_sync(self, image_bytes: bytes) -> list[DecodedCode]:
        self.decode_calls.append(image_bytes)
        if self.decode_result is not None:
            return self.decode_result.pop(0)
        return []


@pytest.fixture()
def qrcode_tab(qtbot, qasync_loop):
    from vibeocr.views.tabs.qrcode_tab import QrcodeTab

    tab = QrcodeTab(backend=_FakeBackend())
    qtbot.addWidget(tab)
    return tab


def _wait_async(qtbot, condition, *, timeout_ms: int = 2000):
    import asyncio

    from tests.conftest import wait_until_done

    wait_until_done(
        qtbot, asyncio.get_event_loop(), condition, timeout_ms=timeout_ms
    )


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

    def test_tab_has_backend(self, qrcode_tab):
        """The tab lazily attaches to the process-wide backend session."""
        assert qrcode_tab._backend is not None or qrcode_tab._uses_shared_backend

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

    def test_text_input_triggers_preview_via_rpc(self, qrcode_tab, qtbot):
        """Text input calls generate_qrcode_sync on the backend."""
        qrcode_tab._text_input.setPlainText("Hello World")
        qrcode_tab._debounce_timer.stop()
        qrcode_tab._refresh_preview()
        _wait_async(qtbot, lambda: qrcode_tab._current_image is not None)
        assert qrcode_tab._current_image is not None
        assert len(qrcode_tab._backend.generate_calls) >= 1
        assert qrcode_tab._backend.generate_calls[-1][0] == "Hello World"

    def test_empty_text_shows_placeholder(self, qrcode_tab, qtbot):
        qrcode_tab._text_input.setPlainText("Hello")
        qrcode_tab._debounce_timer.stop()
        qrcode_tab._refresh_preview()
        _wait_async(qtbot, lambda: qrcode_tab._current_image is not None)
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
        qrcode_tab._debounce_timer.stop()
        qrcode_tab._refresh_preview()
        _wait_async(qtbot, lambda: qrcode_tab._current_image is not None)
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

    def test_decode_qr_shows_result_via_rpc(self, qrcode_tab, qtbot):
        """Decode calls decode_qrcode_sync and renders the returned codes."""
        qrcode_tab._backend.decode_result = [
            [DecodedCode(data="https://decode-test.example", fmt="QRCODE", is_url=True)]
        ]
        from PySide6.QtGui import QPixmap

        pm = QPixmap(10, 10)
        pm.fill()
        qrcode_tab._on_image_input(pm)
        qtbot.waitUntil(lambda: qrcode_tab._btn_decode.isEnabled())
        qrcode_tab._btn_decode.click()
        _wait_async(qtbot, lambda: qrcode_tab._decode_task is None)
        assert qrcode_tab._decode_result_list.count() == 1
        assert "1" in qrcode_tab._result_count_label.text()
        assert len(qrcode_tab._backend.decode_calls) == 1

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

        qrcode_tab._decode_results = [
            DecodedCode(data="a", fmt="QRCODE", is_url=False),
            DecodedCode(data="b", fmt="QRCODE", is_url=False),
        ]
        qrcode_tab._on_copy_all()
        assert QGuiApplication.clipboard().text() == "a\nb"

    def test_blank_image_shows_zero_hint(self, qrcode_tab, qtbot):
        """A blank image decodes to zero codes → hint item shown."""
        qrcode_tab._backend.decode_result = [[]]
        from PIL import Image

        from vibeocr.views.tabs.qrcode_tab import _pil_to_qpixmap

        blank = Image.new("RGB", (100, 100), "white")
        pm = _pil_to_qpixmap(blank)
        qrcode_tab._on_image_input(pm)
        qrcode_tab._btn_decode.click()
        _wait_async(qtbot, lambda: qrcode_tab._decode_task is None)
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


class TestQrcodeAsyncLifecycle:
    def test_slow_decode_keeps_qt_timer_responsive(
        self, qrcode_tab, qtbot, monkeypatch
    ):
        import threading

        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QPixmap

        started = threading.Event()
        release = threading.Event()

        def slow_decode(_payload):
            started.set()
            release.wait(timeout=2)
            return []

        monkeypatch.setattr(qrcode_tab._backend, "decode_qrcode_sync", slow_decode)
        pm = QPixmap(10, 10)
        pm.fill()
        qrcode_tab._on_image_input(pm)
        qrcode_tab._on_decode()
        _wait_async(qtbot, started.is_set)

        timer_fired: list[bool] = []
        QTimer.singleShot(0, lambda: timer_fired.append(True))
        _wait_async(qtbot, lambda: timer_fired == [True])
        assert qrcode_tab._decode_task is not None

        release.set()
        _wait_async(qtbot, lambda: qrcode_tab._decode_task is None)

    def test_old_preview_result_cannot_overwrite_newer_input(
        self, qrcode_tab, qtbot, monkeypatch
    ):
        import threading

        old_started = threading.Event()
        release_old = threading.Event()
        old_finished = threading.Event()

        def generate(data, *, options=None):
            if data == "old":
                old_started.set()
                release_old.wait(timeout=2)
                old_finished.set()
                color = "red"
            else:
                color = "blue"
            buf = io.BytesIO()
            Image.new("RGB", (10, 10), color).save(buf, format="PNG")
            return buf.getvalue()

        monkeypatch.setattr(qrcode_tab._backend, "generate_qrcode_sync", generate)
        qrcode_tab._text_input.setPlainText("old")
        qrcode_tab._debounce_timer.stop()
        qrcode_tab._refresh_preview()
        _wait_async(qtbot, old_started.is_set)
        qrcode_tab._text_input.setPlainText("new")
        qrcode_tab._debounce_timer.stop()
        qrcode_tab._refresh_preview()
        _wait_async(
            qtbot,
            lambda: qrcode_tab._current_image is not None
            and qrcode_tab._current_image.getpixel((0, 0)) == (0, 0, 255),
        )

        release_old.set()
        _wait_async(qtbot, old_finished.is_set)
        assert qrcode_tab._current_image.getpixel((0, 0)) == (0, 0, 255)

    def test_close_cancels_decode_and_duplicate_request_is_ignored(
        self, qrcode_tab, qtbot, monkeypatch
    ):
        import threading

        from PySide6.QtGui import QPixmap

        started = threading.Event()
        release = threading.Event()
        calls: list[bool] = []

        def slow_decode(_payload):
            calls.append(True)
            started.set()
            release.wait(timeout=2)
            return [DecodedCode(data="late", fmt="QRCODE", is_url=False)]

        monkeypatch.setattr(qrcode_tab._backend, "decode_qrcode_sync", slow_decode)
        pm = QPixmap(10, 10)
        pm.fill()
        qrcode_tab._on_image_input(pm)
        qrcode_tab._on_decode()
        _wait_async(qtbot, started.is_set)

        qrcode_tab._on_decode()
        assert calls == [True]
        qrcode_tab.set_closing(True)
        release.set()
        _wait_async(qtbot, lambda: qrcode_tab._decode_task is None)
        assert qrcode_tab._decode_results == []
