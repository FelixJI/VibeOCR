"""Preview widget for image display, file loading and screenshot trigger"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from vibeocr.models.ocr_result import TextBlock

logger = logging.getLogger(__name__)

# 置信度阈值
LOW_CONFIDENCE_THRESHOLD = 0.80

# 置信度着色颜色
HIGH_CONF_FILL = QColor(76, 175, 80, 40)    # 淡绿色填充
HIGH_CONF_BORDER = QColor(76, 175, 80, 160)  # 淡绿色边框
LOW_CONF_FILL = QColor(244, 67, 54, 60)     # 红色填充
LOW_CONF_BORDER = QColor(244, 67, 54, 200)  # 红色边框
EDIT_FILL = QColor(255, 193, 7, 40)         # 琥珀色填充（手动修改）
EDIT_BORDER = QColor(255, 152, 0, 200)      # 橙色边框（手动修改）

# 块类型着色常量（来自 FilePreviewWidget）
BBOX_NORM = 1000.0

# MinerU discarded block types — skip in overlay
DISCARDED_BLOCK_TYPES = frozenset({
    "header", "footer", "page_number", "page_footnote", "aside_text",
})

BLOCK_COLORS = {
    "text": QColor(59, 130, 246, 30),
    "title": QColor(239, 68, 68, 30),
    "table": QColor(34, 197, 94, 30),
    "image": QColor(168, 85, 247, 30),
    "figure": QColor(168, 85, 247, 30),
    "equation": QColor(249, 115, 22, 30),
    "interline_equation": QColor(249, 115, 22, 30),
    "inline_equation": QColor(249, 115, 22, 30),
}

BLOCK_BORDER_COLORS = {
    "text": QColor(59, 130, 246, 200),
    "title": QColor(239, 68, 68, 200),
    "table": QColor(34, 197, 94, 200),
    "image": QColor(168, 85, 247, 200),
    "figure": QColor(168, 85, 247, 200),
    "equation": QColor(249, 115, 22, 200),
    "interline_equation": QColor(249, 115, 22, 200),
    "inline_equation": QColor(249, 115, 22, 200),
}

BLOCK_TYPE_LABELS = {
    "text": "文本",
    "title": "标题",
    "table": "表格",
    "image": "图片",
    "figure": "图片",
    "equation": "公式",
    "interline_equation": "公式",
    "inline_equation": "公式",
}


class UnifiedBBoxOverlay(QWidget):
    """统一 BBox 覆盖层，支持置信度着色和块类型着色两种模式"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # 置信度模式数据: list of (x, y, w, h, score, text, is_manually_edited)
        self._conf_rects: list[tuple[float, float, float, float, float, str, bool]] = []
        # 块类型模式数据: list of (content_index, rect, block_type, fill, border)
        self._type_rects: list[tuple[int, QRectF, str, QColor, QColor]] = []
        self._mode: str = "confidence"  # "confidence" or "block_type"
        self._hovered_index: int = -1
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_confidence_blocks(self, rects) -> None:
        self._mode = "confidence"
        self._conf_rects = rects
        self._hovered_index = -1
        self.update()

    def set_type_blocks(self, rects) -> None:
        self._mode = "block_type"
        self._type_rects = rects
        self._hovered_index = -1
        self.update()

    def set_hovered(self, index: int) -> None:
        if index != self._hovered_index:
            self._hovered_index = index
            self.update()

    def clear(self) -> None:
        self._conf_rects.clear()
        self._type_rects.clear()
        self._hovered_index = -1
        self.update()

    def paintEvent(self, event) -> None:
        if self._mode == "confidence":
            self._paint_confidence()
        else:
            self._paint_block_type()

    def _paint_confidence(self) -> None:
        if not self._conf_rects:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for i, (x, y, w, h, score, text, is_manually_edited) in enumerate(self._conf_rects):
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

    def _paint_block_type(self) -> None:
        if not self._type_rects:
            return
        from PySide6.QtCore import QPointF

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for cl_idx, rect, block_type, fill_color, border_color in self._type_rects:
            is_hovered = cl_idx == self._hovered_index

            if is_hovered:
                fill = QColor(fill_color)
                fill.setAlpha(min(fill.alpha() + 100, 220))
                border = QColor(border_color)
                border.setAlpha(255)
            else:
                fill = fill_color
                border = border_color

            painter.fillRect(rect, fill)
            pen = QPen(border, 1)
            painter.setPen(pen)
            painter.drawRect(rect)

            if is_hovered or len(self._type_rects) <= 20:
                label = BLOCK_TYPE_LABELS.get(block_type, block_type)
                label_rect = QRectF(rect.topLeft(), rect.topLeft() + QPointF(36, 14))
                painter.fillRect(label_rect, border)
                font = painter.font()
                font.setPointSize(7)
                painter.setFont(font)
                painter.setPen(QPen(QColor(255, 255, 255)))
                painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)

        painter.end()


class PreviewWidget(QWidget):
    """统一图片预览组件

    支持图片/PDF 加载、截图触发、BBox 高亮、翻页导航。
    支持两种覆盖层模式：
    - 置信度模式（单次识别）：通过 set_text_blocks 设置
    - 块类型模式（批量识别/文档解析）：通过 set_content_list 设置
    """

    screenshot_requested = Signal()
    file_open_requested = Signal()
    image_changed = Signal()
    block_clicked = Signal(int)
    block_text_edited = Signal(int, str)
    block_hovered = Signal(int)
    block_unhovered = Signal()

    def __init__(
        self, parent: QWidget | None = None, *, empty_text: str = "左键点击截图 · 右键点击选择文件\n\n支持图片、PDF 格式"
    ) -> None:
        super().__init__(parent)
        self._empty_text = empty_text
        self._pixmap: QPixmap | None = None
        self._original_pixmap: QPixmap | None = None
        self._text_blocks: list[TextBlock] = []
        self._block_screen_rects: list[tuple[float, float, float, float]] = []
        self._hovered_block: int = -1
        self._editing_index: int = -1
        self._content_list: list[dict] = []
        self._current_file: str = ""
        self._is_pdf = False
        self._highlight_block_index: int = -1

        # PDF
        self._pdf_doc: QPdfDocument | None = None
        self._current_page: int = 0
        self._total_pages: int = 0

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 翻页导航栏
        self._nav_bar = QWidget()
        nav_layout = QHBoxLayout(self._nav_bar)
        nav_layout.setContentsMargins(4, 0, 4, 0)

        self._prev_btn = QPushButton("<")
        self._prev_btn.setFixedWidth(30)
        self._prev_btn.setEnabled(False)

        self._page_label = QLabel("0 / 0")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._next_btn = QPushButton(">")
        self._next_btn.setFixedWidth(30)
        self._next_btn.setEnabled(False)

        nav_layout.addWidget(self._prev_btn)
        nav_layout.addStretch()
        nav_layout.addWidget(self._page_label)
        nav_layout.addStretch()
        nav_layout.addWidget(self._next_btn)

        layout.addWidget(self._nav_bar)

        # 预览区域（带滚动）
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(200, 200)
        self._image_label.setStyleSheet(
            "QLabel { background-color: #f0f0f0; border: 2px dashed #ccc; }"
        )
        self._image_label.setText(self._empty_text)
        self._image_label.setWordWrap(True)
        self._image_label.mousePressEvent = self._on_label_click  # type: ignore[method-assign]
        self._image_label.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._image_label.customContextMenuRequested.connect(
            self._on_context_menu
        )
        self._scroll_area.setWidget(self._image_label)

        layout.addWidget(self._scroll_area, stretch=1)

        # 覆盖层
        self._overlay = UnifiedBBoxOverlay(self._scroll_area.viewport())

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

        # 翻页信号
        self._prev_btn.clicked.connect(self._on_prev_page)
        self._next_btn.clicked.connect(self._on_next_page)

    # ── 事件过滤器 ──

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
        idx = self._hit_test_block(pos.x(), pos.y())
        if idx != self._hovered_block:
            self._hovered_block = idx
            self._overlay.set_hovered(idx)
            if idx >= 0:
                self.block_hovered.emit(idx)
                block = self._text_blocks[idx]
                tooltip = f"{block.text[:50]}\n置信度: {block.score:.1%}"
                if block.is_manually_edited:
                    tooltip += "\n[手动修改]"
                self._image_label.setToolTip(tooltip)
            else:
                self.block_unhovered.emit()
                self._image_label.setToolTip("")

    def _on_block_click(self, pos) -> None:
        idx = self._hit_test_block(pos.x(), pos.y())
        if idx >= 0:
            self.block_clicked.emit(idx)

    def _on_block_double_click(self, pos) -> None:
        idx = self._hit_test_block(pos.x(), pos.y())
        if idx < 0:
            return
        self._start_inline_edit(idx)

    def _start_inline_edit(self, index: int) -> None:
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
        self._inline_editor.hide()
        self._editing_index = -1

    def _hit_test_block(self, x: int, y: int) -> int:
        for i, (bx, by, bw, bh) in enumerate(self._block_screen_rects):
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return i
        return -1

    # ── 标签点击（空状态触发截图/文件选择）──

    def _on_label_click(self, event) -> None:
        if self._pixmap is None and self._original_pixmap is None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.screenshot_requested.emit()
            elif event.button() == Qt.MouseButton.RightButton:
                self.file_open_requested.emit()

    def _on_context_menu(self, pos) -> None:
        if self._pixmap is not None or self._original_pixmap is not None:
            return
        menu = QMenu(self._image_label)
        action_screenshot = QAction("截图识别", menu)
        action_open_file = QAction("选择文件（图片/PDF）", menu)
        action_screenshot.triggered.connect(self.screenshot_requested.emit)
        action_open_file.triggered.connect(self.file_open_requested.emit)
        menu.addAction(action_screenshot)
        menu.addAction(action_open_file)
        menu.exec(self._image_label.mapToGlobal(pos))

    # ── 图片设置 ──

    def set_pixmap(self, pixmap: QPixmap) -> None:
        """设置预览图片（截图或打开图片）"""
        self._pixmap = pixmap
        self._original_pixmap = pixmap
        self._total_pages = 1
        self._current_page = 0
        self._update_display()
        self._update_nav()
        self.image_changed.emit()

    def pixmap(self) -> QPixmap | None:
        return self._pixmap

    def set_text_blocks(self, blocks: list[TextBlock]) -> None:
        """设置文本块用于置信度模式高亮"""
        self._text_blocks = blocks
        self._update_block_overlay()

    # ── 文件加载（PDF/图片）──

    def load_file(self, file_path: str) -> None:
        """从文件路径加载（自动检测 PDF/图片）"""
        self._current_file = file_path
        ext = Path(file_path).suffix.lower()
        self._is_pdf = ext == ".pdf"

        self._overlay.clear()
        self._highlight_block_index = -1

        if self._is_pdf:
            self._load_pdf(file_path)
        else:
            self._load_image_file(file_path)

    def _load_pdf(self, file_path: str) -> None:
        if self._pdf_doc is not None:
            self._pdf_doc.close()

        self._pdf_doc = QPdfDocument(self)
        error = self._pdf_doc.load(file_path)
        if error != QPdfDocument.Error.None_:
            self._image_label.setText(f"无法加载 PDF: {file_path}")
            self._total_pages = 0
            self._update_nav()
            return

        self._total_pages = self._pdf_doc.pageCount()
        self._current_page = 0
        self._render_current_page()
        self._update_nav()

    def _load_image_file(self, file_path: str) -> None:
        self._total_pages = 1
        self._current_page = 0
        self._original_pixmap = QPixmap(file_path)

        if self._original_pixmap.isNull():
            self._image_label.setText(f"无法加载图片: {file_path}")
        else:
            self._pixmap = self._original_pixmap
            self._update_display()

        self._update_nav()

    def _render_current_page(self) -> None:
        if self._pdf_doc is None:
            return
        if self._current_page >= self._pdf_doc.pageCount():
            return

        page_size = self._pdf_doc.pagePointSize(self._current_page)
        scale = 2.0
        render_size = (page_size * scale).toSize()
        qimage = self._pdf_doc.render(self._current_page, render_size)
        self._original_pixmap = QPixmap.fromImage(qimage)
        self._pixmap = self._original_pixmap
        self._update_display()

    # ── 翻页 ──

    def _on_prev_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            if self._is_pdf:
                self._render_current_page()
            self._update_nav()
            self._reapply_highlight()

    def _on_next_page(self) -> None:
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            if self._is_pdf:
                self._render_current_page()
            self._update_nav()
            self._reapply_highlight()

    def _update_nav(self) -> None:
        has_pages = self._total_pages > 1
        self._prev_btn.setEnabled(has_pages and self._current_page > 0)
        self._next_btn.setEnabled(
            has_pages and self._current_page < self._total_pages - 1
        )
        self._page_label.setText(f"{self._current_page + 1} / {self._total_pages}")
        self._nav_bar.setVisible(self._total_pages > 0)

    def current_page(self) -> int:
        return self._current_page

    def page_count(self) -> int:
        return self._total_pages

    # ── content_list 和块类型着色 ──

    def set_content_list(self, content_list: list[dict]) -> None:
        """设置 content_list 用于块类型着色覆盖"""
        self._content_list = content_list
        self._update_type_overlay()

    def _update_type_overlay(self) -> None:
        """绘制所有 content_list 块的 bbox 覆盖层"""
        if not self._content_list or self._original_pixmap is None:
            self._overlay.set_type_blocks([])
            return

        current_pixmap = self._image_label.pixmap()
        if not current_pixmap:
            self._overlay.set_type_blocks([])
            return

        dpr = current_pixmap.devicePixelRatio() or 1.0
        disp_w = current_pixmap.width() / dpr
        disp_h = current_pixmap.height() / dpr
        label_w = self._image_label.width()
        label_h = self._image_label.height()
        offset_x = (label_w - disp_w) / 2
        offset_y = (label_h - disp_h) / 2

        overlay_rects = []
        for i, block in enumerate(self._content_list):
            if block.get("type", "") in DISCARDED_BLOCK_TYPES:
                continue
            page_idx = block.get("page_idx", 0)
            if page_idx != self._current_page:
                continue
            bbox = block.get("bbox")
            if not bbox or len(bbox) < 4:
                continue
            block_type = block.get("type", "text")
            fill_color = BLOCK_COLORS.get(block_type, BLOCK_COLORS["text"])
            border_color = BLOCK_BORDER_COLORS.get(block_type, BLOCK_BORDER_COLORS["text"])
            screen_rect = QRectF(
                bbox[0] / BBOX_NORM * disp_w + offset_x,
                bbox[1] / BBOX_NORM * disp_h + offset_y,
                (bbox[2] - bbox[0]) / BBOX_NORM * disp_w,
                (bbox[3] - bbox[1]) / BBOX_NORM * disp_h,
            )
            overlay_rects.append((i, screen_rect, block_type, fill_color, border_color))

        self._overlay.set_type_blocks(overlay_rects)
        self._overlay.setGeometry(self._scroll_area.viewport().rect())

    # ── 高亮 ──

    def highlight_block(self, index: int) -> None:
        """高亮指定块（同时支持置信度模式和块类型模式）"""
        # 块类型模式：查找 content_list 中的 bbox，翻页到对应页
        if self._content_list and 0 <= index < len(self._content_list):
            block = self._content_list[index]
            bbox = block.get("bbox")
            if not bbox or len(bbox) < 4:
                self._overlay.set_hovered(-1)
                return
            page_idx = block.get("page_idx", 0)
            if page_idx != self._current_page:
                self._current_page = page_idx
                if self._is_pdf:
                    self._render_current_page()
                self._update_nav()
                self._update_type_overlay()
            self._highlight_block_index = index
            self._overlay.set_hovered(index)
            return

        # 置信度模式：直接设置 overlay hovered index
        self._overlay.set_hovered(index)

    def clear_highlight(self) -> None:
        """清除悬停高亮（保留永久覆盖层）"""
        self._overlay.set_hovered(-1)
        self._highlight_block_index = -1

    def _reapply_highlight(self) -> None:
        """翻页后重新应用高亮和全块覆盖"""
        if self._content_list:
            self._update_type_overlay()
        else:
            self._update_block_overlay()
        if self._highlight_block_index >= 0:
            self.highlight_block(self._highlight_block_index)

    # ── 清除 ──

    def clear(self) -> None:
        """清除图片"""
        self._pixmap = None
        self._original_pixmap = None
        self._text_blocks = []
        self._block_screen_rects = []
        self._content_list = []
        self._hovered_block = -1
        self._highlight_block_index = -1
        self._overlay.clear()
        self._image_label.clear()
        self._image_label.setText(self._empty_text)
        self._image_label.setStyleSheet(
            "QLabel { background-color: #f0f0f0; border: 2px dashed #ccc; }"
        )
        if self._pdf_doc is not None:
            self._pdf_doc.close()
            self._pdf_doc = None
        self._current_file = ""
        self._total_pages = 0
        self._current_page = 0
        self._update_nav()
        self.image_changed.emit()

    # ── 显示更新 ──

    def _update_display(self) -> None:
        if self._pixmap:
            viewport = self._scroll_area.viewport()
            dpr = self.devicePixelRatio()
            max_w = max(viewport.width() - 20, 200)
            max_h = max(viewport.height() - 20, 200)

            scaled = self._pixmap.scaled(
                int(max_w * dpr), int(max_h * dpr),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            self._image_label.setPixmap(scaled)
            self._image_label.setStyleSheet(
                "QLabel { background-color: #fff; border: 1px solid #ddd; }"
            )
            if self._content_list:
                self._update_type_overlay()
            else:
                self._update_block_overlay()

    def _update_block_overlay(self) -> None:
        """根据当前文本块和图片显示计算置信度模式覆盖矩形"""
        self._overlay.clear()
        self._block_screen_rects.clear()

        if not self._pixmap or not self._text_blocks:
            return

        current_pixmap = self._image_label.pixmap()
        if not current_pixmap:
            return

        dpr = current_pixmap.devicePixelRatio() or 1.0
        disp_w = current_pixmap.width() / dpr
        disp_h = current_pixmap.height() / dpr

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
            sx = x0 / 1000.0 * disp_w + offset_x
            sy = y0 / 1000.0 * disp_h + offset_y
            sw = (x1 - x0) / 1000.0 * disp_w
            sh = (y1 - y0) / 1000.0 * disp_h
            self._block_screen_rects.append((sx, sy, sw, sh))
            overlay_rects.append((sx, sy, sw, sh, block.score, block.text, block.is_manually_edited))

        self._overlay.set_confidence_blocks(overlay_rects)
        self._overlay.setGeometry(self._image_label.rect())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._original_pixmap and not self._original_pixmap.isNull():
            self._update_display()
            self._reapply_highlight()
        self._overlay.setGeometry(self._scroll_area.viewport().rect())
