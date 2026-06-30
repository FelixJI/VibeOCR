"""PDF 批量导出 Worker — 跨 session 遍历，各经 doc_lock。

与 PdfMutateWorker（单 doc 绑定）正交：导出需遍历多个 session。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    from vibeocr.models.pdf_session import PdfSession

logger = logging.getLogger(__name__)


class PdfExportWorker(QThread):
    """跨 session 批量导出 Worker。

    Signals:
        progress(current: int, total: int, file_name: str)
        done(exported_paths: list[str])
    """

    progress = Signal(int, int, str)
    done = Signal(list)

    def __init__(
        self,
        sessions: list[PdfSession],
        output_dir: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._sessions = sessions
        self._output_dir = output_dir
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        out = Path(self._output_dir)
        out.mkdir(parents=True, exist_ok=True)
        to_export = [s for s in self._sessions if s.is_modified]
        total = len(to_export)
        exported: list[str] = []

        for n, session in enumerate(to_export):
            if self._cancelled:
                break
            name = Path(session.file_path).name
            dest = out / name
            if dest.exists():
                stem = dest.stem
                counter = 1
                while (out / f"{stem}_{counter}{dest.suffix}").exists():
                    counter += 1
                dest = out / f"{stem}_{counter}{dest.suffix}"
            try:
                with session.doc_lock:
                    PdfService.save_with_rewrite(
                        session.doc, session.pdf_document, path=str(dest),
                    )
                exported.append(str(dest))
            except Exception as e:
                logger.error("导出失败 %s: %s", session.file_path, e)
            self.progress.emit(n + 1, total, name)
        self.done.emit(exported)
