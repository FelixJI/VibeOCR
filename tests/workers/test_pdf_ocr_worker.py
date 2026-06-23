"""Tests for PdfOcrWorker."""

from unittest.mock import MagicMock

import numpy as np
from PySide6.QtCore import Qt

from vibeocr.models.ocr_result import OCRResult
from vibeocr.workers.pdf_ocr_worker import PdfOcrWorker


def _mk_result(text: str = "ok") -> OCRResult:
    return OCRResult(raw_text=text, text_blocks=[])


class TestPdfOcrWorker:
    def test_emits_page_done_for_each_page(self, qapp, wait_worker):
        pages = [
            (0, np.ones((100, 100, 3), dtype=np.uint8)),
            (1, np.ones((100, 100, 3), dtype=np.uint8)),
        ]
        mock_service = MagicMock()
        # 批量识别：返回与输入数量等长的结果列表
        mock_service.recognize_batch.return_value = [_mk_result("a"), _mk_result("b")]

        done_pages: list = []
        done_summary: list = []
        batch_calls: list = []

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
        mock_service.recognize_batch.side_effect = lambda imgs, opts: (
            batch_calls.append(list(imgs)),
            [_mk_result() for _ in imgs],
        )[1]

        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        # 单次批量调用，而非逐页
        assert len(batch_calls) == 1
        assert len(batch_calls[0]) == 2
        assert len(done_pages) == 2
        assert done_pages[0][0] == 0
        assert done_pages[1][0] == 1
        assert done_summary == [("test.pdf", 2, 0)]

    def test_preserves_page_order_with_unsorted_indices(self, qapp, wait_worker):
        """pages 顺序可能不连续（用户选择页面），结果必须按原顺序映射回 page_index。"""
        pages = [
            (3, np.ones((10, 10, 3), dtype=np.uint8)),
            (1, np.ones((10, 10, 3), dtype=np.uint8)),
            (5, np.ones((10, 10, 3), dtype=np.uint8)),
        ]
        mock_service = MagicMock()
        # 模拟批量返回，按输入顺序给出可区分的结果
        mock_service.recognize_batch.return_value = [
            _mk_result("p3"),
            _mk_result("p1"),
            _mk_result("p5"),
        ]

        done_pages: list = []

        worker = PdfOcrWorker(
            session_id="order.pdf",
            pages=pages,
            ocr_service=mock_service,
        )
        worker.page_done.connect(
            lambda i, r: done_pages.append((i, r.raw_text)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        # page_index 与结果文本一一对应
        assert done_pages == [(3, "p3"), (1, "p1"), (5, "p5")]

    def test_handles_ocr_failure_gracefully(self, qapp, wait_worker):
        pages = [(0, np.ones((100, 100, 3), dtype=np.uint8))]
        mock_service = MagicMock()
        # 批量调用整体抛异常 → 回退逐张，逐张也失败 → 结果为 None
        mock_service.recognize_batch.side_effect = RuntimeError("OCR engine error")
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
        wait_worker(worker)

        assert worker.isFinished()
        assert done_pages[0] == (0, None)
        assert done_summary == [("fail.pdf", 0, 1)]

    def test_batch_failure_falls_back_to_per_page(self, qapp, wait_worker):
        """批量调用失败但逐张成功时，应回退逐张并返回正确结果。"""
        pages = [
            (0, np.ones((10, 10, 3), dtype=np.uint8)),
            (1, np.ones((10, 10, 3), dtype=np.uint8)),
        ]
        mock_service = MagicMock()
        mock_service.recognize_batch.side_effect = RuntimeError("batch boom")
        mock_service.recognize.side_effect = [_mk_result("x"), _mk_result("y")]

        done_pages: list = []
        worker = PdfOcrWorker(
            session_id="fallback.pdf",
            pages=pages,
            ocr_service=mock_service,
        )
        worker.page_done.connect(
            lambda i, r: done_pages.append((i, r.raw_text)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert done_pages == [(0, "x"), (1, "y")]
        # 批量失败后回退，逐张被调用 2 次
        assert mock_service.recognize.call_count == 2

    def test_cancel_stops_early(self, qapp, wait_worker):
        pages = [(i, np.ones((100, 100, 3), dtype=np.uint8)) for i in range(10)]
        mock_service = MagicMock()
        call_count = 0

        def slow_batch(imgs, opts):
            # 批量是一次性调用，cancel 在批量返回后才生效；
            # 此处模拟批量完成后逐页 emit 时检测取消。
            return [_mk_result() for _ in imgs]

        mock_service.recognize_batch.side_effect = slow_batch

        done_pages: list = []
        worker = PdfOcrWorker(
            session_id="cancel.pdf",
            pages=pages,
            ocr_service=mock_service,
        )
        # 在首个 page_done 后立即取消
        def on_page_done(i, r):
            done_pages.append(i)
            if len(done_pages) == 1:
                worker.cancel()

        worker.page_done.connect(on_page_done, Qt.ConnectionType.DirectConnection)
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        # 取消后应在首个结果之后很快停止（emit 顺序，可能多 emit 一两个）
        assert len(done_pages) <= 3

    def test_empty_pages_emits_all_done(self, qapp, wait_worker):
        mock_service = MagicMock()
        done_summary: list = []
        worker = PdfOcrWorker(
            session_id="empty.pdf",
            pages=[],
            ocr_service=mock_service,
        )
        worker.all_done.connect(
            lambda sid, s, f: done_summary.append((sid, s, f)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert done_summary == [("empty.pdf", 0, 0)]
        mock_service.recognize_batch.assert_not_called()

    def test_emits_progress(self, qapp, wait_worker):
        pages = [(0, np.ones((100, 100, 3), dtype=np.uint8))]
        mock_service = MagicMock()
        mock_service.recognize_batch.return_value = [_mk_result()]

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
        wait_worker(worker)

        assert worker.isFinished()
        assert (1, 1) in progress_calls
