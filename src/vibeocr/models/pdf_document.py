"""PDF 文档数据模型"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QPixmap


@dataclass
class TextLayerInfo:
    """单个文字层信息"""

    index: int
    text_preview: str
    char_count: int
    bbox: tuple[float, float, float, float]
    color_id: int


@dataclass
class PdfPageInfo:
    """单页状态"""

    page_index: int
    rotation: int = 0
    has_text_layer: bool = False
    text_layers: list[TextLayerInfo] = field(default_factory=list)
    is_scanned: bool = False
    thumbnail: QPixmap | None = None


@dataclass
class PdfDocument:
    """PDF 文档状态"""

    file_path: str | None = None
    pages: list[PdfPageInfo] = field(default_factory=list)
    is_modified: bool = False
    render_dpi: int = 300
    thumbnail_dpi: int = 96

    def get_page(self, index: int) -> PdfPageInfo | None:
        if 0 <= index < len(self.pages):
            return self.pages[index]
        return None

    @property
    def page_count(self) -> int:
        return len(self.pages)
