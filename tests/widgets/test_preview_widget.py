"""PreviewWidget 统一预览组件测试"""

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QScrollArea

from vibeocr.models.ocr_result import TextBlock
from vibeocr.widgets.preview_widget import PreviewWidget


class TestPreviewWidgetBasic:
    def test_creation(self, qapp):
        widget = PreviewWidget()
        assert widget._pixmap is None

    def test_set_pixmap(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        assert widget._pixmap is not None

    def test_clear(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.clear()
        assert widget._pixmap is None
        assert widget._text_blocks == []

    def test_custom_empty_text(self, qapp):
        widget = PreviewWidget(empty_text="自定义文案")
        assert widget._empty_text == "自定义文案"


class TestPreviewWidgetTextBlocks:
    def test_set_text_blocks(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        blocks = [
            TextBlock(text="Hello", score=0.95, bbox=(10, 20, 200, 50)),
            TextBlock(text="World", score=0.60, bbox=(10, 60, 200, 90)),
        ]
        widget.set_text_blocks(blocks)
        assert widget._text_blocks == blocks

    def test_set_content_list(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        content = [
            {"type": "text", "text": "Hello", "bbox": [10, 20, 200, 50]},
            {"type": "table", "text": "data", "bbox": [10, 60, 200, 90]},
        ]
        widget.set_content_list(content)
        assert widget._content_list == content


class TestPreviewWidgetFileLoading:
    def test_load_image_file(self, qapp, temp_image_file):
        widget = PreviewWidget()
        widget.load_file(str(temp_image_file))
        assert widget._original_pixmap is not None
        assert widget._total_pages == 1

    def test_has_scroll_area(self, qapp):
        widget = PreviewWidget()
        scroll_areas = widget.findChildren(QScrollArea)
        assert len(scroll_areas) >= 1


class TestPreviewWidgetHighlights:
    def test_highlight_block_no_crash(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.highlight_block(0)
        widget.highlight_block(-1)

    def test_clear_highlight(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        widget.clear_highlight()
        assert widget._highlight_block_index == -1

    def test_highlight_block_with_content_list(self, qapp, sample_pixmap):
        widget = PreviewWidget()
        widget.set_pixmap(sample_pixmap)
        content = [
            {"type": "text", "text": "Hello", "bbox": [10, 20, 200, 50]},
        ]
        widget.set_content_list(content)
        widget.highlight_block(0)
        assert widget._highlight_block_index == 0
