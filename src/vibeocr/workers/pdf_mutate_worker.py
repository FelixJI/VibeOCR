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
        handlers = {
            TaskKind.DELETE_TEXT_LAYER: self._run_delete_text_layer,
            TaskKind.ROTATE: self._run_rotate,
            TaskKind.DELETE_PAGES: self._run_delete_pages,
            TaskKind.REORDER: self._run_reorder,
            TaskKind.INSERT_BLANK: self._run_insert_blank,
            TaskKind.INSERT_FROM: self._run_insert_from,
            TaskKind.SAVE: self._run_save,
            TaskKind.SAVE_AS: self._run_save_as,
        }
        handler = handlers.get(kind)
        if handler is None:
            raise ValueError(f"未支持的任务类型: {kind}")
        return handler

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

    def _run_rotate(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        indices = self._task.page_indices
        total = len(indices)
        # 旋转是快速原子操作，一次性批量应用；之后逐页 emit 仅用于 UI 进度反馈。
        # （区别于 DELETE_TEXT_LAYER 的逐页加锁——rotate 无需逐页串行化。）
        with self._doc_lock:
            PdfService.rotate_pages(
                self._doc, self._pdf_document, indices, self._task.angle
            )
        for n, idx in enumerate(indices):
            if self._cancelled:
                break
            self.page_done.emit(idx, None)
            self.progress.emit(n + 1, total)
        self.all_done.emit(self._session_id, None)

    def _run_delete_pages(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        with self._doc_lock:
            PdfService.delete_pages(
                self._doc, self._pdf_document, self._task.page_indices
            )
        self.all_done.emit(self._session_id, None)

    def _run_reorder(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        with self._doc_lock:
            PdfService.reorder_pages(
                self._doc, self._pdf_document, self._task.new_order
            )
        self.all_done.emit(self._session_id, None)

    def _run_insert_blank(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        with self._doc_lock:
            PdfService.insert_blank_page(
                self._doc, self._pdf_document,
                self._task.after_index, self._task.width, self._task.height,
            )
        self.all_done.emit(self._session_id, None)

    def _run_insert_from(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        with self._doc_lock:
            PdfService.insert_pages_from(
                self._doc, self._pdf_document,
                self._task.source_path, self._task.after_index,
            )
        self.all_done.emit(self._session_id, None)

    def _run_save(self) -> None:
        self._do_save(path=None)

    def _run_save_as(self) -> None:
        self._do_save(path=self._task.path)

    def _do_save(self, path: str | None) -> None:
        from vibeocr.services.pdf_service import PdfService

        try:
            with self._doc_lock:
                save_result = PdfService.save_with_rewrite(
                    self._doc, self._pdf_document, path=path,
                    pdf_settings=self._task.pdf_settings,
                )
                # 全量压缩覆盖时 doc 已 close+reopen，更新本地引用（manager 的
                # _on_mutate_all_done 会据 save_result.new_doc 更新 session.doc）
                if save_result.new_doc is not None:
                    self._doc = save_result.new_doc
            # save_with_rewrite 内部已 rewrite，一次性 emit 进度
            total = len(save_result.rewritten_pages)
            self.progress.emit(total, total)
            self.all_done.emit(self._session_id, save_result)
        except Exception as e:
            logger.error("保存失败: %s", e, exc_info=True)
            self.failed.emit(self._session_id, str(e))
