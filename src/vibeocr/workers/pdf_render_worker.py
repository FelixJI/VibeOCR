# src/vibeocr/workers/pdf_render_worker.py
"""PDF 渲染 Worker — 后台逐页渲染为 numpy 数组，推入 queue 供 OCR worker 消费。

解决批量 OCR 前置渲染阻塞主线程的问题。queue 有背压（maxsize），
内存峰值受控。
"""

from __future__ import annotations

import logging
from queue import Queue
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal

from vibeocr.workers.pdf_session_worker_base import PdfSessionWorker

if TYPE_CHECKING:
    import threading

    import fitz

    from vibeocr.models.pdf_document import PdfDocument

logger = logging.getLogger(__name__)


class PdfRenderWorker(PdfSessionWorker):
    """逐页渲染 Worker。

    Signals:
        render_progress(session_id: str, current: int, total: int)
        all_done(session_id: str)
    """

    render_progress = Signal(str, int, int)
    all_done = Signal(str)

    def __init__(
        self,
        session_id: str,
        doc: "fitz.Document",
        doc_lock: "threading.RLock",
        page_indices: list[int],
        pdf_settings: object | None,
        render_queue: Queue,
        parent=None,
    ) -> None:
        # PdfRenderWorker 不持有 pdf_document，传 None 占位
        super().__init__(session_id, doc, None, doc_lock, parent)  # type: ignore[arg-type]
        self._page_indices = page_indices
        self._pdf_settings = pdf_settings
        self._queue = render_queue

    def run(self) -> None:
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings
        from vibeocr.services.pdf_service import PdfService

        settings = self._pdf_settings if self._pdf_settings is not None else PdfGlobalSettings()
        total = len(self._page_indices)
        try:
            for n, page_idx in enumerate(self._page_indices):
                if self._cancelled:
                    break
                try:
                    with self._doc_lock:
                        page = self._doc[page_idx]
                        adjusted_dpi = settings.adjust_dpi(
                            page.rect.width, page.rect.height
                        )
                        img_array = PdfService.render_page_as_array(
                            self._doc, page_idx, dpi=adjusted_dpi
                        )
                    if img_array.size > 0:
                        self._queue.put((page_idx, img_array))
                except Exception as e:
                    logger.error("渲染页 %d 失败: %s", page_idx, e)
                    self._queue.put((page_idx, None))
                self.render_progress.emit(self._session_id, n + 1, total)
        finally:
            # 无论完成还是取消，都推哨兵通知 OCR worker 结束
            self._queue.put(None)
            self.all_done.emit(self._session_id)
