"""PDF 多文件会话管理器。

管理 PdfSession 集合，持有 PdfLoadWorker / PdfOcrWorker，中转信号到 UI。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from vibeocr.models.pdf_session import PdfSession
from vibeocr.services.pdf_service import PdfService
from vibeocr.workers.pdf_load_worker import PdfLoadWorker
from vibeocr.workers.pdf_ocr_worker import PdfOcrWorker

if TYPE_CHECKING:
    import numpy as np

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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sessions: dict[str, PdfSession] = {}
        self._active_path: str | None = None
        self._load_worker: PdfLoadWorker | None = None
        self._ocr_worker: PdfOcrWorker | None = None
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

    def switch_session(self, file_path: str) -> None:
        if file_path not in self._sessions:
            return
        if self._active_path == file_path:
            return

        self._cancel_load_worker()
        self._cancel_ocr_worker()
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

        self._cancel_ocr_worker()

        pages: list[tuple[int, np.ndarray]] = []
        for page_idx in page_indices:
            with session.doc_lock:
                page = session.doc[page_idx]
                adjusted_dpi = pdf_settings.adjust_dpi(page.rect.width, page.rect.height)
                img_array = PdfService.render_page_as_array(
                    session.doc, page_idx, dpi=adjusted_dpi
                )
            if img_array.size > 0:
                pages.append((page_idx, img_array))

        if not pages:
            return

        self._pdf_settings = pdf_settings
        self._overwrite_text_layer = overwrite
        session.reset_ocr_stats()

        self._ocr_worker = PdfOcrWorker(
            session_id=session.file_path,
            pages=pages,
            ocr_service=self._ocr_service,
            ocr_options=ocr_options,
        )
        self._ocr_worker.page_done.connect(self._on_ocr_page_done)
        self._ocr_worker.progress.connect(self._on_ocr_progress)
        self._ocr_worker.all_done.connect(self._on_ocr_all_done)
        self._ocr_worker.start()

    def cancel_ocr(self) -> None:
        self._cancel_ocr_worker()

    def _cancel_ocr_worker(self) -> None:
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
            self.ocr_stats_ready.emit(
                session_id, stats["written"], stats["skipped"]
            )
        self._ocr_worker = None

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

    # ---- cleanup ----------------------------------------------------

    def shutdown(self) -> None:
        self._cancel_load_worker()
        self._cancel_ocr_worker()
        for session in self._sessions.values():
            # fitz doc.close() 在文档已关闭/损坏时抛各类异常，关闭路径静默忽略
            try:
                session.doc.close()
            except Exception:
                pass
        self._sessions.clear()
        self._active_path = None


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
