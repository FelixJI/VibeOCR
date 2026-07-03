"""PDF 多文件会话管理器。

管理 PdfSession 集合，持有 PdfLoadWorker / PdfOcrWorker，中转信号到 UI。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from vibeocr.models.pdf_session import PdfSession
from vibeocr.services.pdf_service import PdfService
from vibeocr.workers.pdf_load_worker import PdfLoadWorker
from vibeocr.workers.pdf_open_worker import PdfOpenWorker
from vibeocr.workers.pdf_mutate_worker import MutateTask, PdfMutateWorker, TaskKind
from vibeocr.workers.pdf_export_worker import PdfExportWorker
from vibeocr.workers.pdf_ocr_worker import PdfOcrWorker
from vibeocr.workers.pdf_render_worker import PdfRenderWorker

if TYPE_CHECKING:
    from vibeocr.models.ocr_options import OCROptions
    from vibeocr.models.pdf_ocr_options import PdfGlobalSettings
    from vibeocr.services.ocr_service_base import OCRServiceBase

logger = logging.getLogger(__name__)


class PdfSessionManager(QObject):
    """PDF 多文件会话管理器。

    Signals:
        session_added(file_path: str)
        session_removed(file_path: str)
        active_changed(file_path: str)
        page_loaded(file_path: str, page_index: int)
        load_progress(file_path: str, loaded: int, total: int)
        load_done(file_path: str)
        ocr_page_done(file_path: str, page_index: int, result)
        ocr_progress(file_path: str, current: int, total: int)
        ocr_done(file_path: str, success: int, fail: int)
    """

    session_added = Signal(str)
    session_removed = Signal(str)
    active_changed = Signal(str)
    page_loaded = Signal(str, int)
    load_progress = Signal(str, int, int)
    load_done = Signal(str)
    ocr_page_done = Signal(str, int, object)
    ocr_progress = Signal(str, int, int)
    ocr_done = Signal(str, int, int)
    ocr_stats_ready = Signal(str, int, int)  # (file_path, written, skipped)
    # MinerU 模型下载状态提示（首次使用 PDF 文档解析时）
    mineru_models_status = Signal(str)
    render_progress = Signal(str, int, int)  # (file_path, current, total)
    mutate_progress = Signal(str, int, int)
    mutate_done = Signal(str, object)
    mutate_failed = Signal(str, str)
    save_done = Signal(str)
    delete_layer_done = Signal(str, list)  # (file_path, residual_pages)
    export_progress = Signal(int, int, str)   # (current, total, file_name)
    export_done = Signal(list)                 # (exported_paths)
    # 异步批量打开文件（open_sessions_async）
    open_progress = Signal(int, int)           # (current, total)
    open_failed = Signal(str, str)             # (file_path, error_msg)
    open_done = Signal()                        # 全部批量打开流程结束

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sessions: dict[str, PdfSession] = {}
        self._active_path: str | None = None
        self._load_worker: PdfLoadWorker | None = None
        self._open_worker: PdfOpenWorker | None = None
        self._pending_open_paths: list[str] = []  # open_sessions_async 待处理文件
        self._ocr_worker: PdfOcrWorker | None = None
        self._render_worker: PdfRenderWorker | None = None
        self._mutate_worker: PdfMutateWorker | None = None
        self._export_worker: PdfExportWorker | None = None
        self._ocr_service: OCRServiceBase | None = None
        self._pdf_settings: PdfGlobalSettings | None = None
        self._overwrite_text_layer: bool = False

    @property
    def active_session(self) -> PdfSession | None:
        if self._active_path is not None:
            return self._sessions.get(self._active_path)
        return None

    @property
    def session_paths(self) -> list[str]:
        return list(self._sessions.keys())

    def get_session(self, file_path: str) -> PdfSession | None:
        return self._sessions.get(file_path)

    def set_ocr_service(self, service: OCRServiceBase) -> None:
        self._ocr_service = service

    @property
    def is_ocr_ready(self) -> bool:
        return self._ocr_service is not None

    def get_modified_sessions(self) -> list[tuple[str, PdfSession]]:
        return [(p, s) for p, s in self._sessions.items() if s.is_modified]

    @property
    def load_worker_session_id(self) -> str | None:
        if self._load_worker is not None:
            return self._load_worker.session_id
        return None

    # ---- session lifecycle ------------------------------------------

    def open_session(self, file_path: str) -> PdfSession:
        if file_path in self._sessions:
            self.switch_session(file_path)
            return self._sessions[file_path]

        doc, pdf_doc = PdfService.open_doc(file_path)
        session = PdfSession(file_path=file_path, doc=doc, pdf_document=pdf_doc)
        self._sessions[file_path] = session

        prev_path = self._active_path
        self._active_path = file_path

        self.session_added.emit(file_path)
        if prev_path != file_path:
            self.active_changed.emit(file_path)

        self._start_load_worker(session)
        return session

    # ---- async batch open -------------------------------------------

    def open_sessions_async(self, paths: list[str]) -> None:
        """批量异步打开多个 PDF 文件（后台线程，避免冻结主线程）。

        fitz.open 在 PdfOpenWorker 的后台线程执行；每个文件打开完成后，
        doc_opened 回到主线程创建会话、emit session_added，并启动缩略图加载。
        失败的文件通过 open_failed 信号上报，不阻断其余文件。

        第一个新文件成为 active 并触发一次 active_changed；后续文件不重复
        切换 active（避免每个文件都触发 UI 全量重建缩略图列表）。
        """
        # 过滤已打开的文件，避免重复创建会话
        new_paths = [p for p in paths if p not in self._sessions]
        if not new_paths:
            # 全部已打开：切到第一个
            if paths:
                self.switch_session(paths[0])
            return

        self._cancel_open_worker()
        self._pending_open_paths = list(new_paths)

        self._open_worker = PdfOpenWorker(new_paths)
        self._open_worker.doc_opened.connect(self._on_doc_opened)
        self._open_worker.open_failed.connect(self._on_open_failed)
        self._open_worker.open_progress.connect(self.open_progress)
        self._open_worker.all_done.connect(self._on_open_all_done)
        # 线程结束后安全释放，避免 "Destroyed while still running" 警告
        self._open_worker.finished.connect(self._open_worker.deleteLater)
        self._open_worker.start()

    def _on_doc_opened(self, file_path: str, doc, pdf_document, doc_lock) -> None:
        """PdfOpenWorker 回调：在主线程创建会话。

        仅第一个新文件成为 active 并启动 load worker；后续文件静默加入会话表，
        切换到它们时（switch_session）才按需启动 load worker。
        避免批量导入时每个文件都 cancel+restart load worker + 全量重建 UI。
        """
        session = PdfSession(
            file_path=file_path,
            doc=doc,
            pdf_document=pdf_document,
            doc_lock=doc_lock,
        )
        self._sessions[file_path] = session

        prev_active = self._active_path
        self.session_added.emit(file_path)

        # 第一个成功打开的新文件成为 active（后续不再切换）。
        became_active = prev_active is None
        if became_active:
            self._active_path = file_path
            self.active_changed.emit(file_path)
            # 仅为活动会话启动缩略图加载（非活动会话延迟到 switch_session）
            self._start_load_worker(session)

    def _on_open_failed(self, file_path: str, error: str) -> None:
        logger.warning("异步打开失败 %s: %s", file_path, error)
        self.open_failed.emit(file_path, error)

    def _on_open_all_done(self) -> None:
        self._open_worker = None
        self._pending_open_paths = []
        self.open_done.emit()

    def _cancel_open_worker(self) -> None:
        if self._open_worker is not None:
            self._open_worker.cancel()
            _wait_thread(self._open_worker, timeout=5000)
            self._open_worker = None
        self._pending_open_paths = []

    def switch_session(self, file_path: str) -> None:
        if file_path not in self._sessions:
            return
        if self._active_path == file_path:
            return

        self._cancel_load_worker()
        self._cancel_ocr_pipeline()
        self._cancel_mutate_worker()
        self._active_path = file_path
        self.active_changed.emit(file_path)

        session = self._sessions[file_path]
        if session.load_progress < 1.0:
            self._start_load_worker(session)

    def close_session(self, file_path: str) -> None:
        if file_path not in self._sessions:
            return

        if self.load_worker_session_id == file_path:
            self._cancel_load_worker()

        session = self._sessions.pop(file_path)
        session.doc.close()

        self.session_removed.emit(file_path)

        if self._active_path == file_path:
            self._active_path = None
            if self._sessions:
                last_path = list(self._sessions.keys())[-1]
                self._active_path = last_path
                self.active_changed.emit(last_path)

    # ---- load worker ------------------------------------------------

    def _start_load_worker(self, session: PdfSession) -> None:
        self._cancel_load_worker()

        self._load_worker = PdfLoadWorker(
            session_id=session.file_path,
            doc=session.doc,
            pdf_document=session.pdf_document,
            loaded_pages=session.loaded_pages,
            doc_lock=session.doc_lock,
            thumbnail_dpi=session.pdf_document.thumbnail_dpi,
        )
        self._load_worker.page_ready.connect(self._on_page_ready)
        self._load_worker.all_done.connect(self._on_load_all_done)
        self._load_worker.start()

    def rerender_thumbnails_async(self, page_indices: list[int]) -> None:
        """后台重新渲染指定页的缩略图（旋转全部后调用，避免主线程逐页渲染卡顿）。

        先取消现有 load worker（避免其排队中的 page_ready 把页面标记为已加载，
        导致新 worker 跳过它们），再使指定页 thumbnail 失效并从 loaded_pages 移除，
        最后重启 PdfLoadWorker 异步重渲染（跳过仍有效的已加载页）。
        """
        session = self.active_session
        if session is None or not page_indices:
            return
        self._cancel_load_worker()
        for idx in page_indices:
            if 0 <= idx < len(session.pdf_document.pages):
                session.pdf_document.pages[idx].thumbnail = None
            session.loaded_pages.discard(idx)
        self._start_load_worker(session)

    def _cancel_load_worker(self) -> None:
        if self._load_worker is not None:
            self._load_worker.cancel()
            _wait_thread(self._load_worker, timeout=3000)
            self._load_worker = None

    def _on_page_ready(self, page_index: int, page_info, pixmap) -> None:
        worker = self._load_worker
        if worker is None:
            return
        session = self._sessions.get(worker.session_id)
        if session is None:
            return
        page_info.thumbnail = pixmap
        if page_index < len(session.pdf_document.pages):
            session.pdf_document.pages[page_index] = page_info
        session.loaded_pages.add(page_index)
        loaded = len(session.loaded_pages)
        total = session.pdf_document.page_count
        self.page_loaded.emit(session.file_path, page_index)
        self.load_progress.emit(session.file_path, loaded, total)

    def _on_load_all_done(self, session_id: str) -> None:
        self.load_done.emit(session_id)
        self._load_worker = None

    # ---- OCR --------------------------------------------------------

    def get_pages_without_text_layer(self, session_id: str) -> list[int]:
        """返回该 session 中所有无文字层的页索引列表。"""
        session = self._sessions.get(session_id)
        if session is None:
            return []
        return [
            p.page_index for p in session.pdf_document.pages if not p.has_text_layer
        ]

    def start_ocr(
        self,
        page_indices: list[int],
        ocr_options: OCROptions | None = None,
        pdf_settings: PdfGlobalSettings | None = None,
        overwrite: bool = False,
    ) -> None:
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        if pdf_settings is None:
            pdf_settings = PdfGlobalSettings()

        session = self.active_session
        if session is None or self._ocr_service is None:
            return

        # MinerU 文档解析：首次使用需下载模型（数 GB）。
        # 已成功过（pipeline_success 标记）则跳过，避免每次重复检测。
        if self._is_mineru_first_use(ocr_options):
            if not self._ensure_mineru_models_blocking(session.file_path):
                return

        self._cancel_ocr_pipeline()

        self._pdf_settings = pdf_settings
        self._overwrite_text_layer = overwrite
        session.reset_ocr_stats()

        # 流式：render worker 后台逐页渲染 → queue → ocr worker 消费
        render_queue: Queue = Queue(maxsize=2)
        self._render_worker = PdfRenderWorker(
            session_id=session.file_path,
            doc=session.doc,
            doc_lock=session.doc_lock,
            page_indices=page_indices,
            pdf_settings=pdf_settings,
            render_queue=render_queue,
        )
        self._render_worker.render_progress.connect(self._on_render_progress)
        self._render_worker.all_done.connect(self._on_render_worker_done)

        self._ocr_worker = PdfOcrWorker(
            session_id=session.file_path,
            ocr_service=self._ocr_service,
            ocr_options=ocr_options,
            render_queue=render_queue,
        )
        self._ocr_worker.page_done.connect(self._on_ocr_page_done)
        self._ocr_worker.progress.connect(self._on_ocr_progress)
        self._ocr_worker.all_done.connect(self._on_ocr_all_done)

        self._render_worker.start()
        self._ocr_worker.start()

    def _on_render_progress(self, session_id: str, current: int, total: int) -> None:
        self.render_progress.emit(session_id, current, total)

    def _on_render_worker_done(self, session_id: str) -> None:
        """render worker 正常完成 → 释放引用（避免 QThread 对象残留）。"""
        self._render_worker = None

    def _is_mineru_first_use(self, ocr_options: OCROptions | None) -> bool:
        """判断是否为 MinerU 文档解析管道且模型尚未下载成功"""
        if ocr_options is None:
            return False
        try:
            from vibeocr.core.pipelines import OCRPipeline

            if ocr_options.pipeline != OCRPipeline.DOCUMENT_PARSING:
                return False
            from vibeocr.env_manager import get_project_root
            from vibeocr.pipeline_status import is_pipeline_ever_succeeded

            return not is_pipeline_ever_succeeded("MinerU", get_project_root())
        except Exception:
            return False

    def _ensure_mineru_models_blocking(self, file_path: str) -> bool:
        """下载 MinerU 模型（阻塞主线程，期间通过信号反馈进度）。

        首次使用 PDF 文档解析时调用。模型数 GB，下载耗时较长，
        通过 mineru_models_status 信号通知 UI（状态栏/进度提示）。
        下载期间周期性 processEvents 保持 UI 可响应（避免"无响应"假死）。
        """
        from PySide6.QtWidgets import QApplication

        from vibeocr.env_manager import ensure_mineru_models, get_project_root

        def on_progress(stage: str, message: str):
            self.mineru_models_status.emit(f"[{stage}] {message}")
            # 保持 UI 响应（下载进度逐行回调，每次让出事件循环）
            QApplication.processEvents()

        self.mineru_models_status.emit("首次使用文档解析，正在下载 MinerU 模型（约数 GB）...")
        ok, msg = ensure_mineru_models(get_project_root(), progress_callback=on_progress)
        if ok:
            self.mineru_models_status.emit("MinerU 模型准备就绪")
            return True
        # 下载失败：通知 UI 并视为本次 OCR 失败
        self.mineru_models_status.emit(f"模型下载失败: {msg}")
        self.ocr_done.emit(file_path, 0, 1)
        return False

    def cancel_ocr(self) -> None:
        self._cancel_ocr_pipeline()

    def _cancel_ocr_pipeline(self) -> None:
        """取消 render + ocr worker。render 取消后推哨兵，ocr 自然结束。"""
        if self._render_worker is not None:
            self._render_worker.cancel()
            _wait_thread(self._render_worker, timeout=5000)
            self._render_worker = None
        if self._ocr_worker is not None:
            self._ocr_worker.cancel()
            _wait_thread(self._ocr_worker, timeout=5000)
            self._ocr_worker = None

    def _on_ocr_page_done(self, page_index: int, result) -> None:
        worker = self._ocr_worker
        if worker is None:
            return
        session = self._sessions.get(worker.session_id)
        if session is None:
            return
        if result is not None:
            with session.doc_lock:
                written, skipped = PdfService.add_text_layer(
                    session.doc,
                    session.pdf_document,
                    page_index,
                    result,
                    pdf_settings=self._pdf_settings,
                    overwrite=self._overwrite_text_layer,
                )
            session.add_ocr_stats(written, skipped)
        self.ocr_page_done.emit(session.file_path, page_index, result)

    def _on_ocr_progress(self, current: int, total: int) -> None:
        worker = self._ocr_worker
        if worker is None:
            return
        session = self._sessions.get(worker.session_id)
        if session is None:
            return
        self.ocr_progress.emit(session.file_path, current, total)

    def _on_ocr_all_done(self, session_id: str, success: int, fail: int) -> None:
        self.ocr_done.emit(session_id, success, fail)
        session = self._sessions.get(session_id)
        if session is not None:
            stats = session.ocr_stats
            self.ocr_stats_ready.emit(session_id, stats["written"], stats["skipped"])
        self._ocr_worker = None

    # ---- async mutate (save / delete layer / rotate / etc.) ------------

    def save_async(self, path: str | None = None, pdf_settings=None) -> None:
        """异步保存（rewrite + 落盘在后台）。path=None 覆盖原文件。"""
        session = self.active_session
        if session is None:
            return
        self._cancel_mutate_worker()
        kind = TaskKind.SAVE_AS if path is not None else TaskKind.SAVE
        task = MutateTask(kind=kind, path=path, pdf_settings=pdf_settings)
        self._start_mutate(session, task)

    def delete_text_layers_async(self, page_indices: list[int]) -> None:
        """异步删除文字层（逐页词级 redact 在后台）。"""
        session = self.active_session
        if session is None:
            return
        self._cancel_mutate_worker()
        task = MutateTask(kind=TaskKind.DELETE_TEXT_LAYER, page_indices=page_indices)
        self._start_mutate(session, task)

    def rotate_pages_async(self, page_indices: list[int], angle: int) -> None:
        session = self.active_session
        if session is None:
            return
        self._cancel_mutate_worker()
        task = MutateTask(kind=TaskKind.ROTATE, page_indices=page_indices, angle=angle)
        self._start_mutate(session, task)

    def delete_pages_async(self, page_indices: list[int]) -> None:
        session = self.active_session
        if session is None:
            return
        self._cancel_mutate_worker()
        task = MutateTask(kind=TaskKind.DELETE_PAGES, page_indices=page_indices)
        self._start_mutate(session, task)

    def _start_mutate(self, session, task: MutateTask) -> None:
        self._mutate_worker = PdfMutateWorker(
            session_id=session.file_path,
            doc=session.doc,
            pdf_document=session.pdf_document,
            doc_lock=session.doc_lock,
            task=task,
        )
        self._mutate_worker.page_done.connect(self._on_mutate_page_done)
        self._mutate_worker.progress.connect(self._on_mutate_progress)
        self._mutate_worker.all_done.connect(self._on_mutate_all_done)
        self._mutate_worker.failed.connect(self._on_mutate_failed)
        self._mutate_worker.start()

    def _cancel_mutate_worker(self) -> None:
        if self._mutate_worker is not None:
            self._mutate_worker.cancel()
            _wait_thread(self._mutate_worker, timeout=5000)
            self._mutate_worker = None

    def _on_mutate_progress(self, current: int, total: int) -> None:
        session = self.active_session
        if session:
            self.mutate_progress.emit(session.file_path, current, total)

    def _on_mutate_page_done(self, page_index: int, payload) -> None:
        """逐页完成 → 转发为 mutate_done（payload 含 page_index）供 UI 更新 grid。"""
        session = self.active_session
        if session:
            self.mutate_done.emit(
                session.file_path, {"page": page_index, "payload": payload}
            )

    def _on_mutate_all_done(self, session_id: str, result) -> None:
        if self._mutate_worker is not None:
            self._mutate_worker = None
        session = self._sessions.get(session_id)
        if session is None:
            return
        # 按任务结果类型转发专用信号
        if isinstance(result, dict) and "residual_pages" in result:
            self.delete_layer_done.emit(session_id, result["residual_pages"])
        elif result is not None:
            # SAVE/SAVE_AS：result 是 SaveResult
            # 全量压缩覆盖时 doc 已 close+reopen，更新 session.doc 引用，
            # 否则后续 OCR/渲染/再编辑会操作已关闭的 doc 对象。
            new_doc = getattr(result, "new_doc", None)
            if new_doc is not None:
                session.doc = new_doc
            self.save_done.emit(session_id)
        self.mutate_done.emit(session_id, result)

    def _on_mutate_failed(self, session_id: str, error: str) -> None:
        if self._mutate_worker is not None:
            self._mutate_worker = None
        self.mutate_failed.emit(session_id, error)

    # ---- block text editing (双击改字 → 内存模型更新) ----------------

    def update_page_block_text(
        self, page_index: int, block_index: int, new_text: str
    ) -> bool:
        """更新某页某块的文字（仅内存模型，不落盘 PDF）。

        修改 PdfPageInfo.ocr_text_blocks[block_index].text，标记
        is_manually_edited=True，置 session.is_modified=True。供 PreviewCanvas
        双击编辑后调用。实际写入 PDF 由 rewrite_modified_pages 在保存时执行。

        Args:
            page_index: 页码索引。
            block_index: 该页 ocr_text_blocks 中的块索引。
            new_text: 编辑后的文字。

        Returns:
            是否有实际变化（文字确实改变）。
        """
        session = self.active_session
        if session is None:
            return False
        if page_index < 0 or page_index >= len(session.pdf_document.pages):
            return False
        info = session.pdf_document.pages[page_index]
        if block_index < 0 or block_index >= len(info.ocr_text_blocks):
            return False
        block = info.ocr_text_blocks[block_index]
        if block.text == new_text:
            return False
        block.text = new_text
        block.is_manually_edited = True
        session.pdf_document.is_modified = True
        return True

    # ---- save-time rewrite (保存前按编辑后的块重写 PDF 文字层) ------

    def rewrite_modified_pages(self, file_path: str | None = None) -> None:
        """对所有有 OCR 块缓存且被编辑过的页，重写 PDF 文字层。

        供保存（PdfService.save）前调用：遍历活动会话的页面，对
        has_text_layer 且 ocr_text_blocks 非空的页执行 rewrite_text_layer
        （先删旧文字层再用内存中的块全量重写），确保 PDF 文字层与预览/编辑
        后的块一致。未被编辑的页（ocr_text_blocks 为空）跳过。

        Args:
            file_path: 指定会话文件路径，None 用活动会话。
        """
        session = (
            self._sessions.get(file_path)
            if file_path is not None
            else self.active_session
        )
        if session is None:
            return
        with session.doc_lock:
            # 整文档一次聚合子集字体：把所有有 OCR 块的页字符汇成一个子集，
            # 全文档共享单一字体对象（避免每页一份独立子集放大体积）。
            # 探测失败为 None → rewrite_text_layer 内部回退 china-s。
            from vibeocr.utils.cjk_font_resolver import _CJK_RESOLVER

            target_pages = [
                info
                for info in session.pdf_document.pages
                if info.ocr_text_blocks
            ]
            shared_font_path: str | None = None
            if target_pages:
                all_chars = "".join(
                    b.text
                    for info in target_pages
                    for b in info.ocr_text_blocks
                    if b.text
                )
                shared_font_path = _CJK_RESOLVER.resolve(all_chars)

            for info in target_pages:
                PdfService.rewrite_text_layer(
                    session.doc,
                    session.pdf_document,
                    info.page_index,
                    info.ocr_text_blocks,
                    info.ocr_preproc_angle,
                    pdf_settings=self._pdf_settings,
                    font_path=shared_font_path,
                )

    def _save_active_to_disk_for_test(self) -> None:
        """测试辅助：把活动会话的 fitz.Document 落盘（复刻 PdfService.save）。"""
        session = self.active_session
        if session is None:
            return
        with session.doc_lock:
            new_doc = PdfService.save(session.doc, session.pdf_document)
            # 全量压缩覆盖时 doc 已重开，更新 session.doc 引用
            if new_doc is not None:
                session.doc = new_doc

    # ---- batch export -----------------------------------------------

    def export_all_modified(self, output_dir: str) -> list[str]:
        exported: list[str] = []
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        for file_path, session in self._sessions.items():
            if not session.is_modified:
                continue
            name = Path(file_path).name
            dest = out / name
            if dest.exists():
                stem = dest.stem
                counter = 1
                while (out / f"{stem}_{counter}{dest.suffix}").exists():
                    counter += 1
                dest = out / f"{stem}_{counter}{dest.suffix}"
            try:
                with session.doc_lock:
                    PdfService.save(session.doc, session.pdf_document, path=str(dest))
                exported.append(str(dest))
            except Exception as e:
                logger.error("导出失败 %s: %s", file_path, e)
        return exported

    def export_all_async(self, output_dir: str) -> None:
        """异步批量导出所有 modified session。"""
        sessions = [s for _, s in self.get_modified_sessions()]
        if not sessions:
            self.export_done.emit([])
            return
        self._export_worker = PdfExportWorker(sessions, output_dir)
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.done.connect(self._on_export_done)
        self._export_worker.start()

    def _on_export_progress(self, current: int, total: int, file_name: str) -> None:
        self.export_progress.emit(current, total, file_name)

    def _on_export_done(self, exported_paths: list) -> None:
        self._export_worker = None
        self.export_done.emit(exported_paths)

    # ---- cleanup ----------------------------------------------------

    def shutdown(self) -> None:
        if self._export_worker is not None:
            self._export_worker.cancel()
            self._export_worker = None
        self._cancel_open_worker()
        self._cancel_load_worker()
        self._cancel_ocr_pipeline()
        self._cancel_mutate_worker()
        for session in self._sessions.values():
            # fitz doc.close() 在文档已关闭/损坏时抛各类异常，关闭路径静默忽略
            try:
                session.doc.close()
            except Exception:
                pass
        self._sessions.clear()
        self._active_path = None
        # 释放文字层子集字体临时文件（atexit 兜底，session 关闭时尽早清理，
        # 避免长运行 GUI 进程在 %TEMP% 累积 vibeocr_subset_*.ttf）。
        from vibeocr.utils.cjk_font_resolver import _CJK_RESOLVER

        _CJK_RESOLVER.cleanup()


def _wait_thread(worker: QThread, timeout: int = 3000) -> None:
    """等待 QThread 结束，期间处理事件循环以避免跨线程信号死锁。

    超时后强制终止线程并记录错误日志。
    注意：terminate() 不安全，仅在超时无法恢复时作为最后手段使用。
    """
    start = time.monotonic()
    while not worker.isFinished():
        QCoreApplication.processEvents()
        worker.wait(50)
        if time.monotonic() - start > timeout / 1000:
            logger.error(
                "Worker %s 未在 %dms 内结束，强制终止",
                worker.objectName(),
                timeout,
            )
            worker.terminate()
            worker.wait(500)
            break
    QCoreApplication.processEvents()
