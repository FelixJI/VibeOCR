"""Screenshot overlay widget with magnifier and pixel info"""

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QRegion,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from vibeocr.widgets.magnifier_overlay import MagnifierOverlay


class ScreenshotWidget(QWidget):
    """全屏截图遮罩组件（支持多屏幕）

    增强功能：放大镜、像素颜色值、坐标显示
    """

    selection_done = Signal(QPixmap, QRect)  # 框选完成信号（图片, 选区矩形）

    # 最小选区尺寸
    MIN_SELECTION_SIZE = 5

    # 放大倍数选项
    ZOOM_LEVELS = [2, 4, 8]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # 设置背景透明 - 避免白色闪烁的关键配置
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 不设置 WA_OpaquePaintEvent，允许透明背景
        # 设置无边框窗口的背景模式为无背景
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        # 设置窗口背景为透明色
        self.setStyleSheet("background: transparent;")

        self._start_pos: QPoint | None = None
        self._end_pos: QPoint | None = None
        self._selection_rect: QRect | None = None
        self._screen_pixmap: QPixmap | None = None
        self._screen_image: QImage | None = None  # 用于像素颜色提取
        self._virtual_geometry = QRect()
        self._device_pixel_ratio = 1.0

        # 放大镜相关
        self._current_mouse_pos: QPoint | None = None
        self._magnifier_zoom: int = 4  # 默认4倍
        self._zoom_index: int = 1  # ZOOM_LEVELS 中的索引

    def start_capture(self) -> None:
        """开始截图（支持多屏幕和高DPI）"""
        screens = QGuiApplication.screens()
        if not screens:
            return

        # 计算虚拟桌面几何（包含所有屏幕）- 使用逻辑像素
        self._virtual_geometry = screens[0].geometry()
        for screen in screens[1:]:
            self._virtual_geometry = self._virtual_geometry.united(screen.geometry())

        # 获取最高的设备像素比（用于高DPI支持）
        self._device_pixel_ratio = max(screen.devicePixelRatio() for screen in screens)

        # 创建合并所有屏幕的截图 - 使用物理像素尺寸
        physical_size = self._virtual_geometry.size() * self._device_pixel_ratio
        pixmap = QPixmap(physical_size)
        if pixmap.isNull():
            return

        pixmap.fill(Qt.GlobalColor.black)
        # 设置设备像素比，确保后续坐标计算正确
        pixmap.setDevicePixelRatio(self._device_pixel_ratio)

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
        # 缓存 QImage 用于高效的像素颜色提取
        self._screen_image = pixmap.toImage()

        # 设置窗口大小为虚拟桌面大小（逻辑像素）
        self.setGeometry(self._virtual_geometry)

        # 启用鼠标追踪（无需按下按键即可接收 mouseMoveEvent）
        self.setMouseTracking(True)

        # 先显示窗口但保持透明，然后立即重绘
        self.show()
        self.activateWindow()
        self.grabMouse()
        # 强制立即重绘，确保截图内容立即可见
        self.repaint()

    def paintEvent(self, _event) -> None:
        """绘制遮罩、选区、放大镜和像素信息"""
        painter = QPainter(self)

        # 1. 先绘制整个截图作为背景
        if self._screen_pixmap:
            # 计算绘制位置（考虑虚拟桌面偏移）
            offset = self._virtual_geometry.topLeft()
            painter.drawPixmap(offset, self._screen_pixmap)

        # 2. 创建遮罩区域（减去选区，形成镂空效果）
        mask_region = QRegion(self.rect())
        if self._selection_rect:
            mask_region = mask_region.subtracted(QRegion(self._selection_rect))

        # 3. 只在非选区绘制半透明遮罩（约67%不透明度，参考主流截图软件）
        painter.setClipRegion(mask_region)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 170))

        # 4. 绘制选区边框和尺寸信息（禁用裁剪）
        if self._selection_rect:
            painter.setClipping(False)

            # 绘制选区边框
            pen = QPen(QColor(0, 120, 215), 2)
            painter.setPen(pen)
            painter.drawRect(self._selection_rect)

            # 绘制尺寸信息
            size_text = (
                f"{self._selection_rect.width()} x {self._selection_rect.height()}"
            )
            painter.drawText(self._selection_rect.topLeft() + QPoint(5, -5), size_text)
        else:
            painter.setClipping(False)

        # 5. 绘制放大镜和像素信息
        if self._screen_pixmap and self._current_mouse_pos is not None:
            mag_rect = MagnifierOverlay.draw_magnifier(
                painter,
                self._current_mouse_pos,
                self._screen_pixmap,
                self._virtual_geometry,
                self._magnifier_zoom,
                self._device_pixel_ratio,
                self.rect(),
            )
            MagnifierOverlay.draw_pixel_info(
                painter,
                self._current_mouse_pos,
                self._screen_image,
                self._selection_rect,
                self._virtual_geometry,
                self._device_pixel_ratio,
                mag_rect,
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """鼠标按下开始选择"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_pos = event.pos()
            start_pos = self._start_pos
            self._selection_rect = QRect(start_pos, start_pos)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """鼠标移动更新选区和放大镜"""
        self._current_mouse_pos = event.pos()
        if self._start_pos:
            self._end_pos = event.pos()
            start_pos = self._start_pos
            end_pos = self._end_pos
            self._selection_rect = QRect(start_pos, end_pos).normalized()
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
                # 计算源矩形（考虑虚拟桌面偏移和设备像素比）
                # _selection_rect 是相对于窗口的坐标（逻辑像素）
                # _screen_pixmap 是从虚拟桌面原点开始的物理像素图像
                vg_offset = self._virtual_geometry.topLeft()
                src_x = int(
                    (self._selection_rect.x() + vg_offset.x())
                    * self._device_pixel_ratio
                )
                src_y = int(
                    (self._selection_rect.y() + vg_offset.y())
                    * self._device_pixel_ratio
                )
                src_w = int(self._selection_rect.width() * self._device_pixel_ratio)
                src_h = int(self._selection_rect.height() * self._device_pixel_ratio)
                src_rect = QRect(src_x, src_y, src_w, src_h)

                captured = self._screen_pixmap.copy(src_rect)
                # 复位 devicePixelRatio 为 1.0，确保后续使用时坐标一致
                captured.setDevicePixelRatio(1.0)
                # 发射新的 selection_done 信号（传递图片和选区矩形）
                self.selection_done.emit(captured, self._selection_rect)
                # 不在此处 reset/hide，由外部调用 finish_capture() 控制
                return
            # 选区太小，直接重置
            self._reset()
            self.hide()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """滚轮切换放大倍数"""
        if event.angleDelta().y() > 0:
            self._zoom_index = (self._zoom_index + 1) % len(self.ZOOM_LEVELS)
        else:
            self._zoom_index = (self._zoom_index - 1) % len(self.ZOOM_LEVELS)
        self._magnifier_zoom = self.ZOOM_LEVELS[self._zoom_index]
        self.update()

    def keyPressEvent(self, event) -> None:
        """ESC 取消截图"""
        if event.key() == Qt.Key.Key_Escape:
            self.releaseMouse()
            self._reset()
            self.hide()

    def finish_capture(self) -> None:
        """完成截图（供外部调用，重置并隐藏）"""
        self._reset()
        self.hide()

    def _reset(self) -> None:
        """重置状态"""
        self._start_pos = None
        self._end_pos = None
        self._selection_rect = None
        self._screen_pixmap = None
        self._screen_image = None
        self._virtual_geometry = QRect()
        self._device_pixel_ratio = 1.0
        self._current_mouse_pos = None
        self.update()
