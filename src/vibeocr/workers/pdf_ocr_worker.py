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

    # 每批识别的页数。拆批避免单次 predict(list) 运行过久（>300s）被
    # 健康检查误判为卡死而强制重启（见 worker_manager.STALE_THRESHOLD）。
    # 批间检查 _cancelled 实现可中断的取消。
    BATCH_SIZE = 10

    def run(self) -> None:
        from vibeocr.models.ocr_options import OCROptions

        total = len(self._pages)
        options = self._ocr_options if self._ocr_options is not None else OCROptions()

        if total == 0:
            self.all_done.emit(self._session_id, 0, 0)
            return

        success = 0
        fail = 0
        processed = 0

        # 按 BATCH_SIZE 拆批识别，每批处理完立即 emit page_done，
        # 并在下一批开始前检查 _cancelled。
        for batch_start in range(0, total, self.BATCH_SIZE):
            if self._cancelled:
                break

            batch_end = min(batch_start + self.BATCH_SIZE, total)
            batch_pages = self._pages[batch_start:batch_end]
            batch_indices = [idx for idx, _ in batch_pages]
            batch_images = [img for _, img in batch_pages]

            # 识别当前批（内部已容错：批量失败回退逐张）
            results = self._recognize_batch(batch_images, options)

            # emit 该批结果
            for i, (page_index, result) in enumerate(
                zip(batch_indices, results, strict=False)
            ):
                if self._cancelled:
                    break
                processed += 1
                self.progress.emit(processed, total)
                if result is not None:
                    self.page_done.emit(page_index, result)
                    success += 1
                else:
                    self.page_done.emit(page_index, None)
                    fail += 1

        self.all_done.emit(self._session_id, success, fail)

    def _recognize_batch(self, images, options):
        """批量识别一批图像，逐张容错。

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
