"""Tests for PdfLoadWorker — 只做文字层检测，不再渲染缩略图。"""

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
        )
        worker.page_ready.connect(
            lambda i, info: loaded_pages.append(i),
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
        )
        worker.page_ready.connect(
            lambda i, info: loaded_pages.append(i),
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

        def on_first_page(i, info):
            loaded_pages.append(i)
            worker.cancel()

        worker = PdfLoadWorker(
            session_id=session.file_path,
            doc=session.doc,
            pdf_document=session.pdf_document,
            loaded_pages=session.loaded_pages,
            doc_lock=session.doc_lock,
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
        )
        worker.page_ready.connect(
            lambda i, info: page_infos.append((i, info)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert len(page_infos) == 3
        for idx, info in page_infos:
            assert info.page_index == idx
            assert isinstance(info.has_text_layer, bool)
            # text_layers 延迟加载：load worker 只判 has_text_layer（轻量
            # get_text("text") ~3ms），text_layers 详情留空，预览时按需
            # detect_text_layers 填充（避免每页 ~180ms dict 持 GIL 卡顿）。
            assert info.text_layers == []
        doc.close()

    def test_does_not_render_thumbnail(self, three_page_pdf, qapp, wait_worker):
        """文字层 worker 不应渲染缩略图（由按需 worker 负责）。"""
        from PySide6.QtGui import QPixmap

        import vibeocr.workers.pdf_load_worker as mod

        doc, pdf_doc = PdfService.open_doc(str(three_page_pdf))
        session = PdfSession(
            file_path=str(three_page_pdf), doc=doc, pdf_document=pdf_doc
        )

        called: list = []
        # render_page 不应被调用
        import vibeocr.services.pdf_service as svc

        orig = svc.PdfService.render_page
        svc.PdfService.render_page = staticmethod(  # type: ignore[assignment]
            lambda *a, **k: called.append(1) or QPixmap(10, 10)
        )
        try:
            worker = PdfLoadWorker(
                session_id=session.file_path,
                doc=session.doc,
                pdf_document=session.pdf_document,
                loaded_pages=session.loaded_pages,
                doc_lock=session.doc_lock,
            )
            worker.start()
            wait_worker(worker)
        finally:
            svc.PdfService.render_page = orig  # type: ignore[assignment]

        assert called == []  # 文字层 worker 不渲染缩略图
        doc.close()
