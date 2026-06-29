"""PDF 通用变更 Worker — 后台执行 fitz 重活（删除文字层/旋转/保存等）。

承接所有原本阻塞主线程的 fitz CPU 密集操作，协作式取消。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    import threading

    import fitz

    from vibeocr.models.pdf_document import PdfDocument

logger = logging.getLogger(__name__)


class TaskKind(Enum):
    """变更任务类型。"""

    DELETE_TEXT_LAYER = "delete_text_layer"
    ROTATE = "rotate"
    DELETE_PAGES = "delete_pages"
    REORDER = "reorder"
    INSERT_BLANK = "insert_blank"
    INSERT_FROM = "insert_from"
    SAVE = "save"
    SAVE_AS = "save_as"


@dataclass
class MutateTask:
    """单次变更任务描述（frozen 语义：构造后不改字段）。

    各 kind 所需字段：
        DELETE_TEXT_LAYER: page_indices
        ROTATE: page_indices, angle
        DELETE_PAGES: page_indices
        REORDER: new_order
        INSERT_BLANK: after_index, width, height
        INSERT_FROM: source_path, after_index
        SAVE: path(=None), pdf_settings
        SAVE_AS: path, pdf_settings
    """

    kind: TaskKind
    page_indices: list[int] = field(default_factory=list)
    angle: int = 0
    new_order: list[int] = field(default_factory=list)
    after_index: int = 0
    width: float = 612.0
    height: float = 792.0
    source_path: str | None = None
    path: str | None = None
    pdf_settings: object | None = None


class PdfMutateWorker(QThread):
    """通用 PDF 变更 Worker（单 doc 绑定，一次任务一实例）。

    Signals:
        page_done(page_index: int, payload: object)  逐页任务
        progress(current: int, total: int)
        all_done(session_id: str, result: object)     成功
        failed(session_id: str, error_msg: str)       整体失败
    """

    page_done = Signal(int, object)
    progress = Signal(int, int)
    all_done = Signal(str, object)
    failed = Signal(str, str)

    def __init__(
        self,
        session_id: str,
        doc: "fitz.Document",
        pdf_document: "PdfDocument",
        doc_lock: "threading.RLock",
        task: MutateTask,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._doc = doc
        self._pdf_document = pdf_document
        self._doc_lock = doc_lock
        self._task = task
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def session_id(self) -> str:
        return self._session_id

    def run(self) -> None:
        try:
            handler = self._dispatch()
            handler()
        except Exception as e:
            logger.error("PdfMutateWorker 任务失败: %s", e, exc_info=True)
            self.failed.emit(self._session_id, str(e))

    def _dispatch(self):
        kind = self._task.kind
        if kind == TaskKind.DELETE_TEXT_LAYER:
            return self._run_delete_text_layer
        raise ValueError(f"未支持的任务类型: {kind}")

    def _run_delete_text_layer(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        indices = self._task.page_indices
        total = len(indices)
        residual_pages: list[int] = []
        for n, page_index in enumerate(indices):
            if self._cancelled:
                break
            try:
                with self._doc_lock:
                    page = self._doc[page_index]
                    if not page.get_text().strip():
                        # 无文字 → 跳过 redact，仍清状态
                        PdfService.delete_text_layers(
                            self._doc, self._pdf_document, page_index
                        )
                        self.page_done.emit(page_index, (0, 0, False))
                    else:
                        deleted, rounds, residual = PdfService.delete_text_layers(
                            self._doc, self._pdf_document, page_index
                        )
                        self.page_done.emit(page_index, (deleted, rounds, residual))
                        if residual:
                            residual_pages.append(page_index)
            except Exception as e:
                logger.error("删除页 %d 文字层失败: %s", page_index, e)
                self.page_done.emit(page_index, None)
            self.progress.emit(n + 1, total)
        self.all_done.emit(self._session_id, {"residual_pages": residual_pages})
