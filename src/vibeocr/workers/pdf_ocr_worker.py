"""PDF 异步 OCR Worker — 在后台线程执行 OCR 识别。

接收预渲染的 numpy 数组列表，不直接访问 fitz.Document。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    import numpy as np

    from vibeocr.models.ocr_options import OCROptions
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
        ocr_options: OCROptions | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._pages = pages
        self._ocr_service = ocr_service
        self._ocr_options = ocr_options
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def session_id(self) -> str:
        return self._session_id

    def run(self) -> None:
        from vibeocr.models.ocr_options import OCROptions

        total = len(self._pages)
        options = self._ocr_options if self._ocr_options is not None else OCROptions()

        if total == 0:
            self.all_done.emit(self._session_id, 0, 0)
            return

        # 批量识别：单次 predict(list) 调用，利用 PaddleOCR 内部分批处理，
        # 避免逐页重复管道开销。回退路径（服务不支持批量）在 _recognize_batch
        # 内部逐张处理。
        ordered_indices = [idx for idx, _ in self._pages]
        ordered_images = [img for _, img in self._pages]

        results = self._recognize_batch(ordered_images, options)

        success = 0
        fail = 0
        for i, (page_index, result) in enumerate(zip(ordered_indices, results)):
            if self._cancelled:
                break
            self.progress.emit(i + 1, total)
            if result is not None:
                self.page_done.emit(page_index, result)
                success += 1
            else:
                self.page_done.emit(page_index, None)
                fail += 1

        self.all_done.emit(self._session_id, success, fail)

    def _recognize_batch(self, images, options):
        """批量识别所有图像，逐张容错。

        优先调用服务的 recognize_batch（单次 predict(list)）；失败时回退逐张，
        确保单张图错误不会拖垮整批。返回结果列表，与 images 顺序一致，失败项为 None。
        """
        try:
            return list(self._ocr_service.recognize_batch(images, options))
        except Exception as e:
            logger.warning(
                "PdfOcrWorker 批量识别失败，回退逐张识别: %s", e, exc_info=True
            )
            results: list = []
            for img in images:
                if self._cancelled:
                    results.append(None)
                    continue
                try:
                    results.append(self._ocr_service.recognize(img, options))
                except Exception as e2:
                    logger.error("PdfOcrWorker 单页 OCR 失败: %s", e2)
                    results.append(None)
            return results
