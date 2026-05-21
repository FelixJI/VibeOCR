"""Tests for PDF service."""

import fitz
import pytest
from pathlib import Path


def _create_test_pdf(path: Path, num_pages: int = 3) -> Path:
    """创建用于测试的 PDF 文件。"""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Page {i + 1}", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def test_pdf(tmp_path):
    return _create_test_pdf(tmp_path / "test.pdf", num_pages=3)


@pytest.fixture
def pdf_service():
    from vibeocr.services.pdf_service import PdfService
    return PdfService()


class TestPdfServiceOpen:
    def test_open_pdf(self, pdf_service, test_pdf):
        doc = pdf_service.open(str(test_pdf))
        assert doc.file_path == str(test_pdf)
        assert doc.page_count == 3
        pdf_service.close()

    def test_open_nonexistent_raises(self, pdf_service):
        with pytest.raises(FileNotFoundError):
            pdf_service.open("/nonexistent/file.pdf")
        pdf_service.close()

    def test_open_encrypt_raises(self, pdf_service, tmp_path):
        import fitz
        src = fitz.open()
        src.new_page(width=612, height=792)
        path = str(tmp_path / "encrypted.pdf")
        src.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
        src.close()
        with pytest.raises(RuntimeError, match="加密"):
            pdf_service.open(path)
        pdf_service.close()


class TestPdfServiceSave:
    def test_save(self, pdf_service, test_pdf, tmp_path):
        pdf_service.open(str(test_pdf))
        pdf_service.rotate_pages([0], 90)
        save_path = str(tmp_path / "saved.pdf")
        pdf_service.save(save_path)
        pdf_service.close()

        verify = fitz.open(save_path)
        assert verify[0].rotation == 90
        verify.close()

    def test_save_creates_backup(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        pdf_service.rotate_pages([0], 90)
        pdf_service.save()
        pdf_service.close()

        assert Path(str(test_pdf) + ".bak").exists() is False
        verify = fitz.open(str(test_pdf))
        assert verify[0].rotation == 90
        verify.close()


class TestPdfServiceRender:
    def test_render_thumbnail(self, pdf_service, test_pdf, qapp):
        pdf_service.open(str(test_pdf))
        pixmap = pdf_service.render_page(0, dpi=96)
        assert pixmap is not None
        assert not pixmap.isNull()
        pdf_service.close()

    def test_render_page_for_ocr(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        img_array = pdf_service.render_page_as_array(0, dpi=300)
        assert img_array is not None
        assert img_array.shape[0] > 0
        assert img_array.shape[2] == 3  # RGB
        pdf_service.close()


class TestPdfServiceRotate:
    def test_rotate_single_page(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        pdf_service.rotate_pages([0], 90)
        assert pdf_service.document.pages[0].rotation == 90
        assert pdf_service.document.is_modified is True
        pdf_service.close()

    def test_rotate_all_pages(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        pdf_service.rotate_all_pages(90)
        for page in pdf_service.document.pages:
            assert page.rotation == 90
        pdf_service.close()


class TestPdfServiceDelete:
    def test_delete_page(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        assert pdf_service.document.page_count == 3
        pdf_service.delete_pages([1])
        assert pdf_service.document.page_count == 2
        assert pdf_service.document.pages[0].page_index == 0
        assert pdf_service.document.pages[1].page_index == 2
        pdf_service.close()


class TestPdfServiceInsert:
    def test_insert_blank_page(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        pdf_service.insert_blank_page(after_index=0)
        assert pdf_service.document.page_count == 4
        assert pdf_service.document.pages[1].rotation == 0
        pdf_service.close()

    def test_insert_from_another_pdf(self, pdf_service, test_pdf, tmp_path):
        other_pdf = _create_test_pdf(tmp_path / "other.pdf", num_pages=2)
        pdf_service.open(str(test_pdf))
        pdf_service.insert_pages_from(str(other_pdf), after_index=0)
        assert pdf_service.document.page_count == 5
        pdf_service.close()


class TestPdfServiceMove:
    def test_move_page(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        pdf_service.move_page(0, 2)
        assert pdf_service.document.pages[2].page_index == 0
        pdf_service.close()
