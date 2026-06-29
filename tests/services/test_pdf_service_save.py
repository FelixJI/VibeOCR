"""save_with_rewrite: rewrite + 按结构改动分流落盘。"""

import fitz
import pytest

from vibeocr.models.ocr_result import OCRResult, TextBlock
from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.services.pdf_service import PdfService, SaveResult


def _make_scanned_pdf(path):
    import numpy as np
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    img = np.ones((792, 612, 3), dtype=np.uint8) * 240
    cs = fitz.Colorspace(fitz.CS_RGB)
    pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
    page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
    doc.save(str(path))
    doc.close()
    return path


class TestSaveWithRewrite:
    def test_resets_is_modified_and_structural_flag(self, tmp_path):
        path = tmp_path / "scan.pdf"
        _make_scanned_pdf(path)
        doc = fitz.open(str(path))
        pdf_doc = PdfDocument(file_path=str(path))
        info = PdfPageInfo(page_index=0)
        pdf_doc.pages = [info]

        # 模拟 OCR 注入文字块
        result = OCRResult(
            raw_text="Hello",
            text_blocks=[TextBlock(text="Hello", score=0.9, bbox=(50, 50, 300, 100))],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)

        pdf_doc.is_modified = True
        # 纯文字层编辑，无结构改动
        save_result = PdfService.save_with_rewrite(doc, pdf_doc, path=None)
        assert isinstance(save_result, SaveResult)
        assert pdf_doc.is_modified is False
        assert pdf_doc.has_structural_change is False
        doc.close()

    def test_save_as_writes_new_file(self, tmp_path):
        path = tmp_path / "src.pdf"
        _make_scanned_pdf(path)
        doc = fitz.open(str(path))
        pdf_doc = PdfDocument(file_path=str(path))
        pdf_doc.pages = [PdfPageInfo(page_index=0)]

        dest = tmp_path / "out.pdf"
        PdfService.save_with_rewrite(doc, pdf_doc, path=str(dest))
        assert dest.exists()
        doc.close()
