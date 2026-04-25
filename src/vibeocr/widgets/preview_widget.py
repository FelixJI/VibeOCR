"""Preview widget for image display and screenshot trigger"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QLineEdit, QMenu, QVBoxLayout, QWidget

from vibeocr.models.ocr_result import TextBlock

# 置信度阈值
LOW_CONFIDENCE_THRESHOLD = 0.80

# 高亮颜色
HIGH_CONF_FILL = QColor(76, 175, 80, 40)    # 淡绿色填充
HIGH_CONF_BORDER = QColor(76, 175, 80, 160)  # 淡绿色边框
LOW_CONF_FILL = QColor(244, 67, 54, 60)     # 红色填充
LOW_CONF_BORDER = QColor(244, 67, 54, 200)  # 红色边框
EDIT_FILL = QColor(255, 193, 7, 40)         # 琥珀色填充（手动修改）
EDIT_BORDER = QColor(255, 152, 0, 200)      # 橙色边框（手动修改）


class TextBlockOverlay(QWidget):
    """透明覆盖层，绘制文本块高亮矩形"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rects: list[tuple[float, float, float, float, float, str, bool]] = []
        # (x, y, w, h, score, text, is_manually_edited)
        self._hovered_index: int = -1
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_blocks(
        self,
        rects: list[tuple[float, float, float, float, float, str, bool]],
    ) -> None:
        self._rects = rects
        self._hovered_index = -1
        self.update()

    def set_hovered(self, index: int) -> None:
        if index != self._hovered_index:
            self._hovered_index = index
            self.update()

    def clear(self) -> None:
        self._rects.clear()
        self._hovered_index = -1
        self.update()

    def paintEvent(self, event) -> None:
        if not self._rects:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for i, (x, y, w, h, score, text, is_manually_edited) in enumerate(self._rects):
            from PySide6.QtCore import QRectF

            rect = QRectF(x, y, w, h)
            is_low = score < LOW_CONFIDENCE_THRESHOLD
            is_hovered = i == self._hovered_index

            if is_manually_edited:
                fill = EDIT_FILL
                border = EDIT_BORDER
            elif is_low:
                fill = LOW_CONF_FILL
                border = LOW_CONF_BORDER
            else:
                fill = HIGH_CONF_FILL
                border = HIGH_CONF_BORDER

            if is_hovered:
                fill = QColor(fill)
                fill.setAlpha(min(fill.alpha() + 80, 200))

            painter.fillRect(rect, fill)
            pen = QPen(border, 2)
            painter.setPen(pen)
            painter.drawRect(rect)

        painter.end()


class PreviewWidget(QWidget):
    """图片预览组件，无图片时点击可触发截图或选择文件"""

    screenshot_requested = Signal()  # 请求截图信号
    file_open_requested = Signal()  # 请求打开文件信号
    image_changed = Signal()  # 图片改变信号
    block_clicked = Signal(int)  # 文本块被点击（TextBlock 索引）
    block_text_edited = Signal(int, str)  # 文本块被编辑（索引，新文本）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._text_blocks: list[TextBlock] = []
        self._block_screen_rects: list[tuple[float, float, float, float]] = []
        self._hovered_block: int = -1
        self._editing_index: int = -1
        self._setup_ui()

    def _setup_ui(self) -> None:
        """设置UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 图片显示标签
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(300, 200)
        self._image_label.setStyleSheet(
            "QLabel { background-color: #f0f0f0; border: 2px dashed #ccc; }"
        )
        self._image_label.setText("左键点击截图 · 右键点击选择文件\n\n支持图片、PDF 格式")
        self._image_label.setWordWrap(True)
        self._image_label.mousePressEvent = self._on_label_click  # type: ignore[method-assign]
        self._image_label.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._image_label.customContextMenuRequested.connect(
            self._on_context_menu
        )

        layout.addWidget(self._image_label)

        # 文本块高亮覆盖层
        self._overlay = TextBlockOverlay(self._image_label)

        # 内联文本编辑器
        self._inline_editor = QLineEdit(self._image_label)
        self._inline_editor.setStyleSheet(
            "QLineEdit { background-color: rgba(255,255,255,0.95); "
            "border: 2px solid #ff9800; border-radius: 4px; "
            "padding: 2px 6px; font-size: 13px; }"
        )
        self._inline_editor.setFrame(False)
        self._inline_editor.hide()
        self._inline_editor.editingFinished.connect(self._on_inline_edit_finished)
        self._inline_editor.installEventFilter(self)

        # 事件过滤器用于悬停和点击检测
        self._image_label.setMouseTracking(True)
        self._image_label.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if obj == self._image_label and self._pixmap and self._text_blocks:
            if event.type() == event.Type.MouseMove:
                self._on_mouse_move(event.pos())
            elif event.type() == event.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._on_block_click(event.pos())
            elif event.type() == event.Type.MouseButtonDblClick:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._on_block_double_click(event.pos())
        elif obj == self._inline_editor and event.type() == event.Type.KeyPress:
            from PySide6.QtGui import QKeyEvent
            key_event: QKeyEvent = event
            if key_event.key() == Qt.Key.Key_Escape:
                self._cancel_inline_edit()
                return True
        return super().eventFilter(obj, event)

    def _on_mouse_move(self, pos) -> None:
        """鼠标移动检测所在文本块"""
        idx = self._hit_test_block(pos.x(), pos.y())
        if idx != self._hovered_block:
            self._hovered_block = idx
            self._overlay.set_hovered(idx)
            if idx >= 0:
                block = self._text_blocks[idx]
                tooltip = f"{block.text[:50]}\n置信度: {block.score:.1%}"
                if block.is_manually_edited:
                    tooltip += "\n[手动修改]"
                self._image_label.setToolTip(tooltip)
            else:
                self._image_label.setToolTip("")

    def _on_block_click(self, pos) -> None:
        """点击文本块"""
        idx = self._hit_test_block(pos.x(), pos.y())
        if idx >= 0:
            self.block_clicked.emit(idx)

    def _on_block_double_click(self, pos) -> None:
        """双击文本块进入内联编辑"""
        idx = self._hit_test_block(pos.x(), pos.y())
        if idx < 0:
            return
        self._start_inline_edit(idx)

    def _start_inline_edit(self, index: int) -> None:
        """在指定文本块位置显示内联编辑器"""
        if index < 0 or index >= len(self._block_screen_rects):
            return
        bx, by, bw, bh = self._block_screen_rects[index]
        block = self._text_blocks[index]
        self._editing_index = index
        self._inline_editor.setText(block.text)
        self._inline_editor.setGeometry(int(bx), int(by), max(int(bw), 120), max(int(bh) + 4, 28))
        self._inline_editor.show()
        self._inline_editor.setFocus()
        self._inline_editor.selectAll()

    def _on_inline_edit_finished(self) -> None:
        """内联编辑完成（按回车或失去焦点）"""
        if self._editing_index < 0:
            return
        index = self._editing_index
        new_text = self._inline_editor.text()
        old_text = self._text_blocks[index].text
        self._inline_editor.hide()
        self._editing_index = -1
        if new_text != old_text:
            self.block_text_edited.emit(index, new_text)

    def _cancel_inline_edit(self) -> None:
        """取消内联编辑（按 Esc）"""
        self._inline_editor.hide()
        self._editing_index = -1

    def _hit_test_block(self, x: int, y: int) -> int:
        """检测点击位置命中的文本块索引"""
        for i, (bx, by, bw, bh) in enumerate(self._block_screen_rects):
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return i
        return -1

    def _on_label_click(self, event) -> None:
        """点击标签时：左键截图，右键选择文件"""
        if self._pixmap is None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.screenshot_requested.emit()
            elif event.button() == Qt.MouseButton.RightButton:
                self.file_open_requested.emit()

    def _on_context_menu(self, pos) -> None:
        """右键上下文菜单"""
        if self._pixmap is not None:
            return
        menu = QMenu(self._image_label)
        action_screenshot = QAction("截图识别", menu)
        action_open_file = QAction("选择文件（图片/PDF）", menu)
        action_screenshot.triggered.connect(self.screenshot_requested.emit)
        action_open_file.triggered.connect(self.file_open_requested.emit)
        menu.addAction(action_screenshot)
        menu.addAction(action_open_file)
        menu.exec(self._image_label.mapToGlobal(pos))

    def set_pixmap(self, pixmap: QPixmap) -> None:
        """设置预览图片"""
        self._pixmap = pixmap
        self._update_display()
        self.image_changed.emit()

    def pixmap(self) -> QPixmap | None:
        """获取当前图片"""
        return self._pixmap

    def set_text_blocks(self, blocks: list[TextBlock]) -> None:
        """设置文本块用于高亮显示"""
        self._text_blocks = blocks
        self._update_block_overlay()

    def clear(self) -> None:
        """清除图片"""
        self._pixmap = None
        self._text_blocks = []
        self._block_screen_rects = []
        self._hovered_block = -1
        self._overlay.clear()
        self._image_label.clear()
        self._image_label.setText("左键点击截图 · 右键点击选择文件\n\n支持图片、PDF 格式")
        self._image_label.setStyleSheet(
            "QLabel { background-color: #f0f0f0; border: 2px dashed #ccc; }"
        )
        self.image_changed.emit()

    def _update_display(self) -> None:
        """更新图片显示"""
        if self._pixmap:
            label_size = self._image_label.size()
            dpr = self._image_label.devicePixelRatio()
            physical_size = label_size * dpr

            scaled = self._pixmap.scaled(
                physical_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            self._image_label.setPixmap(scaled)
            self._image_label.setStyleSheet(
                "QLabel { background-color: #fff; border: 1px solid #ddd; }"
            )
            self._update_block_overlay()

    def _update_block_overlay(self) -> None:
        """根据当前文本块和图片显示计算覆盖矩形"""
        self._overlay.clear()
        self._block_screen_rects.clear()

        if not self._pixmap or not self._text_blocks:
            return

        current_pixmap = self._image_label.pixmap()
        if not current_pixmap:
            return

        # 逻辑显示尺寸（考虑 DPR）
        dpr = current_pixmap.devicePixelRatio() or 1.0
        disp_w = current_pixmap.width() / dpr
        disp_h = current_pixmap.height() / dpr

        # label 中的居中偏移
        label_w = self._image_label.width()
        label_h = self._image_label.height()
        offset_x = (label_w - disp_w) / 2
        offset_y = (label_h - disp_h) / 2

        overlay_rects = []
        for block in self._text_blocks:
            if block.bbox is None:
                self._block_screen_rects.append((0, 0, 0, 0))
                continue
            x0, y0, x1, y1 = block.bbox
            # bbox 归一化 [0-1000] → 逻辑显示坐标
            sx = x0 / 1000.0 * disp_w + offset_x
            sy = y0 / 1000.0 * disp_h + offset_y
            sw = (x1 - x0) / 1000.0 * disp_w
            sh = (y1 - y0) / 1000.0 * disp_h
            self._block_screen_rects.append((sx, sy, sw, sh))
            overlay_rects.append((sx, sy, sw, sh, block.score, block.text, block.is_manually_edited))

        self._overlay.set_blocks(overlay_rects)
        self._overlay.setGeometry(self._image_label.rect())

    def highlight_block(self, index: int) -> None:
        """高亮指定文本块"""
        self._overlay.set_hovered(index)

    def resizeEvent(self, event) -> None:
        """窗口大小改变时重新缩放图片"""
        super().resizeEvent(event)
        if self._pixmap:
            self._update_display()
