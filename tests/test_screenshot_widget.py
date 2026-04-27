"""Tests for ScreenshotWidget."""

from PySide6.QtCore import QPoint, QRect, Qt
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
        assert widget._device_pixel_ratio == 1.0

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
        widget.selection_done.connect(lambda p, r: captured.append(p))

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
        widget._device_pixel_ratio = 2.0

        widget._reset()

        assert widget._start_pos is None
        assert widget._end_pos is None
        assert widget._selection_rect is None
        assert widget._screen_pixmap is None
        assert widget._device_pixel_ratio == 1.0

    def test_capture_respects_device_pixel_ratio(self, qapp):
        """截图复制时正确处理设备像素比，保留物理像素分辨率。"""
        widget = ScreenshotWidget()
        # 模拟高DPI环境（2倍缩放）
        widget._device_pixel_ratio = 2.0

        # 创建一个设置了DPR的pixmap（200x200物理像素，DPR=2表示100x100逻辑像素）
        widget._screen_pixmap = QPixmap(200, 200)
        widget._screen_pixmap.fill(Qt.GlobalColor.white)
        widget._screen_pixmap.setDevicePixelRatio(2.0)

        # 创建选区（逻辑像素坐标）
        widget._selection_rect = QRect(10, 10, 50, 50)

        captured = []
        # 使用 selection_done 信号（新 API）
        widget.selection_done.connect(lambda p, r: captured.append(p))

        # 模拟鼠标释放
        class MockEvent:
            def button(self):
                return Qt.MouseButton.LeftButton

        widget.mouseReleaseEvent(MockEvent())

        # 验证捕获的图片尺寸正确
        assert len(captured) == 1
        captured_pixmap = captured[0]
        # DPR 重置为 1.0，但尺寸是物理像素（保留原始分辨率给 OCR）
        assert captured_pixmap.devicePixelRatio() == 1.0
        # 实际尺寸应该是 100x100（物理像素 = 逻辑像素 × DPR）
        # 这样 OCR 才能获得高质量的图片
        assert captured_pixmap.width() == 100
        assert captured_pixmap.height() == 100
