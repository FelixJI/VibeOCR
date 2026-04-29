"""Tests for InlineEditCanvas."""

from PySide6.QtGui import QPixmap
from vibeocr.widgets.inline_edit_canvas import InlineEditCanvas


class TestInlineEditCanvas:
    def test_initial_state(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas._background_pixmap is None

    def test_set_background(self, qapp):
        canvas = InlineEditCanvas()
        pixmap = QPixmap(200, 100)
        pixmap.fill()
        canvas.set_background(pixmap)
        assert canvas._background_pixmap is not None
        assert canvas._background_item is not None

    def test_export_image(self, qapp):
        canvas = InlineEditCanvas()
        pixmap = QPixmap(200, 100)
        pixmap.fill()
        canvas.set_background(pixmap)
        exported = canvas.export_image()
        assert not exported.isNull()

    def test_export_without_background(self, qapp):
        canvas = InlineEditCanvas()
        exported = canvas.export_image()
        assert exported.isNull()

    def test_undo_stack_exists(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas.undo_stack is not None
