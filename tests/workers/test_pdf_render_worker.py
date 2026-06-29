# tests/workers/test_pdf_render_worker.py
"""PdfRenderWorker: 后台逐页渲染，推入 queue。"""

from queue import Queue
from threading import RLock
from unittest.mock import MagicMock

import fitz
import numpy as np
import pytest
from PySide6.QtCore import Qt

from vibeocr.workers.pdf_render_worker import PdfRenderWorker


def _open_pdf(num_pages=3):
    import tempfile, pathlib
    td = tempfile.mkdtemp()
    path = pathlib.Path(td) / "t.pdf"
    doc = fitz.open()
    for i in range(num_pages):
        doc.new_page(width=200, height=200)
    doc.save(str(path))
    doc.close()
    return str(path)


class TestPdfRenderWorker:
    def test_renders_all_pages_to_queue(self, qapp, wait_worker):
        path = _open_pdf(3)
        doc = fitz.open(path)
        q: Queue = Queue(maxsize=5)
        progress: list = []
        worker = PdfRenderWorker(
            session_id=path, doc=doc, doc_lock=RLock(),
            page_indices=[0, 1, 2], pdf_settings=None, render_queue=q,
        )
        worker.render_progress.connect(
            lambda sid, c, t: progress.append((c, t)), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)

        # 取出队列项：应有 3 个数组 + 1 个哨兵
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        arrays = [it for it in items if it is not None]
        assert len(arrays) == 3
        # 最后一项是哨兵 None
        assert items[-1] is None
        # 每项是 (page_index, np.ndarray)
        for idx, arr in arrays:
            assert isinstance(idx, int)
            assert isinstance(arr, np.ndarray) and arr.size > 0
        doc.close()

    def test_cancel_pushes_sentinel(self, qapp, wait_worker):
        """取消后必须推哨兵，避免 OCR worker queue.get() 永久阻塞。"""
        path = _open_pdf(5)
        doc = fitz.open(path)
        q: Queue = Queue(maxsize=10)
        worker = PdfRenderWorker(
            session_id=path, doc=doc, doc_lock=RLock(),
            page_indices=list(range(5)), pdf_settings=None, render_queue=q,
        )
        # 启动后立即取消
        worker.start()
        worker.cancel()
        wait_worker(worker)
        # 必须有哨兵
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert items[-1] is None
        doc.close()
