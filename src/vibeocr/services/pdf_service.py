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
from vibeocr.utils.cjk_font_resolver import _CJK_RESOLVER

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
        pdf_settings: object | None = None,
        overwrite: bool = False,
    ) -> tuple[int, int]:
        """将 OCR 结果作为隐形文字层写入 PDF 页面。

        使用内置 china-s CJK CID 字体，确保中文等字符可被写入并被阅读器提取。

        写入完成后，OCR 原始块（归一化 bbox）缓存到 PdfPageInfo.ocr_text_blocks，
        作为预览/编辑/重写的唯一信源。不再用 detect_text_layers 重读（PyMuPDF 会
        把细粒度块合并成粗块，导致预览显示合并后的错误块）。

        Args:
            doc: fitz.Document 实例。
            pdf_document: PdfDocument 状态对象。
            page_index: 页码索引。
            ocr_result: OCRResult 实例。
            pdf_settings: PdfGlobalSettings 实例（None 则使用默认值）。
            overwrite: 若为 True 且该页已有文字层，先删除再写入；若为 False 且
                该页已有文字层，直接跳过返回 (0, 1)，绝不叠加。

        Returns:
            (written, skipped) 成功写入与被跳过的文本块数量。
        """
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        settings = pdf_settings if pdf_settings is not None else PdfGlobalSettings()
        preproc_angle = getattr(ocr_result, "preproc_angle", 0)
        text_blocks = list(getattr(ocr_result, "text_blocks", []))

        # 防重复守卫：已有文字层时按 overwrite 决定跳过或先删后写
        page_info = pdf_document.pages[page_index]
        if page_info.has_text_layer:
            if not overwrite:
                logger.info("page %d 已有文字层，跳过（overwrite=False）", page_index)
                return 0, 1
            logger.info("page %d 已有文字层，overwrite=True，先删除再写入", page_index)
            PdfService.delete_text_layers(doc, pdf_document, page_index)

        written, skipped = PdfService._write_blocks_to_page(
            doc, page_index, text_blocks, preproc_angle, settings
        )

        # 缓存 OCR 原始块（预览/编辑/重写的唯一信源），替代旧的 detect_text_layers 重读
        pdf_document.is_modified = True
        info = pdf_document.pages[page_index]
        info.ocr_text_blocks = text_blocks
        info.ocr_preproc_angle = preproc_angle
        info.has_text_layer = written > 0
        info.thumbnail = None
        return written, skipped

    @staticmethod
    def _write_blocks_to_page(
        doc: fitz.Document,
        page_index: int,
        text_blocks: list,
        preproc_angle: int,
        settings: object,
    ) -> tuple[int, int]:
        """将文本块逐个写入指定页面（纯写入，不修改 PdfPageInfo 元信息）。

        add_text_layer（首次写入）与 rewrite_text_layer（编辑后重写）共用此方法，
        保证两条路径的字号策略、字体、兜底逻辑完全一致。

        Args:
            doc: fitz.Document 实例。
            page_index: 页码索引。
            text_blocks: TextBlock 列表（归一化 [0,1000] bbox）。
            preproc_angle: OCR 预处理旋转角度（用于坐标逆旋转）。
            settings: PdfGlobalSettings 实例。

        Returns:
            (written, skipped) 成功写入与被跳过的文本块数量。
        """
        page = doc[page_index]
        page_rect = page.rect

        # 页面 /Rotate 处理：OCR 渲染图（get_pixmap 自动应用 /Rotate）与归一化
        # bbox 都在『显示空间』，但 insert_textbox 写入的是『mediabox（未旋转）
        # 空间』。当 page.rotation != 0 时，必须把『显示空间』的 rect 经
        # derotation_matrix 映射到 mediabox 空间，否则会出现『上面的字写到了
        # 右面』（90° 旋转时宽高互换 + 旋转未补偿）。
        # _denormalize_and_unrotate_bbox 用 page_rect（显示尺寸）归一化，产出
        # 仍在『显示空间』；下面 derotate_to_mediabox 把它转到 mediabox 空间。
        page_rotation = int(page.rotation or 0) % 360
        if page_rotation != 0:
            dm = page.derotation_matrix  # 显示空间 → mediabox 空间

            def _derotate_to_mediabox(rect: fitz.Rect) -> fitz.Rect:
                a, b, c, d, e, f = (
                    dm.a,
                    dm.b,
                    dm.c,
                    dm.d,
                    dm.e,
                    dm.f,
                )

                def _tr(x, y):
                    return a * x + c * y + e, b * x + d * y + f

                pts = [
                    _tr(rect.x0, rect.y0),
                    _tr(rect.x1, rect.y0),
                    _tr(rect.x0, rect.y1),
                    _tr(rect.x1, rect.y1),
                ]
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                return fitz.Rect(min(xs), min(ys), max(xs), max(ys))
        else:

            def _derotate_to_mediabox(rect: fitz.Rect) -> fitz.Rect:
                return rect

        # 收集本页所有字符，解析子集字体（探测失败则 None，回退 china-s）。
        # 子集字体嵌入后 PyMuPDF 自动生成 ToUnicode CMap，使文字层在所有
        # 主流阅读器可搜索/复制（china-s 依赖阅读器自带 Adobe GB1 CMap，脆弱）。
        #
        # fontname 必须随子集字体变化：PyMuPDF 按名字缓存字体资源，同一页用
        # 相同名字插入不同 fontfile 时会复用第一个（缺字写入 \x00）。用路径
        # 的 md5 前 4 字节派生名字，保证同页两次写入（add→rewrite）不同字符集
        # 的子集不冲突。子集路径是 tempfile 随机名，fontname 随之每进程不同，
        # 但本进程内同子集（同路径）名字稳定即可。
        import hashlib

        all_chars = "".join(b.text for b in text_blocks if b.text)
        font_path = _CJK_RESOLVER.resolve(all_chars)
        if font_path is not None:
            fontname = "F" + hashlib.md5(font_path.encode()).hexdigest()[:4]
        else:
            fontname = "china-s"

        written = 0
        skipped = 0
        for block in text_blocks:
            if block.text is None or not block.text.strip():
                continue
            bbox = block.bbox
            if bbox is None:
                logger.warning(
                    "page %d block skipped (bbox is None): text=%r",
                    page_index,
                    block.text[:30],
                )
                skipped += 1
                continue

            # 逆旋转(OCR 预处理) + 归一化到『显示空间』坐标，
            # 再补偿页面 /Rotate 转到 mediabox（写入）空间。
            rect = PdfService._denormalize_and_unrotate_bbox(
                bbox, preproc_angle, page_rect
            )
            rect = _derotate_to_mediabox(rect)
            # 仅当矩形退化（宽或高 ≤ 0）才整体跳过；
            # 矮行/窄框不丢弃：字号由 min_font_size 兜底，再交给下方
            # insert_textbox 重试 + insert_text 兜底，保证文字进入文字层。
            if rect.is_empty or rect.width <= 0 or rect.height <= 0:
                logger.warning(
                    "page %d block skipped (rect empty): rect=%s text=%r",
                    page_index,
                    rect,
                    block.text[:30],
                )
                skipped += 1
                continue

            # 字号：行高 × 比例；矮行算出的字号过小时夹紧到 min_font_size，
            # 保证隐形文字仍可被阅读器提取（不丢块）。
            fontsize = max(
                rect.height * settings.font_size_ratio, settings.min_font_size
            )

            render_mode = 0 if settings.text_layer_visible else 3
            inserted = False
            last_fontsize = fontsize
            for _ in range(settings.font_size_retry_count):
                rc = page.insert_textbox(
                    rect,
                    block.text,
                    fontsize=fontsize,
                    fontname=fontname,
                    fontfile=font_path,
                    color=(0, 0, 0),
                    render_mode=render_mode,
                )
                if rc >= 0:
                    inserted = True
                    break
                last_fontsize = fontsize
                fontsize *= settings.font_size_shrink_factor
                if fontsize < 1:
                    break

            if inserted:
                written += 1
            else:
                # 兜底：insert_textbox 在窄/瘦高矩形里装不下时
                # （如竖排文字被聚成瘦高块），降级为 insert_text 单点定位：
                # 文字从矩形左下角（基线）起写，溢出也写入，保证该词进入文字层。
                try:
                    baseline = fitz.Point(rect.x0, rect.y1 - last_fontsize * 0.2)
                    page.insert_text(
                        baseline,
                        block.text,
                        fontsize=last_fontsize,
                        fontname=fontname,
                        fontfile=font_path,
                        color=(0, 0, 0),
                        render_mode=render_mode,
                    )
                    written += 1
                    logger.debug(
                        "page %d block 写入文字层（insert_text 兜底）: rect=%s text=%r",
                        page_index,
                        rect,
                        block.text[:30],
                    )
                except Exception as e:
                    logger.warning(
                        "page %d block skipped (font retry exhausted + "
                        "fallback failed): rect=%s text=%r err=%s",
                        page_index,
                        rect,
                        block.text[:30],
                        e,
                    )
                    skipped += 1

        return written, skipped

    @staticmethod
    def rewrite_text_layer(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        page_index: int,
        text_blocks: list,
        preproc_angle: int,
        pdf_settings: object | None = None,
    ) -> tuple[int, int]:
        """删除整页文字层后，按 text_blocks 全量重写。

        供"保存"时把用户编辑后的块写回 PDF。先 redact 清空旧文字层，
        再逐块写入（复用 _write_blocks_to_page，与首次写入逻辑一致）。

        Args:
            doc: fitz.Document 实例。
            pdf_document: PdfDocument 状态对象。
            page_index: 页码索引。
            text_blocks: 编辑后的 TextBlock 列表（归一化 [0,1000] bbox）。
            preproc_angle: OCR 预处理旋转角度。
            pdf_settings: PdfGlobalSettings 实例（None 则使用默认值）。

        Returns:
            (written, skipped) 成功写入与被跳过的文本块数量。
        """
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        settings = pdf_settings if pdf_settings is not None else PdfGlobalSettings()

        # 先删除旧文字层（redact 全页文字，保留图片）
        # 注意：delete_text_layers 会 update_page_info 并清空 ocr_text_blocks，
        # 所以必须在删除后重新设置 ocr_text_blocks。
        PdfService.delete_text_layers(doc, pdf_document, page_index)

        written, skipped = PdfService._write_blocks_to_page(
            doc, page_index, text_blocks, preproc_angle, settings
        )

        # 重设缓存（delete 清空了）
        pdf_document.is_modified = True
        info = pdf_document.pages[page_index]
        info.ocr_text_blocks = list(text_blocks)
        info.ocr_preproc_angle = preproc_angle
        info.has_text_layer = written > 0
        info.thumbnail = None
        return written, skipped

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
        # 清空 OCR 原始块缓存（文字层已删，缓存的块不再对应任何 PDF 内容）
        info = pdf_document.pages[page_index]
        info.ocr_text_blocks = []
        info.ocr_preproc_angle = 0

    # ---- bbox coordinate transforms --------------------------------

    @staticmethod
    def _denormalize_and_unrotate_bbox(
        bbox: tuple[float, float, float, float],
        preproc_angle: int,
        page_rect: fitz.Rect,
    ) -> fitz.Rect:
        """将 [0, 1000] 归一化 bbox 逆旋转后映射到 PDF 页面坐标。

        当 OCR 预处理旋转了图像（preproc_angle），bbox 坐标在旋转后的空间中。
        此方法执行逆变换，将坐标映射回原始页面坐标。

        Args:
            bbox: 归一化坐标 (x0, y0, x1, y1)，范围 [0, 1000]。
            preproc_angle: 预处理旋转角度 (0, 90, 180, 270)。
            page_rect: PDF 页面矩形 (points)。

        Returns:
            映射后的 fitz.Rect。
        """
        nx0, ny0, nx1, ny1 = (
            bbox[0] / 1000,
            bbox[1] / 1000,
            bbox[2] / 1000,
            bbox[3] / 1000,
        )
        pw, ph = page_rect.width, page_rect.height

        if preproc_angle == 90:
            # 逆时针 90°: y→x, (1-x)→y
            x0 = ny0 * pw
            y0 = (1 - nx1) * ph
            x1 = ny1 * pw
            y1 = (1 - nx0) * ph
        elif preproc_angle == 180:
            # 中心对称
            x0 = (1 - nx1) * pw
            y0 = (1 - ny1) * ph
            x1 = (1 - nx0) * pw
            y1 = (1 - ny0) * ph
        elif preproc_angle == 270:
            # 顺时针 90° (= 逆时针 270°): (1-y)→x, x→y
            x0 = (1 - ny1) * pw
            y0 = nx0 * ph
            x1 = (1 - ny0) * pw
            y1 = nx1 * ph
        else:
            # 0° 或未知角度：直接映射
            x0 = nx0 * pw
            y0 = ny0 * ph
            x1 = nx1 * pw
            y1 = ny1 * ph

        return fitz.Rect(x0, y0, x1, y1)

    @staticmethod
    def bbox_to_pixel(
        bbox: tuple[float, float, float, float],
        page_rect: fitz.Rect,
        render_dpi: int,
        source: str = "pdf",
    ) -> tuple[float, float, float, float]:
        """将 bbox 转换为渲染图像的像素坐标。

        Args:
            bbox: 输入 bbox。
            page_rect: PDF 页面矩形 (points)。
            render_dpi: 渲染 DPI。
            source: "pdf" 表示 bbox 是 PDF points 坐标，
                    "normalized" 表示 [0, 1000] 归一化坐标。

        Returns:
            像素坐标 (x0, y0, x1, y1)。
        """
        if source == "normalized":
            # 先转为 PDF points
            x0 = bbox[0] / 1000 * page_rect.width
            y0 = bbox[1] / 1000 * page_rect.height
            x1 = bbox[2] / 1000 * page_rect.width
            y1 = bbox[3] / 1000 * page_rect.height
        else:
            x0, y0, x1, y1 = bbox

        # PDF points → pixels: coord / 72 * dpi
        scale = render_dpi / 72.0
        return (x0 * scale, y0 * scale, x1 * scale, y1 * scale)

    # ---- helpers ----------------------------------------------------

    @staticmethod
    def invalidate_thumbnails(
        pdf_document: PdfDocument, page_indices: list[int]
    ) -> None:
        for idx in page_indices:
            if 0 <= idx < len(pdf_document.pages):
                pdf_document.pages[idx].thumbnail = None
