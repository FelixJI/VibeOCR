"""PDF 异步 OCR Worker — 在后台线程执行 OCR 识别。

接收预渲染的 numpy 数组列表，不直接访问 fitz.Document。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    import numpy as np

    from vibeocr.services.ocr_service_base import OCRServiceBase

logger = logging.getLogger(__name__)


class PdfOcrWorker(QThread):
    """异步 OCR Worker。

    Signals:
        page_done(page_index: int, result: OCRResult | None)
        progress(current: int, total: int)
        all_done(session_id: str, success_count: int, fail_count: int)
    """

    page_done = Signal(int, object)
    progress = Signal(int, int)
    all_done = Signal(str, int, int)

    def __init__(
        self,
        session_id: str,
        pages: list[tuple[int, np.ndarray]],
        ocr_service: OCRServiceBase,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._pages = pages
        self._ocr_service = ocr_service
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        success = 0
        fail = 0
        total = len(self._pages)

        for i, (page_index, image) in enumerate(self._pages):
            if self._cancelled:
                break
            self.progress.emit(i + 1, total)
            try:
                from vibeocr.models.ocr_options import OCROptions

                result = self._ocr_service.recognize(image, OCROptions())
                self.page_done.emit(page_index, result)
                success += 1
            except Exception as e:
                logger.error("PdfOcrWorker OCR failed (page %d): %s", page_index, e)
                self.page_done.emit(page_index, None)
                fail += 1

        self.all_done.emit(self._session_id, success, fail)
