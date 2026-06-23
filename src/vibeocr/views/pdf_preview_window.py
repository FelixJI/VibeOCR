"""PDF 页面独立预览窗口

双击缩略图时弹出，支持缩放/平移浏览。
PreviewCanvas 支持两种高亮数据源：
  1. OCR 原始块（set_ocr_blocks）：归一化 [0,1000] bbox，与单次识别预览同款逻辑，
     支持置信度着色、双击内联编辑。这是已 OCR 页面的主数据源。
  2. text_layers（set_highlight_layers）：PDF points 坐标，旧接口，
     仅用于未 OCR 页面（扫描件检测）的兼容回退。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    import fitz

logger = logging.getLogger(__name__)

# 置信度着色（与 PreviewWidget 保持一致，确保两条预览路径视觉统一）
LOW_CONFIDENCE_THRESHOLD = 0.80
HIGH_CONF_FILL = QColor(76, 175, 80, 40)
HIGH_CONF_BORDER = QColor(76, 175, 80, 160)
LOW_CONF_FILL = QColor(244, 67, 54, 60)
LOW_CONF_BORDER = QColor(244, 67, 54, 200)
EDIT_FILL = QColor(255, 193, 7, 40)
EDIT_BORDER = QColor(255, 152, 0, 200)

BBOX_NORM = 1000.0


class PreviewCanvas(QWidget):
    """可缩放/平移的画布，支持 OCR 块渲染与双击改字。"""

    # 双击编辑完成信号：(page_index, block_index, new_text)
    block_text_edited = Signal(int, int, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._scale = 1.0

        # 旧数据源：text_layers（PDF points 坐标）
        self._highlight_layers: list = []
        self._render_dpi: int = 150
        self._page_rect: fitz.Rect | None = None
        self._source: str = "pdf"

        # 新数据源：OCR 原始块（归一化 [0,1000] bbox）
        self._ocr_blocks: list | None = None
        self._ocr_page_index: int = -1
        self._ocr_block_rects: list[tuple[float, float, float, float]] = []

        # 内联编辑器
        self._inline_editor: QLineEdit | None = None
        self._editing_index: int = -1

        self.setMouseTracking(True)

    # ---- pixmap / scale ----

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._scale = 1.0
        self._update_size()
        # pixmap 变了，重算 OCR 块屏幕坐标
        if self._ocr_blocks is not None:
            self._compute_ocr_block_rects()
        self.update()

    def _update_size(self) -> None:
        if self._pixmap is None:
            return
        w = int(self._pixmap.width() * self._scale)
        h = int(self._pixmap.height() * self._scale)
        self.setFixedSize(w, h)

    # ---- OCR 原始块（新主数据源）----

    def set_ocr_blocks(
        self,
        page_index: int,
        blocks: list,
        pixmap: QPixmap,
    ) -> None:
        """设置 OCR 原始块用于渲染 + 双击编辑。

        bbox 为归一化 [0,1000] 坐标（与单次识别 PreviewWidget 同款）。
        设置后优先于 text_layers 渲染。

        Args:
            page_index: 该 pixmap 对应的页码（编辑信号回传用）。
            blocks: TextBlock 列表。
            pixmap: 页面渲染图。
        """
        self._ocr_blocks = blocks
        self._ocr_page_index = page_index
        self._pixmap = pixmap
        self._scale = 1.0
        self._update_size()
        self._compute_ocr_block_rects()
        self.update()

    def _compute_ocr_block_rects(self) -> None:
        """将 OCR 块归一化 bbox 映射到 pixmap 像素坐标（× scale）。"""
        self._ocr_block_rects.clear()
        if self._pixmap is None or not self._ocr_blocks:
            return
        pw = self._pixmap.width()
        ph = self._pixmap.height()
        for block in self._ocr_blocks:
            if block.bbox is None:
                self._ocr_block_rects.append((0.0, 0.0, 0.0, 0.0))
                continue
            x0, y0, x1, y1 = block.bbox
            sx = x0 / BBOX_NORM * pw * self._scale
            sy = y0 / BBOX_NORM * ph * self._scale
            sw = (x1 - x0) / BBOX_NORM * pw * self._scale
            sh = (y1 - y0) / BBOX_NORM * ph * self._scale
            self._ocr_block_rects.append((sx, sy, sw, sh))

    def _clear_ocr_blocks(self) -> None:
        self._ocr_blocks = None
        self._ocr_page_index = -1
        self._ocr_block_rects.clear()

    # ---- 旧数据源：text_layers（兼容回退）----

    def set_highlight_layers(
        self,
        layers: list,
        render_dpi: int = 150,
        page_rect: fitz.Rect | None = None,
        source: str = "pdf",
    ) -> None:
        self._clear_ocr_blocks()  # 切换数据源时清除 OCR 块
        self._highlight_layers = layers
        self._render_dpi = render_dpi
        self._page_rect = page_rect
        self._source = source
        self.update()

    # ---- 渲染 ----

    def paintEvent(self, event) -> None:
        if self._pixmap is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(self._scale, self._scale)
        painter.drawPixmap(0, 0, self._pixmap)

        # OCR 块优先渲染
        if self._ocr_blocks is not None:
            self._paint_ocr_blocks(painter)
        elif self._highlight_layers and self._page_rect is not None:
            self._paint_text_layers(painter)
        painter.end()

    def _paint_ocr_blocks(self, painter: QPainter) -> None:
        """渲染 OCR 块：置信度着色（与 PreviewWidget 统一）。"""
        for i, block in enumerate(self._ocr_blocks):
            if i >= len(self._ocr_block_rects):
                break
            sx, sy, sw, sh = self._ocr_block_rects[i]
            if sw <= 0 or sh <= 0:
                continue
            rect = QRectF(sx, sy, sw, sh)

            if getattr(block, "is_manually_edited", False):
                fill, border = EDIT_FILL, EDIT_BORDER
            elif block.score < LOW_CONFIDENCE_THRESHOLD:
                fill, border = LOW_CONF_FILL, LOW_CONF_BORDER
            else:
                fill, border = HIGH_CONF_FILL, HIGH_CONF_BORDER

            painter.fillRect(rect, fill)
            painter.setPen(QPen(border, 2))
            painter.drawRect(rect)

    def _paint_text_layers(self, painter: QPainter) -> None:
        """渲染旧 text_layers（PDF points 坐标）。"""
        from vibeocr.services.pdf_service import PdfService

        for layer in self._highlight_layers:
            bbox = layer.bbox
            color_idx = layer.color_id % 8
            palette = [
                (0, 120, 215, 80), (0, 180, 80, 80), (230, 140, 0, 80),
                (180, 0, 180, 80), (0, 180, 180, 80), (215, 80, 80, 80),
                (140, 100, 0, 80), (80, 80, 215, 80),
            ]
            r, g, b, a = palette[color_idx]
            pixel_bbox = PdfService.bbox_to_pixel(
                bbox, self._page_rect, self._render_dpi, source=self._source
            )
            x0, y0, x1, y1 = pixel_bbox
            painter.setBrush(QColor(r, g, b, a))
            painter.setPen(QPen(QColor(r, g, b, 180), 1))
            painter.drawRect(int(x0), int(y0), int(x1 - x0), int(y1 - y0))

    # ---- 鼠标交互 ----

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._ocr_blocks is not None and self._pixmap is not None:
            self._handle_ocr_hover(event)
        elif self._highlight_layers and self._page_rect is not None:
            self._handle_layer_hover(event)

    def _handle_ocr_hover(self, event: QMouseEvent) -> None:
        mx = event.position().x()
        my = event.position().y()
        for i, (bx, by, bw, bh) in enumerate(self._ocr_block_rects):
            if bw <= 0 or bh <= 0:
                continue
            if bx <= mx <= bx + bw and by <= my <= by + bh:
                block = self._ocr_blocks[i]
                tip = f"{block.text[:50]}\n置信度: {block.score:.1%}"
                if getattr(block, "is_manually_edited", False):
                    tip += "\n[手动修改]"
                self.setToolTip(tip)
                return
        self.setToolTip("")

    def _handle_layer_hover(self, event: QMouseEvent) -> None:
        from vibeocr.services.pdf_service import PdfService

        mx = event.position().x() / self._scale
        my = event.position().y() / self._scale
        for layer in self._highlight_layers:
            pixel_bbox = PdfService.bbox_to_pixel(
                layer.bbox, self._page_rect, self._render_dpi, source=self._source
            )
            x0, y0, x1, y1 = pixel_bbox
            if x0 <= mx <= x1 and y0 <= my <= y1:
                self.setToolTip(layer.text_preview)
                return
        self.setToolTip("")

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击 OCR 块触发内联编辑。"""
        if self._ocr_blocks is None or self._pixmap is None:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        idx = self._hit_test_ocr_block(event.position().x(), event.position().y())
        if idx >= 0:
            self._start_inline_edit(idx)

    def _hit_test_ocr_block(self, x: float, y: float) -> int:
        for i, (bx, by, bw, bh) in enumerate(self._ocr_block_rects):
            if bw <= 0 or bh <= 0:
                continue
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return i
        return -1

    # ---- 内联编辑 ----

    def _ensure_inline_editor(self) -> QLineEdit:
        if self._inline_editor is None:
            self._inline_editor = QLineEdit(self)
            self._inline_editor.editingFinished.connect(self._on_inline_edit_finished)
            self._inline_editor.installEventFilter(self)
        return self._inline_editor

    def _start_inline_edit(self, index: int) -> None:
        if index < 0 or index >= len(self._ocr_block_rects):
            return
        editor = self._ensure_inline_editor()
        bx, by, bw, bh = self._ocr_block_rects[index]
        block = self._ocr_blocks[index]
        self._editing_index = index
        editor.setText(block.text)
        editor.setGeometry(int(bx), int(by), max(int(bw), 120), max(int(bh) + 4, 28))
        editor.show()
        editor.setFocus()
        editor.selectAll()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._inline_editor and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._cancel_inline_edit()
                return True
        return super().eventFilter(obj, event)

    def _on_inline_edit_finished(self) -> None:
        if self._editing_index < 0:
            return
        index = self._editing_index
        new_text = self._inline_editor.text()
        self._inline_editor.hide()
        self._editing_index = -1
        self._apply_block_edit(index, new_text)

    def _apply_block_edit(self, index: int, new_text: str) -> None:
        """应用块编辑：文字变化时 emit 信号。"""
        if self._ocr_blocks is None or index >= len(self._ocr_blocks):
            return
        old_text = self._ocr_blocks[index].text
        if new_text != old_text:
            self.block_text_edited.emit(self._ocr_page_index, index, new_text)

    def _cancel_inline_edit(self) -> None:
        if self._inline_editor is not None:
            self._inline_editor.hide()
        self._editing_index = -1

    # ---- 缩放 ----

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._pixmap is None:
            return
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 0.9
        new_scale = self._scale * factor
        if 0.2 <= new_scale <= 5.0:
            self._scale = new_scale
            self._update_size()
            self._compute_ocr_block_rects()
            self.update()


# 向后兼容别名：旧代码仍可导入 _PreviewCanvas。
_PreviewCanvas = PreviewCanvas


class PdfPreviewWindow(QWidget):
    """PDF 页面预览窗口。"""

    # 转发画布的编辑信号
    block_text_edited = Signal(int, int, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PDF 页面预览")
        self.resize(800, 1000)

        self._canvas = PreviewCanvas()
        self._canvas.block_text_edited.connect(self.block_text_edited)

        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    def set_page_pixmap(self, pixmap: QPixmap) -> None:
        self._canvas.set_pixmap(pixmap)

    def set_highlight(
        self,
        pixmap: QPixmap,
        layers: list,
        render_dpi: int = 150,
        page_rect: fitz.Rect | None = None,
        source: str = "pdf",
    ) -> None:
        """设置预览页面与高亮层（公共 API，替代直接访问 _canvas）。"""
        self._canvas.set_pixmap(pixmap)
        self._canvas.set_highlight_layers(
            layers, render_dpi=render_dpi, page_rect=page_rect, source=source
        )

    def set_ocr_blocks(
        self, page_index: int, blocks: list, pixmap: QPixmap
    ) -> None:
        """设置 OCR 原始块预览（公共 API）。"""
        self._canvas.set_ocr_blocks(page_index, blocks, pixmap)
