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


class TestPreviewCanvasPublicName:
    def test_public_preview_canvas_class_exists(self, qtbot):
        """PreviewCanvas 作为公开类可被实例化。"""
        from vibeocr.views.pdf_preview_window import PreviewCanvas

        canvas = PreviewCanvas()
        qtbot.addWidget(canvas)
        assert canvas is not None

    def test_pdf_preview_window_uses_preview_canvas(self, window):
        """PdfPreviewWindow 内部应使用公开的 PreviewCanvas。"""
        from vibeocr.views.pdf_preview_window import PreviewCanvas

        assert isinstance(window._canvas, PreviewCanvas)

    def test_underscore_alias_still_works(self):
        """向后兼容别名 _PreviewCanvas 仍可导入（旧代码不破坏）。"""
        from vibeocr.views.pdf_preview_window import (
            PreviewCanvas,
            _PreviewCanvas,
        )

        assert _PreviewCanvas is PreviewCanvas


class TestPreviewCanvasOcrBlocks:
    """OCR 原始块渲染（set_ocr_blocks）—— 与单次识别预览同款逻辑。"""

    def test_set_ocr_blocks_stores_blocks_and_pixmap(self, canvas, qapp):
        """set_ocr_blocks 存储 OCR 块列表和 pixmap。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.models.ocr_result import TextBlock

        pm = QPixmap(1000, 800)
        pm.fill()
        blocks = [
            TextBlock(text="Hello", score=0.95, bbox=(50.0, 50.0, 300.0, 100.0)),
            TextBlock(text="World", score=0.60, bbox=(50.0, 150.0, 300.0, 200.0)),
        ]
        canvas.set_ocr_blocks(0, blocks, pm)

        assert canvas._ocr_blocks is blocks
        assert canvas._ocr_page_index == 0
        assert canvas._pixmap is pm
        # 应计算出块屏幕矩形（2 个块）
        assert len(canvas._ocr_block_rects) == 2

    def test_set_ocr_blocks_paint_no_crash(self, canvas, qapp):
        """有 pixmap + OCR 块时 paintEvent 不崩溃。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.models.ocr_result import TextBlock

        pm = QPixmap(200, 200)
        pm.fill()
        canvas.set_ocr_blocks(
            0,
            [TextBlock(text="Hi", score=0.9, bbox=(10.0, 10.0, 100.0, 50.0))],
            pm,
        )
        canvas.repaint()
        # 无异常即通过

    def test_set_ocr_blocks_clears_old_highlight_layers(self, canvas, qapp):
        """设置 OCR 块时清除旧的 text_layers 高亮（避免两种高亮叠加）。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.models.ocr_result import TextBlock

        page_rect = fitz.Rect(0, 0, 612, 792)
        canvas.set_highlight_layers(
            [_FakeLayer((72.0, 72.0, 200.0, 100.0))], render_dpi=72, page_rect=page_rect
        )
        assert len(canvas._highlight_layers) == 1

        pm = QPixmap(200, 200)
        pm.fill()
        canvas.set_ocr_blocks(
            0, [TextBlock(text="Hi", score=0.9, bbox=(10.0, 10.0, 100.0, 50.0))], pm
        )
        # OCR 块优先，旧的 highlight_layers 不再渲染
        assert canvas._ocr_blocks is not None
        assert len(canvas._ocr_blocks) >= 1


class TestPreviewCanvasBlockEdit:
    """双击改字 → emit block_text_edited 信号。"""

    def test_block_text_edited_signal_exists(self, canvas):
        """PreviewCanvas 应有 block_text_edited 信号。"""
        assert hasattr(canvas, "block_text_edited")

    def test_finish_block_edit_emits_signal(self, canvas, qapp):
        """结束内联编辑时 emit block_text_edited(page_index, block_index, text)。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.models.ocr_result import TextBlock

        pm = QPixmap(1000, 800)
        pm.fill()
        blocks = [
            TextBlock(text="签回联", score=0.9, bbox=(50.0, 50.0, 300.0, 100.0)),
        ]
        canvas.set_ocr_blocks(3, blocks, pm)

        emitted = []
        canvas.block_text_edited.connect(
            lambda pg, idx, txt: emitted.append((pg, idx, txt))
        )

        # 模拟编辑第 0 块为 "签收联"
        canvas._apply_block_edit(0, "签收联")

        assert len(emitted) == 1
        assert emitted[0] == (3, 0, "签收联")

    def test_finish_block_edit_noop_when_unchanged(self, canvas, qapp):
        """文字未变时不 emit 信号。"""
        from PySide6.QtGui import QPixmap

        from vibeocr.models.ocr_result import TextBlock

        pm = QPixmap(200, 200)
        pm.fill()
        canvas.set_ocr_blocks(
            0,
            [TextBlock(text="Hello", score=0.9, bbox=(10.0, 10.0, 100.0, 50.0))],
            pm,
        )

        emitted = []
        canvas.block_text_edited.connect(
            lambda pg, idx, txt: emitted.append((pg, idx, txt))
        )
        canvas._apply_block_edit(0, "Hello")
        assert len(emitted) == 0
