"""Tests for MainWindow."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

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
        # 设置测试文本
        main_window._ui.textResult.setPlainText("测试文本")

        # 点击复制按钮
        qtbot.mouseClick(main_window._ui.btnCopy, Qt.MouseButton.LeftButton)

        # 验证剪贴板
        clipboard = QApplication.clipboard()
        assert clipboard.text() == "测试文本"

    def test_status_bar_shows_copied(self, main_window, qtbot):
        """复制后状态栏显示提示。"""
        main_window._ui.textResult.setPlainText("测试文本")
        qtbot.mouseClick(main_window._ui.btnCopy, Qt.MouseButton.LeftButton)

        assert "复制" in main_window._ui.statusbar.currentMessage()

    def test_open_image_file_loads_pixmap(self, main_window, qtbot, temp_image_file):
        """直接加载图片文件到预览组件。"""
        from PySide6.QtGui import QPixmap

        # 直接加载图片（绕过文件对话框和 OCR）
        pixmap = QPixmap(str(temp_image_file))
        assert not pixmap.isNull()

        main_window._ui.previewWidget.set_pixmap(pixmap)

        # 验证图片已加载
        assert main_window._ui.previewWidget.pixmap() is not None

    def test_thread_pool_exists(self, main_window):
        """线程池已创建。"""
        assert main_window._thread_pool is not None

    def test_screenshot_widget_exists(self, main_window):
        """截图组件已创建。"""
        assert main_window._screenshot_widget is not None
