"""选区边界拖拽手柄框架

在 EDITING 阶段覆盖在画布周围的控件，提供 8 个拖拽手柄
用于调整选区大小和位置。
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPaintEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget


class HandlePosition(Enum):
    NONE = "none"
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"
    TOP = "top"
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"
    MOVE = "move"


# 手柄尺寸（半边长，即检测半径）
_HANDLE_HALF = 5


def _handle_positions(rect: QRect) -> dict[HandlePosition, QPoint]:
    """返回 8 个手柄的中心坐标"""
    return {
        HandlePosition.TOP_LEFT: rect.topLeft(),
        HandlePosition.TOP_RIGHT: rect.topRight(),
        HandlePosition.BOTTOM_LEFT: rect.bottomLeft(),
        HandlePosition.BOTTOM_RIGHT: rect.bottomRight(),
        HandlePosition.TOP: QPoint(rect.x() + rect.width() // 2, rect.top()),
        HandlePosition.BOTTOM: QPoint(rect.x() + rect.width() // 2, rect.bottom()),
        HandlePosition.LEFT: QPoint(rect.left(), rect.y() + rect.height() // 2),
        HandlePosition.RIGHT: QPoint(rect.right(), rect.y() + rect.height() // 2),
    }


def _hit_test(pos: QPoint, rect: QRect) -> HandlePosition:
    """检测 pos 命中了哪个手柄"""
    handles = _handle_positions(rect)
    for hp, center in handles.items():
        if abs(pos.x() - center.x()) <= _HANDLE_HALF and abs(pos.y() - center.y()) <= _HANDLE_HALF:
            return hp
    return HandlePosition.NONE


def _cursor_for_handle(handle: HandlePosition) -> Qt.CursorShape:
    """返回手柄对应的光标形状"""
    mapping = {
        HandlePosition.TOP_LEFT: Qt.CursorShape.SizeFDiagCursor,
        HandlePosition.BOTTOM_RIGHT: Qt.CursorShape.SizeFDiagCursor,
        HandlePosition.TOP_RIGHT: Qt.CursorShape.SizeBDiagCursor,
        HandlePosition.BOTTOM_LEFT: Qt.CursorShape.SizeBDiagCursor,
        HandlePosition.TOP: Qt.CursorShape.SizeVerCursor,
        HandlePosition.BOTTOM: Qt.CursorShape.SizeVerCursor,
        HandlePosition.LEFT: Qt.CursorShape.SizeHorCursor,
        HandlePosition.RIGHT: Qt.CursorShape.SizeHorCursor,
        HandlePosition.MOVE: Qt.CursorShape.SizeAllCursor,
        HandlePosition.NONE: Qt.CursorShape.ArrowCursor,
    }
    return mapping.get(handle, Qt.CursorShape.ArrowCursor)


def _apply_resize(
    original: QRect, handle: HandlePosition, delta: QPoint
) -> QRect:
    """根据手柄位置和鼠标 delta 计算新矩形"""
    r = QRect(original)
    if handle == HandlePosition.MOVE:
        r.translate(delta)
    elif handle == HandlePosition.TOP_LEFT:
        r.setTopLeft(r.topLeft() + delta)
    elif handle == HandlePosition.TOP_RIGHT:
        r.setTopRight(r.topRight() + delta)
    elif handle == HandlePosition.BOTTOM_LEFT:
        r.setBottomLeft(r.bottomLeft() + delta)
    elif handle == HandlePosition.BOTTOM_RIGHT:
        r.setBottomRight(r.bottomRight() + delta)
    elif handle == HandlePosition.TOP:
        r.setTop(r.top() + delta.y())
    elif handle == HandlePosition.BOTTOM:
        r.setBottom(r.bottom() + delta.y())
    elif handle == HandlePosition.LEFT:
        r.setLeft(r.left() + delta.x())
    elif handle == HandlePosition.RIGHT:
        r.setRight(r.right() + delta.x())
    return r.normalized()


def _constrain_rect(rect: QRect, bounds: QRect, min_size: int) -> QRect:
    """约束矩形在边界内并保证最小尺寸"""
    r = QRect(rect)

    # 确保最小尺寸
    if r.width() < min_size:
        if r.left() == rect.left():
            r.setWidth(min_size)
        else:
            r.setLeft(r.right() - min_size)
    if r.height() < min_size:
        if r.top() == rect.top():
            r.setHeight(min_size)
        else:
            r.setTop(r.bottom() - min_size)

    # 约束到边界
    if r.left() < bounds.left():
        r.setLeft(bounds.left())
    if r.top() < bounds.top():
        r.setTop(bounds.top())
    if r.right() > bounds.right():
        r.setRight(bounds.right())
    if r.bottom() > bounds.bottom():
        r.setBottom(bounds.bottom())

    return r.normalized()


class SelectionResizeFrame(QWidget):
    """选区边界拖拽手柄框架"""

    selection_changed = Signal(QRect)
    selection_finalized = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        virtual_geometry: QRect | None = None,
        min_size: int = 50,
    ) -> None:
        super().__init__(parent)
        self._virtual_geometry = virtual_geometry or QRect()
        self._min_size = min_size
        self._active_handle = HandlePosition.NONE
        self._drag_start_pos: QPoint | None = None
        self._drag_start_rect: QRect | None = None
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        rect = self.rect()

        # 边框：白色半透明虚线
        pen = QPen(QColor(255, 255, 255, 180), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        # 8 个手柄
        handles = _handle_positions(rect)
        handle_pen = QPen(QColor(255, 255, 255), 1)
        handle_brush = QColor(0, 120, 215)
        for pos in handles.values():
            painter.setPen(handle_pen)
            painter.setBrush(handle_brush)
            painter.drawRect(
                pos.x() - _HANDLE_HALF,
                pos.y() - _HANDLE_HALF,
                _HANDLE_HALF * 2,
                _HANDLE_HALF * 2,
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        pos = event.pos()
        handle = _hit_test(pos, self.rect())
        if handle != HandlePosition.NONE:
            self._active_handle = handle
        elif self.rect().contains(pos):
            self._active_handle = HandlePosition.MOVE
        else:
            self._active_handle = HandlePosition.NONE
            super().mousePressEvent(event)
            return

        self._drag_start_pos = pos
        self._drag_start_rect = QRect(self.geometry())
        self.setCursor(_cursor_for_handle(self._active_handle))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._active_handle == HandlePosition.NONE:
            # 悬浮时更新光标
            handle = _hit_test(event.pos(), self.rect())
            if handle == HandlePosition.NONE and self.rect().contains(event.pos()):
                handle = HandlePosition.MOVE
            self.setCursor(_cursor_for_handle(handle))
            super().mouseMoveEvent(event)
            return

        if not self._drag_start_pos or not self._drag_start_rect:
            return

        delta = event.pos() - self._drag_start_pos
        new_rect = _apply_resize(self._drag_start_rect, self._active_handle, delta)
        new_rect = _constrain_rect(new_rect, self._virtual_geometry, self._min_size)
        self.selection_changed.emit(new_rect)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._active_handle != HandlePosition.NONE:
            self._active_handle = HandlePosition.NONE
            self._drag_start_pos = None
            self._drag_start_rect = None
            self.selection_finalized.emit()
            return
        super().mouseReleaseEvent(event)

    def sync_geometry(self, rect: QRect) -> None:
        """外部调用：将 frame 的几何同步到新选区"""
        self.setGeometry(rect)
        self.update()
