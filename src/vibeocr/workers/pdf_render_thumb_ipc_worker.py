"""缩略图渲染 IPC Worker — 后台线程调后端取 PNG 字节,构 QPixmap 回传。

替代原 ThumbnailRenderWorker(持 doc + doc_lock)。进程化后主进程不持 fitz,
缩略图渲染走 IPC:queue 投页索引 → 后台线程调 client.render_thumbnail(sid, page)
拿 PNG 字节 → QPixmap.loadFromData → emit thumbnail_ready。

generation 校验:请求带 gen,响应带 gen;ThumbnailModel 只在 gen 匹配时入缓存,
丢弃失效后仍在途的旧渲染结果(旋转/删除导致的 ABA 问题)。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QPixmap

if TYPE_CHECKING:
    from vibeocr.services.pdf_backend_client import PdfBackendClient

logger = logging.getLogger(__name__)

_STOP = object()


class ThumbnailIpcWorker(QThread):
    """缩略图 IPC 渲染 worker。

    Signals:
        thumbnail_ready(page_index, pixmap, gen)
    """

    thumbnail_ready = Signal(int, object, int)  # (page_index, QPixmap, gen)

    def __init__(
        self,
        client: "PdfBackendClient",
        session_id: str,
        size: int = 160,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._session_id = session_id
        self._size = size
        self._queue: queue.Queue = queue.Queue()
        self._pending: set[int] = set()
        self._pending_lock = threading.Lock()
        self._gen_map: dict[int, int] = {}  # page_index → 最新 gen
        self._cancelled = False

    def request(self, page_index: int, gen: int = 0) -> None:
        """请求渲染页。gen 用于失效后丢弃旧结果。"""
        with self._pending_lock:
            if page_index in self._pending:
                # 已在队列:更新 gen(取较大值,确保新 invalidate 生效)
                self._gen_map[page_index] = max(self._gen_map.get(page_index, 0), gen)
                return
            self._pending.add(page_index)
            self._gen_map[page_index] = gen
        self._queue.put(page_index)

    def cancel(self) -> None:
        """取消:投哨兵,worker 退出。"""
        self._cancelled = True
        self._queue.put(_STOP)

    def run(self) -> None:
        while True:
            try:
                item = self._queue.get()
            except queue.Empty:
                continue
            if item is _STOP:
                return
            page_index = item  # type: ignore[assignment]
            with self._pending_lock:
                self._pending.discard(page_index)
                gen = self._gen_map.get(page_index, 0)
            if self._cancelled:
                return
            try:
                png_bytes = self._client.render_thumbnail(
                    self._session_id, page_index, size=self._size
                )
                pixmap = QPixmap()
                if pixmap.loadFromData(png_bytes, "PNG"):
                    pixmap = pixmap.scaled(
                        self._size, self._size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.thumbnail_ready.emit(page_index, pixmap, gen)
                else:
                    logger.warning("[thumb-ipc] 页 %d PNG 解析失败", page_index)
            except Exception as e:  # noqa: BLE001
                logger.error("[thumb-ipc] 渲染页 %d 失败: %s", page_index, e)
            # 协作式让步
            time.sleep(0)
