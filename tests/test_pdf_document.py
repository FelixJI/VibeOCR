"""Tests for PDF document data models."""

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo, TextLayerInfo


class TestTextLayerInfo:
    def test_create(self):
        layer = TextLayerInfo(
            index=0,
            text_preview="Hello World",
            char_count=11,
            bbox=(10.0, 20.0, 200.0, 40.0),
            color_id=0,
        )
        assert layer.index == 0
        assert layer.text_preview == "Hello World"
        assert layer.char_count == 11
        assert layer.bbox == (10.0, 20.0, 200.0, 40.0)
        assert layer.color_id == 0


class TestPdfPageInfo:
    def test_defaults(self):
        info = PdfPageInfo(page_index=0)
        assert info.page_index == 0
        assert info.rotation == 0
        assert info.has_text_layer is False
        assert info.text_layers == []
        assert info.is_scanned is False
        assert info.thumbnail is None

    def test_with_text_layers(self):
        layer = TextLayerInfo(
            index=0, text_preview="abc", char_count=3,
            bbox=(0, 0, 100, 20), color_id=0,
        )
        info = PdfPageInfo(page_index=1, has_text_layer=True, text_layers=[layer])
        assert info.has_text_layer is True
        assert len(info.text_layers) == 1


class TestPdfDocument:
    def test_defaults(self):
        doc = PdfDocument()
        assert doc.file_path is None
        assert doc.pages == []
        assert doc.is_modified is False
        assert doc.render_dpi == 300
        assert doc.thumbnail_dpi == 96

    def test_get_page(self):
        p0 = PdfPageInfo(page_index=0)
        p1 = PdfPageInfo(page_index=1)
        doc = PdfDocument(pages=[p0, p1])
        assert doc.get_page(0) is p0
        assert doc.get_page(1) is p1
        assert doc.get_page(2) is None

    def test_page_count(self):
        doc = PdfDocument(pages=[PdfPageInfo(page_index=i) for i in range(5)])
        assert doc.page_count == 5
