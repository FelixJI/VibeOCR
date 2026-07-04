"""PDF 文字层检测 Worker — 后台检测每页文字层/扫描状态（不渲染缩略图）。

缩略图渲染由 ThumbnailRenderWorker 按需完成（只渲染可见页，避免大 PDF
全量渲染卡顿）。本 worker 只做轻量的文字层检测（get_text("dict")，比
get_pixmap 快得多），用于文字层状态网格的绿/灰染色与汇总统计。

Signals:
    page_ready(page_index: int, page_info: PdfPageInfo)
        单页文字层检测完成。
    all_done(session_id: str)
        全部页检测完成。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal

from vibeocr.models.pdf_document import PdfPageInfo
from vibeocr.services.pdf_service import PdfService
from vibeocr.workers.pdf_session_worker_base import PdfSessionWorker

if TYPE_CHECKING:
    import threading

    import fitz

    from vibeocr.models.pdf_document import PdfDocument

logger = logging.getLogger(__name__)


class PdfLoadWorker(PdfSessionWorker):
    """逐页检测文字层状态的异步 Worker（不渲染缩略图）。"""

    page_ready = Signal(int, object)
    all_done = Signal(str)

    def __init__(
        self,
        session_id: str,
        doc: "fitz.Document",
        pdf_document: "PdfDocument",
        loaded_pages: set[int],
        doc_lock: "threading.RLock",
        parent=None,
    ) -> None:
        super().__init__(session_id, doc, pdf_document, doc_lock, parent)
        self._loaded_pages = loaded_pages

    def run(self) -> None:
        for i in range(self._pdf_document.page_count):
            if self._cancelled:
                break
            if i in self._loaded_pages:
                continue
            try:
                with self._doc_lock:
                    text_layers = PdfService.detect_text_layers(self._doc, i)
                    is_scanned = not text_layers and PdfService.is_page_scanned(
                        self._doc, i
                    )
                    page = self._doc[i]
                    page_info = PdfPageInfo(
                        page_index=i,
                        rotation=page.rotation,
                        has_text_layer=len(text_layers) > 0,
                        text_layers=text_layers,
                        is_scanned=is_scanned,
                    )
                self.page_ready.emit(i, page_info)
            except Exception as e:
                logger.error("PdfLoadWorker page %d failed: %s", i, e)
        self.all_done.emit(self._session_id)
