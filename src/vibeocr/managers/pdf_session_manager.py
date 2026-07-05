"""PDF 多文件会话管理器(进程化版本)。

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

from PySide6.QtCore import QObject, Signal

from vibeocr.ipc.model_bridge import apply_diff, mirror_to_doc
from vibeocr.ipc.schemas import ModelDiff, PdfDocumentMirror
from vibeocr.models.pdf_document import PdfDocument
from vibeocr.models.pdf_session import PdfSession
from vibeocr.services.pdf_backend_client import PdfBackendClient, PdfBackendError
from vibeocr.workers.pdf_export_worker import PdfExportWorker
from vibeocr.workers.pdf_ipc_worker import PdfIpcMutateWorker, PdfIpcOpenWorker

if TYPE_CHECKING:
    from vibeocr.models.ocr_options import OCROptions
    from vibeocr.models.ocr_result import OCRResult
    from vibeocr.models.pdf_ocr_options import PdfGlobalSettings
    from vibeocr.services.ocr_service_base import OCRServiceBase

logger = logging.getLogger(__name__)


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
        self._export_worker: PdfExportWorker | None = None
        # OCR 编排状态(主进程:后端渲染 → 主进程 OCR → 后端写层)
        self._ocr_service: OCRServiceBase | None = None
        self._pdf_settings: PdfGlobalSettings | None = None
        self._overwrite_text_layer: bool = False
        self._ocr_running: bool = False
        self._ocr_cancelled: bool = False
        self._client = PdfBackendClient.instance()

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

    def set_ocr_service(self, service: OCRServiceBase) -> None:
        self._ocr_service = service

    @property
    def is_ocr_ready(self) -> bool:
        return self._ocr_service is not None

    def get_modified_sessions(self) -> list[tuple[str, PdfSession]]:
        return [(p, s) for p, s in self._sessions.items() if s.is_modified]

    @property
    def is_deskew_running(self) -> bool:
        """是否正在跑摆正(供 PdfTab cancel 路由判断)。"""
        return self._mutate_worker is not None and self._mutate_worker._op == "deskew"  # noqa: SLF001

    @property
    def is_mutate_running(self) -> bool:
        return self._mutate_worker is not None

    @property
    def is_ocr_running(self) -> bool:
        return self._ocr_running

    @property
    def load_worker_session_id(self) -> str | None:
        # 进程化后 load 在 open worker 内联完成,无独立 load worker
        return None

    @property
    def backend_client(self) -> PdfBackendClient:
        """暴露 client 供 PdfTab 直接调渲染(缩略图/预览,这些是同步快调用)。"""
        return self._client

    # ---- session lifecycle ---------------------------------------------

    def open_session(self, file_path: str) -> PdfSession | None:
        """同步打开单个文件(GUI 线程会短暂阻塞,适合已知小文件)。

        批量打开用 open_sessions_async。本方法保留供测试/特殊路径。
        """
        if file_path in self._sessions:
            self.switch_session(file_path)
            return self._sessions[file_path]
        try:
            self._client.start()
            open_resp = self._client.open_session(file_path)
            load_resp = self._client.load(open_resp.session_id)
            full_model = (
                load_resp.diff.full_model
                if load_resp.diff.full_model is not None
                else open_resp.model
            )
            session = self._make_session(file_path, open_resp.session_id, full_model)
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
        worker.open_failed.connect(self._on_open_failed)
        worker.open_progress.connect(self.open_progress)
        worker.all_done.connect(self._on_open_all_done)
        self._open_worker = worker
        worker.start()

    def _make_session(
        self, file_path: str, session_id: str, full_model: PdfDocumentMirror
    ) -> PdfSession:
        pdf_doc = mirror_to_doc(full_model)
        session = PdfSession(file_path=file_path, session_id=session_id, pdf_document=pdf_doc)
        # load 已在后端完成,标记全部页已加载
        session.loaded_pages = set(range(pdf_doc.page_count))
        return session

    def _on_doc_opened(
        self, file_path: str, session_id: str, full_model: object
    ) -> None:
        """PdfIpcOpenWorker 回调:在主线程创建会话。"""
        assert isinstance(full_model, PdfDocumentMirror)
        session = self._make_session(file_path, session_id, full_model)
        self._sessions[file_path] = session

        prev_active = self._active_path
        self.session_added.emit(file_path)

        # 第一个成功打开的新文件成为 active
        if prev_active is None:
            self._active_path = file_path
            self.active_changed.emit(file_path)
            # 逐页发 page_loaded(供 UI 网格染色)
            total = session.pdf_document.page_count
            for i in range(total):
                self.page_loaded.emit(file_path, i)
                self.load_progress.emit(file_path, i + 1, total)
            self.load_done.emit(file_path)

    def _on_open_failed(self, file_path: str, error: str) -> None:
        logger.warning("异步打开失败 %s: %s", file_path, error)
        self.open_failed.emit(file_path, error)

    def _on_open_all_done(self) -> None:
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
        worker = PdfIpcMutateWorker(
            self._client, session.session_id, op, params
        )
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

    def save_async(
        self, path: str | None = None, pdf_settings=None
    ) -> None:
        """异步保存。pdf_settings 转 dict 传后端。"""
        settings_dict = self._settings_to_dict(pdf_settings)
        self._start_mutate("save", {"path": path, "pdf_settings": settings_dict})

    def delete_text_layers_async(self, page_indices: list[int]) -> None:
        self._start_mutate("delete_text_layers", {"pages": page_indices})

    def rotate_pages_async(self, page_indices: list[int], angle: int) -> None:
        self._start_mutate("rotate", {"pages": page_indices, "angle": angle})

    def delete_pages_async(self, page_indices: list[int]) -> None:
        self._start_mutate("delete_pages", {"pages": page_indices})

    def insert_blank_async(self, after_index: int, width: float = 612.0, height: float = 792.0) -> None:
        self._start_mutate("insert_blank", {"after_index": after_index, "width": width, "height": height})

    def insert_from_async(self, source_path: str, after_index: int) -> None:
        self._start_mutate("insert_from", {"source_path": source_path, "after_index": after_index})

    def move_page_async(self, from_index: int, to_index: int) -> None:
        self._start_mutate("move_page", {"from_index": from_index, "to_index": to_index})

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

            def cancel(self):
                self._cancelled = True

            def run(self):
                try:
                    self._mgr._run_deskew(self, self._sid, self._pages)
                except Exception as e:
                    self.failed.emit(self._sid, str(e))

        self._mutate_worker = _DeskewRunner(self, session.session_id, page_indices)  # type: ignore[assignment]
        self._mutate_worker.progress.connect(lambda sid, c, t: self.deskew_progress.emit(sid, c, t))  # type: ignore[attr-defined]
        self._mutate_worker.page_done.connect(lambda sid, idx, corr: self.deskew_page_done.emit(sid, idx, corr))  # type: ignore[attr-defined]
        self._mutate_worker.all_done.connect(self._on_deskew_all_done)  # type: ignore[attr-defined]
        self._mutate_worker.failed.connect(self._on_deskew_failed)  # type: ignore[attr-defined]
        self._mutate_worker.start()

    def _run_deskew(self, runner, session_id: str, page_indices: list[int]) -> None:
        """在 deskew runner 线程内执行三步摆正。"""
        from vibeocr.models.ocr_options import OCROptions

        session = self._sessions.get(self._active_path or "")
        if session is None or session.session_id != session_id:
            return
        total = len(page_indices)
        # 阶段 1+2:逐页后端渲染 → OCR 方向检测
        results = []  # [(idx, angle)]
        for n, idx in enumerate(page_indices):
            if runner._cancelled:  # noqa: SLF001
                runner.all_done.emit(
                    session_id,
                    {"corrected": len(self._deskew_corrected), "skipped": 0,
                     "corrected_pages": list(self._deskew_corrected)},
                )
                return
            try:
                # 后端渲染预览图(150dpi)→ PNG 字节 → numpy
                png = self._client.render_preview(session_id, idx, dpi=150)
                import io
                import numpy as np
                from PIL import Image
                img = Image.open(io.BytesIO(png)).convert("RGB")
                arr = np.array(img)
                # OCR 方向检测
                options = OCROptions(
                    use_doc_orientation_classify=True,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
                ocr_result = self._ocr_service.recognize(arr, options)  # type: ignore[union-attr]
                angle = int(getattr(ocr_result, "preproc_angle", 0) or 0)
                results.append((idx, angle))
            except Exception as e:
                logger.error("摆正检测页 %d 失败: %s", idx, e)
                results.append((idx, 0))
            runner.progress.emit(session_id, n + 1, total)

        # 阶段 3:逐页按角度旋转(angle → correction: 顺时针偏转 angle,逆时针纠正)
        for n, (idx, angle) in enumerate(results):
            if runner._cancelled:  # noqa: SLF001
                break
            correction = (-int(angle)) % 360
            if correction != 0:
                try:
                    self._client.rotate(session_id, [idx], correction)
                    self._deskew_corrected.append(idx)
                    runner.page_done.emit(session_id, idx, True)
                except Exception as e:
                    logger.error("摆正旋转页 %d 失败: %s", idx, e)
                    runner.page_done.emit(session_id, idx, False)
            else:
                runner.page_done.emit(session_id, idx, False)
            runner.progress.emit(session_id, total + n + 1, total * 2)

        runner.all_done.emit(
            session_id,
            {"corrected": len(self._deskew_corrected),
             "skipped": total - len(self._deskew_corrected),
             "corrected_pages": list(self._deskew_corrected)},
        )

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
        self.deskew_done.emit(session_id, summary)

    def _on_deskew_failed(self, session_id: str, error: str) -> None:
        self._mutate_worker = None
        self.deskew_failed.emit(session_id, error)

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

    def _on_mutate_page_done(self, session_id: str, page_index: int, payload: object) -> None:
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.mutate_done.emit(file_path, {"page": page_index, "payload": payload})

    def _on_mutate_all_done(self, session_id: str, diff: object, extra: object) -> None:
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

        # 后台线程编排 OCR 流程
        from PySide6.QtCore import QThread

        ocr_options_ref = ocr_options
        settings_dict = self._settings_to_dict(pdf_settings)

        class _OcrRunner(QThread):
            page_done = Signal(str, int, object)
            progress = Signal(str, int, int)
            all_done = Signal(str, int, int)
            failed = Signal(str, str)

            def __init__(self, mgr, sid, pages, opts, sdict, overwrite_):
                super().__init__()
                self._mgr = mgr
                self._sid = sid
                self._pages = pages
                self._opts = opts
                self._sdict = sdict
                self._overwrite = overwrite_
                self._cancelled = False
                self._success = 0
                self._fail = 0

            def cancel(self):
                self._cancelled = True

            def run(self):
                self._mgr._run_ocr(self, self._sid, self._pages, self._opts,
                                   self._sdict, self._overwrite)

        self._ocr_worker = _OcrRunner(
            self, session.session_id, page_indices, ocr_options_ref,
            settings_dict, overwrite,
        )
        self._ocr_worker.page_done.connect(self._on_ocr_page_done_signal)
        self._ocr_worker.progress.connect(self._on_ocr_progress_signal)
        self._ocr_worker.all_done.connect(self._on_ocr_all_done_signal)
        self._ocr_worker.failed.connect(lambda sid, e: logger.error("OCR 失败: %s", e))
        self._ocr_worker.start()

    def _run_ocr(self, runner, session_id: str, pages: list[int],
                 ocr_options, settings_dict: dict, overwrite: bool) -> None:
        """在 OCR runner 线程内:逐页后端渲染 → OCR → 后端写文字层。"""
        import io
        import numpy as np
        from PIL import Image
        from vibeocr.models.ocr_options import OCROptions

        session = self._sessions.get(self._active_path or "")
        if session is None or session.session_id != session_id:
            return
        total = len(pages)
        success = 0
        fail = 0
        for n, idx in enumerate(pages):
            if runner._cancelled:  # noqa: SLF001
                break
            try:
                # 后端渲染 300dpi → PNG → numpy
                png = self._client.render_preview(session_id, idx, dpi=300)
                img = Image.open(io.BytesIO(png)).convert("RGB")
                arr = np.array(img)
                # OCR 识别
                opts = ocr_options if ocr_options is not None else OCROptions()
                result = self._ocr_service.recognize(arr, opts)  # type: ignore[union-attr]
                if result is not None and result.text_blocks:
                    # 序列化 OCRResult → dict 传后端写文字层
                    ocr_dict = self._ocr_result_to_dict(result)
                    self._client.add_text_layer(
                        session_id, idx, ocr_dict, settings_dict, overwrite
                    )
                    session.add_ocr_stats(len(result.text_blocks), 0)
                    success += 1
                    runner.page_done.emit(session_id, idx, result)
                else:
                    session.add_ocr_stats(0, 1)
                    runner.page_done.emit(session_id, idx, None)
            except Exception as e:
                logger.error("OCR 页 %d 失败: %s", idx, e)
                fail += 1
                runner.page_done.emit(session_id, idx, None)
            runner.progress.emit(session_id, n + 1, total)

        # 刷新 model(OCR 改变了 has_text_layer + ocr_text_blocks)
        try:
            full = self._client.get_model(session_id)
            session.pdf_document = mirror_to_doc(full)
        except PdfBackendError as e:
            logger.error("OCR 后刷新 model 失败: %s", e)
        runner.all_done.emit(session_id, success, fail)

    def _on_ocr_page_done_signal(self, session_id: str, page_index: int, result: object) -> None:
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.ocr_page_done.emit(file_path, page_index, result)

    def _on_ocr_progress_signal(self, session_id: str, current: int, total: int) -> None:
        file_path = self._path_for_session_id(session_id)
        if file_path:
            self.ocr_progress.emit(file_path, current, total)

    def _on_ocr_all_done_signal(self, session_id: str, success: int, fail: int) -> None:
        self._ocr_running = False
        self._ocr_worker = None
        file_path = self._path_for_session_id(session_id)
        if file_path:
            session = self._sessions[file_path]
            stats = session.ocr_stats
            self.ocr_stats_ready.emit(file_path, stats["written"], stats["skipped"])
            self.ocr_done.emit(file_path, success, fail)

    def cancel_ocr(self) -> None:
        self._cancel_ocr()

    def _cancel_ocr(self) -> None:
        self._ocr_cancelled = True
        w = getattr(self, "_ocr_worker", None)
        if w is not None and hasattr(w, "cancel"):
            w.cancel()
            w.wait(5000)
            self._ocr_worker = None
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
            from vibeocr.pipeline_status import is_pipeline_ever_successful
            return not is_pipeline_ever_succeeded("MinerU", get_project_root())
        except Exception:
            return False

    def _ensure_mineru_models_blocking(self, file_path: str) -> bool:
        from PySide6.QtWidgets import QApplication
        from vibeocr.env_manager import ensure_mineru_models, get_project_root

        def on_progress(stage: str, message: str):
            self.mineru_models_status.emit(f"[{stage}] {message}")
            QApplication.processEvents()

        self.mineru_models_status.emit("首次使用文档解析，正在下载 MinerU 模型（约数 GB）...")
        ok, msg = ensure_mineru_models(get_project_root(), progress_callback=on_progress)
        if ok:
            self.mineru_models_status.emit("MinerU 模型准备就绪")
            return True
        self.mineru_models_status.emit(f"模型下载失败: {msg}")
        self.ocr_done.emit(file_path, 0, 1)
        return False

    # ---- 批量导出 -------------------------------------------------------

    def export_all_modified(self, output_dir: str) -> list[str]:
        """同步批量导出所有 modified session(走 IPC save 到目标路径)。"""
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
                exported = self._mgr.export_all_modified(self._out)
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
        """PdfGlobalSettings → dict。"""
        if settings is None:
            return None
        if hasattr(settings, "model_dump"):
            return settings.model_dump()
        try:
            from vibeocr.models.pdf_ocr_options import PdfGlobalSettings
            if isinstance(settings, PdfGlobalSettings):
                return {
                    "compress_on_save": settings.compress_on_save,
                    "render_dpi": settings.render_dpi,
                }
        except Exception:
            pass
        return None

    def _ocr_result_to_dict(self, result) -> dict[str, Any]:
        """OCRResult → dict(传后端 add_text_layer)。"""
        return {
            "text_blocks": [
                {
                    "text": b.text,
                    "score": b.score,
                    "bbox": list(b.bbox) if b.bbox else None,
                    "page_idx": b.page_idx,
                    "is_manually_edited": b.is_manually_edited,
                    "label": b.label,
                    "order": b.order,
                }
                for b in result.text_blocks
            ],
        }

    # ---- cleanup --------------------------------------------------------

    def shutdown(self) -> None:
        self._cancel_mutate_worker()
        self._cancel_ocr()
        self._cancel_open_worker()
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
