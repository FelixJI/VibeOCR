"""PDF 通用变更 Worker — 后台执行 fitz 重活（删除文字层/旋转/保存/自动摆正等）。

承接所有原本阻塞主线程的 fitz CPU 密集操作，协作式取消。
自动摆正（原独立 PdfDeskewWorker）已并入 AUTO_DESKEW 任务类型。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal

from vibeocr.workers.pdf_session_worker_base import PdfSessionWorker

if TYPE_CHECKING:
    import threading

    import fitz

    from vibeocr.models.pdf_document import PdfDocument
    from vibeocr.services.ocr_service_base import OCRServiceBase

logger = logging.getLogger(__name__)

# 自动摆正方向检测渲染 DPI（低于 OCR 的 300，方向分类不需高分辨率，更快）
_DETECT_DPI = 150


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
    AUTO_DESKEW = "auto_deskew"  # 自动摆正（方向检测+旋转+文字层同步）


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
        AUTO_DESKEW: page_indices, ocr_service, pdf_settings
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
    ocr_service: object | None = None  # AUTO_DESKEW 用


class PdfMutateWorker(PdfSessionWorker):
    """通用 PDF 变更 Worker（单 doc 绑定，一次任务一实例）。

    Signals:
        page_done(page_index: int, payload: object)  逐页任务
            AUTO_DESKEW 时 payload 为 bool(was_corrected)
        progress(current: int, total: int)
        all_done(session_id: str, result: object)     成功
            AUTO_DESKEW 时 result 为 summary dict（corrected/skipped/corrected_pages）
        failed(session_id: str, error_msg: str)       整体失败（继承自基类）
    """

    page_done = Signal(int, object)
    progress = Signal(int, int)
    all_done = Signal(str, object)

    def __init__(
        self,
        session_id: str,
        doc: "fitz.Document",
        pdf_document: "PdfDocument",
        doc_lock: "threading.RLock",
        task: MutateTask,
        parent=None,
    ) -> None:
        super().__init__(session_id, doc, pdf_document, doc_lock, parent)
        self._task = task

    def run(self) -> None:
        self._run_safely(lambda: self._dispatch()())

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
            TaskKind.AUTO_DESKEW: self._run_auto_deskew,
        }
        handler = handlers.get(kind)
        if handler is None:
            raise ValueError(f"未支持的任务类型: {kind}")
        return handler

    # ---- 自动摆正（原 PdfDeskewWorker，已合并） ------------------------

    @staticmethod
    def angle_to_correction(angle: int) -> int:
        """PaddleOCR 方向角 → PDF 旋转纠正角。

        preproc_angle = 内容相对正向的「顺时针」偏转度数。
        让内容回正 → 把页面逆时针转 angle 度 → rotate_pages 的 angle 参数 = (-angle) % 360。
        映射: 0→0, 90→270, 180→180, 270→90。
        """
        return (-int(angle)) % 360

    def _run_auto_deskew(self) -> None:
        from vibeocr.models.ocr_options import OCROptions
        from vibeocr.services.pdf_service import PdfService

        ocr_service = self._task.ocr_service
        if ocr_service is None:
            raise ValueError("AUTO_DESKEW 任务缺少 ocr_service")
        page_indices = self._task.page_indices
        total = len(page_indices)
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
                for idx in page_indices
            ]

        # 2. 方向检测（锁外，模型推理不碰 doc）
        options = OCROptions(
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        results = ocr_service.recognize_batch(images, options)

        # 3. 逐页纠正
        corrected_pages: list[int] = []
        for n, (idx, res) in enumerate(zip(page_indices, results)):
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
                            pdf_settings=self._task.pdf_settings,
                            font_path=None,
                        )
                    info.deskewed = True
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
