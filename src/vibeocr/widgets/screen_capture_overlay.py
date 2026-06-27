"""ScreenCaptureOverlay — 统一的截图+编辑覆盖层

替代原有的 ScreenshotWidget + ScreenshotEditWindow 双窗口流程，
使用状态机管理 CAPTURING → EDITING 两个阶段。

状态机:
  CAPTURING: 全屏透明覆盖层，截图捕获，选区绘制，放大镜
  EDITING:   内联画布 + 工具栏 + 识别面板
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QBuffer,
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
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

from vibeocr.ui import theme
from vibeocr.widgets.editor.annotation_items import (
    BlurItem,
    MosaicItem,
    TextAnnotation,
)
from vibeocr.widgets.inline_edit_canvas import InlineEditCanvas
from vibeocr.widgets.inline_recognition_panel import InlineRecognitionPanel
from vibeocr.widgets.inline_toolbar import InlineToolbar
from vibeocr.widgets.magnifier_overlay import MagnifierOverlay
from vibeocr.widgets.screen_coordinate_mapper import ScreenCoordinateMapper, ScreenInfo
from vibeocr.widgets.selection_resize_frame import SelectionResizeFrame

try:
    from vibeocr.widgets.window_detector import WindowDetector
except ImportError:
    WindowDetector = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


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
    _PANEL_MIN_WIDTH = 120
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
        # WA_TranslucentBackground/WA_NoSystemBackground 会沿窗口树向下传播，
        # 导致子控件（浮动工具栏、颜色选择器等按钮）弹出 QToolTip 时背景无法被系统
        # 填充，呈现为黑色。这里仅为覆盖层自身保持透明，并给 QToolTip 显式指定不透明
        # 背景；不改动子控件样式，也不影响其它界面（全局 QSS 当前未启用）。
        self.setStyleSheet(
            "background: transparent;"
            " QToolTip {"
            f" background-color: {theme.Colors.text};"
            f" color: {theme.Colors.surface};"
            " border: none; padding: 4px;"
            f" border-radius: {theme.Radius.sm}px;"
            " }"
        )

        # 状态
        self._state: str = "CAPTURING"

        # 临时剪贴板文件管理：复制截图时写入 temp 供资源管理器粘贴（CF_HDROP），
        # 维护进程内列表以滚动清理，避免常驻进程长期堆积临时文件。
        self._temp_clip_files: list[Path] = []
        self._temp_clip_max = 10
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._cleanup_temp_clip_files)

        # 截图相关
        self._start_pos: QPoint | None = None
        self._end_pos: QPoint | None = None
        self._selection_rect: QRect | None = None
        self._screen_pixmap: QPixmap | None = None
        self._virtual_geometry = QRect()
        self._mapper: ScreenCoordinateMapper | None = None

        # HOVER/DRAG 子状态
        self._sub_state: str = "HOVER"
        self._detected_rect: QRect | None = None
        self._window_detector: Any = None
        self._last_detect_pos: QPoint = QPoint()

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

    def _logical_rect_to_physical(self, rect: QRect) -> QRect:
        """将逻辑坐标矩形转换为物理坐标矩形，优先使用 mapper，否则回退标量 DPR"""
        if self._mapper is not None:
            return self._mapper.logical_to_screenshot_physical(rect)
        dpr = 1.0
        return QRect(
            int(rect.x() * dpr),
            int(rect.y() * dpr),
            int(rect.width() * dpr),
            int(rect.height() * dpr),
        )

    def start_capture(self) -> None:
        """开始截图（支持多屏幕和高DPI）"""
        screens = QGuiApplication.screens()
        if not screens:
            return

        # 防御性清空上一轮可能残留的选区/检测状态（异常退出路径下 _cleanup 未必执行），
        # 避免本窗口变为可见时 paintEvent 短暂绘制上一轮的选区（即「一闪而过」）。
        self._selection_rect = None
        self._detected_rect = None
        self._start_pos = None
        self._end_pos = None
        self._sub_state = "HOVER"
        self._state = "CAPTURING"

        # 计算虚拟桌面几何
        virtual_geometry = screens[0].geometry()
        for screen in screens[1:]:
            virtual_geometry = virtual_geometry.united(screen.geometry())

        max_dpr = max(screen.devicePixelRatio() for screen in screens)

        # 构建 per-screen info for mapper
        screen_infos = []
        for screen in screens:
            sg = screen.geometry()
            offset = sg.topLeft() - virtual_geometry.topLeft()
            grab = screen.grabWindow(0)
            screen_infos.append(
                ScreenInfo(
                    geometry=QRect(
                        offset.x(),
                        offset.y(),
                        sg.width(),
                        sg.height(),
                    ),
                    dpr=screen.devicePixelRatio(),
                    grab=grab,
                    offset=offset,
                )
            )

        self._mapper = ScreenCoordinateMapper(screen_infos)
        self._virtual_geometry = virtual_geometry

        # 创建合并所有屏幕的截图（用 max_dpr 保证分辨率）
        physical_size = virtual_geometry.size() * max_dpr
        pixmap = QPixmap(physical_size)
        if pixmap.isNull():
            return

        pixmap.fill(Qt.GlobalColor.black)
        pixmap.setDevicePixelRatio(max_dpr)

        painter = QPainter(pixmap)
        for screen in screens:
            screen_geometry = screen.geometry()
            offset = screen_geometry.topLeft() - virtual_geometry.topLeft()
            screen_grab = screen.grabWindow(0)
            painter.drawPixmap(offset, screen_grab)
        painter.end()

        self._screen_pixmap = pixmap

        # 设置窗口大小为虚拟桌面大小
        self.setGeometry(virtual_geometry)
        self.setMouseTracking(True)

        # 初始化窗口检测器
        hwnd = int(self.winId())
        if WindowDetector is not None:
            self._window_detector = WindowDetector(hwnd)

        # 关键：在 show() 前同步重绘，用新截图刷新后备存储。
        # 本覆盖层设置了 WA_NoSystemBackground/WA_TranslucentBackground，窗口系统在
        # show() 时不会清屏，会短暂显示上一轮截图遗留的选区画面（即「一闪而过」）。
        # repaint() 为同步绘制，确保窗口变为可见时后备存储已是本次截图，无残留。
        self.repaint()
        self.show()
        self.activateWindow()
        self.grabMouse()

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

        # HOVER 模式绘制检测高亮
        if self._sub_state == "HOVER" and self._detected_rect:
            painter.fillRect(self._detected_rect, QColor(0, 120, 215, 40))
            pen = QPen(QColor(0, 120, 215), 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._detected_rect)

        # 4. 绘制选区边框和尺寸
        if self._selection_rect:
            pen = QPen(QColor(0, 120, 215), 2)
            painter.setPen(pen)
            painter.drawRect(self._selection_rect)

            size_text = (
                f"{self._selection_rect.width()} x {self._selection_rect.height()}"
            )
            painter.drawText(self._selection_rect.topLeft() + QPoint(5, -5), size_text)

        # 5. 放大镜和像素信息
        if self._current_mouse_pos is not None and self._mapper is not None:
            mag_rect = MagnifierOverlay.draw_magnifier(
                painter,
                self._current_mouse_pos,
                self._screen_pixmap,
                self._virtual_geometry,
                self._magnifier_zoom,
                self._mapper,
                self.rect(),
            )
            MagnifierOverlay.draw_pixel_info(
                painter,
                self._current_mouse_pos,
                self._selection_rect,
                self._virtual_geometry,
                self._mapper,
                mag_rect,
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """CAPTURING: HOVER 点击选中窗口 / DRAG 开始拖拽"""
        if self._state != "CAPTURING":
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._sub_state == "HOVER" and self._detected_rect is not None:
            # 检测到窗口，直接选中
            self._selection_rect = self._detected_rect
            self.releaseMouse()
            physical_rect = self._logical_rect_to_physical(self._selection_rect)
            if self._screen_pixmap is None:
                return
            captured = self._screen_pixmap.copy(physical_rect)
            self._captured_pixmap = captured
            self._enter_editing()
            return

        # 无检测窗口或 DRAG 模式：切换到 DRAG
        self._sub_state = "DRAG"
        self._start_pos = event.pos()
        self._selection_rect = QRect(self._start_pos, self._start_pos)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """CAPTURING: 鼠标移动 — HOVER 检测或 DRAG 更新选区"""
        if self._state != "CAPTURING":
            return
        self._current_mouse_pos = event.pos()

        if self._sub_state == "DRAG":
            if self._start_pos:
                self._end_pos = event.pos()
                self._selection_rect = QRect(
                    self._start_pos, self._end_pos
                ).normalized()
            self.update()
            return

        # HOVER: 窗口检测
        if self._window_detector:
            delta = event.pos() - self._last_detect_pos
            if delta.x() * delta.x() + delta.y() * delta.y() >= 9:
                mapper = self._mapper
                if mapper is not None:
                    self._detected_rect = self._window_detector.detect_at(
                        event.pos(),
                        mapper,
                    )
                self._last_detect_pos = event.pos()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """CAPTURING: 鼠标释放完成选区，进入 EDITING 模式"""
        if self._state != "CAPTURING":
            return
        if event.button() == Qt.MouseButton.LeftButton and self._selection_rect:
            self.releaseMouse()
            if (
                self._screen_pixmap
                and self._selection_rect.width() > self.MIN_SELECTION_SIZE
                and self._selection_rect.height() > self.MIN_SELECTION_SIZE
            ):
                # QPixmap.copy() 操作物理像素，需将逻辑坐标转换为物理坐标
                # 通过 mapper 自动处理多屏 DPR 差异
                physical_rect = self._logical_rect_to_physical(self._selection_rect)
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

        sel_rect = self._selection_rect
        if sel_rect is None:
            return

        # 创建画布
        self._canvas = InlineEditCanvas(self)
        self._canvas.set_background(
            self._captured_pixmap,
            QPointF(sel_rect.x(), sel_rect.y()),
            self._mapper,
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
            forward_target=self._canvas,
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
        self._resize_frame.set_initial_selection(sel_rect)
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

        # 属性变更（画布全局属性 + 选中项属性）
        props = self._toolbar.properties_bar
        props.color_changed.connect(self._on_color_changed)
        props.line_width_changed.connect(self._on_line_width_changed)
        props.fill_enabled_changed.connect(self._on_fill_enabled_changed)
        props.fill_color_changed.connect(self._on_fill_color_changed)
        props.fill_opacity_changed.connect(self._on_fill_opacity_changed)
        props.fill_linked_changed.connect(self._on_fill_linked_changed)
        props.mosaic_strength_changed.connect(self._on_mosaic_strength_changed)
        props.blur_radius_changed.connect(self._on_blur_radius_changed)
        props.font_changed.connect(self._canvas.set_font)
        props.font_size_changed.connect(self._canvas.set_font_size)
        props.bold_changed.connect(self._canvas.set_bold)
        props.italic_changed.connect(self._canvas.set_italic)

        # 撤销/重做
        self._toolbar.undo_requested.connect(self._canvas.undo_stack.undo)
        self._toolbar.redo_requested.connect(self._canvas.undo_stack.redo)
        self._canvas.undo_stack.canUndoChanged.connect(self._toolbar.set_undo_enabled)
        self._canvas.undo_stack.canRedoChanged.connect(self._toolbar.set_redo_enabled)

        # 选中变化 → 属性条更新
        self._canvas._scene.selectionChanged.connect(
            self._on_annotation_selection_changed
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
        if not self._canvas or not self._screen_pixmap or not self._mapper:
            return

        # 批量更新：禁止中间状态重绘，避免波纹
        self.setUpdatesEnabled(False)
        try:
            self._selection_rect = new_rect

            self._canvas.update_crop_region(self._screen_pixmap, new_rect, self._mapper)

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

    def _on_annotation_selection_changed(self) -> None:
        """选中标注项时更新属性条"""
        if not self._toolbar or not self._canvas:
            return

        item = self._canvas.selected_annotation
        props = self._toolbar.properties_bar

        if item:
            props.update_for_selection(item)
        else:
            props.clear_selection()

    def _on_color_changed(self, color) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_pen_color(color)
        item = canvas.selected_annotation
        if item and hasattr(item, "set_pen_color"):
            item.set_pen_color(color)
        elif isinstance(item, TextAnnotation):
            item.set_text_color(color)
        if canvas._fill_linked:
            if item and hasattr(item, "set_fill_color"):
                item.set_fill_color(color)

    def _on_line_width_changed(self, width) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_pen_width(width)
        item = canvas.selected_annotation
        if item and hasattr(item, "set_pen_width"):
            item.set_pen_width(width)

    def _on_fill_enabled_changed(self, enabled) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_fill_enabled(enabled)
        item = canvas.selected_annotation
        if item and hasattr(item, "set_fill_enabled"):
            item.set_fill_enabled(enabled, canvas._fill_color, canvas._fill_opacity)

    def _on_fill_color_changed(self, color) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_fill_color(color)
        item = canvas.selected_annotation
        if item and hasattr(item, "set_fill_color"):
            item.set_fill_color(color)

    def _on_fill_opacity_changed(self, opacity) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_fill_opacity(opacity)
        item = canvas.selected_annotation
        if item and hasattr(item, "set_fill_opacity"):
            item.set_fill_opacity(opacity)

    def _on_fill_linked_changed(self, linked) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_fill_linked(linked)
        if linked:
            item = canvas.selected_annotation
            if item and hasattr(item, "set_fill_color"):
                item.set_fill_color(canvas._pen_color)

    def _on_mosaic_strength_changed(self, value) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_mosaic_strength(value)
        item = canvas.selected_annotation
        if isinstance(item, MosaicItem):
            item.set_strength(value)

    def _on_blur_radius_changed(self, value) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.set_blur_radius(value)
        item = canvas.selected_annotation
        if isinstance(item, BlurItem):
            item.set_radius(value)

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
        """复制到剪贴板

        Windows 下同时写入位图格式（供微信/画图等粘贴）和文件格式（CF_HDROP，
        供资源管理器粘贴到文件夹）；其它平台保持原有位图写入。
        """
        if not self._canvas:
            return
        pixmap = self._canvas.export_image()

        # 统一编码为 PNG 字节，供位图格式与临时文件共用
        png_bytes = self._pixmap_to_png(pixmap)

        clipboard = QApplication.clipboard()
        if sys.platform == "win32":
            mime_data = QMimeData()
            if png_bytes is not None:
                mime_data.setImageData(png_bytes)
            # 写入临时文件并附带本地路径，Qt 在 Windows 上会据此生成
            # CF_HDROP/FileNameW，使资源管理器能够粘贴出文件。
            temp_path = self._write_temp_clip_file(png_bytes)
            if temp_path is not None:
                mime_data.setUrls([QUrl.fromLocalFile(str(temp_path))])
                self._prune_temp_clip_files()
            clipboard.setMimeData(mime_data)
        else:
            clipboard.setPixmap(pixmap)

        self.copied.emit(pixmap)
        self._cleanup()

    @staticmethod
    def _pixmap_to_png(pixmap: QPixmap) -> bytes | None:
        """将 QPixmap 编码为 PNG 字节；失败返回 None。"""
        try:
            image = pixmap.toImage()
            buffer = QBuffer()
            buffer.open(QBuffer.OpenModeFlag.WriteOnly)
            ok = image.save(buffer, "PNG")
            buffer.close()
            if not ok:
                return None
            return bytes(buffer.data())
        except Exception:  # 编码失败不应阻断复制流程
            logger.exception("编码 PNG 失败")
            return None

    def _write_temp_clip_file(self, png_bytes: bytes | None) -> Path | None:
        """写入临时 PNG 文件并登记到进程内列表；失败返回 None。"""
        if png_bytes is None:
            return None
        try:
            fd, name = tempfile.mkstemp(
                prefix="vibeocr_clip_", suffix=".png", dir=tempfile.gettempdir()
            )
            path = Path(name)
            with os.fdopen(fd, "wb") as f:
                f.write(png_bytes)
            self._temp_clip_files.append(path)
            return path
        except Exception:  # 临时文件失败不应阻断复制
            logger.exception("写入临时剪贴板文件失败")
            return None

    def _prune_temp_clip_files(self) -> None:
        """惰性校验 + 滚动清理临时剪贴板文件。

        先剔除被外部删除的幽灵条目（仅 stat），再在超限时删除最旧的若干文件，
        保留最近 _temp_clip_max 个。整个过程不扫描磁盘目录。
        """
        try:
            # 惰性校验：剔除已不存在的条目，保证计数器准确
            self._temp_clip_files = [p for p in self._temp_clip_files if p.exists()]
            # 滚动清理：保留最近 N 个
            overflow = len(self._temp_clip_files) - self._temp_clip_max
            for _ in range(max(0, overflow)):
                oldest = self._temp_clip_files.pop(0)
                try:
                    oldest.unlink(missing_ok=True)
                except OSError:
                    logger.warning("删除临时剪贴板文件失败: %s", oldest)
        except Exception:  # 清理失败不应阻断复制
            logger.exception("清理临时剪贴板文件失败")

    def _cleanup_temp_clip_files(self) -> None:
        """应用退出时兜底清理所有临时剪贴板文件。"""
        try:
            for path in self._temp_clip_files:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("退出清理临时剪贴板文件失败: %s", path)
            self._temp_clip_files.clear()
        except Exception:
            logger.exception("退出清理临时剪贴板文件失败")

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
        toolbar_side = "bottom" if bottom_space >= self._TOOLBAR_MIN_HEIGHT else "top"

        return {"panel_side": panel_side, "toolbar_side": toolbar_side}

    def _calc_toolbar_geometry(self, selection: QRect) -> QRect:
        """计算工具栏的几何位置——靠选区右下角"""
        if self._toolbar:
            toolbar_h = self._toolbar.sizeHint().height()
            toolbar_w = self._toolbar.sizeHint().width()
        else:
            toolbar_h = theme.Layout.toolbar_height
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

    def _calc_recognition_panel_geometry(self, selection: QRect) -> QRect:
        """计算识别面板的几何位置

        面板底部对齐选区下沿，高度仅容纳按钮。
        """
        positions = self._calc_panel_positions(selection)
        side = positions["panel_side"]

        panel_width = 120

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
        effect.setBlurRadius(theme.Layout.shadow_blur)
        effect.setOffset(theme.Layout.shadow_offset_y)
        effect.setColor(QColor(theme.Layout.shadow_color))
        widget.setGraphicsEffect(effect)

    # ==================== 状态重置 ====================

    def _reset_capturing(self) -> None:
        """重置 CAPTURING 状态"""
        self._start_pos = None
        self._end_pos = None
        self._selection_rect = None
        self._screen_pixmap = None
        self._virtual_geometry = QRect()
        self._mapper = None
        self._current_mouse_pos = None
        self._sub_state = "HOVER"
        self._detected_rect = None
        self._last_detect_pos = QPoint()
        self._state = "CAPTURING"
        self.update()

    def finish_capture(self) -> None:
        """完成截图（供外部调用）"""
        self._cleanup()
