"""Tests for MainWindow."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from vibeocr.models.ocr_result import OCRResult
from vibeocr.views.main_window import MainWindow


@pytest.fixture
def main_window(qapp, qtbot):
    """提供 MainWindow 实例。"""
    window = MainWindow()
    window.show()
    qtbot.addWidget(window)
    yield window
    window.close()


class TestMainWindow:
    """测试 MainWindow 集成功能。"""

    def test_window_title(self, main_window):
        """窗口标题正确。"""
        assert main_window.windowTitle() == "VibeOCR"

    def test_copy_result_to_clipboard(self, main_window, qtbot):
        """复制识别结果到剪贴板。"""
        # 设置测试 OCR 结果
        main_window._current_ocr_result = OCRResult(raw_text="测试文本")

        # 点击复制纯文本按钮
        qtbot.mouseClick(main_window._ui.btnCopyPlain, Qt.MouseButton.LeftButton)

        # 验证剪贴板
        clipboard = QApplication.clipboard()
        assert clipboard.text() == "测试文本"

    def test_copy_rich_text_to_clipboard(self, main_window, qtbot):
        """复制富文本到剪贴板。"""
        # 设置测试 OCR 结果（带富文本）
        main_window._current_ocr_result = OCRResult(
            raw_text="测试文本",
            markdown_text="# 标题\n\n测试文本",
            html_text="<h1>标题</h1><p>测试文本</p>",
        )

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
        # 设置测试 OCR 结果
        main_window._current_ocr_result = OCRResult(
            raw_text="测试文本",
            markdown_text="# 标题\n\n测试文本",
        )

        # 点击复制 Markdown 按钮
        qtbot.mouseClick(main_window._ui.btnCopyMarkdown, Qt.MouseButton.LeftButton)

        # 验证剪贴板
        clipboard = QApplication.clipboard()
        assert clipboard.text() == "# 标题\n\n测试文本"

    def test_status_bar_shows_copied(self, main_window, qtbot):
        """复制后状态栏显示提示。"""
        main_window._current_ocr_result = OCRResult(raw_text="测试文本")
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

    def test_screenshot_widget_exists(self, main_window):
        """截图组件已创建。"""
        assert main_window._screenshot_widget is not None
