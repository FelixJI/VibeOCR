"""PySide PDF 多文件会话管理器（WorkerHost 客户端版本）。

管理 PdfSession 集合,通过 PdfBackendClient(httpx)调用 PDF 后端子进程,
中转信号到 UI。fitz 调用全部在后端子进程,主进程零 fitz 直接访问。

所有原有 Qt 信号签名保留不变,PdfTab 侧无需改信号连接。
同步页操作(旋转/删除/插入/重排)改为异步:manager 发 *_async,完成后
通过 mutate_done / thumbnails_invalidated 等信号通知 UI。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QThread, Signal

from vibeocr.client.pdf import PdfBackendClient, PdfClientError
from vibeocr.ipc.model_bridge import apply_diff, mirror_to_doc
from vibeocr.ipc.schemas import ModelDiff, PdfDocumentMirror
from vibeocr.models.pdf_session import PdfSession
from vibeocr.pyside.pdf_ipc_worker import PdfIpcMutateWorker, PdfIpcOpenWorker
from vibeocr.utils import ocr_sidecar

if TYPE_CHECKING:
    from vibeocr.models.ocr_options import OCROptions
    from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

logger = logging.getLogger(__name__)

PdfBackendError = PdfClientError


class PdfSessionManager(QObject):
    """PDF 多文件会话管理器(进程化)。

    信号签名与旧版完全一致,PdfTab 无需改信号连接:
        session_added(file_path)
        session_removed(file_path)
        active_changed(file_path)
        page_loaded(file_path, page_index)
        load_progress(file_path, loaded, total)
        load_done(file_path)
        ocr_page_done(file_path, page_index, result)
        ocr_progress(file_path, current, total)
        ocr_done(file_path, success, fail)
        mutate_progress / mutate_done / mutate_failed
        save_done / delete_layer_done
        deskew_page_done / deskew_progress / deskew_done / deskew_failed
        thumbnails_invalidated(page_indices)
        ...
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
    ocr_stats_ready = Signal(str, int, int)
    ocr_write_error = Signal(str, str)  # (file_path, error_message) — 写层失败详情
    mineru_models_status = Signal(str)
    render_progress = Signal(str, int, int)
    mutate_progress = Signal(str, int, int)
    mutate_done = Signal(str, object)
    mutate_failed = Signal(str, str)
    save_done = Signal(str)
    delete_layer_done = Signal(str, list)
    export_progress = Signal(int, int, str)
    export_done = Signal(list)
    deskew_page_done = Signal(str, int, bool)
    deskew_progress = Signal(str, int, int)
    deskew_done = Signal(str, object)
    deskew_failed = Signal(str, str)
    open_progress = Signal(int, int)
    open_failed = Signal(str, str)
    thumbnails_invalidated = Signal(list)
    open_done = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sessions: dict[str, PdfSession] = {}
        self._active_path: str | None = None
        self._open_worker: PdfIpcOpenWorker | None = None
        self._mutate_worker: PdfIpcMutateWorker | None = None
        self._export_worker: QThread | None = None
        # OCR 编排状态(主进程:后端渲染 → 主进程 OCR → 后端写层)
        self._ocr_service: Any | None = None
        self._pdf_settings: PdfGlobalSettings | None = None
        self._overwrite_text_layer: bool = False
        self._ocr_running: bool = False
        self._ocr_cancelled: bool = False
        self._client = PdfBackendClient.instance()
        # task generation：每类操作（OCR/mutate/export）启动时递增，
        # 信号携带 task_id，done 槽只接受当前代，避免旧任务的迟到信号
        # 清掉新任务状态（ABA/代际竞态）。
        self._task_generation: int = 0

    # ---- 属性 -----------------------------------------------------------

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

    def set_ocr_service(self, service: Any) -> None:
        self._ocr_service = service

    @property
    def is_ocr_ready(self) -> bool:
        return self._ocr_service is not None

    def get_modified_sessions(self) -> list[tuple[str, PdfSession]]:
        return [(p, s) for p, s in self._sessions.items() if s.is_modified]

    @property
    def is_deskew_running(self) -> bool:
        """是否正在跑摆正(供 PdfTab cancel 路由判断)。"""
        return self._mutate_worker is not None and self._mutate_worker._op == "deskew"

    @property
    def is_mutate_running(self) -> bool:
        return self._mutate_worker is not None

    @property
    def is_ocr_running(self) -> bool:
        return self._ocr_running

    @property
    def backend_client(self) -> PdfBackendClient:
        """暴露 client 供 PdfTab 直接调渲染(缩略图/预览,这些是同步快调用)。"""
        return self._client

    # ---- session lifecycle ---------------------------------------------

    def open_session(self, file_path: str) -> PdfSession | None:
        """同步打开单个文件(GUI 线程会短暂阻塞,适合已知小文件)。

        批量打开用 open_sessions_async。本方法保留供测试/特殊路径。
        同步跑完流式 load(逐页检测),适合小文件。
        """
        if file_path in self._sessions:
            self.switch_session(file_path)
            return self._sessions[file_path]
        try:
            self._client.start()
            open_resp = self._client.open_session(file_path)
            session = self._make_session(
                file_path, open_resp.session_id, open_resp.model
            )
            # 流式 load 逐页填充(同步,小文件可接受)
            for ev in self._client.load_stream(open_resp.session_id):
                if ev.page_index is not None and ev.page_payload is not None:
                    self._apply_page_loaded(session, ev.page_index, ev.page_payload)
                if ev.message == "done":
                    break
            self._sessions[file_path] = session
            self._active_path = file_path
            self.session_added.emit(file_path)
            self.active_changed.emit(file_path)
            self.load_done.emit(file_path)
            return session
        except PdfBackendError as e:
            logger.error("打开 %s 失败: %s", file_path, e)
            self.open_failed.emit(file_path, str(e))
            return None

    def open_sessions_async(self, paths: list[str]) -> None:
        """批量异步打开(后台 IPC open + load)。"""
        new_paths = [p for p in paths if p not in self._sessions]
        if not new_paths:
            if paths:
                self.switch_session(paths[0])
            self.open_done.emit()
            return

        self._cancel_open_worker()
        try:
            self._client.start()
        except PdfBackendError as e:
            for p in new_paths:
                self.open_failed.emit(p, str(e))
            self.open_done.emit()
            return

        worker = PdfIpcOpenWorker(self._client, new_paths)
        worker.finished.connect(worker.deleteLater)
        worker.doc_opened.connect(self._on_doc_opened)
        worker.page_loaded.connect(self._on_page_loaded)
        worker.load_progress.connect(self._on_load_progress)
        worker.open_failed.connect(self._on_open_failed)
        worker.open_progress.connect(self.open_progress)
        worker.all_done.connect(self._on_open_all_done)
        self._open_worker = worker
        worker.start()

    def _make_session(
        self, file_path: str, session_id: str, full_model: PdfDocumentMirror
    ) -> PdfSession:
        """从占位 model 创建 session(open 后立即调用,load 尚未跑)。

        page_infos 是占位(rotation=0,has_text_layer=False),逐页真实信息
        由后续 page_loaded 信号流式更新。
        """
        pdf_doc = mirror_to_doc(full_model)
        return PdfSession(
            file_path=file_path, session_id=session_id, pdf_document=pdf_doc
        )

    def _apply_page_loaded(
        self, session: PdfSession, page_index: int, page_mirror: object
    ) -> None:
        """把单页 load 结果 apply 到 session model(就地更新该页 PageInfo)。"""
        from vibeocr.ipc.model_bridge import page_mirror_to_info

        if not isinstance(page_mirror, dict):
            return
        # page_mirror 是 dict(ProgressEvent.page_payload,model_dump(mode="json"))
        from vibeocr.ipc.schemas import PdfPageInfoMirror

        mirror = PdfPageInfoMirror.model_validate(page_mirror)
        if 0 <= page_index < len(session.pdf_document.pages):
            session.pdf_document.pages[page_index] = page_mirror_to_info(mirror)
            session.loaded_pages.add(page_index)

    def _on_doc_opened(
        self, file_path: str, session_id: str, full_model: object
    ) -> None:
        """PdfIpcOpenWorker 阶段 1 回调:open 完成,立即创建占位 session。

        此时 model 是占位(页数已有,但 rotation/has_text_layer 是默认值),
        逐页真实信息由后续 page_loaded 信号流式填充。
        """
        assert isinstance(full_model, PdfDocumentMirror)
        session = self._make_session(file_path, session_id, full_model)
        self._sessions[file_path] = session

        prev_active = self._active_path
        self.session_added.emit(file_path)

        # 第一个成功打开的新文件成为 active(UI 立刻显示页数 + 占位缩略图)
        if prev_active is None:
            self._active_path = file_path
            self.active_changed.emit(file_path)

    def _on_page_loaded(
        self, file_path: str, page_index: int, page_mirror: object
    ) -> None:
        """PdfIpcOpenWorker 阶段 2 回调:单页文字层检测完成,流式更新 UI。"""
        session = self._sessions.get(file_path)
        if session is None:
            return
        self._apply_page_loaded(session, page_index, page_mirror)
        total = session.pdf_document.page_count
        loaded = len(session.loaded_pages)
        self.page_loaded.emit(file_path, page_index)
        self.load_progress.emit(file_path, loaded, total)

    def _on_load_progress(self, file_path: str, current: int, total: int) -> None:
        """PdfIpcOpenWorker load 进度(批量文件场景)。"""
        self.load_progress.emit(file_path, current, total)

    def _on_open_failed(self, file_path: str, error: str) -> None:
        logger.warning("异步打开失败 %s: %s", file_path, error)
        self.open_failed.emit(file_path, error)

    def _on_open_all_done(self) -> None:
        # 所有文件 load 完成后,逐个发 load_done
        for path in list(self._sessions.keys()):
            self.load_done.emit(path)
        self._open_worker = None
        self.open_done.emit()

    def _cancel_open_worker(self) -> None:
        w = self._open_worker
        if w is not None:
            w.cancel()
            w.wait(3000)
            self._open_worker = None

    def switch_session(self, file_path: str) -> None:
        if file_path not in self._sessions:
            return
        if self._active_path == file_path:
            return
        self._cancel_mutate_worker()
        self._cancel_ocr()
        self._active_path = file_path
        self.active_changed.emit(file_path)

    def close_session(self, file_path: str) -> None:
        session = self._sessions.get(file_path)
        if session is None:
            return
        if self._active_path == file_path:
            self._cancel_mutate_worker()
            self._cancel_ocr()
        # 通知后端关闭 session
        try:
            self._client.close_session(session.session_id)
        except PdfBackendError as e:
            logger.warning("后端关闭 session 失败: %s", e)

        self._sessions.pop(file_path, None)
        self.session_removed.emit(file_path)

        if self._active_path == file_path:
            self._active_path = None
            if self._sessions:
                last_path = list(self._sessions.keys())[-1]
                self._active_path = last_path
                self.active_changed.emit(last_path)
            else:
                self.active_changed.emit("")

    def rerender_thumbnails_async(self, page_indices: list[int]) -> None:
        if page_indices:
            self.thumbnails_invalidated.emit(page_indices)

    # ---- 文字层检测(预览按需)------------------------------------------

    def detect_text_layers(self, page_index: int) -> list:
        """同步检测文字层(预览按需)。返回 [TextLayerInfo]。

        走 IPC,GUI 线程会短暂阻塞(单页 ~180ms 可接受,用户主动触发)。
        """
        session = self.active_session
        if session is None:
            return []
        try:
            resp = self._client.detect_text_layers(session.session_id, page_index)
            from vibeocr.ipc.model_bridge import _text_layer_mirror_to_info

            infos = [_text_layer_mirror_to_info(m) for m in resp.text_layers]
            # 同步更新本地 mirror
            if 0 <= page_index < len(session.pdf_document.pages):
                session.pdf_document.pages[page_index].text_layers = infos
                session.pdf_document.pages[page_index].has_text_layer = len(infos) > 0
            return infos
        except PdfBackendError as e:
            logger.error("检测文字层失败: %s", e)
            return []

    # ---- 变更操作(异步,通过 IPC mutate worker)---------------------------

    def _start_mutate(self, op: str, params: dict[str, Any]) -> None:
        """启动通用变更 worker。"""
        session = self.active_session
        if session is None:
            return
        self._cancel_mutate_worker()
        # 递增 task generation，使旧 runner 的迟到信号被 done 槽丢弃
        self._task_generation += 1
        current_task_id = self._task_generation
        worker = PdfIpcMutateWorker(self._client, session.session_id, op, params)
        worker._task_id = current_task_id  # type: ignore[attr-defined]
        worker.progress.connect(self._on_mutate_progress)
        worker.page_done.connect(self._on_mutate_page_done)
        worker.all_done.connect(self._on_mutate_all_done)
        worker.failed.connect(self._on_mutate_failed)
        self._mutate_worker = worker
        worker.start()

    def _cancel_mutate_worker(self) -> None:
        w = self._mutate_worker
        if w is not None:
            w.cancel()
            w.wait(5000)
            self._mutate_worker = None

    def save_async(self, path: str | None = None, pdf_settings=None) -> None:
        """异步保存。pdf_settings 转 dict 传后端。"""
        settings_dict = self._settings_to_dict(pdf_settings)
        self._start_mutate("save", {"path": path, "pdf_settings": settings_dict})

    def delete_text_layers_async(self, page_indices: list[int]) -> None:
        # 仅改内存模型（后端 s.doc / s.pdf_document + is_modified=True），
        # 不写磁盘——磁盘文件仍保留旧文字层，直到显式 save_async。故此处
        # 无需 invalidate sidecar：sidecar 追踪的是「磁盘上哪些页已落盘 OCR
        # 文字层」，而磁盘状态未被本操作改变。用户删除文字层后崩溃（未保存）
        # 时，编辑丢失是既有的「未保存改动随崩溃丢失」行为（与本特性无关），
        # sidecar 仍准确反映磁盘真实状态（旧层仍在），续传跳过该页是正确的。
        self._start_mutate("delete_text_layers", {"pages": page_indices})

    def rotate_pages_async(self, page_indices: list[int], angle: int) -> None:
        self._start_mutate("rotate", {"pages": page_indices, "angle": angle})

    def delete_pages_async(self, page_indices: list[int]) -> None:
        self._start_mutate("delete_pages", {"pages": page_indices})

    def insert_blank_async(
        self, after_index: int, width: float = 612.0, height: float = 792.0
    ) -> None:
        self._start_mutate(
            "insert_blank",
            {"after_index": after_index, "width": width, "height": height},
        )

    def insert_from_async(self, source_path: str, after_index: int) -> None:
        self._start_mutate(
            "insert_from", {"source_path": source_path, "after_index": after_index}
        )

    def move_page_async(self, from_index: int, to_index: int) -> None:
        self._start_mutate(
            "move_page", {"from_index": from_index, "to_index": to_index}
        )

    def reorder_async(self, new_order: list[int]) -> None:
        self._start_mutate("reorder", {"new_order": new_order})

    # ---- 摆正(主进程编排:后端渲染 → OCR 方向检测 → 后端旋转)----------

    def auto_deskew_async(self, page_indices: list[int]) -> None:
        """异步自动摆正。主进程编排三步:
        1. 后端渲染页 → 2. OCR 方向检测 → 3. 后端按角度旋转 + 文字层同步。
        """
        session = self.active_session
        if session is None or self._ocr_service is None:
            return
        self._cancel_mutate_worker()
        self._deskew_pages = list(page_indices)
        self._deskew_corrected: list[int] = []
        self._deskew_cancelled = False
        # 复用 mutate worker 槽位,但用专用 runner(见 _run_deskew)
        from PySide6.QtCore import QThread

        class _DeskewRunner(QThread):
            progress = Signal(str, int, int)  # current, total(用 2*total 兼容阶段)
            page_done = Signal(str, int, bool)
            all_done = Signal(str, object)
            failed = Signal(str, str)

            def __init__(self, mgr, sid, pages):
                super().__init__()
                self._mgr = mgr
                self._sid = sid
                self._pages = pages
                self._cancelled = False
                # 复用渲染线程池（与 _OcrRunner 同理：跨批复用 httpx 连接）。
                from concurrent.futures import ThreadPoolExecutor

                self._render_pool = ThreadPoolExecutor(
                    max_workers=mgr._RENDER_CONCURRENCY,
                    thread_name_prefix="deskew-render",
                )

            def cancel(self):
                self._cancelled = True

            def run(self):
                try:
                    self._mgr._run_deskew(self, self._sid, self._pages)
                except Exception as e:
                    self.failed.emit(self._sid, str(e))
                finally:
                    self._render_pool.shutdown(wait=True)

        self._mutate_worker = _DeskewRunner(self, session.session_id, page_indices)  # type: ignore[assignment]
        # 与 OCR/mutate 一致：runner 信号携带 session_id，须经 _path_for_session_id
        # 翻译成 file_path 再转发给 UI（UI 处理器按 file_path 匹配活跃会话）。
        # 否则 session_id（uuid hex 串）永远 != file_path，UI 处理器全部 early-return，
        # 进度条停滞、完成汇总（"已摆正 N 页"）永不弹出。
        self._mutate_worker.progress.connect(self._on_deskew_progress_signal)  # type: ignore[attr-defined]
        self._mutate_worker.page_done.connect(self._on_deskew_page_done_signal)  # type: ignore[attr-defined]
        self._mutate_worker.all_done.connect(self._on_deskew_all_done)  # type: ignore[attr-defined]
        self._mutate_worker.failed.connect(self._on_deskew_failed_signal)  # type: ignore[attr-defined]
        self._mutate_worker.start()  # type: ignore[attr-defined]

    def _run_deskew(self, runner, session_id: str, page_indices: list[int]) -> None:
        """在 deskew runner 线程内:分批 [并发渲染 → 批量 OCR 方向检测 → 逐页旋转]。

        与 _run_ocr 共用批大小/渲染并发/子步进度（性能1/性能2），仅 DPI 与最终
        动作不同：摆正只需 preproc_angle，用 150dpi（OCR 提取文字需 300dpi）；
        识别后逐页按角度旋转（fitz 写不可并发，串行）。

        旧实现逐页串行：渲染 → 主进程 PIL+numpy 解码 → 单页 recognize（N 次 IPC
        往返）→ rotate。重构后复用 OCR 的批量化路径，省去主进程解码与逐页 IPC。
        """
        from vibeocr.models.ocr_options import OCROptions

        session = self._sessions.get(self._active_path or "")
        if session is None or session.session_id != session_id:
            return
        total = len(page_indices)
        if total == 0:
            runner.all_done.emit(
                session_id,
                {"corrected": 0, "skipped": 0, "corrected_pages": []},
            )
            return

        client = self._client
        dpi = self._DESKEW_DPI
        batch_size = self._OCR_BATCH_SIZE
        substeps = self._OCR_PROGRESS_SUBSTEPS
        progress_total = total * substeps
        progress = 0

        def _emit_progress() -> None:
            runner.progress.emit(session_id, progress, progress_total)

        def _render_page(idx: int) -> bytes | None:
            """渲染单页 dpi → 原始 PNG bytes（不在主进程解码，性能1）。"""
            try:
                return client.render_preview(session_id, idx, dpi=dpi)
            except Exception as e:
                logger.error("摆正渲染页 %d 失败: %s", idx, e)
                return None

        # 方向检测选项：只要角度，关掉去扭曲/文本行方向（更快）
        angle_opts = OCROptions(
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

        for batch_start in range(0, total, batch_size):
            if runner._cancelled:
                break
            batch_pages = page_indices[batch_start : batch_start + batch_size]

            # 阶段1：并发渲染（复用 runner 线程池，结果按 batch_pages 顺序对齐）
            images: list[bytes | None] = [None] * len(batch_pages)
            if not runner._cancelled:
                rendered = runner._render_pool.map(_render_page, batch_pages)
                for i, png in enumerate(rendered):
                    images[i] = png
            page_failed = [png is None for png in images]
            progress += len(batch_pages)  # 渲染子步
            _emit_progress()

            # 阶段2：批量方向检测（单次 recognize_batch，跳过渲染失败的页）
            valid_indices = [i for i, png in enumerate(images) if png is not None]
            angles_map: dict[int, int] = {}
            if valid_indices and not runner._cancelled:
                valid_images = [images[i] for i in valid_indices]  # type: ignore[list-item]
                try:
                    batch_results = self._ocr_service.recognize_batch(  # type: ignore[union-attr]
                        valid_images, angle_opts
                    )
                    for vi, res in zip(valid_indices, batch_results):
                        angles_map[vi] = int(getattr(res, "preproc_angle", 0) or 0)
                except Exception as e:
                    logger.error(
                        "摆正批量方向检测失败(批起始页 %d): %s", batch_pages[0], e
                    )
                    for vi in valid_indices:
                        page_failed[vi] = True
            progress += len(valid_indices)  # 识别子步
            _emit_progress()

            # 阶段3：逐页旋转（fitz 写不可并发，串行）
            for i, idx in enumerate(batch_pages):
                if runner._cancelled:
                    progress += 1
                    _emit_progress()
                    continue
                angle = angles_map.get(i, 0) if not page_failed[i] else 0
                correction = (-int(angle)) % 360
                if correction != 0:
                    try:
                        client.rotate(session_id, [idx], correction)
                        self._deskew_corrected.append(idx)
                        runner.page_done.emit(session_id, idx, True)
                    except Exception as e:
                        logger.error("摆正旋转页 %d 失败: %s", idx, e)
                        runner.page_done.emit(session_id, idx, False)
                else:
                    runner.page_done.emit(session_id, idx, False)
                progress += 1  # 旋转子步
                _emit_progress()

        runner.all_done.emit(
            session_id,
            {
                "corrected": len(self._deskew_corrected),
                "skipped": total - len(self._deskew_corrected),
                "corrected_pages": list(self._deskew_corrected),
            },
        )

    def _on_deskew_progress_signal(
        self, session_id: str, current: int, total: int
    ) -> None:
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.deskew_progress.emit(file_path, current, total)

    def _on_deskew_page_done_signal(
        self, session_id: str, page_index: int, was_corrected: bool
    ) -> None:
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.deskew_page_done.emit(file_path, page_index, was_corrected)

    def _on_deskew_all_done(self, session_id: str, summary: object) -> None:
        self._mutate_worker = None
        session = self._sessions.get(self._active_path or "")
        if session is not None:
            # 刷新 model(旋转改变了 rotation + 缩略图)
            try:
                full = self._client.get_model(session.session_id)
                invalidated = apply_diff(
                    session.pdf_document, ModelDiff(full_model=full)
                )
                if invalidated:
                    self.thumbnails_invalidated.emit(invalidated)
            except PdfBackendError as e:
                logger.error("摆正后刷新 model 失败: %s", e)
        # 翻译 session_id → file_path，UI 处理器按 file_path 匹配
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.deskew_done.emit(file_path, summary)

    def _on_deskew_failed_signal(self, session_id: str, error: str) -> None:
        self._mutate_worker = None
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.deskew_failed.emit(file_path, error)

    def cancel_deskew(self) -> None:
        w = self._mutate_worker
        if w is not None and hasattr(w, "cancel"):
            w.cancel()
            w.wait(5000)
            self._mutate_worker = None

    # ---- mutate worker 信号处理 -----------------------------------------

    def _on_mutate_progress(self, session_id: str, current: int, total: int) -> None:
        # 找到 session_id 对应的 file_path
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.mutate_progress.emit(file_path, current, total)

    def _on_mutate_page_done(
        self, session_id: str, page_index: int, payload: object
    ) -> None:
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.mutate_done.emit(file_path, {"page": page_index, "payload": payload})

    def _on_mutate_all_done(
        self, session_id: str, diff: object, extra: object, task_id: int = 0
    ) -> None:
        # task_id 默认 0 时从 sender 读取（真实信号连接路径）
        if task_id == 0:
            sender = self.sender()
            if sender is not None and hasattr(sender, "_task_id"):
                task_id = sender._task_id  # type: ignore[attr-defined]
        # 只接受当前代的信号，丢弃旧任务的迟到信号
        if task_id != 0 and task_id != self._task_generation:
            logger.debug(
                f"忽略旧任务 task_id={task_id} 的迟到 mutate all_done（当前代={self._task_generation}）"
            )
            return
        self._mutate_worker = None
        file_path = self._path_for_session_id(session_id)
        if file_path is None:
            return
        session = self._sessions[file_path]
        assert isinstance(diff, ModelDiff)
        invalidated = apply_diff(session.pdf_document, diff)
        if invalidated:
            self.thumbnails_invalidated.emit(invalidated)

        extra_dict = extra if isinstance(extra, dict) else {}
        # 按操作类型转发专用信号
        if "residual_pages" in extra_dict:
            self.delete_layer_done.emit(file_path, extra_dict["residual_pages"])
        elif "path" in extra_dict:
            self.save_done.emit(file_path)
        self.mutate_done.emit(file_path, {"diff_applied": True, "extra": extra_dict})

    def _on_mutate_failed(self, session_id: str, error: str) -> None:
        self._mutate_worker = None
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.mutate_failed.emit(file_path, error)

    def _path_for_session_id(self, session_id: str) -> str | None:
        for path, session in self._sessions.items():
            if session.session_id == session_id:
                return path
        return None

    # ---- 文字块编辑(双击改字,仅内存模型)-------------------------------
    # 注：以下块编辑只改内存模型（后端规范模型 + 本地 mirror），不写磁盘，
    # 故不影响 sidecar（sidecar 追踪磁盘落盘状态）。崩溃丢失未保存的块编辑
    # 是既有行为，与本特性无关。

    def update_page_block_text(
        self, page_index: int, block_index: int, new_text: str
    ) -> bool:
        """更新某页某块文字(走 IPC,后端更新规范模型)。"""
        session = self.active_session
        if session is None:
            return False
        try:
            resp = self._client.update_block_text(
                session.session_id, page_index, block_index, new_text
            )
            apply_diff(session.pdf_document, resp.diff)
            return True
        except PdfBackendError as e:
            logger.error("更新块文字失败: %s", e)
            return False

    # ---- OCR(主进程编排:后端渲染 → 主进程 OCR → 后端写文字层)--------

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

        if self._is_mineru_first_use(ocr_options):
            if not self._ensure_mineru_models_blocking(session.file_path):
                return

        self._cancel_ocr()
        self._pdf_settings = pdf_settings
        self._overwrite_text_layer = overwrite
        session.reset_ocr_stats()
        self._ocr_running = True
        self._ocr_cancelled = False
        # 清掉可能残留的后端 cancel 标志：上一次取消（OCR/mutate）置位的
        # cancel_event 若不清，会让本次 OCR 写层时后端 add_text_layer_batch
        # 立即停在页边界（协作式取消语义）。reset_cancel 原本全代码库无调用点，
        # 属遗漏。
        try:
            self._client.reset_cancel(session.session_id)
        except Exception:
            logger.debug("reset_cancel 失败（忽略）", exc_info=True)
        # 断点续传：读取 sidecar，过滤掉已增量落盘的页（崩溃恢复）。
        # overwrite=True 时不过滤（用户明确要求重写）。
        if not overwrite and session.file_path:
            try:
                pending = ocr_sidecar.restore_pending_pages(session.file_path)
                if pending:
                    already = set(pending.keys())
                    page_indices = [p for p in page_indices if p not in already]
                    if not page_indices:
                        logger.info("start_ocr: 所有请求页已落盘（sidecar），跳过 OCR")
                        self._ocr_running = False
                        # 调用方（PdfTab）在 start_ocr 之前已 _begin_ocr_ui：进度条
                        # 已显示、按钮已禁用、格子已置 processing。这里提前 return 不
                        # 构造 runner，故 all_done 永不发出，ocr_done 不会触发，
                        # _on_ocr_finished 不会复位 UI → 用户卡在 0% 进度条 + 蓝格子。
                        # 镜像 _on_ocr_all_done_signal 的收尾：发 ocr_stats_ready +
                        # ocr_done（0 成功 0 失败：无事可做），让 UI 正常复位。
                        stats = session.ocr_stats
                        self.ocr_stats_ready.emit(
                            session.file_path, stats["written"], stats["skipped"]
                        )
                        self.ocr_done.emit(session.file_path, 0, 0)
                        return
                    logger.info(
                        "start_ocr: sidecar 续传，跳过已落盘页 %s",
                        sorted(already),
                    )
            except Exception:
                logger.debug("start_ocr: sidecar 读取失败，全量 OCR", exc_info=True)
        # 递增 task generation，使旧 runner 的迟到信号被 done 槽丢弃
        self._task_generation += 1
        current_task_id = self._task_generation

        # 后台线程编排 OCR 流程
        from PySide6.QtCore import QThread

        ocr_options_ref = ocr_options
        settings_dict = self._settings_to_dict(pdf_settings)

        class _OcrRunner(QThread):
            page_done = Signal(str, int, object)
            progress = Signal(str, int, int)
            all_done = Signal(str, int, int, int)  # session_id, success, fail, task_id
            failed = Signal(str, str)

            def __init__(self, mgr, sid, pages, opts, sdict, overwrite_, task_id):
                super().__init__()
                self._mgr = mgr
                self._client = mgr._client
                self._sid = sid
                self._pages = pages
                self._opts = opts
                self._sdict = sdict
                self._overwrite = overwrite_
                self._task_id = task_id
                self._cancelled = False
                self._success = 0
                self._fail = 0
                # runner 生命周期内复用一个渲染线程池：跨批次复用同一组工作
                # 线程，从而复用 PdfBackendClient 按线程 ident 缓存的 httpx
                # Client（每线程 1 个 Client，4 线程跨 N 批始终命中同一组连接
                # 池），避免每批 16 页都重建线程池 + 新建 TCP 连接。
                from concurrent.futures import ThreadPoolExecutor

                self._render_pool = ThreadPoolExecutor(
                    max_workers=mgr._RENDER_CONCURRENCY,
                    thread_name_prefix="ocr-render",
                )

            def cancel(self):
                self._cancelled = True
                # 通知后端协作式取消（对齐 PdfIpcMutateWorker.cancel 的成熟模式）：
                # 后端 add_text_layer_batch 逐页检查 cancel_event，停在页边界，
                # 让当前 HTTP 尽快返回，避免 runner 一直阻塞到整批写完。
                try:
                    self._client.cancel(self._sid)
                except Exception:
                    logger.debug("通知后端取消失败（忽略）", exc_info=True)

            def run(self):
                try:
                    self._mgr._run_ocr(
                        self,
                        self._sid,
                        self._pages,
                        self._opts,
                        self._sdict,
                        self._overwrite,
                    )
                except Exception as e:
                    # _run_ocr 末尾已对已知失败点做了 try/except，但末尾的
                    # get_model/mirror_to_doc 块在大文件场景仍可能抛非
                    # PdfBackendError（pydantic.ValidationError / MemoryError /
                    # httpx 传输错误）。此前此处无 except，异常逃逸 → QThread
                    # 静默死亡 → all_done/failed 都不发 → UI 永久卡在「OCR 进行中」。
                    # 与 _DeskewRunner.run() 对齐：捕获后发 failed，让槽重置 UI。
                    logger.exception("_run_ocr 未捕获异常，发 failed 信号: %s", e)
                    self.failed.emit(self._sid, str(e))
                finally:
                    # runner 退出即关闭线程池，释放 4 个工作线程及其 httpx Client。
                    self._render_pool.shutdown(wait=True)

        self._ocr_worker = _OcrRunner(
            self,
            session.session_id,
            page_indices,
            ocr_options_ref,
            settings_dict,
            overwrite,
            current_task_id,
        )
        self._ocr_worker.page_done.connect(self._on_ocr_page_done_signal)
        self._ocr_worker.progress.connect(self._on_ocr_progress_signal)
        self._ocr_worker.all_done.connect(self._on_ocr_all_done_signal)
        # failed 信号此前只记日志，不清状态/不发 ocr_done，UI 永久卡死。
        # 改连 _on_ocr_failed_signal：重置 _ocr_running/_ocr_worker 并发 ocr_done
        # 让 PdfTab._on_ocr_finished 复位 UI（隐进度条、启用按钮）。
        self._ocr_worker.failed.connect(self._on_ocr_failed_signal)
        self._ocr_worker.start()

    # 三层批关系（性能2）：
    #   页批(此处 16) ≥ 传输批(SHM 一条消息装下的页数) ≥ 计算批(GPU predict)。
    #   计算批 = text_recognition_batch_size=8（pipeline_ocr.py，GPU）；
    #   传输批 由 SHM 预算 0.7×(128MB−9)≈90MB 决定，足以装下 16 页 → 传输不卡计算。
    #   页批=16 正好 2×计算批，喂满 GPU 且不让单批 predict 超时。
    _OCR_BATCH_SIZE = 16
    # 渲染并发线程数。后端 fitz 栅格化由 fitz_lock 串行化，但 PIL/PNG 编码 +
    # HTTP 往返可并行，N 并发可掩盖单页往返延迟。httpx Client 按线程独立(见
    # PdfBackendClient._ensure_started)，故可安全并发调用 render_preview。
    _RENDER_CONCURRENCY = 4
    # 进度子步数：每页拆成 渲染/识别/写层 3 个子步，让进度条在整批渲染/识别
    # 期间也能推进（而非只在写层时跳变），避免长时间静止被误判为卡死。
    # UI（PdfTab._begin_ocr_ui）的进度条范围须用 同样的子步数。
    _OCR_PROGRESS_SUBSTEPS = 3

    # ---- 自动摆正(与 OCR 共用批/并发/子步，仅 DPI 与动作不同)-----------
    # 摆正只需方向检测，150dpi 足够（OCR 需 300dpi 提取文字）；低 DPI 渲染更快、
    # PNG 更小、传输更省。批大小/并发/子步复用 OCR 的常量，保证两路径行为一致，
    # 也让进度模型统一（每页 渲染/识别/旋转 3 子步）。
    _DESKEW_DPI = 150

    def _run_ocr(
        self,
        runner,
        session_id: str,
        pages: list[int],
        ocr_options,
        settings_dict: dict,
        overwrite: bool,
    ) -> None:
        """在 OCR runner 线程内执行带一批预取的渲染/OCR/写层流水线。

        - 渲染:线程池并发调 render_preview(后端 fitz_lock 串行化栅格化，
          PNG 编码并行)，结果按页序对齐；返回原始 PNG bytes，不在主进程解码
          （由 worker 子进程解码一次，避免 PNG 双重编解码，性能1）。
        - 识别:recognize_batch() 一次 predict(list)，利用 PaddleOCR 内部
          ImageBatchSampler 分批，省去每页重复管道开销。
        - 流水:当前批 OCR 时预取下一批渲染，重叠 PDF 栅格/PNG 与 GPU 计算。
        - 写层:逐页串行 add_text_layer(fitz 写操作不可并发)。
        """
        from vibeocr.models.ocr_options import OCROptions

        session = self._sessions.get(self._active_path or "")
        if session is None or session.session_id != session_id:
            return
        total = len(pages)
        success = 0
        fail = 0
        done = 0  # 已写层页数(跨批次累计，用于 page_done 与最终统计)
        opts = ocr_options if ocr_options is not None else OCROptions()
        batch_size = self._OCR_BATCH_SIZE
        client = self._client
        # 进度按子步计：每页 渲染/识别/写层 各 1 步，total_steps = 页数 × 子步数。
        # 这样整批渲染/识别完成后进度也会推进，避免长时间静止被误判为卡死。
        substeps = self._OCR_PROGRESS_SUBSTEPS
        progress_total = total * substeps
        progress = 0

        def _emit_progress() -> None:
            runner.progress.emit(session_id, progress, progress_total)

        def _render_page(idx: int) -> bytes | None:
            """渲染单页 300dpi → 原始 PNG bytes。

            不在主进程解码为 ndarray：recognize_batch 的 IPC 路径对 bytes 输入
            原样透传（_prepare_image_data:419-420），由 worker 子进程的 _to_ndarray
            解码一次即可。省去主进程的 PNG 解码 + 重新 PNG 编码（性能1）。
            """
            try:
                return client.render_preview(session_id, idx, dpi=300)
            except Exception as e:
                logger.error("渲染页 %d 失败: %s", idx, e)
                return None

        page_batches = [
            pages[start : start + batch_size]
            for start in range(0, total, batch_size)
        ]
        render_iter = None
        for batch_number, batch_pages in enumerate(page_batches):
            if runner._cancelled:
                break

            # 阶段1：并发渲染(线程池，结果按 batch_pages 顺序对齐)
            images: list[bytes | None] = [None] * len(batch_pages)
            if not runner._cancelled:
                # 复用 runner 生命周期内的线程池（_OcrRunner.__init__ 创建），
                # 不再每批新建/销毁。除首批外，render_iter 已在上一批 OCR
                # 开始前提交，消费时多数页面已经完成渲染。
                if render_iter is None:
                    render_iter = runner._render_pool.map(_render_page, batch_pages)
                for i, arr in enumerate(render_iter):
                    images[i] = arr
                render_iter = None
            page_failed = [arr is None for arr in images]
            # 渲染子步进度：本批每页 +1（含渲染失败的页，它们仍“处理完”了渲染阶段）
            progress += len(batch_pages)
            _emit_progress()

            # 提前提交下一批渲染。ThreadPoolExecutor.map 会立即排队所有任务，
            # iterator 留到下一轮再消费；当前线程随即进入 WorkerHost 批量 OCR，
            # 从而让 PDF 栅格/PNG 编码与 OCR 子进程/GPU 计算重叠。仅预取一批，
            # 把额外峰值内存限制在最多 2×_OCR_BATCH_SIZE 页。
            if not runner._cancelled and batch_number + 1 < len(page_batches):
                render_iter = runner._render_pool.map(
                    _render_page, page_batches[batch_number + 1]
                )

            # 阶段2：批量识别(单次 predict，跳过渲染失败的页)
            valid_indices = [i for i, img in enumerate(images) if img is not None]
            results_map: dict[int, object] = {}
            if valid_indices and not runner._cancelled:
                valid_images = [images[i] for i in valid_indices]  # type: ignore[list-item]
                try:
                    batch_results = self._ocr_service.recognize_batch(  # type: ignore[union-attr]
                        valid_images, opts
                    )
                    for vi, res in zip(valid_indices, batch_results):
                        results_map[vi] = res
                except Exception as e:
                    logger.error("批量识别失败(批起始页 %d): %s", batch_pages[0], e)
                    # 整批识别失败：标记这些页失败
                    for vi in valid_indices:
                        page_failed[vi] = True
            # 识别子步进度：仅识别成功的页（渲染失败的页不再走识别）
            progress += len(valid_indices)
            _emit_progress()

            # 阶段3：批量写层（一次 HTTP，共享聚合子集字体）+ 逐页进度信号。
            # 先收集本批要写层的有效页，一次 add_text_layer_batch 调用让后端聚合
            # 所有页字符解析单一子集字体（避免逐页各解析一份放大体积），写层返回
            # 后再逐页发 page_done 信号，保持 UI 流式反馈不变。
            # 取消/失败/空结果页不进 batch，单独处理。
            write_items: list[dict] = []  # [{page, ocr_result, result_ref, list_idx}]
            for i, idx in enumerate(batch_pages):
                if runner._cancelled or page_failed[i]:
                    continue
                result = results_map.get(i)
                if result is not None and result.text_blocks:
                    write_items.append(
                        {
                            "page": idx,
                            "ocr_result": self._ocr_result_to_dict(result),
                            "_result": result,
                            "_list_idx": i,
                        }
                    )

            write_page_results: dict[int, bool] = {}  # page -> ok
            batch_persisted = False
            batch_write_error: str | None = None
            if write_items and not runner._cancelled:
                try:
                    wire_items = [
                        {key: value for key, value in item.items() if not key.startswith("_")}
                        for item in write_items
                    ]
                    resp = self._client.add_text_layer_batch(
                        session_id,
                        wire_items,
                        settings_dict,
                        overwrite,
                        save=True,
                    )
                    batch_persisted = bool((resp.extra or {}).get("saved", False))
                    for item in write_items:
                        write_page_results[item["page"]] = True
                except Exception as e:
                    logger.error("批量写文字层失败(批起始页 %d): %s", batch_pages[0], e)
                    batch_write_error = str(e)
                    # 整批写层失败：标记这些页失败
                    for item in write_items:
                        write_page_results[item["page"]] = False

            # 把后端写层错误详情通知 UI（此前只记日志，用户看不到原因，
            # 只看到"失败 N 页"无法排查）。取 file_path 翻译 session_id。
            if batch_write_error:
                fp = self._path_for_session_id(session_id)
                if fp:
                    self.ocr_write_error.emit(fp, batch_write_error)

            # 本批 incremental save 成功 → 写 sidecar 标记已落盘页（断点续传）
            # sidecar 是"尽力而为"：写入失败只记日志，不阻断 OCR 主流程。
            if batch_persisted and session.file_path:
                try:
                    angles = {
                        item["page"]: int(
                            getattr(item["_result"], "preproc_angle", 0) or 0
                        )
                        for item in write_items
                        if write_page_results.get(item["page"], False)
                    }
                    saved_pages = list(angles.keys())
                    if saved_pages:
                        ocr_sidecar.mark_pages_saved(
                            session.file_path, saved_pages, angles
                        )
                except Exception:
                    logger.debug(
                        "sidecar mark_pages_saved 失败（忽略）", exc_info=True
                    )

            # 逐页发 page_done + 进度信号（保持 UI 流式反馈）
            for i, idx in enumerate(batch_pages):
                if runner._cancelled:
                    done += 1
                    progress += 1
                    _emit_progress()
                    continue
                if page_failed[i]:
                    fail += 1
                    session.add_ocr_stats(0, 1)
                    runner.page_done.emit(session_id, idx, None)
                    done += 1
                    progress += 1
                    _emit_progress()
                    continue
                result = results_map.get(i)
                if (
                    result is not None
                    and result.text_blocks
                    and write_page_results.get(idx, False)
                ):
                    session.add_ocr_stats(len(result.text_blocks), 0)
                    success += 1
                    runner.page_done.emit(session_id, idx, result)
                elif (
                    result is not None
                    and result.text_blocks
                    and not write_page_results.get(idx, False)
                ):
                    # 有结果但写层失败
                    fail += 1
                    runner.page_done.emit(session_id, idx, None)
                else:
                    # 无文本块的空结果页
                    session.add_ocr_stats(0, 1)
                    runner.page_done.emit(session_id, idx, None)
                done += 1
                progress += 1
                _emit_progress()

        # 末尾整文档快速压缩：批量写层已经完成，显式跳过逐页删除/重写，
        # 只复用 save 路由做最终压缩落盘。代价是保留每批一个字体子集，换取
        # 不再二次处理全部页。compress 失败时 sidecar 保持 completed=false
        #（已 incremental 落盘的页仍有效，下次 start_ocr 续传）。
        if not runner._cancelled and success > 0 and session.file_path:
            try:
                runner.progress.emit(session_id, 0, 0)  # 不确定进度（COMPRESS 态）
                self._client.save(
                    session_id,
                    None,
                    settings_dict,
                    rewrite_text_layers=False,
                )
                # 全量压缩整体重写了 PDF（可能变小）。先刷新 sidecar 基线为
                # 当前文件状态，否则 mark_completed→load_sidecar 的增长校验会
                # 因 size < original 失败而落盘一个空 sidecar（同 Task1 的指纹
                # 漂移 bug 的等价表现）。
                try:
                    ocr_sidecar.refresh_baseline(session.file_path)
                    ocr_sidecar.mark_completed(session.file_path)
                except Exception:
                    logger.debug(
                        "sidecar mark_completed 失败（忽略）", exc_info=True
                    )
            except Exception as e:
                logger.error("OCR 末尾压缩失败（中间结果已增量落盘）: %s", e)

        # 刷新 model(OCR 改变了 has_text_layer + ocr_text_blocks)
        # 大文件场景此步是内存峰值：get_model 返回全文档 mirror（含所有页的
        # ocr_text_blocks），mirror_to_doc 全量重建 PdfPageInfo + TextBlock。
        # 可能抛非 PdfBackendError（pydantic.ValidationError / MemoryError /
        # httpx 传输错误）。此前只捕获 PdfBackendError，其余异常逃逸到
        # _OcrRunner.run()，线程静默死亡导致 UI 卡死。放宽到 Exception 确保
        # all_done 始终发出（UI 得以复位），刷新失败仅记日志（OCR 结果已写层）。
        try:
            full = self._client.get_model(session_id)
            session.pdf_document = mirror_to_doc(full)
        except Exception as e:
            logger.error("OCR 后刷新 model 失败: %s", e)
        runner.all_done.emit(session_id, success, fail, runner._task_id)

    def _on_ocr_page_done_signal(
        self, session_id: str, page_index: int, result: object
    ) -> None:
        file_path = self._path_for_session_id(session_id)
        if file_path:
            # 增量落 model：把 result.text_blocks 立即写入该页 PdfPageInfo，
            # 消除预览滞后（此前只在整批结束 get_model 才全量刷新）。
            # result 为 None（失败/空页）时跳过，仅转发信号。
            if result is not None:
                session = self._sessions.get(file_path)
                if session is not None:
                    info = session.pdf_document.get_page(page_index)
                    if info is not None:
                        info.ocr_text_blocks = list(
                            getattr(result, "text_blocks", []) or []
                        )
                        info.ocr_preproc_angle = int(
                            getattr(result, "preproc_angle", 0) or 0
                        )
                        if info.ocr_text_blocks:
                            info.has_text_layer = True
            self.ocr_page_done.emit(file_path, page_index, result)

    def _on_ocr_progress_signal(
        self, session_id: str, current: int, total: int
    ) -> None:
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.ocr_progress.emit(file_path, current, total)

    def _on_ocr_all_done_signal(
        self, session_id: str, success: int, fail: int, task_id: int = 0
    ) -> None:
        # 只接受当前代的信号，丢弃旧任务的迟到信号
        if task_id != 0 and task_id != self._task_generation:
            logger.debug(
                f"忽略旧任务 task_id={task_id} 的迟到 all_done（当前代={self._task_generation}）"
            )
            return
        self._ocr_running = False
        self._ocr_worker = None
        file_path = self._path_for_session_id(session_id)
        if file_path:
            session = self._sessions[file_path]
            stats = session.ocr_stats
            self.ocr_stats_ready.emit(file_path, stats["written"], stats["skipped"])
            self.ocr_done.emit(file_path, success, fail)

    def _on_ocr_failed_signal(self, session_id: str, error: str) -> None:
        """OCR runner 未捕获异常时调用：重置内部状态并通知 UI 复位。

        此前 failed 信号只连了一个记日志的 lambda，_ocr_running / _ocr_worker
        不清、ocr_done 不发，UI 永久卡在「OCR 进行中」（进度条不隐、按钮禁用）。
        这里复用 _on_ocr_all_done_signal 的清理逻辑，并以 (0, total) 失败计数
        发 ocr_done，让 PdfTab._on_ocr_finished 隐藏进度条、启用按钮。
        total 取当前会话页数（无会话时退化为 0）。
        """
        logger.error("OCR runner 失败: %s", error)
        self._ocr_running = False
        self._ocr_worker = None
        file_path = self._path_for_session_id(session_id)
        if file_path:
            session = self._sessions.get(file_path)
            total = len(session.pdf_document.pages) if session else 0
            self.ocr_done.emit(file_path, 0, total)

    def cancel_ocr(self) -> None:
        self._cancel_ocr()

    def _cancel_ocr(self) -> None:
        self._ocr_cancelled = True
        w = getattr(self, "_ocr_worker", None)
        if w is not None and hasattr(w, "cancel"):
            w.cancel()
            # 轮询事件循环等待 worker 退出，让取消前已发出的 page_done/progress
            # 等跨线程排队信号在主线程排干（否则裸 wait() 会把它们搁置在队列，
            # 导致已写层的页格子来不及变绿——见 Bug A）。
            # 不用 _wait_thread 的 terminate() 兜底：OCR worker 可能在 SHM 往返
            # 中途，强杀会留下半写状态。超时后交由 all_done 信号（runner 退出时
            # 一定会发）做最终清理（_on_ocr_all_done_signal 重置 _ocr_worker）。
            import time

            from PySide6.QtCore import QCoreApplication

            deadline = time.monotonic() + 5.0
            while not w.isFinished():
                QCoreApplication.processEvents()
                w.wait(50)
                if time.monotonic() > deadline:
                    break
            QCoreApplication.processEvents()
        self._ocr_running = False

    def get_pages_without_text_layer(self, session_id: str) -> list[int]:
        """返回该 session 中所有无文字层的页索引列表。"""
        session = self._sessions.get(session_id)
        if session is None:
            return []
        return [
            p.page_index for p in session.pdf_document.pages if not p.has_text_layer
        ]

    # ---- MinerU 模型下载(保留原逻辑)-----------------------------------

    def _is_mineru_first_use(self, ocr_options: OCROptions | None) -> bool:
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
        from PySide6.QtWidgets import QApplication

        from vibeocr.env_manager import ensure_mineru_models, get_project_root

        def on_progress(stage: str, message: str):
            self.mineru_models_status.emit(f"[{stage}] {message}")
            QApplication.processEvents()

        self.mineru_models_status.emit(
            "首次使用文档解析，正在下载 MinerU 模型（约数 GB）..."
        )
        ok, msg = ensure_mineru_models(
            get_project_root(), progress_callback=on_progress
        )
        if ok:
            self.mineru_models_status.emit("MinerU 模型准备就绪")
            return True
        self.mineru_models_status.emit(f"模型下载失败: {msg}")
        self.ocr_done.emit(file_path, 0, 1)
        return False

    # ---- 批量导出 -------------------------------------------------------

    def export_all_modified(self, output_dir: str, cancel_check=None) -> list[str]:
        """同步批量导出所有 modified session(走 IPC save 到目标路径)。

        Args:
            output_dir: 输出目录
            cancel_check: 可选的无参可调用，返回 True 时停止导出后续文件
        """
        exported: list[str] = []
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        for file_path, session in self._sessions.items():
            if not session.is_modified:
                continue
            # 逐文件检查取消标志
            if cancel_check and cancel_check():
                logger.info("导出已取消，停止后续文件")
                break
            name = Path(file_path).name
            dest = out / name
            if dest.exists():
                stem = dest.stem
                counter = 1
                while (out / f"{stem}_{counter}{dest.suffix}").exists():
                    counter += 1
                dest = out / f"{stem}_{counter}{dest.suffix}"
            try:
                settings_dict = self._settings_to_dict(self._pdf_settings)
                self._client.save(
                    session.session_id, path=str(dest), pdf_settings=settings_dict
                )
                exported.append(str(dest))
            except PdfBackendError as e:
                logger.error("导出失败 %s: %s", file_path, e)
        return exported

    def export_all_async(self, output_dir: str) -> None:
        """异步批量导出。保留 PdfExportWorker 接口,但内部走 IPC。

        简化:复用同步 export_all_modified 在后台线程跑。
        """
        from PySide6.QtCore import QThread

        sessions = [s for _, s in self.get_modified_sessions()]
        if not sessions:
            self.export_done.emit([])
            return

        class _ExportRunner(QThread):
            progress = Signal(int, int, str)
            done = Signal(list)

            def __init__(self, mgr, out_dir):
                super().__init__()
                self._mgr = mgr
                self._out = out_dir
                self._cancelled = False

            def cancel(self):
                self._cancelled = True

            def run(self):
                exported = self._mgr.export_all_modified(
                    self._out, cancel_check=lambda: self._cancelled
                )
                self.done.emit(exported)

        worker = _ExportRunner(self, output_dir)
        worker.done.connect(self._on_export_done)
        self._export_worker = worker
        worker.start()

    def _on_export_done(self, exported_paths: list) -> None:
        self._export_worker = None
        self.export_done.emit(exported_paths)

    # ---- 辅助 -----------------------------------------------------------

    def _settings_to_dict(self, settings) -> dict[str, Any] | None:
        """PdfGlobalSettings → dict(传后端)。"""
        if settings is None:
            return None
        if hasattr(settings, "to_dict"):
            return settings.to_dict()
        if isinstance(settings, dict):
            return settings
        return None

    def _ocr_result_to_dict(self, result) -> dict[str, Any]:
        """OCRResult → dict(传后端 add_text_layer)。

        必须带上 preproc_angle：OCR 预处理旋转了图像时，bbox 在旋转后空间，
        后端 add_text_layer → _denormalize_and_unrotate_bbox 需要该角度把
        bbox 逆变换回页面坐标。此前漏传导致 angle 恒为 0，开启文档方向分类
        时文字层坐标严重偏离（90° 时 X 轴可偏移数百点）。
        """
        return {
            "text_blocks": [
                {
                    "text": b.text,
                    "score": b.score,
                    "bbox": list(b.bbox) if b.bbox else None,
                    "polygon": list(b.polygon) if b.polygon else None,
                    "page_idx": b.page_idx,
                    "is_manually_edited": b.is_manually_edited,
                    "label": b.label,
                    "order": b.order,
                }
                for b in result.text_blocks
            ],
            "preproc_angle": int(getattr(result, "preproc_angle", 0) or 0),
        }

    # ---- cleanup --------------------------------------------------------

    def shutdown(self) -> None:
        self._cancel_mutate_worker()
        self._cancel_ocr()
        self._cancel_open_worker()
        if self._export_worker is not None:
            if hasattr(self._export_worker, "cancel"):
                self._export_worker.cancel()
            self._export_worker.wait(5000)
            self._export_worker = None
        # 关闭所有后端 session
        for session in list(self._sessions.values()):
            try:
                self._client.close_session(session.session_id)
            except Exception:
                pass
        self._sessions.clear()
        self._active_path = None
        # 停后端子进程
        try:
            self._client.stop()
        except Exception:
            pass
        from vibeocr.utils.cjk_font_resolver import _CJK_RESOLVER

        _CJK_RESOLVER.cleanup()


def _wait_thread(worker, timeout_ms: int | None = None) -> bool:
    """等待 QThread 结束,期间处理事件循环以避免跨线程信号死锁。

    PdfTab 的缩略图 worker 停止时用。超时后返回 False，**不调用 terminate()**——
    worker 仍持有 ThreadPoolExecutor 和 HTTP 连接，强杀会留下半写状态。
    调用方应通过 cancel() 协作取消 + 有界 HTTP 超时确保 worker 最终自然退出。

    Returns:
        True 如果 worker 在超时内结束；False 如果超时（worker 仍在运行）。
    """
    if not worker.isRunning():
        return True

    import time

    from PySide6.QtCore import QCoreApplication

    from vibeocr.core.constants import Constants

    if timeout_ms is None:
        timeout_ms = Constants.Timeout.Ms.PDF_WORKER_CANCEL_SHORT
    start = time.monotonic()
    while not worker.isFinished():
        QCoreApplication.processEvents()
        worker.wait(Constants.Timeout.Ms.PDF_WORKER_POLL_STEP)
        if time.monotonic() - start > timeout_ms / 1000:
            logger.warning(
                "Worker 未在 %dms 内结束，保持运行等待自然退出（不 terminate）",
                timeout_ms,
            )
            QCoreApplication.processEvents()
            return False
    QCoreApplication.processEvents()
    return True
