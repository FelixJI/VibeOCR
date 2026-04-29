"""桌面边缘工具栏

可拖拽到屏幕边缘并自动隐藏的浮动工具栏。
"""

from __future__ import annotations

import logging
from enum import Enum, auto

from PySide6.QtCore import (
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

logger = logging.getLogger(__name__)

# 靠边检测阈值（像素）
_EDGE_THRESHOLD = 20
# 隐藏后露出的像素宽度
_VISIBLE_STRIP = 3
# 动画持续时间（毫秒）
_ANIM_DURATION = 200


class EdgeSide(Enum):
    """工具栏停靠的屏幕边"""

    NONE = auto()
    TOP = auto()
    LEFT = auto()
    RIGHT = auto()


class EdgeToolbar(QWidget):
    """桌面边缘工具栏

    特性：
      - 无边框、置顶浮动窗口
      - 可拖拽移动
      - 靠近屏幕边缘自动隐藏（可配置延迟）
      - 鼠标移到边缘时平滑显示

    Signals:
        screenshot_requested: 截图按钮点击
        show_main_requested: 显示主窗口按钮点击
    """

    screenshot_requested = Signal()
    show_main_requested = Signal()
    position_changed = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_pos: QPoint | None = None
        self._dragging = False
        self._docked_side: EdgeSide = EdgeSide.NONE
        self._is_hidden = False
        self._auto_hide_enabled = False
        self._hide_delay_ms = 500

        # 隐藏延迟定时器
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._slide_hide)

        # 鼠标检测定时器（检测鼠标是否离开）
        self._mouse_check_timer = QTimer(self)
        self._mouse_check_timer.setInterval(100)
        self._mouse_check_timer.timeout.connect(self._check_mouse_position)

        # 动画
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(_ANIM_DURATION)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """初始化工具栏 UI"""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self.setStyleSheet("""
            EdgeToolbar {
                background-color: #2b2b2b;
                border: 1px solid #555;
                border-radius: 6px;
            }
            QPushButton {
                color: #ddd;
                background-color: transparent;
                border: none;
                padding: 6px 10px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #444;
                border-radius: 4px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        # 截图按钮
        btn_screenshot = QPushButton("截图")
        btn_screenshot.setToolTip("截图识别 (Ctrl+S)")
        btn_screenshot.clicked.connect(self.screenshot_requested.emit)
        layout.addWidget(btn_screenshot)

        # 显示主窗口按钮
        btn_main = QPushButton("主窗口")
        btn_main.setToolTip("显示主窗口")
        btn_main.clicked.connect(self.show_main_requested.emit)
        layout.addWidget(btn_main)

        self.setFixedHeight(36)
        self.setMinimumWidth(120)
        self.adjustSize()

    # ============================================================
    # 公共接口
    # ============================================================

    def set_auto_hide(self, enabled: bool) -> None:
        """启用/禁用自动隐藏"""
        self._auto_hide_enabled = enabled
        if not enabled:
            self._hide_timer.stop()
            self._mouse_check_timer.stop()
            if self._is_hidden:
                self._slide_show()
        else:
            # 如果当前已靠边，启动隐藏检测
            if self._docked_side != EdgeSide.NONE:
                self._start_hide_countdown()

    def set_hide_delay(self, delay_ms: int) -> None:
        """设置隐藏延迟（毫秒）"""
        self._hide_delay_ms = max(100, min(5000, delay_ms))

    def set_initial_position(self) -> None:
        """将工具栏定位到主屏幕顶部居中"""
        screen = QApplication.primaryScreen()
        if not screen:
            return
        screen_geo = screen.availableGeometry()
        x = screen_geo.center().x() - self.width() // 2
        self.move(x, screen_geo.top())

    # ============================================================
    # 拖拽逻辑
    # ============================================================

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()
            self._dragging = True
            if self._is_hidden:
                self._slide_show()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self._dragging = False
                self._detect_edge()
                self.position_changed.emit(self.pos())
        super().mouseReleaseEvent(event)

    # ============================================================
    # 边缘检测与自动隐藏
    # ============================================================

    def _detect_edge(self) -> None:
        """检测工具栏是否靠近屏幕边缘"""
        screen = QApplication.screenAt(self.geometry().center())
        if not screen:
            screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()

        pos = self.pos()
        geo = self.geometry()

        if pos.y() - screen_geo.top() <= _EDGE_THRESHOLD:
            self._docked_side = EdgeSide.TOP
            # 吸附到顶部边缘
            self.move(pos.x(), screen_geo.top())
        elif pos.x() - screen_geo.left() <= _EDGE_THRESHOLD:
            self._docked_side = EdgeSide.LEFT
            self.move(screen_geo.left(), pos.y())
        elif screen_geo.right() - geo.right() <= _EDGE_THRESHOLD:
            self._docked_side = EdgeSide.RIGHT
            self.move(screen_geo.right() - self.width(), pos.y())
        else:
            self._docked_side = EdgeSide.NONE

        if self._docked_side != EdgeSide.NONE:
            logger.debug(f"工具栏停靠于 {self._docked_side.name}")
            if self._auto_hide_enabled:
                self._start_hide_countdown()
        else:
            self._hide_timer.stop()
            self._mouse_check_timer.stop()

    def _start_hide_countdown(self) -> None:
        """启动隐藏倒计时"""
        self._hide_timer.start(self._hide_delay_ms)

    def _slide_hide(self) -> None:
        """将工具栏滑出屏幕边缘，仅露出几个像素"""
        if self._is_hidden or self._docked_side == EdgeSide.NONE:
            return

        screen = QApplication.screenAt(self.geometry().center())
        if not screen:
            screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()
        geo = self.geometry()

        target = QRect(geo)
        if self._docked_side == EdgeSide.TOP:
            target.moveTop(screen_geo.top() - geo.height() + _VISIBLE_STRIP)
        elif self._docked_side == EdgeSide.LEFT:
            target.moveLeft(screen_geo.left() - geo.width() + _VISIBLE_STRIP)
        elif self._docked_side == EdgeSide.RIGHT:
            target.moveLeft(screen_geo.right() - _VISIBLE_STRIP)

        self._anim.stop()
        self._anim.setStartValue(geo)
        self._anim.setEndValue(target)
        self._anim.start()
        self._is_hidden = True

        # 启动鼠标位置检查
        self._mouse_check_timer.start()
        logger.debug("工具栏已隐藏")

    def _slide_show(self) -> None:
        """将工具栏从屏幕边缘滑入恢复显示"""
        if not self._is_hidden or self._docked_side == EdgeSide.NONE:
            return

        screen = QApplication.screenAt(self.geometry().center())
        if not screen:
            screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry()
        geo = self.geometry()

        target = QRect(geo)
        if self._docked_side == EdgeSide.TOP:
            target.moveTop(screen_geo.top())
        elif self._docked_side == EdgeSide.LEFT:
            target.moveLeft(screen_geo.left())
        elif self._docked_side == EdgeSide.RIGHT:
            target.moveLeft(screen_geo.right() - self.width())

        self._anim.stop()
        self._anim.setStartValue(geo)
        self._anim.setEndValue(target)
        self._anim.start()
        self._is_hidden = False
        self._mouse_check_timer.stop()
        logger.debug("工具栏已显示")

    def _check_mouse_position(self) -> None:
        """定期检查鼠标位置，在鼠标靠近边缘时显示工具栏"""
        cursor_pos = QCursor.pos()
        geo = self.geometry()

        # 扩大检测区域（包含工具栏完全展开后的区域 + 一些余量）
        detect_rect = geo.adjusted(-10, -10, 10, 10)

        if self._is_hidden:
            # 隐藏状态：检测鼠标是否在工具栏附近（边缘检测区）
            if detect_rect.contains(cursor_pos):
                self._slide_show()
        else:
            # 显示状态：如果鼠标离开，重新启动隐藏倒计时
            expanded_rect = geo.adjusted(-30, -30, 30, 30)
            if not expanded_rect.contains(cursor_pos):
                if self._auto_hide_enabled and self._docked_side != EdgeSide.NONE:
                    self._start_hide_countdown()

    def enterEvent(self, event) -> None:
        """鼠标进入时显示工具栏"""
        if self._is_hidden:
            self._slide_show()
        self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """鼠标离开时启动隐藏倒计时"""
        if self._auto_hide_enabled and self._docked_side != EdgeSide.NONE:
            self._start_hide_countdown()
        super().leaveEvent(event)
