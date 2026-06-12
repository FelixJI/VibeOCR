"""PDF 操作服务（无状态工具层）

所有方法为 @staticmethod，接收 fitz.Document / PdfDocument 参数，不持有任何实例状态。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import fitz
import numpy as np
from PySide6.QtGui import QImage, QPixmap

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo, TextLayerInfo

logger = logging.getLogger(__name__)


class PdfService:
    # ---- open / save ------------------------------------------------

    @staticmethod
    def open_doc(file_path: str) -> tuple[fitz.Document, PdfDocument]:
        """打开 PDF 并返回 (fitz.Document, PdfDocument)。"""
        if not Path(file_path).exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        doc = fitz.open(file_path)
        if doc.is_encrypted:
            doc.close()
            raise RuntimeError("不支持加密 PDF 文件")

        pdf_document = PdfDocument(file_path=file_path)
        # 创建轻量占位页面，避免在主线程上对每页执行 detect_text_layers /
        # is_page_scanned 等耗时操作。详细的页面信息由 PdfLoadWorker 在后台逐页填充。
        pdf_document.pages = [
            PdfPageInfo(page_index=i, rotation=doc[i].rotation)
            for i in range(doc.page_count)
        ]
        return doc, pdf_document

    @staticmethod
    def save(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        path: str | None = None,
    ) -> None:
        if path is None:
            save_path = pdf_document.file_path
            if save_path is None:
                return
            backup_path = save_path + ".bak"
            shutil.copy2(save_path, backup_path)
            try:
                doc.save(save_path, incremental=True, encryption=0)
                Path(backup_path).unlink(missing_ok=True)
            except Exception:
                shutil.copy2(backup_path, save_path)
                Path(backup_path).unlink(missing_ok=True)
                raise
        else:
            doc.save(path, deflate=True)
        pdf_document.is_modified = False

    # ---- render -----------------------------------------------------

    @staticmethod
    def render_page(doc: fitz.Document, page_index: int, dpi: int = 96) -> QPixmap:
        page = doc[page_index]
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

    @staticmethod
    def render_page_as_array(
        doc: fitz.Document, page_index: int, dpi: int = 300
    ) -> np.ndarray:
        page = doc[page_index]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=mat)
        return (
            np.frombuffer(pixmap.samples, dtype=np.uint8)
            .reshape(pixmap.height, pixmap.width, 3)
            .copy()
        )

    # ---- text layer detection ---------------------------------------

    @staticmethod
    def detect_text_layers(doc: fitz.Document, page_index: int) -> list[TextLayerInfo]:
        page = doc[page_index]
        page_dict: dict[str, Any] = page.get_text("dict")  # type: ignore[assignment]
        blocks: list[dict[str, Any]] = page_dict["blocks"]

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
            layers.append(
                TextLayerInfo(
                    index=layer_index,
                    text_preview=full_text[:30],
                    char_count=len(full_text),
                    bbox=(
                        float(bbox[0]),
                        float(bbox[1]),
                        float(bbox[2]),
                        float(bbox[3]),
                    ),
                    color_id=layer_index % 8,
                )
            )
            layer_index += 1
        return layers

    @staticmethod
    def is_page_scanned(doc: fitz.Document, page_index: int) -> bool:
        page = doc[page_index]
        images = page.get_images(full=True)
        if not images:
            return False
        page_rect = page.rect
        for img_info in images:
            xref = img_info[0]
            for rect in page.get_image_rects(xref):
                coverage = (rect.width * rect.height) / (
                    page_rect.width * page_rect.height
                )
                if coverage > 0.5:
                    return True
        return False

    # ---- page infos -------------------------------------------------

    @staticmethod
    def build_page_infos(doc: fitz.Document, pdf_document: PdfDocument) -> None:
        pages: list[PdfPageInfo] = []
        for i in range(doc.page_count):
            text_layers = PdfService.detect_text_layers(doc, i)
            page = doc[i]
            pages.append(
                PdfPageInfo(
                    page_index=i,
                    rotation=page.rotation,
                    has_text_layer=len(text_layers) > 0,
                    text_layers=text_layers,
                    is_scanned=len(text_layers) == 0
                    and PdfService.is_page_scanned(doc, i),
                )
            )
        pdf_document.pages = pages

    @staticmethod
    def update_page_info(
        doc: fitz.Document, pdf_document: PdfDocument, page_index: int
    ) -> None:
        if page_index >= len(pdf_document.pages):
            return
        text_layers = PdfService.detect_text_layers(doc, page_index)
        page = doc[page_index]
        info = pdf_document.pages[page_index]
        info.rotation = page.rotation
        info.has_text_layer = len(text_layers) > 0
        info.text_layers = text_layers
        info.is_scanned = not text_layers and PdfService.is_page_scanned(
            doc, page_index
        )
        info.thumbnail = None

    # ---- page mutations ---------------------------------------------

    @staticmethod
    def rotate_pages(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        page_indices: list[int],
        angle: int,
    ) -> None:
        for idx in page_indices:
            if 0 <= idx < doc.page_count:
                page = doc[idx]
                page.set_rotation((page.rotation + angle) % 360)
                pdf_document.pages[idx].rotation = page.rotation
        pdf_document.is_modified = True
        PdfService.invalidate_thumbnails(pdf_document, page_indices)

    @staticmethod
    def delete_pages(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        page_indices: list[int],
    ) -> None:
        remaining = [
            p for i, p in enumerate(pdf_document.pages) if i not in page_indices
        ]
        for idx in sorted(page_indices, reverse=True):
            if 0 <= idx < doc.page_count:
                doc.delete_page(idx)
        pdf_document.pages = remaining
        pdf_document.is_modified = True

    @staticmethod
    def insert_blank_page(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        after_index: int,
        width: float = 612,
        height: float = 792,
    ) -> None:
        insert_at = after_index + 1
        doc.new_page(pno=insert_at, width=width, height=height)
        pdf_document.is_modified = True
        PdfService.build_page_infos(doc, pdf_document)

    @staticmethod
    def insert_pages_from(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        source_path: str,
        after_index: int,
    ) -> None:
        src = fitz.open(source_path)
        insert_at = after_index + 1
        doc.insert_pdf(src, start_at=insert_at)
        src.close()
        pdf_document.is_modified = True
        PdfService.build_page_infos(doc, pdf_document)

    @staticmethod
    def move_page(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        from_index: int,
        to_index: int,
    ) -> None:
        if from_index == to_index:
            return
        page_info = pdf_document.pages[from_index]
        doc.move_page(from_index, to_index)
        pages = list(pdf_document.pages)
        pages.pop(from_index)
        pages.insert(to_index, page_info)
        pdf_document.pages = pages
        pdf_document.is_modified = True

    @staticmethod
    def reorder_pages(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        new_order: list[int],
    ) -> None:
        """按 new_order 指定的顺序重排页面。

        new_order[i] = j 表示新位置 i 应放原索引 j 的页面。
        """
        n = len(new_order)
        if n != doc.page_count or n != len(pdf_document.pages):
            return
        if new_order == list(range(n)):
            return

        doc.select(new_order)
        pdf_document.pages = [pdf_document.pages[i] for i in new_order]
        pdf_document.is_modified = True

    # ---- text layer mutations ---------------------------------------

    @staticmethod
    def add_text_layer(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        page_index: int,
        ocr_result: object,
    ) -> None:
        page = doc[page_index]
        page_rect = page.rect

        text_blocks = getattr(ocr_result, "text_blocks", [])
        for block in text_blocks:
            if block.text is None or not block.text.strip():
                continue
            bbox = block.bbox
            if bbox is None:
                continue
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

            for _ in range(5):
                rc = page.insert_textbox(
                    rect,
                    block.text,
                    fontsize=fontsize,
                    color=(0, 0, 0),
                    render_mode=3,
                )
                if rc >= 0:
                    break
                fontsize *= 0.75
                if fontsize < 1:
                    break

        pdf_document.is_modified = True
        PdfService.update_page_info(doc, pdf_document, page_index)

    @staticmethod
    def delete_text_layers(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        page_index: int,
    ) -> None:
        page = doc[page_index]
        page_dict: dict[str, Any] = page.get_text("dict")  # type: ignore[assignment]
        blocks: list[dict[str, Any]] = page_dict["blocks"]

        has_text = any(block["type"] == 0 for block in blocks)
        if not has_text:
            return

        for block in blocks:
            if block["type"] != 0:
                continue
            rect = fitz.Rect(block["bbox"])
            page.add_redact_annot(rect, fill=None)

        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)  # type: ignore[attr-defined]
        pdf_document.is_modified = True
        PdfService.update_page_info(doc, pdf_document, page_index)

    # ---- helpers ----------------------------------------------------

    @staticmethod
    def invalidate_thumbnails(
        pdf_document: PdfDocument, page_indices: list[int]
    ) -> None:
        for idx in page_indices:
            if 0 <= idx < len(pdf_document.pages):
                pdf_document.pages[idx].thumbnail = None
