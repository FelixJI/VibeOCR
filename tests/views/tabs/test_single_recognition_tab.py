"""SingleRecognitionTab 测试"""

from PySide6.QtGui import QGuiApplication
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

    def test_has_action_buttons(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._screenshot_btn is not None
        assert tab._file_btn is not None
        assert tab._paste_btn is not None

    def test_has_copy_image_button(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._copy_image_btn is not None
        assert tab._copy_image_btn.text() == "复制图片"

    def test_copy_image_btn_disabled_by_default(self, qapp):
        tab = SingleRecognitionTab()
        assert tab._copy_image_btn.isEnabled() is False

    def test_copy_image_btn_enabled_after_set_pixmap(self, qapp, sample_pixmap):
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        assert tab._copy_image_btn.isEnabled() is True

    def test_copy_image_btn_enabled_after_set_image_for_recognition(
        self, qapp, sample_pixmap
    ):
        # 真实流程（main_window）：set_image_for_recognition 与 set_pixmap 配合调用；
        # 仅前者不加载预览，故复制按钮以预览 original_pixmap 为准。
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        tab.set_image_for_recognition(sample_pixmap)
        assert tab._copy_image_btn.isEnabled() is True

    def test_copy_image_btn_enabled_after_paste(self, qapp, sample_pixmap, monkeypatch):
        """模拟粘贴：让剪贴板返回 sample_pixmap（_on_paste 用 QGuiApplication）。"""
        from PySide6.QtGui import QGuiApplication

        class FakeClipboard:
            def pixmap(self):
                return sample_pixmap

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()
        tab._on_paste()
        assert tab._copy_image_btn.isEnabled() is True

    def test_screenshot_btn_emits_signal(self, qapp):
        tab = SingleRecognitionTab()
        emitted = []
        tab.screenshot_requested.connect(lambda: emitted.append(True))
        tab._screenshot_btn.click()
        assert emitted


class TestSingleRecognitionTabCopyImage:
    """「复制图片」复制逻辑测试"""

    def test_on_copy_image_copies_original(self, qapp, sample_pixmap, monkeypatch):
        """点复制图片后，剪贴板收到的是原始图片（original_pixmap）。"""
        captured = {}

        class FakeClipboard:
            def setPixmap(self, pm):
                captured["pixmap"] = pm

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        tab._on_copy_image()
        assert "pixmap" in captured
        # 复制的是原图（cacheKey 与 original_pixmap 一致）
        assert (
            captured["pixmap"].cacheKey()
            == tab._preview_widget.original_pixmap().cacheKey()
        )

    def test_on_copy_image_shows_toast(self, qapp, sample_pixmap, monkeypatch):
        class FakeClipboard:
            def setPixmap(self, pm):
                pass

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        tab._on_copy_image()
        # 注意：未 show() 顶层窗口时 isVisible() 恒为 False，改用 isHidden() 判定
        assert tab._copy_toast.isHidden() is False
        assert "原图已复制" in tab._copy_toast.text()

    def test_on_copy_image_noop_without_pixmap(self, qapp, monkeypatch):
        called = {"yes": False}

        class FakeClipboard:
            def setPixmap(self, pm):
                called["yes"] = True

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()  # 无图
        tab._on_copy_image()
        assert called["yes"] is False

    def test_copy_image_btn_triggers_on_copy(self, qapp, sample_pixmap, monkeypatch):
        called = {"yes": False}

        class FakeClipboard:
            def setPixmap(self, pm):
                called["yes"] = True

        monkeypatch.setattr(QGuiApplication, "clipboard", lambda *a, **k: FakeClipboard())
        tab = SingleRecognitionTab()
        tab.set_pixmap(sample_pixmap)
        tab._copy_image_btn.click()
        assert called["yes"] is True
