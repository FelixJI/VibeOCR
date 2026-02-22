"""Tests for ScreenshotWidget."""

import pytest
from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QKeyEvent, QPixmap

from vibeocr.widgets.screenshot_widget import ScreenshotWidget


class TestScreenshotWidget:
    """测试 ScreenshotWidget 组件。"""

    def test_min_selection_size_constant(self, qapp):
        """最小选区尺寸常量定义正确。"""
        assert ScreenshotWidget.MIN_SELECTION_SIZE == 5

    def test_initial_state(self, qapp):
        """初始状态正确。"""
        widget = ScreenshotWidget()

        assert widget._start_pos is None
        assert widget._end_pos is None
        assert widget._selection_rect is None
        assert widget._screen_pixmap is None

    def test_mouse_press_creates_selection(self, qapp):
        """鼠标按下创建选区。"""
        widget = ScreenshotWidget()

        # 模拟鼠标按下
        class MockEvent:
            def button(self):
                return Qt.MouseButton.LeftButton

            def pos(self):
                return QPoint(10, 10)

        widget.mousePressEvent(MockEvent())

        assert widget._start_pos == QPoint(10, 10)
        assert widget._selection_rect is not None

    def test_small_selection_not_captured(self, qapp):
        """小于最小尺寸的选区不触发捕获。"""
        widget = ScreenshotWidget()
        # 设置一个假的屏幕截图
        widget._screen_pixmap = QPixmap(100, 100)
        widget._screen_pixmap.fill(Qt.GlobalColor.white)

        # 创建太小的选区（3x3）
        widget._selection_rect = QRect(0, 0, 3, 3)

        captured = []
        widget.captured.connect(lambda p: captured.append(p))

        # 模拟鼠标释放
        class MockEvent:
            def button(self):
                return Qt.MouseButton.LeftButton

        widget.mouseReleaseEvent(MockEvent())

        assert len(captured) == 0

    def test_escape_cancels_screenshot(self, qapp):
        """ESC 键取消截图。"""
        widget = ScreenshotWidget()
        widget._selection_rect = QRect(0, 0, 100, 100)
        widget._screen_pixmap = QPixmap(100, 100)
        widget._screen_pixmap.fill(Qt.GlobalColor.white)

        # 模拟 ESC 键
        key_event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        widget.keyPressEvent(key_event)

        assert widget._selection_rect is None
        assert widget._screen_pixmap is None

    def test_reset_clears_state(self, qapp):
        """_reset 方法清除所有状态。"""
        widget = ScreenshotWidget()
        widget._start_pos = QPoint(10, 10)
        widget._end_pos = QPoint(100, 100)
        widget._selection_rect = QRect(10, 10, 90, 90)
        widget._screen_pixmap = QPixmap(100, 100)

        widget._reset()

        assert widget._start_pos is None
        assert widget._end_pos is None
        assert widget._selection_rect is None
        assert widget._screen_pixmap is None
