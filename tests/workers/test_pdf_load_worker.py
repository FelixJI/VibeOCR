"""Tests for PdfLoadWorker."""

import fitz
import pytest
from PySide6.QtCore import Qt

from vibeocr.models.pdf_session import PdfSession
from vibeocr.services.pdf_service import PdfService
from vibeocr.workers.pdf_load_worker import PdfLoadWorker


@pytest.fixture
def three_page_pdf(tmp_path):
    path = tmp_path / "test.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Page {i + 1}", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


class TestPdfLoadWorker:
    def test_emits_page_ready_for_each_page(self, three_page_pdf, qapp, wait_worker):
        doc, pdf_doc = PdfService.open_doc(str(three_page_pdf))
        session = PdfSession(
            file_path=str(three_page_pdf), doc=doc, pdf_document=pdf_doc
        )

        loaded_pages: list[int] = []

        worker = PdfLoadWorker(
            session_id=session.file_path,
            doc=session.doc,
            pdf_document=session.pdf_document,
            loaded_pages=session.loaded_pages,
            doc_lock=session.doc_lock,
            thumbnail_dpi=96,
        )
        worker.page_ready.connect(
            lambda i, info, pm: loaded_pages.append(i),
            Qt.ConnectionType.DirectConnection,
        )

        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert len(loaded_pages) == 3
        assert loaded_pages == [0, 1, 2]
        doc.close()

    def test_skips_already_loaded_pages(self, three_page_pdf, qapp, wait_worker):
        doc, pdf_doc = PdfService.open_doc(str(three_page_pdf))
        session = PdfSession(
            file_path=str(three_page_pdf), doc=doc, pdf_document=pdf_doc
        )
        session.loaded_pages.add(1)  # page 1 already loaded

        loaded_pages: list[int] = []

        worker = PdfLoadWorker(
            session_id=session.file_path,
            doc=session.doc,
            pdf_document=session.pdf_document,
            loaded_pages=session.loaded_pages,
            doc_lock=session.doc_lock,
            thumbnail_dpi=96,
        )
        worker.page_ready.connect(
            lambda i, info, pm: loaded_pages.append(i),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert loaded_pages == [0, 2]
        doc.close()

    def test_cancel_stops_early(self, three_page_pdf, qapp, wait_worker):
        doc, pdf_doc = PdfService.open_doc(str(three_page_pdf))
        session = PdfSession(
            file_path=str(three_page_pdf), doc=doc, pdf_document=pdf_doc
        )

        loaded_pages: list[int] = []

        def on_first_page(i, info, pm):
            loaded_pages.append(i)
            worker.cancel()

        worker = PdfLoadWorker(
            session_id=session.file_path,
            doc=session.doc,
            pdf_document=session.pdf_document,
            loaded_pages=session.loaded_pages,
            doc_lock=session.doc_lock,
            thumbnail_dpi=96,
        )
        worker.page_ready.connect(
            on_first_page,
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert len(loaded_pages) <= 1
        doc.close()

    def test_page_ready_contains_text_layer_info(
        self, three_page_pdf, qapp, wait_worker
    ):
        doc, pdf_doc = PdfService.open_doc(str(three_page_pdf))
        session = PdfSession(
            file_path=str(three_page_pdf), doc=doc, pdf_document=pdf_doc
        )

        page_infos: list = []

        worker = PdfLoadWorker(
            session_id=session.file_path,
            doc=session.doc,
            pdf_document=session.pdf_document,
            loaded_pages=session.loaded_pages,
            doc_lock=session.doc_lock,
            thumbnail_dpi=96,
        )
        worker.page_ready.connect(
            lambda i, info, pm: page_infos.append((i, info)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert len(page_infos) == 3
        for idx, info in page_infos:
            assert info.page_index == idx
            assert isinstance(info.has_text_layer, bool)
            if info.has_text_layer:
                assert len(info.text_layers) > 0
        doc.close()
