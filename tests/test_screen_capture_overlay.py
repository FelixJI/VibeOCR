"""Tests for ScreenCaptureOverlay."""

from PySide6.QtCore import QPoint, QRect
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
        overlay._device_pixel_ratio = 2.0
        overlay._reset_capturing()
        assert overlay._start_pos is None
        assert overlay._end_pos is None
        assert overlay._selection_rect is None
        assert overlay._screen_pixmap is None
        assert overlay._device_pixel_ratio == 1.0


class TestScreenCaptureOverlaySignals:
    def test_confirmed_signal_defined(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert hasattr(overlay, 'confirmed')

    def test_copied_signal_defined(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert hasattr(overlay, 'copied')

    def test_saved_signal_defined(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert hasattr(overlay, 'saved')

    def test_cancelled_signal_defined(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert hasattr(overlay, 'cancelled')


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
