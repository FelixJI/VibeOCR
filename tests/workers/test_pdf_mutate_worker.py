"""PdfMutateWorker 测试。"""

from threading import RLock
from unittest.mock import MagicMock

import fitz
import pytest
from PySide6.QtCore import Qt

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.workers.pdf_mutate_worker import MutateTask, TaskKind, PdfMutateWorker


class TestMutateTaskDataclass:
    def test_delete_text_layer_task(self):
        task = MutateTask(kind=TaskKind.DELETE_TEXT_LAYER, page_indices=[0, 1])
        assert task.kind == TaskKind.DELETE_TEXT_LAYER
        assert task.page_indices == [0, 1]

    def test_rotate_task(self):
        task = MutateTask(kind=TaskKind.ROTATE, page_indices=[0], angle=90)
        assert task.angle == 90

    def test_save_task_default_path_none(self):
        task = MutateTask(kind=TaskKind.SAVE)
        assert task.path is None

    def test_save_as_task(self):
        task = MutateTask(kind=TaskKind.SAVE_AS, path="/tmp/out.pdf")
        assert task.path == "/tmp/out.pdf"


def _open_text_pdf(num_pages=3):
    """创建含文字层的测试 PDF（每页有文字）。"""
    import tempfile, pathlib
    td = tempfile.mkdtemp()
    path = pathlib.Path(td) / "t.pdf"
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Page {i}", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


class TestDeleteTextLayerTask:
    def test_deletes_text_and_emits_page_done(self, qapp, wait_worker):
        path = _open_text_pdf(3)
        doc = fitz.open(path)
        pdf_doc = PdfDocument(file_path=path)
        pdf_doc.pages = [PdfPageInfo(page_index=i, has_text_layer=True) for i in range(3)]

        task = MutateTask(kind=TaskKind.DELETE_TEXT_LAYER, page_indices=[0, 1, 2])
        worker = PdfMutateWorker(
            session_id=path, doc=doc, pdf_document=pdf_doc,
            doc_lock=RLock(), task=task,
        )
        done_pages: list = []
        all_done: list = []
        worker.page_done.connect(
            lambda i, p: done_pages.append((i, p)), Qt.ConnectionType.DirectConnection
        )
        worker.all_done.connect(
            lambda sid, r: all_done.append((sid, r)), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert len(done_pages) == 3
        # all_done result contains residual_pages key
        residual = [r for sid, r in all_done if isinstance(r, dict)]
        assert residual and residual[0].get("residual_pages") == []
        for i in range(3):
            assert doc[i].get_text().strip() == ""
        doc.close()

    def test_skips_pages_without_text(self, qapp, wait_worker):
        """无文字的页不进 redact，但仍 emit page_done。"""
        doc = fitz.open()
        doc.new_page(width=612, height=792)  # 空白页
        pdf_doc = PdfDocument()
        pdf_doc.pages = [PdfPageInfo(page_index=0)]

        task = MutateTask(kind=TaskKind.DELETE_TEXT_LAYER, page_indices=[0])
        worker = PdfMutateWorker(
            session_id="blank.pdf", doc=doc, pdf_document=pdf_doc,
            doc_lock=RLock(), task=task,
        )
        done_pages: list = []
        worker.page_done.connect(
            lambda i, p: done_pages.append((i, p)), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)
        assert len(done_pages) == 1
        assert done_pages[0][1] == (0, 0, False)  # 无文字
        doc.close()

    def test_cancel_stops_early(self, qapp, wait_worker):
        path = _open_text_pdf(5)
        doc = fitz.open(path)
        pdf_doc = PdfDocument(file_path=path)
        pdf_doc.pages = [PdfPageInfo(page_index=i, has_text_layer=True) for i in range(5)]

        task = MutateTask(kind=TaskKind.DELETE_TEXT_LAYER, page_indices=list(range(5)))
        worker = PdfMutateWorker(
            session_id=path, doc=doc, pdf_document=pdf_doc,
            doc_lock=RLock(), task=task,
        )
        done_pages: list = []

        def on_done(i, p):
            done_pages.append(i)
            if len(done_pages) == 1:
                worker.cancel()

        worker.page_done.connect(on_done, Qt.ConnectionType.DirectConnection)
        worker.start()
        wait_worker(worker)
        assert worker.isFinished()
        assert len(done_pages) <= 3  # 取消后很快停
        doc.close()


class TestRotateTask:
    def test_rotates_pages_and_emits_done(self, qapp, wait_worker):
        doc = fitz.open()
        for _ in range(2):
            doc.new_page(width=612, height=792)
        pdf_doc = PdfDocument()
        pdf_doc.pages = [PdfPageInfo(page_index=i) for i in range(2)]
        task = MutateTask(kind=TaskKind.ROTATE, page_indices=[0, 1], angle=90)
        worker = PdfMutateWorker("rot.pdf", doc, pdf_doc, RLock(), task)
        done: list = []
        worker.all_done.connect(
            lambda sid, r: done.append(sid), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)
        assert done == ["rot.pdf"]
        assert doc[0].rotation == 90
        assert doc[1].rotation == 90
        doc.close()


class TestDeletePagesTask:
    def test_deletes_pages(self, qapp, wait_worker):
        path = _open_text_pdf(3)
        doc = fitz.open(path)
        pdf_doc = PdfDocument(file_path=path)
        pdf_doc.pages = [PdfPageInfo(page_index=i) for i in range(3)]
        task = MutateTask(kind=TaskKind.DELETE_PAGES, page_indices=[1])
        worker = PdfMutateWorker(path, doc, pdf_doc, RLock(), task)
        done: list = []
        worker.all_done.connect(
            lambda sid, r: done.append(sid), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)
        assert done == [path]
        assert doc.page_count == 2
        doc.close()


class TestSaveTask:
    def test_save_resets_modified_flag(self, qapp, wait_worker, tmp_path):
        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.services.pdf_service import PdfService
        import numpy as np

        # 扫描件 PDF + 注入文字层
        path = tmp_path / "scan.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pm = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pm)
        doc.save(str(path))
        doc.close()

        doc = fitz.open(str(path))
        pdf_doc = PdfDocument(file_path=str(path))
        pdf_doc.pages = [PdfPageInfo(page_index=0)]
        result = OCRResult(
            raw_text="Hi",
            text_blocks=[TextBlock(text="Hi", score=0.9, bbox=(50, 50, 200, 100))],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        pdf_doc.is_modified = True

        task = MutateTask(kind=TaskKind.SAVE)
        worker = PdfMutateWorker(str(path), doc, pdf_doc, RLock(), task)
        done: list = []
        worker.all_done.connect(
            lambda sid, r: done.append(r), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)
        assert len(done) == 1
        assert pdf_doc.is_modified is False
        # 验证落盘内容
        verify = fitz.open(str(path))
        assert "Hi" in verify[0].get_text()
        verify.close()
        doc.close()
