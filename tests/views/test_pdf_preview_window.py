# tests/views/test_pdf_preview_window.py
"""PdfPreviewWindow / _PreviewCanvas 测试"""

import fitz
import pytest

from vibeocr.views.pdf_preview_window import PdfPreviewWindow, _PreviewCanvas


@pytest.fixture
def canvas(qtbot):
    c = _PreviewCanvas()
    qtbot.addWidget(c)
    return c


@pytest.fixture
def window(qtbot):
    w = PdfPreviewWindow()
    qtbot.addWidget(w)
    return w


class _FakeLayer:
    """模拟 TextLayerInfo。"""

    def __init__(self, bbox, text_preview="hello", color_id=0):
        self.bbox = bbox
        self.text_preview = text_preview
        self.color_id = color_id


class TestPreviewCanvasState:
    def test_set_highlight_layers_stores_params(self, canvas):
        """set_highlight_layers 应存储 render_dpi/page_rect/source。"""
        page_rect = fitz.Rect(0, 0, 612, 792)
        layers = [_FakeLayer((72.0, 72.0, 200.0, 100.0))]
        canvas.set_highlight_layers(
            layers, render_dpi=144, page_rect=page_rect, source="pdf"
        )
        assert canvas._render_dpi == 144
        assert canvas._page_rect == page_rect
        assert canvas._source == "pdf"
        assert canvas._highlight_layers is layers

    def test_paint_event_no_crash_without_pixmap(self, canvas):
        """无 pixmap 时 paintEvent 不应崩溃。"""
        # 触发一次重绘
        canvas.update()
        # 无异常即通过

    def test_paint_event_no_crash_with_pixmap_and_highlights(self, canvas, qapp):
        """有 pixmap 和高亮层时 paintEvent 不应崩溃。"""
        from PySide6.QtGui import QPixmap

        pm = QPixmap(100, 100)
        pm.fill()
        canvas.set_pixmap(pm)
        page_rect = fitz.Rect(0, 0, 612, 792)
        canvas.set_highlight_layers(
            [_FakeLayer((72.0, 72.0, 200.0, 100.0))], render_dpi=72, page_rect=page_rect
        )
        # 强制同步重绘以触发 paintEvent
        canvas.repaint()
        # 无异常即通过


class TestPdfPreviewWindowPublicApi:
    def test_set_highlight_forwards_to_canvas(self, window):
        """set_highlight 应转发参数到 _canvas。"""
        from PySide6.QtGui import QPixmap

        pm = QPixmap(100, 100)
        pm.fill()
        page_rect = fitz.Rect(0, 0, 612, 792)
        layers = [_FakeLayer((0.0, 0.0, 100.0, 100.0))]

        window.set_highlight(
            pm, layers, render_dpi=200, page_rect=page_rect, source="pdf"
        )

        assert window._canvas._pixmap is not None
        assert window._canvas._render_dpi == 200
        assert window._canvas._page_rect == page_rect
        assert window._canvas._highlight_layers is layers
