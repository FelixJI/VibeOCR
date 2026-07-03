"""Tests for ThumbnailRenderWorker — 按需渲染单页缩略图的后台 worker。"""

import threading

import fitz
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from vibeocr.workers.pdf_render_thumb_worker import ThumbnailRenderWorker


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


class TestThumbnailRenderWorker:
    def test_renders_requested_page(self, three_page_pdf, qapp, wait_worker):
        """request 后,后台渲染该页并 emit thumbnail_ready。"""
        doc = fitz.open(str(three_page_pdf))
        lock = threading.RLock()
        results: list[tuple[int, QPixmap]] = []

        worker = ThumbnailRenderWorker(doc=doc, doc_lock=lock, dpi=96, size=160)
        worker.thumbnail_ready.connect(
            lambda idx, pm: results.append((idx, pm)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        worker.request(0)
        worker.request(1)
        worker.stop()  # 投递停止哨兵
        wait_worker(worker)

        assert worker.isFinished()
        rendered_indices = sorted(idx for idx, _ in results)
        assert 0 in rendered_indices
        assert 1 in rendered_indices
        # pixmap 是有效的且已缩放到 size 范围
        for _, pm in results:
            assert not pm.isNull()
            assert pm.width() <= 160 and pm.height() <= 160
        doc.close()

    def test_dedupes_pending_requests(self, three_page_pdf, qapp, wait_worker):
        """同一页连续 request 多次只渲染一次。"""
        doc = fitz.open(str(three_page_pdf))
        lock = threading.RLock()
        results: list[int] = []

        worker = ThumbnailRenderWorker(doc=doc, doc_lock=lock, dpi=96, size=160)
        worker.thumbnail_ready.connect(
            lambda idx, pm: results.append(idx),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        worker.request(0)
        worker.request(0)
        worker.request(0)
        worker.stop()
        wait_worker(worker)

        assert worker.isFinished()
        # page 0 只渲染一次
        assert results.count(0) == 1
        doc.close()

    def test_stop_terminates_worker(self, three_page_pdf, qapp, wait_worker):
        """stop() 投递哨兵让 worker 正常退出。"""
        doc = fitz.open(str(three_page_pdf))
        lock = threading.RLock()

        worker = ThumbnailRenderWorker(doc=doc, doc_lock=lock, dpi=96, size=160)
        worker.start()
        worker.stop()
        wait_worker(worker)

        assert worker.isFinished()
        doc.close()

    def test_cancel_clears_pending(self, three_page_pdf, qapp, wait_worker):
        """cancel() 清空待处理队列并停止 worker。"""
        doc = fitz.open(str(three_page_pdf))
        lock = threading.RLock()
        results: list[int] = []

        worker = ThumbnailRenderWorker(doc=doc, doc_lock=lock, dpi=96, size=160)
        worker.thumbnail_ready.connect(
            lambda idx, pm: results.append(idx),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        worker.request(0)
        worker.cancel()
        wait_worker(worker)

        assert worker.isFinished()
        doc.close()
