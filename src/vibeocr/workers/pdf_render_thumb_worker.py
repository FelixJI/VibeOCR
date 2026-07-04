"""按需渲染缩略图的 Worker — 只渲染被请求的页，滚动到哪渲到哪。

替代原 PdfLoadWorker 打开即全量渲染的策略（大 PDF 全量渲染需数十秒，
期间缩略图空白）。本 worker 持有任务队列，request(page_index) 投递请求，
后台单线程串行渲染（fitz 不线程安全，且避免并发内存峰值）。
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

from vibeocr.services.pdf_service import PdfService

if TYPE_CHECKING:
    import fitz

logger = logging.getLogger(__name__)

# 停止哨兵：投入队列让 worker 正常退出
_STOP = object()


class ThumbnailRenderWorker(QThread):
    """单线程按需渲染缩略图。

    Signals:
        thumbnail_ready(page_index: int, pixmap: QPixmap)
            单页渲染完成。
    """

    thumbnail_ready = Signal(int, QPixmap)

    def __init__(
        self,
        doc: fitz.Document,
        doc_lock: threading.RLock,
        dpi: int = 96,
        size: int = 160,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._doc = doc
        self._doc_lock = doc_lock
        self._dpi = dpi
        self._size = size
        self._queue: queue.Queue = queue.Queue()
        self._pending: set[int] = set()  # 已在队列中待渲染的页（去重）
        self._pending_lock = threading.Lock()
        self._cancelled = False

    def request(self, page_index: int) -> None:
        """投递单页渲染请求（已在队列中则跳过，避免重复渲染）。"""
        if self._cancelled:
            return
        with self._pending_lock:
            if page_index in self._pending:
                return
            self._pending.add(page_index)
        self._queue.put(page_index)

    def stop(self) -> None:
        """投递停止哨兵，让 worker 处理完当前页后正常退出。"""
        self._queue.put(_STOP)

    def cancel(self) -> None:
        """立即清空待处理队列并停止 worker。"""
        self._cancelled = True
        with self._pending_lock:
            self._pending.clear()
        # 清空队列
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put(_STOP)

    def run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=10)
            except queue.Empty:
                # 长时间无任务，退出避免线程泄漏（调用方需重新 start）
                return
            if item is _STOP:
                return
            page_index = item  # type: ignore[assignment]
            with self._pending_lock:
                self._pending.discard(page_index)
            if self._cancelled:
                return
            try:
                with self._doc_lock:
                    pixmap = PdfService.render_page(
                        self._doc, page_index, dpi=self._dpi
                    )
                scaled = pixmap.scaled(
                    self._size,
                    self._size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
                self.thumbnail_ready.emit(page_index, scaled)
            except Exception as e:  # noqa: BLE001 — 单页失败不阻断其余
                logger.error(
                    "ThumbnailRenderWorker 渲染页 %d 失败: %s", page_index, e
                )
