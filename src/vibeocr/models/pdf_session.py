"""PDF 会话数据模型 — 单个已打开文件的状态。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import fitz

from vibeocr.models.pdf_document import PdfDocument  # noqa: TC001


@dataclass
class PdfSession:
    """单个 PDF 文件的会话状态。

    doc_lock 用于保护 fitz.Document 的跨线程访问，
    PdfLoadWorker（后台线程）和主线程操作必须通过此锁序列化。
    """

    file_path: str
    doc: fitz.Document
    pdf_document: PdfDocument
    loaded_pages: set[int] = field(default_factory=set)
    doc_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def is_modified(self) -> bool:
        return self.pdf_document.is_modified

    @property
    def load_progress(self) -> float:
        total = self.pdf_document.page_count
        if total == 0:
            return 1.0
        return len(self.loaded_pages) / total

    def __enter__(self) -> PdfSession:
        return self

    def __exit__(self, *args) -> None:
        try:
            self.doc.close()
        except Exception:
            pass
