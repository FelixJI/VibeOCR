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
