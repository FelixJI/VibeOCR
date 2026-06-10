"""PDF 异步加载 Worker — 后台检测文字层 + 渲染缩略图。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QPixmap

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.services.pdf_service import PdfService

if TYPE_CHECKING:
    import fitz

logger = logging.getLogger(__name__)


class PdfLoadWorker(QThread):
    """逐页检测文字层并渲染缩略图的异步 Worker。

    Signals:
        page_ready(page_index: int, page_info: PdfPageInfo, thumbnail: QPixmap)
        all_done(session_id: str)
    """

    page_ready = Signal(int, object, QPixmap)
    all_done = Signal(str)

    def __init__(
        self,
        session_id: str,
        doc: fitz.Document,
        pdf_document: PdfDocument,
        loaded_pages: set[int],
        thumbnail_dpi: int = 96,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._doc = doc
        self._pdf_document = pdf_document
        self._loaded_pages = loaded_pages
        self._thumbnail_dpi = thumbnail_dpi
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        for i in range(self._pdf_document.page_count):
            if self._cancelled:
                break
            if i in self._loaded_pages:
                continue
            try:
                text_layers = PdfService.detect_text_layers(self._doc, i)
                is_scanned = (
                    not text_layers
                    and PdfService.is_page_scanned(self._doc, i)
                )
                page = self._doc[i]
                page_info = PdfPageInfo(
                    page_index=i,
                    rotation=page.rotation,
                    has_text_layer=len(text_layers) > 0,
                    text_layers=text_layers,
                    is_scanned=is_scanned,
                )
                pixmap = PdfService.render_page(self._doc, i, dpi=self._thumbnail_dpi)
                self.page_ready.emit(i, page_info, pixmap)
            except Exception as e:
                logger.error("PdfLoadWorker page %d failed: %s", i, e)
        self.all_done.emit(self._session_id)
