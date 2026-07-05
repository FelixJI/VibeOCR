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
            # 永久阻塞等待任务：worker 生命周期由 cancel() 投 _STOP 哨兵显式终止，
            # 不再用 10s idle 超时自杀（自杀后 request_render 不检查 isFinished()
            # 会把请求投进无人消费的队列，导致缩略图永久空白）。
            try:
                item = self._queue.get()
            except queue.Empty:
                continue
            if item is _STOP:
                return
            page_index = item  # type: ignore[assignment]
            with self._pending_lock:
                self._pending.discard(page_index)
            if self._cancelled:
                return
            try:
                # 带超时获取 doc_lock：OCR 前置渲染（PdfRenderWorker）逐页持锁
                # 渲 300DPI（~500ms/页），本 worker 渲 96DPI（~50ms/页）。若用
                # 阻塞 with 会饿死——OCR 跑时缩略图永远拿不到锁。这里 0.5s 抢不到
                # 就让出（重新投递到队尾），让 OCR 翻页间隙的空闲窗口能插入缩略图。
                if not self._doc_lock.acquire(timeout=0.5):
                    # 锁被 OCR 长持有：重新入队尾，避免饥饿，也让出当前页给 OCR
                    with self._pending_lock:
                        if page_index not in self._pending and not self._cancelled:
                            self._pending.add(page_index)
                            self._queue.put(page_index)
                    continue
                try:
                    pixmap = PdfService.render_page(
                        self._doc, page_index, dpi=self._dpi
                    )
                finally:
                    self._doc_lock.release()
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
