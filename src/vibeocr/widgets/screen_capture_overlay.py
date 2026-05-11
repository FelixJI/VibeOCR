"""ScreenCaptureOverlay — 统一的截图+编辑覆盖层

替代原有的 ScreenshotWidget + ScreenshotEditWindow 双窗口流程，
使用状态机管理 CAPTURING → EDITING 两个阶段。

状态机:
  CAPTURING: 全屏透明覆盖层，截图捕获，选区绘制，放大镜
  EDITING:   内联画布 + 工具栏 + 识别面板
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, Signal
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
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QWidget,
)

from vibeocr.core.inline_styles import InlineStyles
from vibeocr.widgets.inline_edit_canvas import InlineEditCanvas
from vibeocr.widgets.inline_recognition_panel import InlineRecognitionPanel
from vibeocr.widgets.inline_toolbar import InlineToolbar
from vibeocr.widgets.magnifier_overlay import MagnifierOverlay
from vibeocr.widgets.selection_resize_frame import SelectionResizeFrame


class ScreenCaptureOverlay(QWidget):
    """统一的截图+编辑覆盖层

    Signals:
        confirmed(QPixmap, object): 确认识别，传递截图和 OCROptions
        copied(QPixmap): 复制到剪贴板
        saved(str): 另存为文件路径
        cancelled(): 取消
    """

    confirmed = Signal(QPixmap, object)
    copied = Signal(QPixmap)
    saved = Signal(str)
    cancelled = Signal()

    # 最小选区尺寸
    MIN_SELECTION_SIZE = 5

    # 放大倍数选项
    ZOOM_LEVELS = [2, 4, 8]

    # 面板定位阈值
    _PANEL_MIN_WIDTH = 200
    _TOOLBAR_MIN_HEIGHT = 48

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # 窗口标志：无边框、置顶、工具窗口
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setStyleSheet("background: transparent;")

        # 状态
        self._state: str = "CAPTURING"

        # 截图相关
        self._start_pos: QPoint | None = None
        self._end_pos: QPoint | None = None
        self._selection_rect: QRect | None = None
        self._screen_pixmap: QPixmap | None = None
        self._screen_image: QImage | None = None
        self._virtual_geometry = QRect()
        self._device_pixel_ratio: float = 1.0

        # 放大镜相关
        self._current_mouse_pos: QPoint | None = None
        self._magnifier_zoom: int = 4
        self._zoom_index: int = 1

        # EDITING 模式子组件
        self._canvas: InlineEditCanvas | None = None
        self._toolbar: InlineToolbar | None = None
        self._recognition_panel: InlineRecognitionPanel | None = None
        self._captured_pixmap: QPixmap | None = None
        self._resize_frame: SelectionResizeFrame | None = None

    # ==================== CAPTURING 模式 ====================

    def start_capture(self) -> None:
        """开始截图（支持多屏幕和高DPI）"""
        screens = QGuiApplication.screens()
        if not screens:
            return

        # 计算虚拟桌面几何
        self._virtual_geometry = screens[0].geometry()
        for screen in screens[1:]:
            self._virtual_geometry = self._virtual_geometry.united(screen.geometry())

        # 获取最高的设备像素比
        self._device_pixel_ratio = max(
            screen.devicePixelRatio() for screen in screens
        )

        # 创建合并所有屏幕的截图
        physical_size = self._virtual_geometry.size() * self._device_pixel_ratio
        pixmap = QPixmap(physical_size)
        if pixmap.isNull():
            return

        pixmap.fill(Qt.GlobalColor.black)
        pixmap.setDevicePixelRatio(self._device_pixel_ratio)

        painter = QPainter(pixmap)
        for screen in screens:
            screen_geometry = screen.geometry()
            offset = screen_geometry.topLeft() - self._virtual_geometry.topLeft()
            screen_grab = screen.grabWindow(0)
            painter.drawPixmap(offset, screen_grab)
        painter.end()

        self._screen_pixmap = pixmap
        self._screen_image = pixmap.toImage()

        # 设置窗口大小为虚拟桌面大小
        self.setGeometry(self._virtual_geometry)
        self.setMouseTracking(True)

        self.show()
        self.activateWindow()
        self.grabMouse()
        self.repaint()

    def paintEvent(self, _event) -> None:
        """绘制冻结截图背景、遮罩（CAPTURING/EDITING 共用）和放大镜"""
        if not self._screen_pixmap:
            return

        painter = QPainter(self)

        # 1. 绘制冻结截图背景
        painter.drawPixmap(QPoint(0, 0), self._screen_pixmap)

        # 2. 创建遮罩（减去选区，镂空效果）
        mask_region = QRegion(self.rect())
        if self._selection_rect:
            mask_region = mask_region.subtracted(QRegion(self._selection_rect))

        # 3. 非选区绘制半透明遮罩
        painter.setClipRegion(mask_region)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 170))
        painter.setClipping(False)

        if self._state != "CAPTURING":
            return

        # --- 以下仅 CAPTURING 模式 ---

        # 4. 绘制选区边框和尺寸
        if self._selection_rect:
            pen = QPen(QColor(0, 120, 215), 2)
            painter.setPen(pen)
            painter.drawRect(self._selection_rect)

            size_text = (
                f"{self._selection_rect.width()} x {self._selection_rect.height()}"
            )
            painter.drawText(
                self._selection_rect.topLeft() + QPoint(5, -5), size_text
            )

        # 5. 放大镜和像素信息
        if self._current_mouse_pos is not None:
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
        """CAPTURING: 鼠标按下开始选区"""
        if self._state != "CAPTURING":
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_pos = event.pos()
            self._selection_rect = QRect(self._start_pos, self._start_pos)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """CAPTURING: 鼠标移动更新选区和放大镜"""
        if self._state != "CAPTURING":
            return
        self._current_mouse_pos = event.pos()
        if self._start_pos:
            self._end_pos = event.pos()
            self._selection_rect = QRect(self._start_pos, self._end_pos).normalized()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """CAPTURING: 鼠标释放完成选区，进入 EDITING 模式"""
        if self._state != "CAPTURING":
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._selection_rect
        ):
            self.releaseMouse()
            if (
                self._screen_pixmap
                and self._selection_rect.width() > self.MIN_SELECTION_SIZE
                and self._selection_rect.height() > self.MIN_SELECTION_SIZE
            ):
                # QPixmap.copy() 操作物理像素，需将逻辑坐标转换为物理坐标
                # QGraphicsPixmapItem.boundingRect() 会自动除以 DPR，
                # 所以 captured pixmap 的逻辑尺寸等于 sel 尺寸
                sel = self._selection_rect
                dpr = self._device_pixel_ratio
                physical_rect = QRect(
                    int(sel.x() * dpr),
                    int(sel.y() * dpr),
                    int(sel.width() * dpr),
                    int(sel.height() * dpr),
                )
                captured = self._screen_pixmap.copy(physical_rect)
                self._captured_pixmap = captured

                # 进入 EDITING 模式
                self._enter_editing()
                return

            # 选区太小，重置
            self._reset_capturing()
            self.hide()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """CAPTURING: 滚轮切换放大倍数"""
        if self._state != "CAPTURING":
            return
        if event.angleDelta().y() > 0:
            self._zoom_index = (self._zoom_index + 1) % len(self.ZOOM_LEVELS)
        else:
            self._zoom_index = (self._zoom_index - 1) % len(self.ZOOM_LEVELS)
        self._magnifier_zoom = self.ZOOM_LEVELS[self._zoom_index]
        self.update()

    def keyPressEvent(self, event) -> None:
        """ESC 取消"""
        if event.key() == Qt.Key.Key_Escape:
            if self._state == "CAPTURING":
                self.releaseMouse()
            self._do_cancel()

    # ==================== EDITING 模式 ====================

    def _enter_editing(self) -> None:
        """进入 EDITING 模式，创建子组件"""
        self._state = "EDITING"
        self.setMouseTracking(False)

        if not self._captured_pixmap:
            return

        # 创建画布
        self._canvas = InlineEditCanvas(self)
        self._canvas.set_background(
            self._captured_pixmap,
            QPointF(self._selection_rect.x(), self._selection_rect.y()),
        )

        # 创建工具栏
        self._toolbar = InlineToolbar(self)

        # 创建识别面板
        self._recognition_panel = InlineRecognitionPanel(self)

        # 创建 resize 框架
        self._resize_frame = SelectionResizeFrame(
            self,
            virtual_geometry=self._virtual_geometry,
            min_size=self.MIN_SELECTION_SIZE,
        )
        self._resize_frame.selection_changed.connect(self._on_selection_changed)
        self._resize_frame.selection_finalized.connect(self._on_selection_finalized)

        # 定位子组件

        # 定位子组件
        self._position_editing_widgets()

        # 连接信号
        self._connect_editing_signals()

        # 显示子组件
        self._canvas.show()
        self._toolbar.show()
        self._recognition_panel.show()
        self._resize_frame.set_initial_selection(self._selection_rect)
        self._resize_frame.show()
        self._toolbar.raise_()
        self._recognition_panel.raise_()

        # 重绘覆盖层（EDITING 模式下 paintEvent 不绘制）
        self.update()

    def _position_editing_widgets(self) -> None:
        """定位 EDITING 模式的子组件"""
        if (
            not self._selection_rect
            or not self._canvas
            or not self._toolbar
            or not self._recognition_panel
        ):
            return

        sel = self._selection_rect

        # 画布定位在选区位置
        self._canvas.setGeometry(sel)

        # 工具栏几何
        toolbar_geo = self._calc_toolbar_geometry(sel)
        self._toolbar.setGeometry(toolbar_geo)

        # 识别面板几何
        panel_geo = self._calc_recognition_panel_geometry(sel)
        self._recognition_panel.setGeometry(panel_geo)
        self._recognition_panel.setFixedWidth(panel_geo.width())

    def _connect_editing_signals(self) -> None:
        """连接工具栏信号"""
        if not self._toolbar or not self._canvas:
            return

        # 工具切换
        self._toolbar.tool_changed.connect(self._canvas.set_tool)
        self._toolbar.tool_changed.connect(lambda _: self._reposition_toolbar())

        # 属性变更
        props = self._toolbar.properties_bar
        props.color_changed.connect(self._canvas.set_pen_color)
        props.line_width_changed.connect(self._canvas.set_pen_width)
        props.fill_enabled_changed.connect(self._canvas.set_fill_enabled)
        props.font_changed.connect(self._canvas.set_font)
        props.font_size_changed.connect(self._canvas.set_font_size)
        props.bold_changed.connect(self._canvas.set_bold)
        props.italic_changed.connect(self._canvas.set_italic)
        props.mosaic_strength_changed.connect(self._canvas.set_mosaic_strength)
        props.blur_radius_changed.connect(self._canvas.set_blur_radius)

        # 撤销/重做
        self._toolbar.undo_requested.connect(self._canvas.undo_stack.undo)
        self._toolbar.redo_requested.connect(self._canvas.undo_stack.redo)
        self._canvas.undo_stack.canUndoChanged.connect(
            self._toolbar.set_undo_enabled
        )
        self._canvas.undo_stack.canRedoChanged.connect(
            self._toolbar.set_redo_enabled
        )

        # 操作按钮
        self._toolbar.copy_requested.connect(self._on_copy)
        self._toolbar.save_requested.connect(self._on_save)
        self._toolbar.cancel_requested.connect(self._do_cancel)

        # 识别面板
        if self._recognition_panel:
            self._recognition_panel.recognize_requested.connect(self._on_confirm)

    def _on_selection_changed(self, new_rect: QRect) -> None:
        """选区 resize/move 过程中持续更新"""
        if not self._canvas or not self._screen_pixmap:
            return

        # 批量更新：禁止中间状态重绘，避免波纹
        self.setUpdatesEnabled(False)
        try:
            self._selection_rect = new_rect

            self._canvas.update_crop_region(
                self._screen_pixmap, new_rect, self._device_pixel_ratio
            )

            self._canvas.setGeometry(new_rect)
            if self._resize_frame:
                self._resize_frame.sync_selection(new_rect)

            toolbar_geo = self._calc_toolbar_geometry(new_rect)
            if self._toolbar:
                self._toolbar.setGeometry(toolbar_geo)

            panel_geo = self._calc_recognition_panel_geometry(new_rect)
            if self._recognition_panel:
                self._recognition_panel.setGeometry(panel_geo)
                self._recognition_panel.setFixedWidth(panel_geo.width())

            self.update()
        finally:
            self.setUpdatesEnabled(True)

    def _on_selection_finalized(self) -> None:
        """选区拖拽结束"""
        pass

    def _on_confirm(self) -> None:
        """确认识别"""
        if not self._canvas:
            return
        pixmap = self._canvas.export_image()
        options = None
        if self._recognition_panel:
            options = self._recognition_panel.get_options()
        self.confirmed.emit(pixmap, options)
        self._cleanup()

    def _on_copy(self) -> None:
        """复制到剪贴板"""
        if not self._canvas:
            return
        pixmap = self._canvas.export_image()
        clipboard = QApplication.clipboard()
        clipboard.setPixmap(pixmap)
        self.copied.emit(pixmap)
        self._cleanup()

    def _on_save(self) -> None:
        """另存为"""
        if not self._canvas:
            return
        from PySide6.QtWidgets import QFileDialog

        pixmap = self._canvas.export_image()
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", "", "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)"
        )
        if path:
            pixmap.save(path)
            self.saved.emit(path)
        self._cleanup()

    def _do_cancel(self) -> None:
        """取消操作"""
        self.cancelled.emit()
        self._cleanup()

    def _cleanup(self) -> None:
        """清理子组件并重置"""
        # 销毁子组件
        if self._canvas:
            self._canvas.deleteLater()
            self._canvas = None
        if self._toolbar:
            self._toolbar.deleteLater()
            self._toolbar = None
        if self._recognition_panel:
            self._recognition_panel.deleteLater()
            self._recognition_panel = None
        if self._resize_frame:
            self._resize_frame.deleteLater()
            self._resize_frame = None

        self._captured_pixmap = None
        self._reset_capturing()
        self.hide()

    # ==================== 智能定位 ====================

    def _calc_panel_positions(self, selection: QRect) -> dict:
        """计算面板和工具栏的方向

        Returns:
            dict: {"panel_side": "left"|"right", "toolbar_side": "top"|"bottom"}
        """
        vg = self._virtual_geometry

        # 识别面板：默认右侧，右侧空间不足则翻到左侧
        right_space = vg.right() - selection.right()
        panel_side = "right" if right_space >= self._PANEL_MIN_WIDTH else "left"

        # 工具栏：默认底部，底部空间不足则翻到顶部
        bottom_space = vg.bottom() - selection.bottom()
        toolbar_side = (
            "bottom" if bottom_space >= self._TOOLBAR_MIN_HEIGHT else "top"
        )

        return {"panel_side": panel_side, "toolbar_side": toolbar_side}

    def _calc_toolbar_geometry(self, selection: QRect) -> QRect:
        """计算工具栏的几何位置——靠选区右下角"""
        if self._toolbar:
            toolbar_h = self._toolbar.sizeHint().height()
            toolbar_w = self._toolbar.sizeHint().width()
        else:
            toolbar_h = InlineStyles.TOOLBAR_HEIGHT
            toolbar_w = 400
        vg = self._virtual_geometry

        # 右对齐选区，下方 4px
        x = selection.right() - toolbar_w
        y = selection.bottom() + 4

        # 边界约束
        x = max(vg.left(), min(x, vg.right() - toolbar_w))
        if y + toolbar_h > vg.bottom():
            y = selection.top() - toolbar_h - 4

        return QRect(x, y, toolbar_w, toolbar_h)

    def _reposition_toolbar(self) -> None:
        """工具切换后重新定位工具栏（属性条显隐改变高度）"""
        if self._toolbar and self._selection_rect:
            geo = self._calc_toolbar_geometry(self._selection_rect)
            self._toolbar.setGeometry(geo)

    def _calc_recognition_panel_geometry(
        self, selection: QRect
    ) -> QRect:
        """计算识别面板的几何位置

        面板底部对齐选区下沿，高度仅容纳按钮。
        """
        positions = self._calc_panel_positions(selection)
        side = positions["panel_side"]

        panel_width = 200

        # 紧凑高度：仅够容纳按钮
        if self._recognition_panel:
            panel_height = max(self._recognition_panel.sizeHint().height(), 100)
        else:
            panel_height = 200

        if side == "right":
            x = selection.right() + 4
        else:
            x = selection.left() - panel_width - 4

        # 底部对齐选区下沿
        y = selection.bottom() - panel_height

        return QRect(x, y, panel_width, panel_height)

    # ==================== 阴影效果 ====================

    @staticmethod
    def _add_shadow(widget: QWidget) -> None:
        """为控件添加阴影效果"""
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(InlineStyles.SHADOW_BLUR)
        effect.setOffset(InlineStyles.SHADOW_OFFSET)
        effect.setColor(QColor(InlineStyles.SHADOW_COLOR))
        widget.setGraphicsEffect(effect)

    # ==================== 状态重置 ====================

    def _reset_capturing(self) -> None:
        """重置 CAPTURING 状态"""
        self._start_pos = None
        self._end_pos = None
        self._selection_rect = None
        self._screen_pixmap = None
        self._screen_image = None
        self._virtual_geometry = QRect()
        self._device_pixel_ratio = 1.0
        self._current_mouse_pos = None
        self._state = "CAPTURING"
        self.update()

    def finish_capture(self) -> None:
        """完成截图（供外部调用）"""
        self._cleanup()
