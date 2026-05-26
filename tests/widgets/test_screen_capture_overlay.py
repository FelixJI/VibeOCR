"""Tests for ScreenCaptureOverlay."""

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QPixmap

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


from unittest.mock import MagicMock


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
