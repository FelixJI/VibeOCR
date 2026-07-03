"""Tests for PdfOpenWorker — 异步打开 PDF（批量导入时不冻结主线程）。"""

import fitz
import pytest
from PySide6.QtCore import Qt

from vibeocr.workers.pdf_open_worker import PdfOpenWorker


@pytest.fixture
def single_page_pdf(tmp_path):
    path = tmp_path / "single.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "Hello", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def multi_file_pdfs(tmp_path):
    """生成 3 个独立的小 PDF 文件。"""
    paths = []
    for n in range(3):
        path = tmp_path / f"doc_{n}.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"File {n}", fontsize=12)
        doc.save(str(path))
        doc.close()
        paths.append(path)
    return paths


class TestPdfOpenWorker:
    def test_emits_doc_opened_for_each_file(self, multi_file_pdfs, qapp, wait_worker):
        """对 3 个文件,逐个 emit doc_opened,每个携带可用的 doc/pdf_document/doc_lock。"""
        opened: list[tuple[str, object, object, object]] = []

        worker = PdfOpenWorker([str(p) for p in multi_file_pdfs])
        worker.doc_opened.connect(
            lambda path, doc, pdf_doc, lock: opened.append((path, doc, pdf_doc, lock)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert len(opened) == 3
        # 每个 doc_opened 携带的对象类型正确
        for path, doc, pdf_doc, lock in opened:
            assert path in [str(p) for p in multi_file_pdfs]
            # doc 是可用的 fitz.Document
            assert doc.page_count == 1
            # pdf_document 占位页已创建
            assert pdf_doc.page_count == 1
            assert len(pdf_doc.pages) == 1
            # doc_lock 可用（RLock，可 acquire/release）
            assert lock.acquire()
            lock.release()
            doc.close()

    def test_open_failed_for_nonexistent_file(self, qapp, wait_worker):
        """不存在的文件 → open_failed 信号,携带 file_path 和错误信息。"""
        opened: list = []
        failed: list[tuple[str, str]] = []

        worker = PdfOpenWorker(["nonexistent.pdf"])
        worker.doc_opened.connect(
            lambda *args: opened.append(args), Qt.ConnectionType.DirectConnection
        )
        worker.open_failed.connect(
            lambda path, err: failed.append((path, err)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert opened == []
        assert len(failed) == 1
        assert failed[0][0] == "nonexistent.pdf"
        assert "不存在" in failed[0][1]

    def test_mixed_success_and_failure(self, single_page_pdf, qapp, wait_worker):
        """部分文件成功、部分失败:成功的 emit doc_opened,失败的 emit open_failed。"""
        opened: list = []
        failed: list = []

        paths = [str(single_page_pdf), "missing.pdf"]
        worker = PdfOpenWorker(paths)
        worker.doc_opened.connect(
            lambda *args: opened.append(args), Qt.ConnectionType.DirectConnection
        )
        worker.open_failed.connect(
            lambda path, err: failed.append((path, err)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert len(opened) == 1
        assert len(failed) == 1
        assert failed[0][0] == "missing.pdf"
        opened[0][1].close()  # cleanup doc

    def test_emits_progress(self, multi_file_pdfs, qapp, wait_worker):
        """每个文件开始打开前 emit open_progress(current, total)。"""
        progresses: list[tuple[int, int]] = []

        worker = PdfOpenWorker([str(p) for p in multi_file_pdfs])
        worker.open_progress.connect(
            lambda current, total: progresses.append((current, total)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert len(progresses) == 3
        assert progresses[0] == (1, 3)
        assert progresses[-1] == (3, 3)

    def test_cancel_stops_remaining_files(self, multi_file_pdfs, qapp, wait_worker):
        """cancel 后不再处理后续文件。"""
        opened: list = []

        def on_first(*args):
            opened.append(args)
            worker.cancel()

        worker = PdfOpenWorker([str(p) for p in multi_file_pdfs])
        worker.doc_opened.connect(on_first, Qt.ConnectionType.DirectConnection)
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        # cancel 在第 1 个之后触发,后续文件不再 emit
        assert len(opened) <= 1
        if opened:
            opened[0][1].close()

    def test_all_done_emitted(self, multi_file_pdfs, qapp, wait_worker):
        """全部处理完后 emit all_done。"""
        done_emitted = False

        worker = PdfOpenWorker([str(p) for p in multi_file_pdfs])

        def on_done():
            nonlocal done_emitted
            done_emitted = True

        worker.all_done.connect(on_done, Qt.ConnectionType.DirectConnection)

        # 需在 done 时清理 doc
        docs: list = []
        worker.doc_opened.connect(
            lambda *args: docs.append(args[1]), Qt.ConnectionType.DirectConnection
        )

        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert done_emitted
        for d in docs:
            d.close()
