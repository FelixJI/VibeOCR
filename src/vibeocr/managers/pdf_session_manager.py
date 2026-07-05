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
from vibeocr.workers.pdf_export_worker import PdfExportWorker
from vibeocr.workers.pdf_load_worker import PdfLoadWorker
from vibeocr.workers.pdf_open_worker import PdfOpenWorker
from vibeocr.workers.pdf_mutate_worker import MutateTask, PdfMutateWorker, TaskKind
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
    deskew_page_done = Signal(str, int, bool)  # (file_path, page_index, was_corrected)
    deskew_progress = Signal(str, int, int)    # (file_path, current, total)
    deskew_done = Signal(str, object)          # (file_path, summary dict)
    deskew_failed = Signal(str, str)           # (file_path, error_msg)
    # 异步批量打开文件（open_sessions_async）
    open_progress = Signal(int, int)           # (current, total)
    open_failed = Signal(str, str)             # (file_path, error_msg)
    # 缩略图缓存失效（旋转后），由 ThumbnailModel 监听清缓存并按需重渲
    thumbnails_invalidated = Signal(list)      # (page_indices) 或 [] 表示全部
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
        # 当前 mutate 任务的 kind（用于回调分流到 mutate_* 还是 deskew_* 信号）
        self._current_mutate_kind: TaskKind | None = None
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

    # ---- worker 生命周期 helper（统一 6 处重复的 cancel/start 脚手架） --

    def _cancel_thread(self, attr: str, timeout: int = 5000) -> None:
        """取消某个 worker 线程（cancel → 同步等待结束 → 释放）。

        等待用 _wait_thread（纯阻塞 wait，不调 processEvents）。
        此前用 processEvents + wait(50) 循环是 Qt 反模式：processEvents 让
        迟到的跨线程信号在等待期间重入执行，用旧数据错更新新 session。

        不调 w.disconnect()：实测 PySide6 中对一个仍连接信号的 QThread 调
        disconnect() 会导致后续 wait() 无法返回（疑似破坏 QThread 内部
        finished 信号链）。改由各 slot 用 session_id 守卫过滤迟到信号。

        Args:
            attr: self 上的 worker 字段名（如 "_load_worker"）。
            timeout: 等待线程退出的超时（毫秒）。
        """
        w = getattr(self, attr, None)
        if w is not None:
            if hasattr(w, "cancel"):
                w.cancel()
            _wait_thread(w, timeout=timeout)
            setattr(self, attr, None)

    def _start_worker(
        self,
        attr: str,
        worker: QThread,
        connections: list[tuple[str, object]],
        timeout: int = 5000,
    ) -> None:
        """取消旧 worker → 连接信号 → 启动新 worker 并存入字段。

        Args:
            attr: self 上的 worker 字段名。
            worker: 待启动的 worker（已构造）。
            connections: [(signal属性名, slot), ...]。
            timeout: 取消旧 worker 的等待超时（毫秒）。
        """
        self._cancel_thread(attr, timeout=timeout)
        for signal_attr, slot in connections:
            getattr(worker, signal_attr).connect(slot)
        setattr(self, attr, worker)
        worker.start()

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
            # 全部已打开：切到第一个。仍需 emit open_done，
            # 否则调用方（_on_open_done）的 _batch_opening 标志不会复位。
            if paths:
                self.switch_session(paths[0])
            self.open_done.emit()
            return

        self._cancel_open_worker()
        self._pending_open_paths = list(new_paths)

        open_worker = PdfOpenWorker(new_paths)
        # 线程结束后安全释放，避免 "Destroyed while still running" 警告
        open_worker.finished.connect(open_worker.deleteLater)
        self._start_worker(
            "_open_worker",
            open_worker,
            [
                ("doc_opened", self._on_doc_opened),
                ("open_failed", self._on_open_failed),
                ("open_progress", self.open_progress),
                ("all_done", self._on_open_all_done),
            ],
        )

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
        self._cancel_thread("_open_worker")
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

        # 仅当被关闭的是活动会话时，才取消可能正在跑的 OCR/mutate worker
        # （这些 worker 只作用于活动会话）。load worker 按 session_id 精确匹配。
        if self.load_worker_session_id == file_path:
            self._cancel_load_worker()
        if self._active_path == file_path:
            self._cancel_ocr_pipeline()
            self._cancel_mutate_worker()

        session = self._sessions.pop(file_path)
        session.doc.close()

        self.session_removed.emit(file_path)

        if self._active_path == file_path:
            self._active_path = None
            if self._sessions:
                last_path = list(self._sessions.keys())[-1]
                self._active_path = last_path
                self.active_changed.emit(last_path)
            else:
                # 最后一个文件被移除：必须 emit active_changed 通知 UI 清空，
                # 否则 _on_active_changed 永不运行 → set_session(None) /
                # grid.clear() 不执行 → 缩略图和文字层网格残留已删文件内容。
                # 信号类型为 Signal(str)，用空串作哨兵（slot 用 active_session
                # 属性判 None，不直接读此参数）。
                self.active_changed.emit("")

    # ---- load worker ------------------------------------------------

    def _start_load_worker(self, session: PdfSession) -> None:
        worker = PdfLoadWorker(
            session_id=session.file_path,
            doc=session.doc,
            pdf_document=session.pdf_document,
            loaded_pages=session.loaded_pages,
            doc_lock=session.doc_lock,
        )
        self._start_worker(
            "_load_worker",
            worker,
            [
                ("page_ready", self._on_page_ready),
                ("all_done", self._on_load_all_done),
            ],
            timeout=3000,
        )

    def rerender_thumbnails_async(self, page_indices: list[int]) -> None:
        """通知缩略图缓存失效（旋转后），由 ThumbnailModel 按需重渲可见页。

        文字层状态未变（旋转不改 has_text_layer），无需重启文字层 load worker。
        缩略图按需渲染：失效缓存后，滚动到该页或该页可见时自动重渲。
        """
        if not page_indices:
            return
        self.thumbnails_invalidated.emit(page_indices)

    def _cancel_load_worker(self) -> None:
        self._cancel_thread("_load_worker", timeout=3000)

    def _on_page_ready(self, page_index: int, page_info) -> None:
        # processEvents 重入守卫：_wait_thread 期间 processEvents 可能让旧
        # load worker 的迟到 page_ready 信号在此执行。此时 self._load_worker
        # 已指向新 worker，旧信号会用旧 page_info 错更新新 session。
        # 用 sender() 比对当前 worker，拒绝非当前 worker 的信号。
        sender = self.sender()
        if sender is not self._load_worker:
            return
        worker = self._load_worker
        if worker is None:
            return
        session = self._sessions.get(worker.session_id)
        if session is None:
            return
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
        self._cancel_thread("_render_worker")
        self._cancel_thread("_ocr_worker")

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

    # ---- async deskew（经 _start_mutate 统一路径，task.kind=AUTO_DESKEW） -

    def auto_deskew_async(self, page_indices: list[int]) -> None:
        """异步自动摆正选中页（方向检测+旋转+文字层同步在后台）。"""
        session = self.active_session
        if session is None or self._ocr_service is None:
            return
        task = MutateTask(
            kind=TaskKind.AUTO_DESKEW,
            page_indices=page_indices,
            ocr_service=self._ocr_service,
            pdf_settings=self._pdf_settings,
        )
        self._start_mutate(session, task)

    def cancel_deskew(self) -> None:
        """取消正在进行的自动摆正（仅当当前 mutate 任务是 AUTO_DESKEW）。"""
        if self._current_mutate_kind == TaskKind.AUTO_DESKEW:
            self._cancel_mutate_worker()

    def _start_mutate(self, session, task: MutateTask) -> None:
        worker = PdfMutateWorker(
            session_id=session.file_path,
            doc=session.doc,
            pdf_document=session.pdf_document,
            doc_lock=session.doc_lock,
            task=task,
        )
        self._current_mutate_kind = task.kind
        self._start_worker(
            "_mutate_worker",
            worker,
            [
                ("page_done", self._on_mutate_page_done),
                ("progress", self._on_mutate_progress),
                ("all_done", self._on_mutate_all_done),
                ("failed", self._on_mutate_failed),
            ],
        )

    def _cancel_mutate_worker(self) -> None:
        self._cancel_thread("_mutate_worker")
        self._current_mutate_kind = None

    def _on_mutate_progress(self, current: int, total: int) -> None:
        worker = self._mutate_worker
        session_id = worker.session_id if worker is not None else None
        if session_id is None:
            return
        # AUTO_DESKEW 转发到 deskew_progress，其余转发到 mutate_progress。
        # 用 worker.session_id 而非 active_session，避免切换文件时错位。
        if self._current_mutate_kind == TaskKind.AUTO_DESKEW:
            self.deskew_progress.emit(session_id, current, total)
        else:
            self.mutate_progress.emit(session_id, current, total)

    def _on_mutate_page_done(self, page_index: int, payload) -> None:
        """逐页完成 → 按任务类型转发专用信号供 UI 更新。"""
        worker = self._mutate_worker
        session_id = worker.session_id if worker is not None else None
        if session_id is None:
            return
        # 用 worker.session_id 而非 active_session，避免切换文件时错位。
        if self._current_mutate_kind == TaskKind.AUTO_DESKEW:
            # payload 是 was_corrected: bool
            self.deskew_page_done.emit(session_id, page_index, bool(payload))
        else:
            self.mutate_done.emit(
                session_id, {"page": page_index, "payload": payload}
            )

    def _on_mutate_all_done(self, session_id: str, result) -> None:
        self._mutate_worker = None
        kind = self._current_mutate_kind
        self._current_mutate_kind = None
        # AUTO_DESKEW：result 是 summary dict → 转发 deskew_done + 缩略图失效
        if kind == TaskKind.AUTO_DESKEW:
            self.deskew_done.emit(session_id, result)
            # 统一缩略图失效入口：corrected_pages 非空时清缓存并触发按需重渲。
            corrected = (result or {}).get("corrected_pages", [])
            if corrected:
                self.thumbnails_invalidated.emit(list(corrected))
            return
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
        kind = self._current_mutate_kind
        self._mutate_worker = None
        self._current_mutate_kind = None
        if kind == TaskKind.AUTO_DESKEW:
            self.deskew_failed.emit(session_id, error)
        else:
            self.mutate_failed.emit(session_id, error)
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
        export_worker = PdfExportWorker(sessions, output_dir)
        self._start_worker(
            "_export_worker",
            export_worker,
            [
                ("progress", self._on_export_progress),
                ("done", self._on_export_done),
            ],
        )

    def _on_export_progress(self, current: int, total: int, file_name: str) -> None:
        self.export_progress.emit(current, total, file_name)

    def _on_export_done(self, exported_paths: list) -> None:
        self._export_worker = None
        self.export_done.emit(exported_paths)

    # ---- cleanup ----------------------------------------------------

    def shutdown(self) -> None:
        self._cancel_thread("_export_worker")
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

    PySide6 中 worker.wait() 在不泵事件循环时会与排队信号投递死锁
    （worker 的 page_ready.emit 入队但主线程不处理 → 实测 wait 永不返回），
    故必须 processEvents。超时后强制 terminate（不安全，仅兜底）。

    重入风险（processEvents 让迟到信号执行）由各 slot 的 session_id /
    worker 守卫过滤，不在此处规避。
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
