"""Tests for WindowDetector."""

import ctypes.wintypes

import pytest

from PySide6.QtCore import QRect

from vibeocr.widgets.window_detector import WindowDetector


@pytest.fixture
def detector(qapp):
    overlay_hwnd = 12345
    return WindowDetector(overlay_hwnd)


class _MockWin32:
    def __init__(self, **kwargs):
        self._kwargs = kwargs

    def WindowFromPoint(self, point):
        return self._kwargs.get("window_from_point_result", 0)

    def GetAncestor(self, hwnd, flags):
        return self._kwargs.get("ancestor_result", hwnd)

    def IsWindowVisible(self, hwnd):
        return self._kwargs.get("is_visible", False)

    def GetWindowRect(self, hwnd, rect):
        result = self._kwargs.get("get_window_rect_result")
        if result is None:
            return False
        rect.left = result.left
        rect.top = result.top
        rect.right = result.right
        rect.bottom = result.bottom
        return True


class TestWindowDetectorInit:
    def test_stores_overlay_hwnd(self, detector):
        assert detector._overlay_hwnd == 12345

    def test_initial_cache_is_none(self, detector):
        assert detector._cached_hwnd is None
        assert detector._cached_rect is None


class TestHitTest:
    def test_returns_none_when_no_window(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(window_from_point_result=0),
        )
        result = detector._hit_test((100, 200))
        assert result is None

    def test_filters_overlay_hwnd(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(window_from_point_result=12345, ancestor_result=12345),
        )
        result = detector._hit_test((100, 200))
        assert result is None

    def test_returns_root_hwnd(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(window_from_point_result=999, ancestor_result=888, is_visible=True),
        )
        result = detector._hit_test((100, 200))
        assert result == 888


class TestGetWindowRect:
    def test_returns_rect_for_valid_hwnd(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(get_window_rect_result=ctypes.wintypes.RECT(100, 200, 500, 400)),
        )
        result = detector._get_window_rect(888)
        assert result == QRect(100, 200, 400, 200)

    def test_returns_none_for_invalid_hwnd(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(get_window_rect_result=None),
        )
        result = detector._get_window_rect(0)
        assert result is None
