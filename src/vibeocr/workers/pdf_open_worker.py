"""PDF 异步打开 Worker — 批量导入时在后台线程打开多个文件，避免冻结主线程。

把原本在主线程串行执行的 ``fitz.open`` 循环移入后台线程。对每个文件：

1. 调 ``PdfService.open_doc``（含 fitz.open + 轻量占位页创建）；
2. 成功 → emit ``doc_opened``，把 (doc, pdf_document, doc_lock) 交还主线程；
3. 失败 → emit ``open_failed``，携带错误信息。

每个文件的 ``fitz.Document`` 在本线程 open，之后交给 ``PdfLoadWorker`` 时由
``doc_lock`` 串行化所有访问（open 动作换线程，访问模式不变）。
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

from vibeocr.services.pdf_service import PdfService

if TYPE_CHECKING:
    import fitz

    from vibeocr.models.pdf_document import PdfDocument

logger = logging.getLogger(__name__)


class PdfOpenWorker(QThread):
    """批量异步打开 PDF 文件的 Worker。

    Signals:
        doc_opened(file_path: str, doc: fitz.Document,
                   pdf_document: PdfDocument, doc_lock: threading.RLock)
            单个文件打开成功。
        open_failed(file_path: str, error: str)
            单个文件打开失败。
        open_progress(current: int, total: int)
            开始处理第 current 个文件（1-based），共 total 个。
        all_done()
            全部文件处理完毕（成功或失败）。
    """

    doc_opened = Signal(str, object, object, object)
    open_failed = Signal(str, str)
    open_progress = Signal(int, int)
    all_done = Signal()

    def __init__(self, file_paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self._file_paths = list(file_paths)
        self._cancelled = False

    def cancel(self) -> None:
        """协作式取消：在下一个文件处理前生效。"""
        self._cancelled = True

    def run(self) -> None:
        total = len(self._file_paths)
        for i, path in enumerate(self._file_paths):
            if self._cancelled:
                break
            self.open_progress.emit(i + 1, total)
            try:
                doc, pdf_document = PdfService.open_doc(path)
            except FileNotFoundError as e:
                logger.warning("PdfOpenWorker: 文件不存在 %s", path)
                self.open_failed.emit(path, str(e))
                continue
            except Exception as e:  # noqa: BLE001 — 逐文件隔离，单文件失败不阻断其余
                logger.warning("PdfOpenWorker: 打开失败 %s: %s", path, e)
                self.open_failed.emit(path, str(e))
                continue
            # 每个会话独立锁：open 在本线程完成后，该 doc 的后续访问
            # （PdfLoadWorker 渲染缩略图）全部在同一把 doc_lock 下。
            doc_lock = threading.RLock()
            self.doc_opened.emit(path, doc, pdf_document, doc_lock)
        self.all_done.emit()
