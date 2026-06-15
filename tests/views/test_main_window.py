"""Tests for MainWindow."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from vibeocr.models.ocr_result import OCRResult
from vibeocr.views.main_window import MainWindow


@pytest.fixture
def main_window(qapp, qtbot, tmp_path):
    """提供 MainWindow 实例。"""
    from vibeocr.managers.config_manager import ConfigManager

    ConfigManager.reset_instance()
    ConfigManager.instance(tmp_path)
    window = MainWindow()
    window.show()
    qtbot.addWidget(window)
    yield window
    window.close()
    ConfigManager.reset_instance()


class TestMainWindow:
    """测试 MainWindow 集成功能。"""

    def test_window_title(self, main_window):
        """窗口标题正确。"""
        assert main_window.windowTitle() == "VibeOCR"

    def test_copy_result_to_clipboard(self, main_window, qtbot):
        """复制识别结果到剪贴板。"""
        result = OCRResult(raw_text="测试文本")
        main_window._current_ocr_result = result
        main_window._clipboard_controller.set_result(result)

        # 点击复制纯文本按钮
        qtbot.mouseClick(main_window._ui.btnCopyPlain, Qt.MouseButton.LeftButton)

        # 验证剪贴板
        clipboard = QApplication.clipboard()
        assert clipboard.text() == "测试文本"

    def test_copy_rich_text_to_clipboard(self, main_window, qtbot):
        """复制富文本到剪贴板。"""
        result = OCRResult(
            raw_text="测试文本",
            markdown_text="# 标题\n\n测试文本",
            html_text="<h1>标题</h1><p>测试文本</p>",
        )
        main_window._current_ocr_result = result
        main_window._clipboard_controller.set_result(result)

        # 点击复制富文本按钮
        qtbot.mouseClick(main_window._ui.btnCopyRich, Qt.MouseButton.LeftButton)

        # 验证剪贴板有 HTML 内容
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        assert mime_data.hasHtml()
        assert "测试文本" in clipboard.text()

        # 验证 CF_HTML 格式（Microsoft Office 专用）
        cf_html_data = mime_data.data("HTML Format")
        assert not cf_html_data.isEmpty()
        cf_html_str = bytes(cf_html_data).decode("utf-8")
        assert "Version:0.9" in cf_html_str
        assert "StartFragment" in cf_html_str
        assert "测试文本" in cf_html_str

    def test_copy_markdown_to_clipboard(self, main_window, qtbot):
        """复制 Markdown 到剪贴板。"""
        result = OCRResult(
            raw_text="测试文本",
            markdown_text="# 标题\n\n测试文本",
        )
        main_window._current_ocr_result = result
        main_window._clipboard_controller.set_result(result)

        # 点击复制 Markdown 按钮
        qtbot.mouseClick(main_window._ui.btnCopyMarkdown, Qt.MouseButton.LeftButton)

        # 验证剪贴板
        clipboard = QApplication.clipboard()
        assert clipboard.text() == "# 标题\n\n测试文本"

    def test_status_bar_shows_copied(self, main_window, qtbot):
        """复制后状态栏显示提示。"""
        result = OCRResult(raw_text="测试文本")
        main_window._current_ocr_result = result
        main_window._clipboard_controller.set_result(result)
        qtbot.mouseClick(main_window._ui.btnCopyPlain, Qt.MouseButton.LeftButton)

        assert "复制" in main_window._statusbar.currentMessage()

    def test_open_image_file_loads_pixmap(self, main_window, qtbot, temp_image_file):
        """直接加载图片文件到预览组件。"""
        from PySide6.QtGui import QPixmap

        # 直接加载图片（绕过文件对话框和 OCR）
        pixmap = QPixmap(str(temp_image_file))
        assert not pixmap.isNull()

        main_window._ui.previewWidget.set_pixmap(pixmap)

        # 验证图片已加载
        assert main_window._ui.previewWidget.pixmap() is not None

    def test_overlay_exists(self, main_window):
        """截图遮罩组件已创建。"""
        assert main_window._overlay is not None


class TestQrcodeTabIntegration:
    def test_main_window_has_qrcode_tab(self, main_window):
        tab_widget = main_window._ui.tabWidget
        tab_names = [tab_widget.tabText(i) for i in range(tab_widget.count())]
        assert "二维码" in tab_names

    def test_qrcode_tab_position_before_settings(self, main_window):
        tab_widget = main_window._ui.tabWidget
        qrcode_idx = None
        settings_idx = None
        for i in range(tab_widget.count()):
            text = tab_widget.tabText(i)
            if text == "二维码":
                qrcode_idx = i
            elif "设置" in text:
                settings_idx = i
        assert qrcode_idx is not None
        assert settings_idx is not None
        assert qrcode_idx < settings_idx
