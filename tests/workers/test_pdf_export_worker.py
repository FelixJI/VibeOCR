"""PdfExportWorker: 跨 session 批量导出。"""

import fitz
import pytest
from PySide6.QtCore import Qt

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.models.pdf_session import PdfSession
from vibeocr.workers.pdf_export_worker import PdfExportWorker


def _make_session(path, modified=True):
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    pdf_doc = PdfDocument(file_path=path)
    pdf_doc.pages = [PdfPageInfo(page_index=0)]
    pdf_doc.is_modified = modified
    return PdfSession(file_path=path, doc=doc, pdf_document=pdf_doc)


class TestPdfExportWorker:
    def test_exports_modified_sessions(self, qapp, wait_worker, tmp_path):
        s1 = _make_session(str(tmp_path / "a.pdf"))
        s2 = _make_session(str(tmp_path / "b.pdf"))
        out = tmp_path / "out"
        out.mkdir()
        worker = PdfExportWorker([s1, s2], str(out))
        exported: list = []
        worker.done.connect(
            lambda paths: exported.extend(paths), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)
        assert len(exported) == 2
        assert (out / "a.pdf").exists()
        assert (out / "b.pdf").exists()
        s1.doc.close()
        s2.doc.close()

    def test_skips_unmodified(self, qapp, wait_worker, tmp_path):
        s1 = _make_session(str(tmp_path / "a.pdf"), modified=True)
        s2 = _make_session(str(tmp_path / "b.pdf"), modified=False)
        out = tmp_path / "out"
        out.mkdir()
        worker = PdfExportWorker([s1, s2], str(out))
        exported: list = []
        worker.done.connect(
            lambda paths: exported.extend(paths), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)
        assert len(exported) == 1
        s1.doc.close()
        s2.doc.close()
