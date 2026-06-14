"""PDF 页面独立预览窗口

双击缩略图时弹出，支持缩放/平移浏览。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

if TYPE_CHECKING:
    import fitz

logger = logging.getLogger(__name__)


class _PreviewCanvas(QWidget):
    """可缩放/平移的画布。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._scale = 1.0
        self._highlight_layers: list = []
        self._render_dpi: int = 150
        self._page_rect: fitz.Rect | None = None
        self._source: str = "pdf"  # "pdf" or "normalized"
        self.setMouseTracking(True)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._scale = 1.0
        self._update_size()
        self.update()

    def set_highlight_layers(
        self,
        layers: list,
        render_dpi: int = 150,
        page_rect: fitz.Rect | None = None,
        source: str = "pdf",
    ) -> None:
        self._highlight_layers = layers
        self._render_dpi = render_dpi
        self._page_rect = page_rect
        self._source = source
        self.update()

    def _update_size(self) -> None:
        if self._pixmap is None:
            return
        w = int(self._pixmap.width() * self._scale)
        h = int(self._pixmap.height() * self._scale)
        self.setFixedSize(w, h)

    def paintEvent(self, event) -> None:
        if self._pixmap is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(self._scale, self._scale)
        painter.drawPixmap(0, 0, self._pixmap)

        if not self._highlight_layers or self._page_rect is None:
            painter.end()
            return

        colors = [
            (0, 120, 215, 80),
            (0, 180, 80, 80),
            (230, 140, 0, 80),
            (180, 0, 180, 80),
            (0, 180, 180, 80),
            (215, 80, 80, 80),
            (140, 100, 0, 80),
            (80, 80, 215, 80),
        ]

        from vibeocr.services.pdf_service import PdfService

        for layer in self._highlight_layers:
            bbox = layer.bbox
            color_idx = layer.color_id % len(colors)
            r, g, b, a = colors[color_idx]

            # 使用 PdfService.bbox_to_pixel 转换坐标
            pixel_bbox = PdfService.bbox_to_pixel(
                bbox, self._page_rect, self._render_dpi, source=self._source
            )
            x0, y0, x1, y1 = pixel_bbox

            painter.setBrush(QColor(r, g, b, a))
            painter.setPen(QPen(QColor(r, g, b, 180), 1))
            painter.drawRect(
                int(x0),
                int(y0),
                int(x1 - x0),
                int(y1 - y0),
            )
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """鼠标悬停时显示文字块内容 tooltip。"""
        if not self._highlight_layers or self._page_rect is None or self._pixmap is None:
            self.setToolTip("")
            return

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

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._pixmap is None:
            return
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 0.9
        new_scale = self._scale * factor
        if 0.2 <= new_scale <= 5.0:
            self._scale = new_scale
            self._update_size()
            self.update()


class PdfPreviewWindow(QWidget):
    """PDF 页面预览窗口。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PDF 页面预览")
        self.resize(800, 1000)

        self._canvas = _PreviewCanvas()

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
