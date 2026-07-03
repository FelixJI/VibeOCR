"""PDF 自动摆正 Worker — 后台检测页面方向并旋转至文字朝上。

复用 OCR 管道的 use_doc_orientation_classify 做方向检测（0/90/180/270°），
按 (-angle) mod 360 旋转页面；对已有 OCR 文字层的页调 rewrite_text_layer
重算坐标使其与底层图像对齐。仅 90° 倍数方向纠正，不做细粒度倾斜。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    import threading

    import fitz

    from vibeocr.models.ocr_options import OCROptions
    from vibeocr.models.pdf_document import PdfDocument
    from vibeocr.services.ocr_service_base import OCRServiceBase

logger = logging.getLogger(__name__)

# 方向检测渲染 DPI（低于 OCR 的 300，方向分类不需高分辨率，更快）
_DETECT_DPI = 150


class PdfDeskewWorker(QThread):
    """自动摆正 Worker（单 doc 绑定，一次任务一实例）。

    Signals:
        page_done(page_index: int, was_corrected: bool)  逐页完成
        progress(current: int, total: int)
        all_done(session_id: str, summary: dict)          summary 含
            corrected/skipped 计数与 corrected_pages 列表
        failed(session_id: str, error_msg: str)
    """

    page_done = Signal(int, bool)
    progress = Signal(int, int)
    all_done = Signal(str, object)
    failed = Signal(str, str)

    def __init__(
        self,
        session_id: str,
        doc: "fitz.Document",
        pdf_document: "PdfDocument",
        doc_lock: "threading.RLock",
        ocr_service: "OCRServiceBase",
        page_indices: list[int],
        pdf_settings: object | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._doc = doc
        self._pdf_document = pdf_document
        self._doc_lock = doc_lock
        self._ocr_service = ocr_service
        self._page_indices = list(page_indices)
        self._pdf_settings = pdf_settings
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def session_id(self) -> str:
        return self._session_id

    @staticmethod
    def angle_to_correction(angle: int) -> int:
        """PaddleOCR 方向角 → PDF 旋转纠正角。

        preproc_angle = 内容相对正向的「顺时针」偏转度数（pdf_service.py:838-841）。
        让内容回正 → 把页面逆时针转 angle 度 → rotate_pages 的 angle 参数 = (-angle) % 360。
        映射: 0→0, 90→270, 180→180, 270→90。
        """
        return (-int(angle)) % 360

    def run(self) -> None:
        from vibeocr.models.ocr_options import OCROptions
        from vibeocr.services.pdf_service import PdfService

        try:
            total = len(self._page_indices)
            if total == 0:
                self.all_done.emit(
                    self._session_id,
                    {"corrected": 0, "skipped": 0, "corrected_pages": []},
                )
                return

            # 1. 批量渲染（加锁访问 fitz doc）
            with self._doc_lock:
                images = [
                    PdfService.render_page_as_array(self._doc, idx, dpi=_DETECT_DPI)
                    for idx in self._page_indices
                ]

            # 2. 方向检测（锁外，模型推理不碰 doc）
            options = OCROptions(
                use_doc_orientation_classify=True,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            results = self._ocr_service.recognize_batch(images, options)

            # 3. 逐页纠正
            corrected_pages: list[int] = []
            for n, (idx, res) in enumerate(zip(self._page_indices, results)):
                if self._cancelled:
                    break
                angle = int(getattr(res, "preproc_angle", 0) or 0)
                correction = self.angle_to_correction(angle)
                was_corrected = False
                if correction != 0:
                    with self._doc_lock:
                        PdfService.rotate_pages(
                            self._doc, self._pdf_document, [idx], correction
                        )
                        info = self._pdf_document.pages[idx]
                        if info.ocr_text_blocks:
                            PdfService.rewrite_text_layer(
                                self._doc,
                                self._pdf_document,
                                idx,
                                info.ocr_text_blocks,
                                info.ocr_preproc_angle,
                                pdf_settings=self._pdf_settings,
                                font_path=None,
                            )
                        info.deskewed = True
                        PdfService.invalidate_thumbnails(self._pdf_document, [idx])
                    was_corrected = True
                    corrected_pages.append(idx)
                self.page_done.emit(idx, was_corrected)
                self.progress.emit(n + 1, total)

            self.all_done.emit(
                self._session_id,
                {
                    "corrected": len(corrected_pages),
                    "skipped": total - len(corrected_pages),
                    "corrected_pages": corrected_pages,
                },
            )
        except Exception as e:
            logger.error("PdfDeskewWorker 任务失败: %s", e, exc_info=True)
            self.failed.emit(self._session_id, str(e))
