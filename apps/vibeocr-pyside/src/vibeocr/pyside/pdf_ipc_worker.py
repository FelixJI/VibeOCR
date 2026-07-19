"""PySide PDF RPC worker：在线程中调用共享 WorkerHost 客户端。

PDF 模块进程化后,所有 fitz 操作在后端子进程。主进程通过 PdfBackendClient
(httpx)调用,这些调用是阻塞的,不能在 GUI 线程跑。本 worker 包装常见的
长耗时 IPC 操作(批量打开/加载/变更/删除文字层/保存/OCR 写层),把结果
转成 Qt 信号。

协作式取消:通过 cancel_event 标志,后端侧也有 cancel_event(POST /cancel)。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    from vibeocr.client.pdf import PdfBackendClient

logger = logging.getLogger(__name__)


class PdfIpcOpenWorker(QThread):
    """批量打开 PDF(后台 IPC open + 流式 load)。

    两阶段渐进展示:
    1. open_session(快,fitz.open + 占位页)→ 立即 emit doc_opened(占位 model)
       主进程收到后立刻创建 session + 显示页数 + 占位缩略图
    2. load_stream(逐页文字层检测)→ 每页 emit page_loaded(page_index + page_mirror)
       主进程逐页染色文字层状态,无需等全部检测完

    Signals:
        doc_opened(file_path, session_id, model_mirror_dict)  open 完成(占位)
        page_loaded(file_path, page_index, page_mirror_dict)  单页 load 完成
        load_progress(file_path, current, total)              load 进度
        open_failed(file_path, error_msg)
        open_progress(current, total)                         批量文件进度
        all_done()
    """

    doc_opened = Signal(str, str, object)  # (file_path, session_id, 占位 full_model)
    page_loaded = Signal(str, int, object)  # (file_path, page_index, page_mirror_dict)
    load_progress = Signal(str, int, int)  # (file_path, current, total)
    open_failed = Signal(str, str)
    open_progress = Signal(int, int)
    all_done = Signal()

    def __init__(
        self,
        client: PdfBackendClient,
        paths: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._paths = paths
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:
        total = len(self._paths)
        for n, path in enumerate(self._paths):
            if self._cancelled:
                break
            try:
                # 阶段 1:open(快)→ 立即 emit 占位 model
                open_resp = self._client.open_session(path)
                self.doc_opened.emit(path, open_resp.session_id, open_resp.model)

                # 阶段 2:流式 load → 逐页 emit
                for ev in self._client.load_stream(open_resp.session_id):
                    if self._cancelled:
                        break
                    if ev.page_index is not None:
                        self.page_loaded.emit(path, ev.page_index, ev.page_payload)
                    if ev.total > 0:
                        self.load_progress.emit(path, ev.current, ev.total)
                    if ev.message == "done":
                        break
            except Exception as e:
                logger.error("[ipc-open] 打开 %s 失败: %s", path, e)
                self.open_failed.emit(path, str(e))
            self.open_progress.emit(n + 1, total)
        self.all_done.emit()


class PdfIpcMutateWorker(QThread):
    """通用变更操作(后台 IPC),支持流式进度。

    Signals:
        progress(session_id, current, total)
        page_done(session_id, page_index, payload)   逐页结果(可选)
        all_done(session_id, diff, extra)
        failed(session_id, error_msg)
    """

    progress = Signal(str, int, int)
    page_done = Signal(str, int, object)
    all_done = Signal(str, object, object)  # (session_id, diff, extra)
    failed = Signal(str, str)

    def __init__(
        self,
        client: PdfBackendClient,
        session_id: str,
        op: str,
        params: dict[str, Any],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._session_id = session_id
        self._op = op  # "rotate" / "delete_pages" / "save" / "delete_text_layers" / ...
        self._params = params
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        # 通知后端取消(协作式)
        try:
            self._client.cancel(self._session_id)
        except Exception:
            pass

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def session_id(self) -> str:
        return self._session_id

    def run(self) -> None:
        try:
            if self._op == "delete_text_layers":
                # 流式:迭代 ProgressEvent
                pages = self._params.get("pages", [])
                for ev in self._client.delete_text_layers_stream(self._session_id, pages):
                    if self._cancelled:
                        break
                    if ev.page_index is not None:
                        self.page_done.emit(self._session_id, ev.page_index, ev.page_payload)
                    if ev.total > 0:
                        self.progress.emit(self._session_id, ev.current, ev.total)
                # 流结束后取一次 model 拿 diff(删除文字层改变 has_text_layer)
                # 简化:用 get_model 构造 full diff
                from vibeocr.ipc.schemas import ModelDiff
                full_model = self._client.get_model(self._session_id)
                extra = {"residual_pages": []}
                self.all_done.emit(self._session_id, ModelDiff(full_model=full_model), extra)
                return

            # 非流式:单次调用
            resp = self._call_op()
            diff = getattr(resp, "diff", None)
            extra = getattr(resp, "extra", None)
            # 保存的 path 字段
            if hasattr(resp, "path"):
                extra = {"path": resp.path}
            self.all_done.emit(self._session_id, diff, extra)
        except Exception as e:
            logger.error("[ipc-mutate] %s 失败: %s", self._op, e)
            self.failed.emit(self._session_id, str(e))

    def _call_op(self):
        c = self._client
        sid = self._session_id
        p = self._params
        if self._op == "rotate":
            return c.rotate(sid, p["pages"], p["angle"])
        if self._op == "delete_pages":
            return c.delete_pages(sid, p["pages"])
        if self._op == "insert_blank":
            return c.insert_blank(sid, p["after_index"], p.get("width", 612.0), p.get("height", 792.0))
        if self._op == "insert_from":
            return c.insert_from(sid, p["source_path"], p["after_index"])
        if self._op == "move_page":
            return c.move_page(sid, p["from_index"], p["to_index"])
        if self._op == "reorder":
            return c.reorder(sid, p["new_order"])
        if self._op == "save":
            return c.save(sid, p.get("path"), p.get("pdf_settings"))
        if self._op == "add_text_layer":
            return c.add_text_layer(sid, p["page"], p["ocr_result"], p.get("pdf_settings"), p.get("overwrite", False))
        if self._op == "rewrite_text_layer":
            return c.rewrite_text_layer(sid, p["page"], p["text_blocks"], p.get("preproc_angle", 0), p.get("pdf_settings"))
        if self._op == "update_block_text":
            return c.update_block_text(sid, p["page"], p["block_index"], p["new_text"])
        raise ValueError(f"未知 op: {self._op}")
