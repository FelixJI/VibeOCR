"""PDF 页面独立预览窗口

双击缩略图时弹出，支持缩放/平移浏览。
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)


class _PreviewCanvas(QWidget):
    """可缩放/平移的画布。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._scale = 1.0
        self._highlight_layers: list = []

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._scale = 1.0
        self._update_size()
        self.update()

    def set_highlight_layers(self, layers: list) -> None:
        self._highlight_layers = layers
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
        for layer in self._highlight_layers:
            bbox = layer.bbox
            color_idx = layer.color_id % len(colors)
            r, g, b, a = colors[color_idx]
            from PySide6.QtGui import QColor, QPen
            painter.setBrush(QColor(r, g, b, a))
            painter.setPen(QPen(QColor(r, g, b, 180), 1))
            painter.drawRect(
                int(bbox[0] * self._scale),
                int(bbox[1] * self._scale),
                int((bbox[2] - bbox[0]) * self._scale),
                int((bbox[3] - bbox[1]) * self._scale),
            )
        painter.end()

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
