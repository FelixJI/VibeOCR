"""WindowDetector — 通过 Win32 API 检测鼠标下的窗口和子控件边界。

仅 Windows 平台可用。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys

from PySide6.QtCore import QPoint, QRect

if sys.platform != "win32":
    raise ImportError("WindowDetector is only available on Windows")

user32 = ctypes.windll.user32


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


GA_ROOT = 2


class _Win32Bindings:
    def WindowFromPoint(self, point: _POINT) -> int:
        return user32.WindowFromPoint(point)

    def GetAncestor(self, hwnd: int, flags: int) -> int:
        return user32.GetAncestor(hwnd, flags)

    def IsWindowVisible(self, hwnd: int) -> bool:
        return bool(user32.IsWindowVisible(hwnd))


_win = _Win32Bindings()


class WindowDetector:
    def __init__(self, overlay_hwnd: int) -> None:
        self._overlay_hwnd = overlay_hwnd
        self._cached_hwnd: int | None = None
        self._cached_rect: QRect | None = None

    def detect_at(
        self,
        pos: QPoint,
        dpr: float,
        virtual_offset: QPoint,
    ) -> QRect | None:
        physical_x = int(pos.x() * dpr) + int(virtual_offset.x() * dpr)
        physical_y = int(pos.y() * dpr) + int(virtual_offset.y() * dpr)
        hwnd = self._hit_test((physical_x, physical_y))
        if hwnd is None:
            self._cached_hwnd = None
            self._cached_rect = None
            return None

        rect = self._get_control_rect(hwnd, (physical_x, physical_y))
        if rect is None:
            rect = self._get_window_rect(hwnd)
        if rect is None:
            return None

        logical = QRect(
            int((rect.x() - virtual_offset.x()) / dpr),
            int((rect.y() - virtual_offset.y()) / dpr),
            int(rect.width() / dpr),
            int(rect.height() / dpr),
        )
        self._cached_hwnd = hwnd
        self._cached_rect = logical
        return logical

    def _hit_test(self, physical_pos: tuple[int, int]) -> int | None:
        point = _POINT(physical_pos[0], physical_pos[1])
        hwnd = _win.WindowFromPoint(point)
        if hwnd == 0:
            return None

        root = _win.GetAncestor(hwnd, GA_ROOT)
        if root == 0:
            root = hwnd

        if root == self._overlay_hwnd:
            return None

        if not _win.IsWindowVisible(root):
            return None

        return root

    def _get_control_rect(
        self, hwnd: int, physical_pos: tuple[int, int]
    ) -> QRect | None:
        raise NotImplementedError

    def _get_window_rect(self, hwnd: int) -> QRect | None:
        raise NotImplementedError
