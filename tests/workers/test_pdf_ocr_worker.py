"""Tests for PdfOcrWorker."""

import time
from unittest.mock import MagicMock

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, Qt

from vibeocr.models.ocr_result import OCRResult, TextBlock
from vibeocr.workers.pdf_ocr_worker import PdfOcrWorker


def _wait_worker(worker, timeout=10000):
    """等待 worker 完成，期间处理事件循环以接收跨线程信号。"""
    start = time.monotonic()
    while not worker.isFinished():
        QCoreApplication.processEvents()
        worker.wait(50)
        if time.monotonic() - start > timeout / 1000:
            break
    # 处理完剩余排队信号
    QCoreApplication.processEvents()


class TestPdfOcrWorker:
    def test_emits_page_done_for_each_page(self, qapp):
        pages = [
            (0, np.ones((100, 100, 3), dtype=np.uint8)),
            (1, np.ones((100, 100, 3), dtype=np.uint8)),
        ]
        mock_service = MagicMock()
        mock_service.recognize.return_value = OCRResult(raw_text="ok", text_blocks=[])

        done_pages: list = []
        done_summary: list = []

        worker = PdfOcrWorker(
            session_id="test.pdf",
            pages=pages,
            ocr_service=mock_service,
        )
        worker.page_done.connect(
            lambda i, r: done_pages.append((i, r)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.all_done.connect(
            lambda sid, s, f: done_summary.append((sid, s, f)),
            Qt.ConnectionType.DirectConnection,
        )

        worker.start()
        _wait_worker(worker)

        assert worker.isFinished()
        assert len(done_pages) == 2
        assert done_pages[0][0] == 0
        assert done_pages[1][0] == 1
        assert done_summary == [("test.pdf", 2, 0)]

    def test_handles_ocr_failure_gracefully(self, qapp):
        pages = [(0, np.ones((100, 100, 3), dtype=np.uint8))]
        mock_service = MagicMock()
        mock_service.recognize.side_effect = RuntimeError("OCR engine error")

        done_pages: list = []
        done_summary: list = []

        worker = PdfOcrWorker(
            session_id="fail.pdf",
            pages=pages,
            ocr_service=mock_service,
        )
        worker.page_done.connect(
            lambda i, r: done_pages.append((i, r)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.all_done.connect(
            lambda sid, s, f: done_summary.append((sid, s, f)),
            Qt.ConnectionType.DirectConnection,
        )

        worker.start()
        _wait_worker(worker)

        assert worker.isFinished()
        assert done_pages[0] == (0, None)
        assert done_summary == [("fail.pdf", 0, 1)]

    def test_cancel_stops_early(self, qapp):
        pages = [
            (i, np.ones((100, 100, 3), dtype=np.uint8)) for i in range(10)
        ]
        mock_service = MagicMock()
        call_count = 0

        def slow_recognize(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                worker.cancel()
            return OCRResult(raw_text="ok", text_blocks=[])

        mock_service.recognize.side_effect = slow_recognize

        done_pages: list = []
        worker = PdfOcrWorker(
            session_id="cancel.pdf",
            pages=pages,
            ocr_service=mock_service,
        )
        worker.page_done.connect(
            lambda i, r: done_pages.append(i),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        _wait_worker(worker)

        assert worker.isFinished()
        assert len(done_pages) <= 3

    def test_emits_progress(self, qapp):
        pages = [(0, np.ones((100, 100, 3), dtype=np.uint8))]
        mock_service = MagicMock()
        mock_service.recognize.return_value = OCRResult(raw_text="ok", text_blocks=[])

        progress_calls: list = []
        worker = PdfOcrWorker(
            session_id="progress.pdf",
            pages=pages,
            ocr_service=mock_service,
        )
        worker.progress.connect(
            lambda cur, total: progress_calls.append((cur, total)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        _wait_worker(worker)

        assert worker.isFinished()
        assert (1, 1) in progress_calls
