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
    # OCR 原始块（归一化 [0,1000] bbox），预览/编辑/重写 PDF 文字层的唯一信源。
    # detect_text_layers 重读会被 PyMuPDF 合并块，不能作为预览信源，故单独缓存。
    ocr_text_blocks: list = field(default_factory=list)
    ocr_preproc_angle: int = 0


@dataclass
class PdfDocument:
    """PDF 文档状态"""

    file_path: str | None = None
    pages: list[PdfPageInfo] = field(default_factory=list)
    is_modified: bool = False
    has_structural_change: bool = False  # 结构性改动（删页/插页/重排），影响保存策略
    render_dpi: int = 300
    thumbnail_dpi: int = 96

    def get_page(self, index: int) -> PdfPageInfo | None:
        if 0 <= index < len(self.pages):
            return self.pages[index]
        return None

    @property
    def page_count(self) -> int:
        return len(self.pages)
