"""PDF 操作服务

基于 PyMuPDF (fitz) 封装 PDF 操作，包括打开/保存/渲染/旋转/插入/删除/文字层操作。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import fitz
import numpy as np
from PySide6.QtGui import QImage, QPixmap

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo, TextLayerInfo

logger = logging.getLogger(__name__)


class PdfService:
    """PDF 操作服务"""

    def __init__(self) -> None:
        self._doc: fitz.Document | None = None
        self._pdf_document: PdfDocument | None = None

    @property
    def document(self) -> PdfDocument | None:
        return self._pdf_document

    def is_open(self) -> bool:
        return self._doc is not None

    def open(self, file_path: str) -> PdfDocument:
        """打开 PDF 文件。"""
        if not Path(file_path).exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        doc = fitz.open(file_path)

        if doc.is_encrypted:
            doc.close()
            raise RuntimeError("不支持加密 PDF 文件")

        self._doc = doc
        self._pdf_document = PdfDocument(file_path=file_path)
        self._build_page_infos()
        return self._pdf_document

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self._pdf_document = None

    def _build_page_infos(self) -> None:
        if self._doc is None or self._pdf_document is None:
            return
        pages = []
        for i in range(self._doc.page_count):
            page = self._doc[i]
            text_layers = self._detect_text_layers(i)
            pages.append(PdfPageInfo(
                page_index=i,
                rotation=page.rotation,
                has_text_layer=len(text_layers) > 0,
                text_layers=text_layers,
                is_scanned=len(text_layers) == 0 and self._is_page_scanned(i),
            ))
        self._pdf_document.pages = pages

    def _is_page_scanned(self, page_index: int) -> bool:
        """判断页面是否为扫描件（有大面积图片覆盖）。"""
        if self._doc is None:
            return False
        page = self._doc[page_index]
        images = page.get_images(full=True)
        if not images:
            return False
        page_rect = page.rect
        for img_info in images:
            xref = img_info[0]
            rects = page.get_image_rects(xref)
            for rect in rects:
                coverage = (rect.width * rect.height) / (page_rect.width * page_rect.height)
                if coverage > 0.5:
                    return True
        return False

    def save(self, path: str | None = None) -> None:
        """保存 PDF。如果 path 为 None 则覆盖原文件（先备份）。"""
        if self._doc is None or self._pdf_document is None:
            return

        save_path = path or self._pdf_document.file_path
        if save_path is None:
            return

        if path is None:
            backup_path = save_path + ".bak"
            shutil.copy2(save_path, backup_path)
            try:
                self._doc.save(save_path, incremental=True, encryption=0)
                Path(backup_path).unlink(missing_ok=True)
            except Exception:
                shutil.copy2(backup_path, save_path)
                Path(backup_path).unlink(missing_ok=True)
                raise
        else:
            self._doc.save(save_path, deflate=True)

        self._pdf_document.is_modified = False

    def render_page(self, page_index: int, dpi: int = 96) -> QPixmap:
        """将页面渲染为 QPixmap。"""
        if self._doc is None:
            return QPixmap()
        page = self._doc[page_index]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=mat)
        qimage = QImage(
            pixmap.samples,
            pixmap.width,
            pixmap.height,
            pixmap.stride,
            QImage.Format.Format_RGB888,
        )
        return QPixmap.fromImage(qimage.copy())

    def render_page_as_array(self, page_index: int, dpi: int = 300) -> np.ndarray:
        """将页面渲染为 numpy 数组（RGB），用于 OCR。"""
        if self._doc is None:
            return np.array([])
        page = self._doc[page_index]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=mat)
        return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, 3
        )

    def _detect_text_layers(self, page_index: int) -> list[TextLayerInfo]:
        """检测页面中的文字层。"""
        if self._doc is None:
            return []
        page = self._doc[page_index]
        blocks = page.get_text("dict")["blocks"]

        layers: list[TextLayerInfo] = []
        layer_index = 0
        for block in blocks:
            if block["type"] != 0:
                continue
            lines = block.get("lines", [])
            if not lines:
                continue
            text_parts = []
            for line in lines:
                for span in line.get("spans", []):
                    text_parts.append(span.get("text", ""))
            full_text = "".join(text_parts).strip()
            if not full_text:
                continue
            bbox = block["bbox"]
            layers.append(TextLayerInfo(
                index=layer_index,
                text_preview=full_text[:30],
                char_count=len(full_text),
                bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
                color_id=layer_index % 8,
            ))
            layer_index += 1
        return layers

    def rotate_pages(self, page_indices: list[int], angle: int) -> None:
        """旋转指定页面。"""
        if self._doc is None or self._pdf_document is None:
            return
        for idx in page_indices:
            if 0 <= idx < self._doc.page_count:
                page = self._doc[idx]
                page.set_rotation((page.rotation + angle) % 360)
                self._pdf_document.pages[idx].rotation = page.rotation
        self._pdf_document.is_modified = True
        self._invalidate_thumbnails(page_indices)

    def rotate_all_pages(self, angle: int) -> None:
        if self._doc is None or self._pdf_document is None:
            return
        self.rotate_pages(list(range(self._doc.page_count)), angle)

    def delete_pages(self, page_indices: list[int]) -> None:
        """删除指定页面（按索引降序删除）。"""
        if self._doc is None or self._pdf_document is None:
            return
        # 先从模型中移除对应页面（保留原始 page_index）
        remaining = [p for i, p in enumerate(self._pdf_document.pages) if i not in page_indices]
        for idx in sorted(page_indices, reverse=True):
            if 0 <= idx < self._doc.page_count:
                self._doc.delete_page(idx)
        self._pdf_document.pages = remaining
        self._pdf_document.is_modified = True

    def insert_blank_page(self, after_index: int, width: float = 612, height: float = 792) -> None:
        """在指定页面后插入空白页。"""
        if self._doc is None or self._pdf_document is None:
            return
        insert_at = after_index + 1
        self._doc.new_page(pno=insert_at, width=width, height=height)
        self._pdf_document.is_modified = True
        self._build_page_infos()

    def insert_pages_from(self, source_path: str, after_index: int) -> None:
        """从另一个 PDF 插入所有页面到指定位置之后。"""
        if self._doc is None or self._pdf_document is None:
            return
        src = fitz.open(source_path)
        insert_at = after_index + 1
        self._doc.insert_pdf(src, start_at=insert_at)
        src.close()
        self._pdf_document.is_modified = True
        self._build_page_infos()

    def move_page(self, from_index: int, to_index: int) -> None:
        """移动页面位置。"""
        if self._doc is None or self._pdf_document is None:
            return
        if from_index == to_index:
            return
        # 先保存要移动的页面信息（保留原始 page_index）
        page_info = self._pdf_document.pages[from_index]
        self._doc.move_page(from_index, to_index)
        # 手动调整模型中的页面列表
        pages = list(self._pdf_document.pages)
        pages.pop(from_index)
        pages.insert(to_index, page_info)
        self._pdf_document.pages = pages
        self._pdf_document.is_modified = True

    def add_text_layer(self, page_index: int, ocr_result: object) -> None:
        """将 OCR 结果作为隐形文字层写入页面。"""
        if self._doc is None or self._pdf_document is None:
            return

        page = self._doc[page_index]
        page_rect = page.rect

        text_blocks = getattr(ocr_result, "text_blocks", [])
        for block in text_blocks:
            if block.text is None or not block.text.strip():
                continue
            bbox = block.bbox
            if bbox is None:
                continue
            # bbox 归一化 [0,1000] → PDF 坐标
            x0 = bbox[0] / 1000.0 * page_rect.width
            y0 = bbox[1] / 1000.0 * page_rect.height
            x1 = bbox[2] / 1000.0 * page_rect.width
            y1 = bbox[3] / 1000.0 * page_rect.height

            rect = fitz.Rect(x0, y0, x1, y1)
            if rect.is_empty or rect.width < 1 or rect.height < 1:
                continue

            fontsize = rect.height * 0.8
            if fontsize < 1:
                continue

            # 自适应字体大小：若文本溢出则逐步缩小
            for _ in range(5):
                rc = page.insert_textbox(
                    rect,
                    block.text,
                    fontsize=fontsize,
                    color=(0, 0, 0),
                    render_mode=3,  # 不可见但可选中/搜索
                )
                if rc >= 0:
                    break
                fontsize *= 0.75
                if fontsize < 1:
                    break

        self._pdf_document.is_modified = True
        self._update_page_info(page_index)

    def _update_page_info(self, page_index: int) -> None:
        """更新指定页面的状态信息。"""
        if self._doc is None or self._pdf_document is None:
            return
        if page_index >= len(self._pdf_document.pages):
            return
        text_layers = self._detect_text_layers(page_index)
        page = self._doc[page_index]
        info = self._pdf_document.pages[page_index]
        info.rotation = page.rotation
        info.has_text_layer = len(text_layers) > 0
        info.text_layers = text_layers
        info.is_scanned = not text_layers and self._is_page_scanned(page_index)
        info.thumbnail = None

    def _invalidate_thumbnails(self, page_indices: list[int]) -> None:
        """清除指定页面的缩略图缓存。"""
        if self._pdf_document is None:
            return
        for idx in page_indices:
            if 0 <= idx < len(self._pdf_document.pages):
                self._pdf_document.pages[idx].thumbnail = None
