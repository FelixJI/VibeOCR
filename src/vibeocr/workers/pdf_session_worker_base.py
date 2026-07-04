"""单 doc 绑定的 PDF 后台任务基类。

 PdfLoadWorker / PdfRenderWorker / PdfMutateWorker 等单 session worker 的
共同脚手架（session_id / doc / pdf_document / doc_lock / 协作式取消 /
统一错误处理），消除每个 worker 各自重复的 ~15 行模板。

不复用 core/base_worker.py 的 BaseWorker：它的抽象是 item-loop +
_get_items/_process_item，与本类所服务的「一次性 dispatch + handler」语义
不匹配。两者并存，各司其职。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    import threading

    import fitz

    from vibeocr.models.pdf_document import PdfDocument

logger = logging.getLogger(__name__)


class PdfSessionWorker(QThread):
    """单 doc 绑定的 PDF 后台任务基类（一次任务一实例）。

    子类只需：
        - 声明自己的额外 Signal（page_done / progress / all_done / failed 等）
        - 实现 run()（建议用 self._run_safely(fn) 包裹以统一错误处理）
        - __init__ 里 super().__init__(...) 后再存自己的额外字段

    继承得到：
        - session_id / doc / pdf_document / doc_lock 字段
        - 协作式取消：cancel() / is_cancelled
        - _run_safely(fn, *args)：统一 try/except + 日志 + emit failed
    """

    failed = Signal(str, str)  # (session_id, error_msg)

    def __init__(
        self,
        session_id: str,
        doc: "fitz.Document",
        pdf_document: "PdfDocument",
        doc_lock: "threading.RLock",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._doc = doc
        self._pdf_document = pdf_document
        self._doc_lock = doc_lock
        self._cancelled = False

    def cancel(self) -> None:
        """协作式取消：设置标志，任务在下一个检查点停止。"""
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def session_id(self) -> str:
        return self._session_id

    def _run_safely(self, fn, *args, **kwargs) -> None:
        """统一 try/except + 日志 + emit failed。

        子类 run() 应这样用：
            def run(self):
                self._run_safely(self._do_work)
        而非每个子类各写一遍 try/except/log/emit。
        """
        try:
            fn(*args, **kwargs)
        except Exception as e:
            logger.error(
                "%s 任务失败: %s", type(self).__name__, e, exc_info=True
            )
            self.failed.emit(self._session_id, str(e))
