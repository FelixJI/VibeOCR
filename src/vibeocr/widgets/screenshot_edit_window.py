"""全屏截图编辑窗口

框选完成后的编辑界面，包含画布、工具栏和识别设置面板。
"""

import logging

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from vibeocr.core.editor_styles import EditorStyles
from vibeocr.widgets.editor.edit_canvas import EditCanvas
from vibeocr.widgets.editor.edit_toolbar import EditorToolbar
from vibeocr.widgets.recognition_panel import RecognitionPanel


class ScreenshotEditWindow(QWidget):
    """全屏截图编辑窗口"""

    confirmed = Signal(QPixmap, object)  # (编辑后图片, OCROptions)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(
            f"QWidget#editWindowBg {{ background-color: {EditorStyles.EDITOR_BG_ALPHA}; }}"
        )

        self._setup_ui()
        self._connect_signals()
        self._setup_shortcuts()

    def _setup_ui(self) -> None:
        # 背景容器（用于应用半透明背景）
        self._bg_widget = QWidget(self)
        self._bg_widget.setObjectName("editWindowBg")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self._bg_widget)

        bg_layout = QVBoxLayout(self._bg_widget)
        bg_layout.setContentsMargins(8, 8, 8, 0)
        bg_layout.setSpacing(0)

        # 中间区域：画布 + 右侧面板
        center_layout = QHBoxLayout()
        center_layout.setSpacing(0)

        self._canvas = EditCanvas()
        center_layout.addWidget(self._canvas, 1)

        self._recognition_panel = RecognitionPanel()
        center_layout.addWidget(self._recognition_panel)

        bg_layout.addLayout(center_layout, 1)

        # 底部工具栏
        self._toolbar = EditorToolbar()
        bg_layout.addWidget(self._toolbar)

    def _connect_signals(self) -> None:
        toolbar = self._toolbar
        canvas = self._canvas
        props = toolbar.properties_bar

        # 工具切换
        toolbar.tool_changed.connect(canvas.set_tool)

        # 操作按钮
        toolbar.undo_requested.connect(canvas.undo_stack.undo)
        toolbar.redo_requested.connect(canvas.undo_stack.redo)
        toolbar.save_requested.connect(self._on_save)
        toolbar.copy_requested.connect(self._on_copy)
        toolbar.confirm_requested.connect(self._on_confirm)
        toolbar.cancel_requested.connect(self._on_cancel)

        # 撤销/重做状态
        canvas.undo_stack.canUndoChanged.connect(toolbar.set_undo_enabled)
        canvas.undo_stack.canRedoChanged.connect(toolbar.set_redo_enabled)

        # 工具属性 -> 画布
        props.color_changed.connect(canvas.set_pen_color)
        props.line_width_changed.connect(canvas.set_pen_width)
        props.fill_enabled_changed.connect(canvas.set_fill_enabled)
        props.font_changed.connect(canvas.set_font)
        props.font_size_changed.connect(canvas.set_font_size)
        props.bold_changed.connect(canvas.set_bold)
        props.italic_changed.connect(canvas.set_italic)
        props.mosaic_strength_changed.connect(canvas.set_mosaic_strength)
        props.blur_radius_changed.connect(canvas.set_blur_radius)

    def _setup_shortcuts(self) -> None:
        """设置键盘快捷键"""
        # ESC 取消
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._on_cancel)
        # Ctrl+Z 撤销
        QShortcut(
            QKeySequence.StandardKey.Undo,
            self,
            self._canvas.undo_stack.undo,
        )
        # Ctrl+Y 重做
        QShortcut(
            QKeySequence.StandardKey.Redo,
            self,
            self._canvas.undo_stack.redo,
        )
        # Ctrl+Shift+Z 重做（备用）
        QShortcut(
            QKeySequence("Ctrl+Shift+Z"),
            self,
            self._canvas.undo_stack.redo,
        )
        # Ctrl+S 另存为
        QShortcut(QKeySequence.StandardKey.Save, self, self._on_save)
        # Ctrl+C 复制
        QShortcut(QKeySequence.StandardKey.Copy, self, self._on_copy)
        # Enter 确认识别
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, self._on_confirm)

    def open_editor(self, pixmap: QPixmap, screen_rect: QRect) -> None:
        """打开编辑器

        Args:
            pixmap: 截取的图像
            screen_rect: 截图时的选区矩形（逻辑坐标，用于确定显示屏幕）
        """
        logging.info(
            f"打开编辑窗口: 图像 {pixmap.width()}x{pixmap.height()}, 选区 {screen_rect}"
        )

        # 确定应显示在哪个屏幕上
        app = QApplication.instance()
        screen = None
        if app:
            screen = app.screenAt(screen_rect.center())
        if not screen:
            screens = QApplication.screens()
            screen = screens[0] if screens else None

        if screen:
            self.setGeometry(screen.geometry())
        else:
            self.showFullScreen()

        # 设置画布背景
        self._canvas.set_background(pixmap)

        # 清空撤销栈
        self._canvas.undo_stack.clear()

        self.showFullScreen()
        self.activateWindow()

    def _on_confirm(self) -> None:
        """确认识别"""
        pixmap = self._canvas.export_image()
        options = self._recognition_panel.get_options()
        logging.info(f"编辑确认: 管道={options.pipeline.display_name}")
        self.confirmed.emit(pixmap, options)

    def _on_cancel(self) -> None:
        """取消编辑"""
        self.cancelled.emit()

    def _on_save(self) -> None:
        """另存为"""
        pixmap = self._canvas.export_image()
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "另存为",
            "",
            "PNG 图片 (*.png);;JPEG 图片 (*.jpg *.jpeg);;所有文件 (*)",
        )
        if file_path:
            if pixmap.save(file_path):
                logging.info(f"图片已保存: {file_path}")
            else:
                QMessageBox.warning(self, "保存失败", f"无法保存图片到: {file_path}")

    def _on_copy(self) -> None:
        """复制到剪贴板"""
        pixmap = self._canvas.export_image()
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setPixmap(pixmap)
            logging.info("图片已复制到剪贴板")

    def keyPressEvent(self, event) -> None:
        """键盘事件（备用处理）"""
        # 快捷键主要由 QShortcut 处理，这里做兜底
        super().keyPressEvent(event)
