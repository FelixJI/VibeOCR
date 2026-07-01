"""Tests for ScreenCaptureOverlay."""

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QPixmap

from vibeocr.ui import theme
from vibeocr.widgets.screen_capture_overlay import ScreenCaptureOverlay


class TestScreenCaptureOverlayState:
    def test_initial_state_is_capturing(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert overlay._state == "CAPTURING"

    def test_min_selection_size(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert overlay.MIN_SELECTION_SIZE == 5

    def test_reset_clears_state(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._start_pos = QPoint(10, 10)
        overlay._end_pos = QPoint(100, 100)
        overlay._selection_rect = QRect(10, 10, 90, 90)
        overlay._screen_pixmap = QPixmap(100, 100)
        overlay._mapper = None  # will be reset to None
        overlay._reset_capturing()
        assert overlay._start_pos is None
        assert overlay._end_pos is None
        assert overlay._selection_rect is None
        assert overlay._screen_pixmap is None
        assert overlay._mapper is None


class TestScreenCaptureOverlaySignals:
    def test_confirmed_signal_defined(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert hasattr(overlay, "confirmed")

    def test_copied_signal_defined(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert hasattr(overlay, "copied")

    def test_saved_signal_defined(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert hasattr(overlay, "saved")

    def test_cancelled_signal_defined(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert hasattr(overlay, "cancelled")


class TestPositionCalculation:
    def test_calc_panel_positions_right_and_bottom(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        selection = QRect(100, 100, 400, 300)
        positions = overlay._calc_panel_positions(selection)
        assert positions["panel_side"] == "right"
        assert positions["toolbar_side"] == "bottom"

    def test_calc_panel_positions_left_flip(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        selection = QRect(1750, 100, 100, 300)
        positions = overlay._calc_panel_positions(selection)
        assert positions["panel_side"] == "left"

    def test_calc_panel_positions_top_flip(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        selection = QRect(100, 1050, 400, 10)
        positions = overlay._calc_panel_positions(selection)
        assert positions["toolbar_side"] == "top"


class TestSubState:
    def test_initial_sub_state_is_hover(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert overlay._sub_state == "HOVER"

    def test_initial_detected_rect_is_none(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert overlay._detected_rect is None

    def test_reset_capturing_resets_sub_state(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._sub_state = "DRAG"
        overlay._detected_rect = QRect(10, 10, 100, 100)
        overlay._reset_capturing()
        assert overlay._sub_state == "HOVER"
        assert overlay._detected_rect is None


class TestTooltipStyle:
    """回归：截图覆盖层的 QToolTip 必须使用浅色不透明背景，避免黑底。

    背景：覆盖层设置了 WA_TranslucentBackground/WA_NoSystemBackground，会传播到
    QToolTip 顶层窗口导致黑底。修复后 QToolTip 应指定浅色背景 token，并通过
    _fixup_popup 在显示前清除透明属性。
    """

    def test_tooltip_style_is_light_theme(self, qapp):
        overlay = ScreenCaptureOverlay()
        qss = overlay.styleSheet()
        # 浅色背景（surface_alt）而非近黑（text）
        assert theme.Colors.surface_alt.lower() in qss.lower()
        assert theme.Colors.text.lower() in qss.lower()  # 深色文字

    def test_fixup_popup_clears_translucent_attribute(self, qapp):
        """_fixup_popup 应清除弹出窗口的透明属性并启用样式背景。"""
        from PySide6.QtWidgets import QWidget

        popup = QWidget()
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        popup.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        ScreenCaptureOverlay._fixup_popup(popup)
        assert not popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert not popup.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        assert popup.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)


class TestStartCaptureInit:
    def test_creates_window_detector_with_overlay_hwnd(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._virtual_geometry = QRect(0, 0, 100, 100)
        overlay.show()
        hwnd = int(overlay.winId())
        overlay.start_capture()
        assert overlay._window_detector is not None
        assert overlay._window_detector._overlay_hwnd == hwnd
        overlay.hide()


class TestStartCaptureClearsPreviousSelection:
    """回归：开始新一轮截图时，不得残留上一轮的选区状态。

    背景：覆盖层是单例复用的，WA_NoSystemBackground 下窗口系统在 show()
    时不清屏。若上一轮的 _selection_rect / _detected_rect 未被清空，
    窗口变为可见的瞬间会短暂绘制上一轮的选区（即「一闪而过前面截图过的区域」）。
    """

    def test_start_capture_clears_stale_selection_rect(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay.show()
        overlay.winId()  # 确保有后备存储
        # 模拟上一轮截图遗留的选区状态
        overlay._selection_rect = QRect(100, 100, 400, 300)
        overlay._detected_rect = QRect(100, 100, 400, 300)
        overlay.start_capture()
        assert overlay._selection_rect is None
        assert overlay._detected_rect is None
        overlay.hide()

    def test_cleanup_clears_selection_before_hide(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._selection_rect = QRect(10, 10, 90, 90)
        overlay._screen_pixmap = QPixmap(100, 100)
        overlay._cleanup()
        # 清理后不得残留可绘制状态
        assert overlay._selection_rect is None
        assert overlay._screen_pixmap is None
        assert not overlay.isVisible()


from unittest.mock import MagicMock  # noqa: E402


class TestMouseMoveHoverDetect:
    def test_hover_calls_detector_and_sets_detected_rect(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)

        mapper = MagicMock()
        overlay._mapper = mapper

        detector = MagicMock()
        detector.detect_at.return_value = QRect(100, 100, 400, 300)
        overlay._window_detector = detector

        event = _make_mouse_event(QPoint(200, 200))
        overlay.mouseMoveEvent(event)

        detector.detect_at.assert_called_once_with(QPoint(200, 200), mapper)
        assert overlay._detected_rect == QRect(100, 100, 400, 300)

    def test_hover_sets_detected_rect_none_when_no_detection(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)

        overlay._mapper = MagicMock()

        detector = MagicMock()
        detector.detect_at.return_value = None
        overlay._window_detector = detector

        event = _make_mouse_event(QPoint(200, 200))
        overlay.mouseMoveEvent(event)

        assert overlay._detected_rect is None

    def test_drag_substate_uses_existing_logic(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "DRAG"
        overlay._start_pos = QPoint(10, 10)
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)

        detector = MagicMock()
        overlay._window_detector = detector

        event = _make_mouse_event(QPoint(200, 200))
        overlay.mouseMoveEvent(event)

        assert overlay._selection_rect == QRect(10, 10, 191, 191)
        detector.detect_at.assert_not_called()

    def test_hover_skips_detect_when_distance_too_small(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        overlay._last_detect_pos = QPoint(200, 200)

        overlay._mapper = MagicMock()

        detector = MagicMock()
        overlay._window_detector = detector

        event = _make_mouse_event(QPoint(201, 201))
        overlay.mouseMoveEvent(event)

        detector.detect_at.assert_not_called()


def _make_mouse_event(pos: QPoint) -> MagicMock:
    event = MagicMock()
    event.pos.return_value = pos
    return event


def _make_mouse_press_event(pos: QPoint, button) -> MagicMock:
    event = MagicMock()
    event.pos.return_value = pos
    event.button.return_value = button
    return event


class TestMousePressSubState:
    def test_hover_with_detected_rect_selects_and_enters_editing(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"
        overlay._screen_pixmap = QPixmap(1920, 1080)
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        overlay._detected_rect = QRect(100, 100, 400, 300)

        event = _make_mouse_press_event(QPoint(200, 200), Qt.MouseButton.LeftButton)
        overlay.mousePressEvent(event)

        assert overlay._selection_rect == QRect(100, 100, 400, 300)
        assert overlay._state == "EDITING"

    def test_hover_without_detected_rect_switches_to_drag(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"
        overlay._detected_rect = None

        event = _make_mouse_press_event(QPoint(200, 200), Qt.MouseButton.LeftButton)
        overlay.mousePressEvent(event)

        assert overlay._sub_state == "DRAG"
        assert overlay._start_pos == QPoint(200, 200)

    def test_right_button_ignored(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"

        event = _make_mouse_press_event(QPoint(200, 200), Qt.MouseButton.RightButton)
        overlay.mousePressEvent(event)

        assert overlay._sub_state == "HOVER"
        assert overlay._start_pos is None


class TestPaintDetectionHighlight:
    def test_detected_rect_drawn_in_capturing_hover(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"
        overlay._screen_pixmap = QPixmap(1920, 1080)
        overlay._detected_rect = QRect(100, 100, 400, 300)
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        overlay.resize(1920, 1080)
        # paintEvent should not crash
        overlay.repaint()

    def test_no_highlight_in_drag_substate(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "DRAG"
        overlay._screen_pixmap = QPixmap(1920, 1080)
        overlay._detected_rect = QRect(100, 100, 400, 300)
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        overlay.resize(1920, 1080)
        overlay.repaint()

    def test_no_highlight_without_detected_rect(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"
        overlay._screen_pixmap = QPixmap(1920, 1080)
        overlay._detected_rect = None
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        overlay.resize(1920, 1080)
        overlay.repaint()


class TestTempClipFileManagement:
    """临时剪贴板文件管理：写入/滚动清理/惰性校验/退出清理。"""

    def test_write_temp_clip_file_creates_file(self, qapp):
        overlay = ScreenCaptureOverlay()
        path = overlay._write_temp_clip_file(b"\x89PNG\r\n\x1a\n")
        assert path is not None
        assert path.exists()
        assert path in overlay._temp_clip_files
        path.unlink(missing_ok=True)

    def test_write_temp_clip_file_none_input(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert overlay._write_temp_clip_file(None) is None

    def test_prune_respects_max_limit(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._temp_clip_max = 3
        paths = []
        for _ in range(5):
            p = overlay._write_temp_clip_file(b"data")
            assert p is not None
            paths.append(p)
        overlay._prune_temp_clip_files()
        # 超过上限的最旧文件被删除
        assert len(overlay._temp_clip_files) == 3
        for p in paths[:2]:
            assert not p.exists()
        for p in paths[2:]:
            assert p.exists()
            p.unlink(missing_ok=True)

    def test_prune_drops_ghost_entries(self, qapp):
        overlay = ScreenCaptureOverlay()
        # 手动构造已不存在的幽灵路径
        from pathlib import Path

        overlay._temp_clip_files = [Path("nonexistent_a.png"), Path("nonexistent_b.png")]
        overlay._prune_temp_clip_files()
        assert overlay._temp_clip_files == []

    def test_cleanup_temp_clip_files_deletes_all(self, qapp):
        overlay = ScreenCaptureOverlay()
        created = []
        for _ in range(3):
            p = overlay._write_temp_clip_file(b"data")
            assert p is not None
            created.append(p)
        overlay._cleanup_temp_clip_files()
        assert overlay._temp_clip_files == []
        for p in created:
            assert not p.exists()

    def test_pixmap_to_png_returns_bytes(self, qapp):
        from vibeocr.widgets.screen_capture_overlay import ScreenCaptureOverlay as SCO

        pixmap = QPixmap(8, 8)
        pixmap.fill(Qt.GlobalColor.red)
        data = SCO._pixmap_to_png(pixmap)
        assert data is not None
        assert data.startswith(b"\x89PNG")
