"""Screenshot overlay widget"""

from typing import Optional

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRect, Signal, QPoint
from PySide6.QtGui import (
    QPainter,
    QColor,
    QPen,
    QPixmap,
    QGuiApplication,
)


class ScreenshotWidget(QWidget):
    """全屏截图遮罩组件（支持多屏幕）"""

    captured = Signal(QPixmap)  # 截图完成信号

    # 最小选区尺寸
    MIN_SELECTION_SIZE = 5

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._start_pos = None
        self._end_pos = None
        self._selection_rect: Optional[QRect] = None
        self._screen_pixmap: Optional[QPixmap] = None
        self._virtual_geometry = QRect()

    def start_capture(self) -> None:
        """开始截图（支持多屏幕和高DPI）"""
        screens = QGuiApplication.screens()
        if not screens:
            return

        # 计算虚拟桌面几何（包含所有屏幕）- 使用逻辑像素
        self._virtual_geometry = screens[0].geometry()
        for screen in screens[1:]:
            self._virtual_geometry = self._virtual_geometry.united(screen.geometry())

        # 设置窗口大小为虚拟桌面大小（逻辑像素）
        self.setGeometry(self._virtual_geometry)

        # 获取最高的设备像素比（用于高DPI支持）
        max_dpr = max(screen.devicePixelRatio() for screen in screens)

        # 创建合并所有屏幕的截图 - 使用物理像素尺寸
        physical_size = self._virtual_geometry.size() * max_dpr
        pixmap = QPixmap(physical_size)
        if pixmap.isNull():
            return

        pixmap.fill(Qt.GlobalColor.black)
        # 设置设备像素比，确保后续坐标计算正确
        pixmap.setDevicePixelRatio(max_dpr)

        painter = QPainter(pixmap)
        for screen in screens:
            # 计算屏幕在虚拟桌面中的相对位置（逻辑像素）
            screen_geometry = screen.geometry()
            offset = screen_geometry.topLeft() - self._virtual_geometry.topLeft()

            # 抓取屏幕内容（返回物理像素 pixmap，已设置 devicePixelRatio）
            screen_grab = screen.grabWindow(0)
            painter.drawPixmap(offset, screen_grab)
        painter.end()

        self._screen_pixmap = pixmap
        self.show()
        self.activateWindow()
        self.grabMouse()

    def paintEvent(self, _event) -> None:
        """绘制遮罩和选区"""
        painter = QPainter(self)

        # 绘制半透明遮罩
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))

        if self._selection_rect:
            # 清除选区内的遮罩，显示原始截图
            if self._screen_pixmap:
                painter.drawPixmap(
                    self._selection_rect,
                    self._screen_pixmap,
                    self._selection_rect,
                )

            # 绘制选区边框
            pen = QPen(QColor(0, 120, 215), 2)
            painter.setPen(pen)
            painter.drawRect(self._selection_rect)

            # 绘制尺寸信息
            size_text = f"{self._selection_rect.width()} x {self._selection_rect.height()}"
            painter.drawText(self._selection_rect.topLeft() + QPoint(5, -5), size_text)

    def mousePressEvent(self, event) -> None:
        """鼠标按下开始选择"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_pos = event.pos()
            self._selection_rect = QRect(self._start_pos, self._start_pos)
            self.update()

    def mouseMoveEvent(self, event) -> None:
        """鼠标移动更新选区"""
        if self._start_pos:
            self._end_pos = event.pos()
            self._selection_rect = QRect(self._start_pos, self._end_pos).normalized()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        """鼠标释放完成选择"""
        if event.button() == Qt.MouseButton.LeftButton and self._selection_rect:
            self.releaseMouse()
            # 检查选区是否足够大（宽度和高度都要检查）
            if (
                self._screen_pixmap
                and self._selection_rect.width() > self.MIN_SELECTION_SIZE
                and self._selection_rect.height() > self.MIN_SELECTION_SIZE
            ):
                captured = self._screen_pixmap.copy(self._selection_rect)
                self.captured.emit(captured)
            self._reset()
            self.hide()

    def keyPressEvent(self, event) -> None:
        """ESC 取消截图"""
        if event.key() == Qt.Key.Key_Escape:
            self.releaseMouse()
            self._reset()
            self.hide()

    def _reset(self) -> None:
        """重置状态"""
        self._start_pos = None
        self._end_pos = None
        self._selection_rect = None
        self._screen_pixmap = None
        self._virtual_geometry = QRect()
        self.update()
