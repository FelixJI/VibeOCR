"""PDF 会话数据模型 — 单个已打开文件的状态。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import fitz

from vibeocr.models.pdf_document import PdfDocument  # noqa: TC001


@dataclass
class PdfSession:
    """单个 PDF 文件的会话状态。"""

    file_path: str
    doc: fitz.Document
    pdf_document: PdfDocument
    loaded_pages: set[int] = field(default_factory=set)

    @property
    def is_modified(self) -> bool:
        return self.pdf_document.is_modified

    @property
    def load_progress(self) -> float:
        total = self.pdf_document.page_count
        if total == 0:
            return 1.0
        return len(self.loaded_pages) / total
