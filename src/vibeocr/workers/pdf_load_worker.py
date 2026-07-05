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
        import time

        for i in range(self._pdf_document.page_count):
            if self._cancelled:
                break
            if i in self._loaded_pages:
                continue
            try:
                # 轻量文字层检测：get_text("text") ~3ms 判断有无文字（用于网格染色），
                # 不调 get_text("dict") ~180ms（扫描件每页都跑会卡 ~118s 持 GIL）。
                # text_layers 详情（bbox）延迟到预览/tooltip 时按需 detect_text_layers。
                # is_page_scanned 仅在无文字层时才检测。
                # 几何（rect）/旋转统一走 PdfService 访问器，避免 worker 直接持 fitz 对象，
                # 为 PDF 模块进程化下沉铺路（子进程内单点访问 fitz）。
                with self._doc_lock:
                    rotation = PdfService.page_rotation(self._doc, i)
                    page_rect = PdfService.page_rect(self._doc, i)
                    has_text_layer = bool(self._doc[i].get_text("text").strip())
                    is_scanned = (
                        not has_text_layer
                        and PdfService.is_page_scanned(self._doc, i)
                    )
                page_info = PdfPageInfo(
                    page_index=i,
                    rotation=rotation,
                    has_text_layer=has_text_layer,
                    text_layers=[],  # 延迟加载：预览/tooltip 时按需 detect
                    is_scanned=is_scanned,
                    rect=page_rect,
                )
                self.page_ready.emit(i, page_info)
                # 协作式让步：fitz 调用持 GIL，连续跑饿死主线程 Qt 事件循环。
                time.sleep(0)
            except Exception as e:
                logger.error("PdfLoadWorker page %d failed: %s", i, e)
        self.all_done.emit(self._session_id)
