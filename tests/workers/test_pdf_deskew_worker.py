from threading import RLock
from unittest.mock import MagicMock

import fitz
import pytest
from PySide6.QtCore import Qt

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.workers.pdf_deskew_worker import PdfDeskewWorker


@pytest.mark.parametrize("angle,expected", [
    (0, 0),
    (90, 270),
    (180, 180),
    (270, 90),
    (360, 0),
    (-90, 90),     # 负值取模
    (450, 270),    # 越界取模 (450%360=90 → 270)
])
def test_angle_to_correction(angle, expected):
    assert PdfDeskewWorker.angle_to_correction(angle) == expected


def _make_session(tmp_path, angle_report=90):
    """构造单页 PDF + PdfDocument + mock ocr_service（返回固定 preproc_angle）。"""
    path = str(tmp_path / "t.pdf")
    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    page.insert_text((10, 50), "hello", fontsize=12)
    doc.save(path)
    doc.close()
    doc = fitz.open(path)

    pdf_doc = PdfDocument(file_path=path)
    pdf_doc.pages = [PdfPageInfo(page_index=0)]

    result = MagicMock()
    result.preproc_angle = angle_report
    ocr_service = MagicMock()
    ocr_service.recognize_batch.return_value = [result]
    return path, doc, pdf_doc, ocr_service


def test_deskew_rotates_and_marks_deskewed(qapp, wait_worker, tmp_path):
    path, doc, pdf_doc, ocr_service = _make_session(tmp_path, angle_report=90)
    worker = PdfDeskewWorker(
        session_id=path, doc=doc, pdf_document=pdf_doc,
        doc_lock=RLock(), ocr_service=ocr_service, page_indices=[0],
    )
    done: list = []
    worker.all_done.connect(
        lambda sid, s: done.append(s), Qt.ConnectionType.DirectConnection
    )
    worker.start()
    wait_worker(worker)

    assert done, "all_done 未触发"
    summary = done[0]
    assert summary["corrected"] == 1
    assert summary["skipped"] == 0
    assert summary["corrected_pages"] == [0]
    # angle=90 → correction=270 → rotation 从 0 变 270
    assert doc[0].rotation == 270
    assert pdf_doc.pages[0].deskewed is True


def test_deskew_skips_already_upright(qapp, wait_worker, tmp_path):
    path, doc, pdf_doc, ocr_service = _make_session(tmp_path, angle_report=0)
    worker = PdfDeskewWorker(
        session_id=path, doc=doc, pdf_document=pdf_doc,
        doc_lock=RLock(), ocr_service=ocr_service, page_indices=[0],
    )
    done: list = []
    worker.all_done.connect(
        lambda sid, s: done.append(s), Qt.ConnectionType.DirectConnection
    )
    worker.start()
    wait_worker(worker)

    summary = done[0]
    assert summary["corrected"] == 0
    assert summary["skipped"] == 1
    assert doc[0].rotation == 0
    assert pdf_doc.pages[0].deskewed is False
