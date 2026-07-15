# tests/services/test_pdf_service.py
"""Tests for PDF service (stateless static methods)."""

from pathlib import Path
from typing import Any, cast

import fitz
import pytest

from vibeocr.services.pdf_service import PdfService


def _create_test_pdf(path: Path, num_pages: int = 3) -> Path:
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Page {i + 1}", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


def _create_scanned_pdf(path: Path, width: int = 612, height: int = 792) -> Path:
    """创建单页扫描件 PDF（整页是灰底图，无内嵌文字层）。"""
    import numpy as np

    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    img = np.ones((height, width, 3), dtype=np.uint8) * 240
    cs = fitz.Colorspace(fitz.CS_RGB)
    pixmap = fitz.Pixmap(cs, width, height, img.tobytes(), 0)
    page.insert_image(fitz.Rect(0, 0, width, height), pixmap=pixmap)
    doc.save(str(path))
    doc.close()
    return path


def _make_ocr_result(*blocks_texts, angle=0):
    """便捷构造 OCRResult（多个 (text, bbox) 对，归一化 [0,1000] bbox）。"""
    from vibeocr.models.ocr_result import OCRResult, TextBlock

    text_blocks = []
    for text, bbox in blocks_texts:
        text_blocks.append(TextBlock(text=text, score=0.95, bbox=bbox))
    return OCRResult(
        raw_text="\n".join(t for t, _ in blocks_texts),
        text_blocks=text_blocks,
        preproc_angle=angle,
    )


@pytest.fixture
def test_pdf(tmp_path):
    return _create_test_pdf(tmp_path / "test.pdf", num_pages=3)


@pytest.fixture
def opened_doc(test_pdf):
    """返回 (doc, pdf_document) 元组，测试后自动关闭。

    显式调用 build_page_infos 以模拟 PdfLoadWorker 的后台分析结果。
    """
    doc, pdf_doc = PdfService.open_doc(str(test_pdf))
    PdfService.build_page_infos(doc, pdf_doc)
    yield doc, pdf_doc
    doc.close()


class TestPdfServiceOpen:
    def test_open_doc(self, test_pdf):
        doc, pdf_doc = PdfService.open_doc(str(test_pdf))
        assert pdf_doc.file_path == str(test_pdf)
        assert pdf_doc.page_count == 3
        assert len(pdf_doc.pages) == 3
        assert pdf_doc.pages[0].rotation == 0
        doc.close()

    def test_open_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            PdfService.open_doc("/nonexistent/file.pdf")

    def test_open_encrypt_raises(self, tmp_path):
        src = fitz.open()
        src.new_page(width=612, height=792)
        path = str(tmp_path / "encrypted.pdf")
        src.save(
            path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user"
        )
        src.close()
        with pytest.raises(RuntimeError, match="加密"):
            PdfService.open_doc(path)


class TestPdfServiceSave:
    def test_save_to_new_path(self, opened_doc, tmp_path):
        doc, pdf_doc = opened_doc
        PdfService.rotate_pages(doc, pdf_doc, [0], 90)
        save_path = str(tmp_path / "saved.pdf")
        PdfService.save(doc, pdf_doc, path=save_path)

        verify = fitz.open(save_path)
        assert verify[0].rotation == 90
        verify.close()

    def test_save_in_place_creates_backup(self, test_pdf):
        # 不用 opened_doc fixture：默认 compress_on_save=True 时 save 会
        # close+reopen doc，fixture 持有的旧 doc 引用会失效。
        doc, pdf_doc = PdfService.open_doc(str(test_pdf))
        try:
            PdfService.rotate_pages(doc, pdf_doc, [0], 90)
            file_path = pdf_doc.file_path
            new_doc = PdfService.save(doc, pdf_doc)

            assert Path(str(file_path) + ".bak").exists() is False
            verify = fitz.open(str(file_path))
            assert verify[0].rotation == 90
            verify.close()
            # 全量压缩覆盖：关新 doc（原 doc 已 close）
            if new_doc is not None:
                new_doc.close()
            else:
                doc.close()
        except Exception:
            doc.close()
            raise

    def test_save_clean_on_save_false_preserves_content(self, tmp_path):
        """clean_on_save=False（默认）时全量压缩不深度清理内容流，
        但仍产出可读、内容完整的 PDF。

        回归 issue: 加文字层后体积翻倍。根因 clean=True 解压重写扫描件
        内容流 + MuPDF 无法保留 ObjStm/CrossRefStream。改默认 clean=False 后，
        文字层仍应正常写入、可搜索、页面结构完整。
        """
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        path = tmp_path / "scan_clean.pdf"
        doc, pdf_doc = PdfService.open_doc(str(_create_scanned_pdf(path)))
        try:
            from vibeocr.models.ocr_result import OCRResult, TextBlock

            result = OCRResult(
                raw_text="测试文字层",
                text_blocks=[
                    TextBlock(
                        text="测试文字层",
                        score=0.99,
                        bbox=(100.0, 100.0, 500.0, 200.0),
                        page_idx=0,
                    ),
                ],
            )
            PdfService.add_text_layer(doc, pdf_doc, 0, result)
            # clean_on_save=False 显式传入
            settings = PdfGlobalSettings(
                compress_on_save=True, clean_on_save=False
            )
            new_doc = PdfService.save(doc, pdf_doc, pdf_settings=settings)
            close_doc = new_doc if new_doc is not None else doc
            # 验证落盘文件可读、文字层可提取
            verify = fitz.open(str(path))
            text = verify[0].get_text()
            assert "测试" in text or "文字层" in text
            assert verify.page_count == 1
            verify.close()
            close_doc.close()
        except Exception:
            doc.close()
            raise

    def test_clean_on_save_default_is_false(self):
        """模型默认 clean_on_save=False（避免扫描件体积膨胀）。"""
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        s = PdfGlobalSettings()
        assert s.clean_on_save is False
        # round-trip
        d = s.to_dict()
        assert d["clean_on_save"] is False
        s2 = PdfGlobalSettings.from_dict(d)
        assert s2.clean_on_save is False


class TestPdfServiceRender:
    def test_render_thumbnail(self, opened_doc, qapp):
        from vibeocr.pyside.pdf_render import render_page_pixmap

        doc, _ = opened_doc
        pixmap = render_page_pixmap(doc, 0, dpi=96)
        assert pixmap is not None
        assert not pixmap.isNull()

    def test_render_page_for_ocr(self, opened_doc):
        doc, _ = opened_doc
        img_array = PdfService.render_page_as_array(doc, 0, dpi=300)
        assert img_array is not None
        assert img_array.shape[0] > 0
        assert img_array.shape[2] == 3


class TestPdfServiceRotate:
    def test_rotate_single_page(self, opened_doc):
        doc, pdf_doc = opened_doc
        PdfService.rotate_pages(doc, pdf_doc, [0], 90)
        assert pdf_doc.pages[0].rotation == 90
        assert pdf_doc.is_modified is True

    def test_rotate_all_pages(self, opened_doc):
        doc, pdf_doc = opened_doc
        indices = list(range(pdf_doc.page_count))
        PdfService.rotate_pages(doc, pdf_doc, indices, 90)
        for page in pdf_doc.pages:
            assert page.rotation == 90


class TestPdfServiceDelete:
    def test_delete_page(self, opened_doc):
        doc, pdf_doc = opened_doc
        assert pdf_doc.page_count == 3
        PdfService.delete_pages(doc, pdf_doc, [1])
        assert pdf_doc.page_count == 2
        assert pdf_doc.pages[0].page_index == 0
        assert pdf_doc.pages[1].page_index == 2


class TestPdfServiceInsert:
    def test_insert_blank_page(self, opened_doc):
        doc, pdf_doc = opened_doc
        PdfService.insert_blank_page(doc, pdf_doc, after_index=0)
        assert pdf_doc.page_count == 4
        assert pdf_doc.pages[1].rotation == 0

    def test_insert_from_another_pdf(self, opened_doc, tmp_path):
        other_pdf = _create_test_pdf(tmp_path / "other.pdf", num_pages=2)
        doc, pdf_doc = opened_doc
        PdfService.insert_pages_from(doc, pdf_doc, str(other_pdf), after_index=0)
        assert pdf_doc.page_count == 5


class TestPdfServiceMove:
    def test_move_page(self, opened_doc):
        doc, pdf_doc = opened_doc
        PdfService.move_page(doc, pdf_doc, 0, 2)
        assert pdf_doc.pages[2].page_index == 0


class TestPdfServiceTextLayer:
    def test_detect_text_layers(self, opened_doc):
        doc, _ = opened_doc
        layers = PdfService.detect_text_layers(doc, 0)
        assert len(layers) > 0
        assert layers[0].text_preview.startswith("Page")

    def test_detect_text_layers_line_level_no_far_merge(self, tmp_path):
        """detect_text_layers 用 line 级 bbox：纵向相距很远的文本不被合并到一个高亮框。

        Bug：旧版用 block['bbox']，PyMuPDF 的 block 会把同一文本列里纵向相邻但
        实际相距 300pt+ 的行合并成一个超大 bbox，预览高亮把不相邻的文字框在一起。
        修复：改用 line['bbox']，每条 bbox 是一条连续文本，与 OCR 行级粒度一致。
        """
        # 构造一页：两条横向文本，纵向相距 300pt（PyMuPDF 会合并到同一 block）
        path = tmp_path / "far.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((100, 100), "TOP TEXT", fontsize=14)
        page.insert_text((100, 400), "BOTTOM TEXT", fontsize=14)  # 300pt 下方
        doc.save(str(path))
        doc.close()

        doc, _ = PdfService.open_doc(str(path))
        layers = PdfService.detect_text_layers(doc, 0)
        # 应检测到 ≥2 个 layer（top/bottom 分开），而非合并成 1 个
        assert len(layers) >= 2, (
            f"相距 300pt 的两行文本应各自独立，实际只有 {len(layers)} 个 layer"
            f"（旧 block 级会合并成一个跨 300pt 的大框）"
        )
        # 各 layer 的 bbox 高度应远小于 300（不跨整列）
        for layer in layers:
            h = layer.bbox[3] - layer.bbox[1]
            assert h < 100, (
                f"layer bbox 高度 {h:.0f} 过大（应 < 100，远小于 300pt 间距）"
                f" text={layer.text_preview!r}"
            )
        doc.close()

    def test_add_text_layer_from_ocr_result(self, tmp_path):
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        path = tmp_path / "scan.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        assert pdf_doc.pages[0].has_text_layer is False

        result = OCRResult(
            raw_text="Hello World",
            text_blocks=[
                TextBlock(
                    text="Hello World",
                    score=0.99,
                    bbox=(50.0, 50.0, 300.0, 100.0),
                    page_idx=0,
                ),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        assert pdf_doc.pages[0].has_text_layer is True
        assert pdf_doc.is_modified is True
        doc.close()

    def test_add_text_layer_writes_chinese_text(self, tmp_path):
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        path = tmp_path / "scan_cn.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        chinese = "你好世界，这是一段测试文字。"
        result = OCRResult(
            raw_text=chinese,
            text_blocks=[
                TextBlock(
                    text=chinese,
                    score=0.99,
                    bbox=(50.0, 50.0, 500.0, 120.0),
                    page_idx=0,
                ),
            ],
        )
        written, skipped = PdfService.add_text_layer(doc, pdf_doc, 0, result)
        assert written == 1
        assert skipped == 0
        # 中文必须能被回读（验证 china-s 字体生效）
        extracted = doc[0].get_text()
        assert "你好世界" in extracted
        doc.close()

    def test_add_text_layer_skips_none_bbox_with_warning(self, tmp_path, caplog):
        import logging

        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        path = tmp_path / "scan_tiny.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        good = TextBlock(
            text="正常文字", score=0.9, bbox=(50.0, 50.0, 400.0, 100.0), page_idx=0
        )
        # bbox=None 的块（OCR 未给出坐标）会被跳过并记录警告
        no_bbox = TextBlock(text="无坐标", score=0.9, bbox=None, page_idx=0)
        result = OCRResult(raw_text="x", text_blocks=[good, no_bbox])

        with caplog.at_level(logging.WARNING, logger="vibeocr.services.pdf_service"):
            written, skipped = PdfService.add_text_layer(doc, pdf_doc, 0, result)

        assert written == 1
        assert skipped == 1
        assert any("skipped" in rec.message for rec in caplog.records)
        doc.close()

    def test_add_text_layer_fallback_insert_text_on_narrow_bbox(self, tmp_path):
        """窄/瘦高矩形装不下横向文字时，缩字号后 insert_textbox 自动换行写入（不再跳过/溢出）。

        复现真实报错场景：bbox 宽 ~20pt、高 ~50pt，文字是 3 个汉字。
        修正后字号策略（rect.height/1.6 且受宽度约束 + min_font_size 兜底）会让
        insert_textbox 以最小字号自动换行写入，文字留在 bbox 内、可搜索/可选中，
        不再退化为 insert_text 单点写入导致横向大幅溢出到无关区域。
        """
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        path = tmp_path / "scan_narrow.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        # 瘦高矩形（宽 20pt、高 50pt）+ 3 个汉字
        narrow = TextBlock(
            text="签回联",
            score=0.95,
            bbox=(50.0, 50.0, 70.0, 100.0),
            page_idx=0,
        )
        result = OCRResult(raw_text="签回联", text_blocks=[narrow])

        written, skipped = PdfService.add_text_layer(doc, pdf_doc, 0, result)

        # 成功写入，不计 skip
        assert written == 1
        assert skipped == 0
        # 文字层确实包含全部字符（可搜索，可能换行）
        extracted = doc[0].get_text()
        assert "签" in extracted and "回" in extracted and "联" in extracted
        doc.close()

    def test_add_text_layer_fallback_logs_debug(self, tmp_path, caplog):
        """窄/高块装不下时兜底 insert_text 写入走 DEBUG 日志（便于排查）。

        窄/高块（width < height）走 insert_textbox 路径；长文本在小框内缩字号
        仍装不下时退化 insert_text 兜底，应有 DEBUG 日志。
        """
        import logging

        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        path = tmp_path / "scan_fb.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        narrow = TextBlock(
            text="中文测试",
            score=0.9,
            # 极小窄/高块（width=2 < height=3，均 < min_font_size 行距预算）：
            # 走 insert_textbox 路径，字号缩到 min_font_size 仍装不下 → insert_text 兜底。
            bbox=(50.0, 50.0, 52.0, 53.0),
            page_idx=0,
        )
        result = OCRResult(raw_text="中文测试", text_blocks=[narrow])

        with caplog.at_level(logging.DEBUG, logger="vibeocr.services.pdf_service"):
            PdfService.add_text_layer(doc, pdf_doc, 0, result)

        # 兜底写入应有 DEBUG 日志
        assert any("insert_text 兜底" in rec.message for rec in caplog.records)
        doc.close()

    def test_add_text_layer_with_90_rotation(self, tmp_path):
        """90° 预处理旋转后 bbox 仍然映射到正确位置。"""
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        path = tmp_path / "scan_rotated.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        # 模拟 OCR 检测到图像旋转 90°，返回旋转后空间中的 bbox
        result = OCRResult(
            raw_text="Hello",
            text_blocks=[
                TextBlock(
                    text="Hello",
                    score=0.99,
                    bbox=(
                        400.0,
                        100.0,
                        600.0,
                        350.0,
                    ),  # [0, 1000] 归一化（足够宽以容纳 CJK 字体下的拉丁字形）
                    page_idx=0,
                ),
            ],
            preproc_angle=90,
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        assert pdf_doc.pages[0].has_text_layer is True

        # 验证文字层出现在页面上（而非页面外）
        layers = PdfService.detect_text_layers(doc, 0)
        assert len(layers) > 0
        # 文字层应该完全在页面内
        page_rect = doc[0].rect
        for layer in layers:
            layer_rect = fitz.Rect(layer.bbox)
            assert layer_rect.x0 >= -1  # 允许 1pt 误差
            assert layer_rect.y0 >= -1
            assert layer_rect.x1 <= page_rect.width + 1
            assert layer_rect.y1 <= page_rect.height + 1
        doc.close()

    def test_add_text_layer_uses_global_settings(self, tmp_path):
        """PdfGlobalSettings 控制字号和重试参数。"""
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        path = tmp_path / "scan_settings.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        settings = PdfGlobalSettings(font_size_ratio=0.5, font_size_retry_count=2)
        result = OCRResult(
            raw_text="Test",
            text_blocks=[
                TextBlock(
                    text="Test",
                    score=0.99,
                    bbox=(100.0, 100.0, 500.0, 200.0),
                    page_idx=0,
                ),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result, pdf_settings=settings)
        assert pdf_doc.pages[0].has_text_layer is True
        doc.close()

    def test_add_text_layer_default_angle_in_page_bounds(self, tmp_path):
        """preproc_angle=0（当前生产路径）时，文字层完全落在页面内。"""
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        path = tmp_path / "scan_default.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        # 不传 preproc_angle → 默认 0（模拟当前 MineRU/VL 管道的真实输出）
        result = OCRResult(
            raw_text="Hello",
            text_blocks=[
                TextBlock(
                    text="Hello",
                    score=0.99,
                    bbox=(100.0, 100.0, 500.0, 200.0),  # [0, 1000] 归一化
                    page_idx=0,
                ),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)

        layers = PdfService.detect_text_layers(doc, 0)
        assert len(layers) > 0
        page_rect = doc[0].rect
        for layer in layers:
            lr = fitz.Rect(layer.bbox)
            assert lr.x0 >= -1
            assert lr.y0 >= -1
            assert lr.x1 <= page_rect.width + 1
            assert lr.y1 <= page_rect.height + 1
        doc.close()

    def _measure_visible_text_aspect(self, doc, page_index: int = 0) -> float:
        """渲染页面（自动应用 /Rotate），测深色（文字）像素包围盒的长宽比 w/h。

        用于回归『外部阅读器打开文字层旋转 90°』：写入可见文字层（render_mode=0）
        后，渲染页面取文字像素的 w/h。水平文字 w/h ≫ 1，被错误旋转的竖排文字
        w/h ≈ 1 或更小。
        """
        import numpy as np

        page = doc[page_index]
        pix = page.get_pixmap(dpi=72)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        gray = arr[:, :, :3].mean(axis=2)
        mask = gray < 128  # 深色 = 可见文字
        ys, xs = np.where(mask)
        assert len(xs) > 0, "未渲染出可见文字（render_mode 可能仍是隐形 3）"
        w = xs.max() - xs.min() + 1
        h = ys.max() - ys.min() + 1
        return w / h

    def test_add_text_layer_page_rotation_90_glyph_orientation(self, tmp_path):
        """页面 /Rotate=90（区别于 preproc_angle=90）时，写入的文字层字形方向正确。

        回归 issue: 程序内预览正常，但外部阅读器打开文字层旋转 90°。
        根因: _derotate_to_mediabox 把显示空间宽框转成 mediabox 瘦高框，
        insert_textbox 默认 rotate=0 把字排进瘦高框 → 字竖排 → /Rotate 渲染后
        看起来旋转 90°。修复: /Rotate∈{90,270} 时给 insert_textbox 传 rotate=90。

        断言: 写入可见文字层后渲染，文字像素 w/h 与 rotation=0 基准接近
        （都是水平文字，w/h ≫ 1），而非被旋转成接近 1。
        """
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        visible = PdfGlobalSettings(text_layer_visible=True)

        def _build(path, page_rotation: int):
            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            img = np.ones((792, 612, 3), dtype=np.uint8) * 240
            cs = fitz.Colorspace(fitz.CS_RGB)
            pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
            page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
            doc.save(str(path))
            doc.close()
            # 设置真实页面 /Rotate（在重新打开后设置，确保落盘属性正确）
            doc = fitz.open(str(path))
            doc[0].set_rotation(page_rotation)
            doc.saveIncr()
            doc.close()

        def _aspect_for(page_rotation: int) -> float:
            path = tmp_path / f"scan_pagerot_{page_rotation}.pdf"
            _build(path, page_rotation)
            doc, pdf_doc = PdfService.open_doc(str(path))
            result = OCRResult(
                raw_text="HELLO WORLD",
                text_blocks=[
                    TextBlock(
                        text="HELLO WORLD",
                        score=0.99,
                        bbox=(150.0, 250.0, 550.0, 300.0),  # 宽框 [0,1000]
                        page_idx=0,
                    ),
                ],
            )
            PdfService.add_text_layer(doc, pdf_doc, 0, result, pdf_settings=visible)
            aspect = self._measure_visible_text_aspect(doc, 0)
            doc.close()
            return aspect

        baseline = _aspect_for(0)  # rotation=0 基准（水平文字）
        # 基准应是明显的水平文字（w/h 明显大于 1）
        assert baseline > 3.0, f"rotation=0 基准文字应水平(w/h>3)，实际 {baseline:.2f}"
        for rot in (90, 270):
            aspect = _aspect_for(rot)
            # 修复后各旋转的文字长宽比应与基准同量级（都是水平文字），
            # 而非被错误旋转成接近 1（竖排）。允许一定浮动（旋转后字距/对齐差异）。
            assert aspect > baseline * 0.5, (
                f"page.rotation={rot} 文字层字形被旋转 90°（w/h={aspect:.2f}，"
                f"基准 {baseline:.2f}）。应为水平文字。"
            )

    def test_add_text_layer_writes_short_height_bbox(self, tmp_path):
        """矮行框（OCR 偶发返回的薄行）不应被整体丢弃。

        归一化高度 1（=0.79pt）的框，字号经最小字号兜底后仍应写入文字。
        回归 issue: 文字层识别遗漏。
        """
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        path = tmp_path / "scan_short.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        result = OCRResult(
            raw_text="矮行文字",
            text_blocks=[
                TextBlock(
                    text="矮行文字",
                    score=0.99,
                    bbox=(100.0, 100.0, 500.0, 101.0),  # 归一化高度 1
                    page_idx=0,
                ),
            ],
        )
        written, skipped = PdfService.add_text_layer(doc, pdf_doc, 0, result)
        assert written == 1
        assert skipped == 0
        extracted = doc[0].get_text()
        assert "矮行文字" in extracted
        doc.close()

    def test_add_text_layer_writes_narrow_bbox(self, tmp_path):
        """窄框（宽度小于字号）也不应被丢弃——文字按行写入（可换行），全部字符可搜索。

        修正后：窄框下 insert_textbox 以最小字号自动换行写入，文字留在 bbox 内，
        不再退化为 insert_text 单点写入横向溢出。文字层可搜索到全部字符（可能
        分多行）。
        """
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        path = tmp_path / "scan_narrow.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        text = "这是一行较长的中文识别结果文本"
        result = OCRResult(
            raw_text=text,
            text_blocks=[
                TextBlock(
                    text=text,
                    score=0.99,
                    # 归一化宽 10（=6.12pt），高 30（=23.76pt）→ 窄框
                    bbox=(100.0, 100.0, 110.0, 130.0),
                    page_idx=0,
                ),
            ],
        )
        written, skipped = PdfService.add_text_layer(doc, pdf_doc, 0, result)
        assert written == 1
        assert skipped == 0
        extracted = doc[0].get_text()
        # 全部字符可搜索（换行后不连续，逐字校验）
        for ch in text:
            assert ch in extracted, f"窄框文字层丢失字符 {ch!r}"
        doc.close()

    def test_add_text_layer_respects_min_font_size_setting(self, tmp_path):
        """PdfGlobalSettings.min_font_size 控制最小字号兜底。"""
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        path = tmp_path / "scan_minfont.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        settings = PdfGlobalSettings(min_font_size=6.0)
        result = OCRResult(
            raw_text="x",
            text_blocks=[
                TextBlock(
                    text="兜底字号",
                    score=0.99,
                    bbox=(100.0, 100.0, 500.0, 101.0),  # 矮框
                    page_idx=0,
                ),
            ],
        )
        written, _ = PdfService.add_text_layer(
            doc, pdf_doc, 0, result, pdf_settings=settings
        )
        assert written == 1
        assert "兜底字号" in doc[0].get_text()
        doc.close()

    def test_add_text_layer_skips_page_with_existing_layer(self, tmp_path):
        """已有文字层的页面，默认 overwrite=False 应跳过，不产生重复文本。"""
        path = tmp_path / "has_layer.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), "原始文字", fontsize=12)
        doc.save(str(path))
        doc.close()

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        doc, pdf_doc = PdfService.open_doc(str(path))
        PdfService.build_page_infos(doc, pdf_doc)
        assert pdf_doc.pages[0].has_text_layer is True
        before = doc[0].get_text()

        result = OCRResult(
            raw_text="新OCR文字",
            text_blocks=[
                TextBlock(
                    text="新OCR文字",
                    score=0.99,
                    bbox=(50.0, 50.0, 300.0, 100.0),
                    page_idx=0,
                ),
            ],
        )
        written, skipped = PdfService.add_text_layer(doc, pdf_doc, 0, result)

        assert written == 0
        assert skipped == 1
        # 文本未变（未叠加）
        assert doc[0].get_text() == before
        doc.close()

    def test_add_text_layer_overwrite_deletes_then_writes(self, tmp_path):
        """overwrite=True 时先删除旧文字层再写入，文本不重复。"""
        path = tmp_path / "overwrite.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), "原始文字", fontsize=12)
        doc.save(str(path))
        doc.close()

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        doc, pdf_doc = PdfService.open_doc(str(path))
        PdfService.build_page_infos(doc, pdf_doc)
        assert pdf_doc.pages[0].has_text_layer is True

        result = OCRResult(
            raw_text="新OCR文字",
            text_blocks=[
                TextBlock(
                    text="新OCR文字",
                    score=0.99,
                    bbox=(50.0, 50.0, 300.0, 100.0),
                    page_idx=0,
                ),
            ],
        )
        written, skipped = PdfService.add_text_layer(
            doc, pdf_doc, 0, result, overwrite=True
        )

        assert written == 1
        assert skipped == 0
        text = doc[0].get_text()
        # 旧文字被删除，只剩新 OCR 文字
        assert "新OCR文字" in text
        assert "原始文字" not in text
        doc.close()

    def test_delete_text_layer(self, opened_doc):
        doc, pdf_doc = opened_doc
        assert pdf_doc.pages[0].has_text_layer is True
        PdfService.delete_text_layers(doc, pdf_doc, 0)
        assert pdf_doc.pages[0].has_text_layer is False
        assert pdf_doc.is_modified is True

    def test_delete_text_layer_preserves_images(self, tmp_path):
        import numpy as np

        path = tmp_path / "mixed.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), "Some text", fontsize=12)
        cs = fitz.Colorspace(fitz.CS_RGB)
        img = np.ones((100, 100, 3), dtype=np.uint8) * 128
        page.insert_image(
            fitz.Rect(72, 200, 172, 300),
            pixmap=fitz.Pixmap(cs, 100, 100, img.tobytes(), 0),
        )
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        PdfService.build_page_infos(doc, pdf_doc)
        assert pdf_doc.pages[0].has_text_layer is True
        PdfService.delete_text_layers(doc, pdf_doc, 0)
        assert pdf_doc.pages[0].has_text_layer is False

        page = doc[0]
        assert len(page.get_images(full=True)) == 1
        doc.close()


class TestPdfServiceTextLayerPlacement:
    """文字层落点与字号回归（Bug：预览框得对，写入 PDF 后部分严重偏离/大小异常）。

    根因：fontsize = rect.height × font_size_ratio(0.8) 恒满足不了 insert_textbox
    的行距预算（rect.height ≥ fontsize × 1.6 才返回 rc≥0 并真正写入）。几乎每个
    块都触发缩字号重试（写入字号偏小）或退化为 insert_text 单点写入（窄/高块
    文字横向大幅溢出到无关区域）。

    修正：fontsize = rect.height / _LINE_LEADING(1.6)，并受宽度约束，使常见宽行
    首次即写入、字号匹配 OCR 行高；窄/高块以最小字号自动换行写入，不溢出。
    """

    def _make_scan(self, tmp_path):
        path = tmp_path / "scan.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = __import__("numpy").ones((792, 612, 3), dtype=__import__("numpy").uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()
        return PdfService.open_doc(str(path))

    def test_wide_line_ink_region_matches_bbox(self, tmp_path):
        """典型宽行（OCR 常见）：文字层 ink 区域高度 ≈ bbox 高度（不再被压到 73%）。

        Bug 症状：insert_textbox 行距开销 1.319×fs 把字号压到 bbox 的 ~73%，
        ink 区域远小于 bbox（『区域太小』）。修复用 insert_text 单点写入，
        fontsize = bbox_height / 0.955，ink 高度匹配 bbox。
        """
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        doc, pdf_doc = self._make_scan(tmp_path)
        # 归一化 bbox：宽 600（=367pt）、高 40（=31.7pt）的典型 OCR 行
        block = TextBlock(text="这是一行示例文字", score=0.99, bbox=(50, 100, 650, 140))
        result = OCRResult(text_blocks=[block])
        settings = PdfGlobalSettings(text_layer_visible=True)
        PdfService.add_text_layer(doc, pdf_doc, 0, result, pdf_settings=settings)

        page_rect = doc[0].rect
        assert block.bbox is not None
        intended = PdfService._denormalize_and_unrotate_bbox(block.bbox, 0, page_rect)
        # 渲染并测量 ink 像素高度（与 OCR bbox 检测的 ink 高度同口径）
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        # 在 bbox 区域 + 右侧余量内找 ink（文本可能横向延伸出 bbox）
        x0 = int(max(0, intended.x0 * 4 - 10))
        x1 = int(min(pix.width, (intended.x1 + 200) * 4))
        y0 = int(max(0, intended.y0 * 4 - 10))
        y1 = int(min(pix.height, intended.y1 * 4 + 10))
        crop = img[y0:y1, x0:x1]
        ink = crop[:, :, 0] < 128
        ys, _xs = np.where(ink)
        assert len(ys) > 0, "宽行应写入可见文字（text_layer_visible=True）"
        ink_h = (ys.max() - ys.min()) / 4
        ink_top = (ys.min() + y0) / 4
        # ink 高度 ≈ bbox 高度（±15%，不再被压到 73%）
        assert 0.85 <= ink_h / intended.height <= 1.15, (
            f"ink 高度 {ink_h:.1f} 应 ≈ bbox 高度 {intended.height:.1f}"
            f"（ratio={ink_h/intended.height:.2f}，旧 bug ~0.73=区域太小）"
        )
        # ink 顶部对齐 bbox 顶部（±2pt）
        assert abs(ink_top - intended.y0) < 2, (
            f"ink 顶部 {ink_top:.1f} 应 ≈ bbox 顶部 {intended.y0:.1f}"
        )
        doc.close()

    def test_narrow_tall_block_no_horizontal_overflow(self, tmp_path):
        """窄/高块：文字不再横向溢出到无关区域（旧 bug 溢出数百 pt）。

        Bug 症状：旧 insert_text 兜底用初始大字号（如 190pt）单点写入，
        文字横向延伸到 rect 右侧数百 pt 外 → '严重偏离'。
        """
        from vibeocr.models.ocr_result import OCRResult, TextBlock

        doc, pdf_doc = self._make_scan(tmp_path)
        # 窄高块：宽 40（=24.5pt）、高 300（=237.6pt）
        block = TextBlock(text="窄高块标签文字", score=0.99, bbox=(50, 400, 90, 700))
        result = OCRResult(text_blocks=[block])
        PdfService.add_text_layer(doc, pdf_doc, 0, result)

        page_rect = doc[0].rect
        assert block.bbox is not None
        intended = PdfService._denormalize_and_unrotate_bbox(block.bbox, 0, page_rect)
        page_text = cast("dict[str, Any]", doc[0].get_text("dict"))
        spans = [
            s for b in page_text["blocks"] if b["type"] == 0
            for line in b.get("lines", []) for s in line.get("spans", [])
        ]
        assert spans, "窄高块应写入（可换行）"
        # 所有 span 的 x1 不得远超 rect 右边界（旧 bug 会到 x≈630）
        max_x1 = max(s["bbox"][2] for s in spans)
        overflow = max_x1 - intended.x1
        assert overflow < intended.width * 0.5, (
            f"窄高块文字横向溢出 {overflow:.1f}pt（应 < rect 宽度的 50%={intended.width*0.5:.1f}），"
            f"max_x1={max_x1:.1f}, rect.x1={intended.x1:.1f}（旧 bug 会溢出到 ~630）"
        )
        doc.close()

    def test_fontsize_matches_ocr_line_height(self, tmp_path):
        """多个不同行高的块：写入字号应与各自行高成正比（不再统一缩到偏小）。"""
        from vibeocr.models.ocr_result import OCRResult, TextBlock

        doc, pdf_doc = self._make_scan(tmp_path)
        # 三个不同行高的行：高 40/60/80（归一化）
        blocks = [
            TextBlock(text="小号行", score=0.99, bbox=(50, 100, 400, 140)),  # h≈31.7pt
            TextBlock(text="中号行文字", score=0.99, bbox=(50, 200, 400, 260)),  # h≈47.5pt
            TextBlock(text="大号行标题文字", score=0.99, bbox=(50, 300, 400, 380)),  # h≈63.4pt
        ]
        result = OCRResult(text_blocks=blocks)
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        PdfService.add_text_layer(
            doc, pdf_doc, 0, result, pdf_settings=PdfGlobalSettings(text_layer_visible=True)
        )

        page_text = cast("dict[str, Any]", doc[0].get_text("dict"))
        spans_by_size = sorted(
            [
                s for b in page_text["blocks"] if b["type"] == 0
                for line in b.get("lines", []) for s in line.get("spans", [])
            ],
            key=lambda s: s["size"],
        )
        assert len(spans_by_size) >= 3
        # 字号应随行高递增（不再被统一缩到接近的偏小值）
        sizes = [s["size"] for s in spans_by_size[:3]]
        # 预期字号比 ≈ 行高比（31.7:47.5:63.4）
        assert sizes[0] < sizes[1] < sizes[2], (
            f"字号应随行高递增，实际 {sizes}（旧 bug 缩字号后差异被压缩）"
        )
        doc.close()

    def test_cropbox_offset_text_layer_lands_on_visible_text(self, tmp_path):
        """CropBox != MediaBox 时文字层必须落在可见文字上（不整体偏移）。

        Bug：page.rect 返回『归零 CropBox』，OCR 渲染图也是 CropBox 区域，但
        insert_textbox 写 MediaBox 空间。此前漏算 CropBox 原点偏移，文字层整体
        偏到 CropBox 原点处（『部分文字层离文字很远』）。

        本测试：在 MediaBox 已知位置画可见标记 → 渲染找标记像素（模拟 OCR 输入）
        → 写文字层 → 断言写入的 MediaBox 坐标覆盖标记（IoU 高）。
        复现矩阵：全 4 旋转 × {无 CropBox, CropBox 偏移}。
        """
        import numpy as np

        from vibeocr.models.ocr_result import TextBlock
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        def _check(rot, cropbox):
            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            # 可见标记矩形（MediaBox 坐标）
            marker_mb = fitz.Rect(200, 300, 260, 330)
            page.draw_rect(marker_mb, color=(1, 0, 0), width=1)
            if cropbox is not None:
                page.set_cropbox(cropbox)
            if rot:
                page.set_rotation(rot)
            # 渲染找标记像素（模拟 OCR 在渲染图上看到的 bbox）
            pix = page.get_pixmap(matrix=fitz.Matrix(1, 1))
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            red = (img[:, :, 0] > 200) & (img[:, :, 1] < 100) & (img[:, :, 2] < 100)
            ys, xs = np.where(red)
            if len(xs) == 0:
                return None  # 标记在裁剪区外
            page_rect = page.rect
            norm_bbox = (
                xs.min() / page_rect.width * 1000,
                ys.min() / page_rect.height * 1000,
                xs.max() / page_rect.width * 1000,
                ys.max() / page_rect.height * 1000,
            )
            # 写文字层（可见模式便于读回）
            blocks = [TextBlock(text="TEST", score=0.95, bbox=norm_bbox)]
            settings = PdfGlobalSettings(text_layer_visible=True)
            PdfService._write_blocks_to_page(doc, 0, blocks, 0, settings)
            page_text = cast("dict[str, Any]", doc[0].get_text("dict"))
            spans = [
                s for b in page_text["blocks"] if b["type"] == 0
                for line in b.get("lines", []) for s in line.get("spans", [])
            ]
            doc.close()
            if not spans:
                return 0.0
            text_rect = fitz.Rect(spans[0]["bbox"])
            overlap = marker_mb & text_rect
            if text_rect.get_area() <= 0:
                return 0.0
            return overlap.get_area() / text_rect.get_area()

        cases = [
            (0, None, "rot0 no-cb"),
            (0, fitz.Rect(50, 50, 562, 742), "rot0 cb50"),
            (90, fitz.Rect(50, 50, 562, 742), "rot90 cb50"),
            (180, fitz.Rect(50, 50, 562, 742), "rot180 cb50"),
            (270, fitz.Rect(50, 50, 562, 742), "rot270 cb50"),
        ]
        for rot, cb, name in cases:
            iou = _check(rot, cb)
            assert iou is not None, f"{name}: 标记应在裁剪区内可见"
            assert iou > 0.3, (
                f"{name}: 文字层应覆盖可见标记，IoU={iou:.2f}"
                f"（旧 bug 在 cb50 下 IoU≈0，文字层偏移到 CropBox 原点）"
            )

    def test_ink_height_matches_bbox_across_line_sizes(self, tmp_path):
        """多个不同行高的块：文字层 ink 高度 ≈ bbox 高度（不再统一只有 73%）。

        Bug 症状：insert_textbox 行距开销把字号压到 bbox 的 ~73%，ink 区域远小于
        bbox（『区域太小，与实际 bbox 框大小差异太大』）。修复用 insert_text 单点
        写入，fontsize = bbox_height / 0.955，ink 高度匹配 bbox。
        """
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        doc, pdf_doc = self._make_scan(tmp_path)
        # 三个不同行高的水平行（宽 > 高，走 insert_text 主路径）
        cases = [
            ("小号行", (50, 100, 400, 140)),  # h≈31.7pt
            ("中号行文字", (50, 250, 400, 310)),  # h≈47.5pt
            ("大号行标题文字", (50, 450, 400, 530)),  # h≈63.4pt
        ]
        text_blocks = [TextBlock(text=t, score=0.99, bbox=b) for t, b in cases]
        result = OCRResult(text_blocks=text_blocks)
        settings = PdfGlobalSettings(text_layer_visible=True)
        PdfService.add_text_layer(doc, pdf_doc, 0, result, pdf_settings=settings)

        page_rect = doc[0].rect
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        for _, nbbox in cases:
            intended = PdfService._denormalize_and_unrotate_bbox(nbbox, 0, page_rect)
            x0 = int(max(0, intended.x0 * 4 - 10))
            x1 = int(min(pix.width, (intended.x1 + 200) * 4))
            y0 = int(max(0, intended.y0 * 4 - 10))
            y1 = int(min(pix.height, intended.y1 * 4 + 10))
            crop = img[y0:y1, x0:x1]
            ink = crop[:, :, 0] < 128
            ys, _xs = np.where(ink)
            assert len(ys) > 0, f"bbox {nbbox} 应有 ink"
            ink_h = (ys.max() - ys.min()) / 4
            ratio = ink_h / intended.height
            # ink 高度 ≈ bbox 高度（±15%），旧 bug 此 ratio ≈ 0.73
            assert 0.85 <= ratio <= 1.15, (
                f"ink 高度 {ink_h:.1f} 应 ≈ bbox 高度 {intended.height:.1f}"
                f"（ratio={ratio:.2f}，旧 bug ~0.73=区域太小）"
            )
        doc.close()

    def test_rotated_page_90_ink_matches_bbox(self, tmp_path):
        """rotation=90 横向页：文字层 ink 高度 ≈ bbox 高度（不再被压到 ~16%）。

        Bug：rotation=90 时 mediabox 矩形宽高互换（300pt 宽 display → 30pt 宽
        mediabox），insert_textbox 宽度约束把字号压到 min_font_size，ink 高度
        只有 bbox 的 ~16%（『区域太小』）。修复：rotation=90 也走 insert_text
        主路径，display 基线经 derotation_matrix 转 mediabox，ink 匹配 bbox。
        """
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.models.pdf_document import PdfDocument
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        doc = fitz.open()
        page = doc.new_page(width=595.2, height=841.68)
        page.set_rotation(90)
        pr = page.rect
        # 显示空间水平行 x=100-400, y=100-130（30pt 高）
        nbbox = (
            100 / pr.width * 1000, 100 / pr.height * 1000,
            400 / pr.width * 1000, 130 / pr.height * 1000,
        )
        block = TextBlock(text="测试文字示例", score=0.99, bbox=nbbox)
        result = OCRResult(text_blocks=[block], preproc_angle=0)
        settings = PdfGlobalSettings(text_layer_visible=True)
        pdf_doc = PdfDocument(file_path="x.pdf")
        PdfService.build_page_infos(doc, pdf_doc)
        PdfService.add_text_layer(doc, pdf_doc, 0, result, pdf_settings=settings)

        # 渲染测量 ink（显示空间 = 渲染图）
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        ink = img[:, :, 0] < 128
        ys, _xs = np.where(ink)
        assert len(ys) > 0, "rotation=90 应写入可见文字"
        ink_h = (ys.max() - ys.min()) / 4
        ink_top = ys.min() / 4
        # ink 高度 ≈ bbox 高度 30pt（±15%），旧 bug 此 ratio ≈ 0.16
        assert 0.85 <= ink_h / 30 <= 1.15, (
            f"rotation=90 ink 高度 {ink_h:.1f} 应 ≈ bbox 高度 30"
            f"（ratio={ink_h/30:.2f}，旧 bug ~0.16=区域太小）"
        )
        assert abs(ink_top - 100) < 3, (
            f"rotation=90 ink 顶部 {ink_top:.1f} 应 ≈ bbox 顶部 100"
        )
        doc.close()

    def test_ink_width_matches_bbox_via_horizontal_scale(self, tmp_path):
        """文字层 ink 宽度 ≈ bbox 宽度（morph 水平缩放匹配，不再只有 45-62%）。

        Bug：CJK 字符宽 ≈ fontsize，OCR bbox 常比自然文本宽，不缩放时 ink 只
        覆盖 bbox 宽度的 45-62%（『宽度还有优化空间』）。修复：morph 水平缩放
        （rot=0 → Matrix(scale_x,1)，rot=90 → Matrix(1,scale_x)）把 ink 拉伸到
        bbox 宽度。隐形层下字形拉伸不可见，选中框覆盖 bbox 才是目标。
        """
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.models.pdf_document import PdfDocument
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        for rot, page_w, page_h, name in [
            (0, 612, 792, "rot0"),
            (90, 595.2, 841.68, "rot90"),
        ]:
            doc = fitz.open()
            page = doc.new_page(width=page_w, height=page_h)
            page.set_rotation(rot)
            pr = page.rect
            # 5 CJK 字符，bbox 宽 300（60pt/字，比自然 ~30pt/字宽一倍），
            # 高 30。旧路径 ink_w ≈ 155（ratio 0.52）。
            x0, y0, x1, y1 = 100, 100, 400, 130
            nbbox = (
                x0 / pr.width * 1000, y0 / pr.height * 1000,
                x1 / pr.width * 1000, y1 / pr.height * 1000,
            )
            block = TextBlock(text="平顶补强块", score=0.99, bbox=nbbox)
            result = OCRResult(text_blocks=[block], preproc_angle=0)
            settings = PdfGlobalSettings(text_layer_visible=True)
            pdf_doc = PdfDocument(file_path="x.pdf")
            PdfService.build_page_infos(doc, pdf_doc)
            PdfService.add_text_layer(doc, pdf_doc, 0, result, pdf_settings=settings)

            pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4))
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            ink = img[:, :, 0] < 128
            ys, xs = np.where(ink)
            assert len(ys) > 0, f"{name}: 应写入可见文字"
            ink_w = (xs.max() - xs.min()) / 4
            # ink 宽度 ≈ bbox 宽度 300（±15%），旧 bug ratio ≈ 0.52
            assert 0.85 <= ink_w / 300 <= 1.15, (
                f"{name} ink 宽度 {ink_w:.1f} 应 ≈ bbox 宽度 300"
                f"（ratio={ink_w/300:.2f}，旧 bug ~0.52=宽度太小）"
            )
            doc.close()

    def test_digit_block_ink_not_overstretched(self, tmp_path):
        """数字块 ink 不溢出 bbox 右边界（位置错位/bbox 偏大根因）。

        Bug：width_units 启发式把数字按 0.5×fs 估算，但子集字体（msyh.ttc）数字
        真实 advance≈0.586×fs。低估 17% 使 natural_w 偏小、scale_x 偏大，morph 把
        数字 ink 横向过度拉伸——ink 右边界越过 bbox 右边界（实测 bbox 宽 400 时 ink
        宽 457，右溢 65pt），数字跑到下一个块/空白区域，表现为『位置错位、bbox 异常』。
        修复：用 fitz.Font.text_length 取子集字体真实 advance width 计算 natural_w。

        取 bbox 宽 400（9 位数字 505710786，fontsize≈31.4，自然宽≈165.6）：
        旧代码 ink fill=1.14（溢出），修复后 ink fill≈0.97（落在 bbox 内）。
        """
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.models.pdf_document import PdfDocument
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        pr = page.rect
        # bbox 宽 400：旧代码 scale_x 未触顶但已使 ink 溢出 bbox（fill=1.14）。
        x0, y0, x1, y1 = 100, 100, 500, 130
        nbbox = (
            x0 / pr.width * 1000, y0 / pr.height * 1000,
            x1 / pr.width * 1000, y1 / pr.height * 1000,
        )
        block = TextBlock(text="505710786", score=0.99, bbox=nbbox)
        result = OCRResult(text_blocks=[block], preproc_angle=0)
        settings = PdfGlobalSettings(text_layer_visible=True)
        pdf_doc = PdfDocument(file_path="x.pdf")
        PdfService.build_page_infos(doc, pdf_doc)
        PdfService.add_text_layer(doc, pdf_doc, 0, result, pdf_settings=settings)

        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        ink = img[:, :, 0] < 128
        ys, xs = np.where(ink)
        assert len(ys) > 0, "应写入可见文字"
        ink_x1 = xs.max() / 4  # ink 右边界（显示空间，未旋转，坐标=mediabox 坐标）
        ink_x0 = xs.min() / 4
        bbox_w = x1 - x0  # 400
        # 关键断言：ink 右边界不得越过 bbox 右边界（旧 bug 溢出 ~65pt）。
        # 允许 5pt 容差（抗锯齿边缘）。
        assert ink_x1 <= x1 + 5, (
            f"数字 ink 右边界 {ink_x1:.1f} 溢出 bbox 右边界 {x1}"
            f"（溢出 {ink_x1-x1:.1f}pt，旧 bug 因数字宽度被低估而过度拉伸）"
        )
        # ink 宽度也应与 bbox 宽度同量级（修复后 fill≈0.97，旧 bug fill=1.14）。
        ink_w = ink_x1 - ink_x0
        assert ink_w / bbox_w <= 1.05, (
            f"数字块 ink 宽度 {ink_w:.1f} 不应超过 bbox 宽度 {bbox_w}"
            f"（ratio={ink_w/bbox_w:.2f}，旧 bug=1.14 因数字低估→scale_x 偏大）"
        )
        doc.close()

    def test_digit_block_no_clamp_overflow_on_narrow_bbox(self, tmp_path):
        """窄 bbox 数字块 scale_x 不触顶 3.0（旧 bug：数字低估→scale_x 恒触顶→严重错位）。

        构造一个 bbox 比数字自然宽窄的场景：旧启发式 natural_w 偏小使 scale_x 计算值
        远大于 1，经 [0.5,3.0] 夹紧后恒为 3.0，ink 被拉到 bbox 的 ~3 倍宽——选中框
        覆盖到无关区域。修复后用真实 advance width，scale_x 合理，ink 不溢出。
        """
        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.models.pdf_document import PdfDocument
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings
        from vibeocr.utils.cjk_font_resolver import _CJK_RESOLVER

        # 直接验证 _natural_width 逻辑：真实 advance vs 旧启发式
        chars = "505710786"
        fp = _CJK_RESOLVER.resolve(chars)
        assert fp is not None, "需系统 CJK 字体才能测真实字形宽度"
        font = fitz.Font(fontfile=fp)
        fs = 31.4  # 与上例相当的字号
        true_w = font.text_length(chars, fontsize=fs)  # type: ignore[arg-type]
        heuristic_w = len(chars) * 0.5 * fs
        # 数字真实宽度比启发式大 ~17%
        assert true_w > heuristic_w * 1.10, (
            f"数字真实宽度 {true_w:.1f} 应明显大于启发式 {heuristic_w:.1f}"
            f"（旧 bug 根因：低估数字宽度）"
        )

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        pr = page.rect
        # bbox 宽 = 数字真实宽度（让 scale_x ≈ 1.0 才正确）
        bbox_w_pt = true_w  # 点
        x0, y0, x1, y1 = 100, 100, 100 + bbox_w_pt, 130
        nbbox = (
            x0 / pr.width * 1000, y0 / pr.height * 1000,
            x1 / pr.width * 1000, y1 / pr.height * 1000,
        )
        block = TextBlock(text=chars, score=0.99, bbox=nbbox)
        result = OCRResult(text_blocks=[block], preproc_angle=0)
        settings = PdfGlobalSettings(text_layer_visible=True)
        pdf_doc = PdfDocument(file_path="x.pdf")
        PdfService.build_page_infos(doc, pdf_doc)
        PdfService.add_text_layer(doc, pdf_doc, 0, result, pdf_settings=settings)

        # ink 覆盖度：修复后 ink 宽 ≈ bbox 宽（scale_x≈1.0），旧 bug 会触顶 3.0 溢出
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4))
        import numpy as np
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        ink = img[:, :, 0] < 128
        _ys, xs = np.where(ink)
        ink_w = (xs.max() - xs.min()) / 4
        # 当 bbox 恰为真实宽度时，ink 不应超过 bbox 宽度的 1.2 倍
        assert ink_w / bbox_w_pt <= 1.2, (
            f"数字块 ink 宽度 {ink_w:.1f} 不应远超 bbox 宽度 {bbox_w_pt:.1f}"
            f"（ratio={ink_w/bbox_w_pt:.2f}，旧 bug 因 scale_x 触顶 3.0 严重溢出）"
        )
        doc.close()

    @pytest.mark.parametrize(
        "text, bbox_w, label",
        [
            # 含 @（实测真实宽 1.03×fs，旧启发式 0.5 低估 51%）
            ("test@email.com", 280, "email(@)"),
            # % & 真实宽 0.89/0.87×fs，旧启发式低估 → ink 溢出
            ("100%&key", 220, "percent+amp"),
            # < = > 真实宽 0.74×fs，旧启发式低估 → ink 溢出
            ("a<=b>=c", 200, "compare ops"),
            # CJK 宽度引号 U+2018/2019（1.0×fs）、破折号 U+2014（1.08×fs）、
            # 省略号 U+2026（0.81×fs）都不在旧硬编码 CJK 范围（0x2E80–0x9FFF 等）内，
            # 被误判为 0.5 → 低估 → ink 严重溢出（旧实测溢出 79pt）
            ("“引号”—破折号…省略", 380, "cjk-width-symbols"),
        ],
    )
    def test_symbol_block_ink_not_overstretched(self, tmp_path, text, bbox_w, label):
        """符号/标点块 ink 不溢出 bbox（举一反三：数字之外的字符同样被低估）。

        Bug：width_units 启发式按字符 Unicode 范围二分（CJK=1.0/其余=0.5），
        但子集字体中各类符号真实 advance 与 0.5 差距极大：
          ASCII 标点：. , ; : = 0.24×fs（高估 108%→填不满）；
                      @ = 1.03×fs（低估 51%→溢出）；% = 0.89，& = 0.87；
                      < = > + = ~ ^ = 0.74；()[]{} = 0.33
          CJK 宽度但落在硬编码范围外的符号：U+2018/2019 引号 = 1.0（误判 0.5→溢出）、
                      U+2014 破折号 = 1.08、U+2026 省略号 = 0.81
        这些字符的真实宽度与 0.5 偏差大，scale_x 偏大 → morph 过度拉伸 → ink 溢出
        bbox 右边界（位置错位/bbox 异常）。修复用 fitz.Font.text_length 取真实
        advance width，与字符类别无关，对所有字符一视同仁。
        """
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.models.pdf_document import PdfDocument
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        pr = page.rect
        x0, y0, x1, y1 = 100, 100, 100 + bbox_w, 130
        nbbox = (
            x0 / pr.width * 1000, y0 / pr.height * 1000,
            x1 / pr.width * 1000, y1 / pr.height * 1000,
        )
        block = TextBlock(text=text, score=0.99, bbox=nbbox)
        result = OCRResult(text_blocks=[block], preproc_angle=0)
        settings = PdfGlobalSettings(text_layer_visible=True)
        pdf_doc = PdfDocument(file_path="x.pdf")
        PdfService.build_page_infos(doc, pdf_doc)
        PdfService.add_text_layer(doc, pdf_doc, 0, result, pdf_settings=settings)

        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        ink = img[:, :, 0] < 128
        ys, xs = np.where(ink)
        assert len(ys) > 0, f"{label}: 应写入可见文字"
        ink_x1 = xs.max() / 4
        ink_x0 = xs.min() / 4
        ink_w = ink_x1 - ink_x0
        # ink 右边界不得越过 bbox 右边界（5pt 容差为抗锯齿边缘）。
        # 旧 bug 各用例溢出 +25 ~ +79pt，修复后 overflow ≤ 0。
        assert ink_x1 <= x1 + 5, (
            f"{label}: 符号 ink 右边界 {ink_x1:.1f} 溢出 bbox 右边界 {x1}"
            f"（溢出 {ink_x1-x1:.1f}pt，旧 bug 因符号宽度被低估→scale_x 偏大→morph 过度拉伸）"
        )
        # fill 应落在 bbox 内（修复后 0.94–0.99，旧 bug 最高 1.30）。
        assert ink_w / bbox_w <= 1.05, (
            f"{label}: 符号块 ink 宽度 {ink_w:.1f} 不应超过 bbox 宽度 {bbox_w}"
            f"（ratio={ink_w/bbox_w:.2f}，旧 bug 因符号低估→scale_x 偏大）"
        )
        doc.close()


class TestPdfServiceOcrBlocksCache:
    """OCR 原始块缓存（ocr_text_blocks）—— 预览/编辑/重写的唯一信源。

    核心问题：add_text_layer 写入后曾用 detect_text_layers 重读，
    导致 PyMuPDF 把 OCR 的细粒度块合并成粗块。现改为缓存 OCR 原始块。
    """

    def test_add_text_layer_preserves_ocr_blocks(self, tmp_path):
        """写入后 PdfPageInfo.ocr_text_blocks 等于 OCR 原始块（不被合并）。"""
        path = _create_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))

        ocr_blocks = [
            ("供应商：徐州中车", (50.0, 50.0, 300.0, 100.0)),
            ("客户：苏州中车", (50.0, 150.0, 300.0, 200.0)),
        ]
        result = _make_ocr_result(*ocr_blocks)
        written, _ = PdfService.add_text_layer(doc, pdf_doc, 0, result)

        assert written == 2
        info = pdf_doc.pages[0]
        assert info.has_text_layer is True
        # 关键：缓存的块数 == OCR 原始块数（2），不是 PyMuPDF 重读后可能的合并数
        assert len(info.ocr_text_blocks) == 2
        assert info.ocr_text_blocks[0].text == "供应商：徐州中车"
        assert info.ocr_text_blocks[1].text == "客户：苏州中车"
        doc.close()

    def test_add_text_layer_preserves_preproc_angle(self, tmp_path):
        """OCR 预处理旋转角度随块缓存，重写时坐标逆旋转才不会错位。"""
        path = _create_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))

        result = _make_ocr_result(("Hello", (400.0, 100.0, 600.0, 350.0)), angle=90)
        PdfService.add_text_layer(doc, pdf_doc, 0, result)

        assert pdf_doc.pages[0].ocr_preproc_angle == 90
        doc.close()

    def test_add_text_layer_ocr_blocks_survive_pymupdf_merge(self, tmp_path):
        """即使 PyMuPDF get_text 把多块合并，ocr_text_blocks 仍是细粒度。

        这是用户报告问题的根因：detect_text_layers 重读会合并块，
        导致预览显示合并后的粗块。ocr_text_blocks 必须保持原始细粒度。
        """
        path = _create_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))

        # 两行紧挨的文字（PyMuPDF 可能合并为一个 block）
        result = _make_ocr_result(
            ("第一行", (100.0, 100.0, 500.0, 130.0)),
            ("第二行", (100.0, 135.0, 500.0, 165.0)),
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)

        info = pdf_doc.pages[0]
        # OCR 原始块仍是 2 个（细粒度）
        assert len(info.ocr_text_blocks) == 2
        # detect_text_layers 重读可能合并成 1 个（粗粒度）—— 这正是问题
        # 但 ocr_text_blocks 不受影响
        doc.close()


class TestPdfServiceRewriteTextLayer:
    """rewrite_text_layer —— 保存时按编辑后的块全量重写整页文字层。"""

    def test_rewrite_after_edit_updates_text(self, tmp_path):
        """写入 → 编辑某块 → rewrite → PDF 文字层包含编辑后文字。"""
        path = _create_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))

        result = _make_ocr_result(
            ("签回联", (50.0, 50.0, 200.0, 120.0)),
            ("505710786", (50.0, 150.0, 700.0, 220.0)),  # 宽框容纳长数字
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        assert "签回联" in doc[0].get_text()

        # 模拟双击改字：把 "签回联" 改成 "签收联"
        info = pdf_doc.pages[0]
        info.ocr_text_blocks[0].text = "签收联"
        info.ocr_text_blocks[0].is_manually_edited = True

        # rewrite：删除旧文字层，用编辑后的块重写
        written, _ = PdfService.rewrite_text_layer(
            doc,
            pdf_doc,
            0,
            info.ocr_text_blocks,
            info.ocr_preproc_angle,
        )
        assert written == 2
        # 旧文字消失，新文字出现
        assert "签回联" not in doc[0].get_text()
        assert "签收联" in doc[0].get_text()
        assert "505710786" in doc[0].get_text()
        doc.close()

    def test_rewrite_preserves_block_count(self, tmp_path):
        """rewrite 后 ocr_text_blocks 不变（仍是细粒度）。"""
        path = _create_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))

        result = _make_ocr_result(
            ("A", (100.0, 100.0, 200.0, 150.0)),
            ("B", (100.0, 200.0, 200.0, 250.0)),
            ("C", (100.0, 300.0, 200.0, 350.0)),
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        info = pdf_doc.pages[0]
        blocks_before = list(info.ocr_text_blocks)

        PdfService.rewrite_text_layer(
            doc,
            pdf_doc,
            0,
            info.ocr_text_blocks,
            info.ocr_preproc_angle,
        )
        # rewrite 后块缓存仍完整
        assert len(info.ocr_text_blocks) == len(blocks_before)
        doc.close()

    def test_rewrite_with_rotation_angle(self, tmp_path):
        """带 preproc_angle 的 rewrite 坐标正确（不超出页面边界）。"""
        path = _create_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))

        result = _make_ocr_result(
            ("Rotated", (400.0, 100.0, 600.0, 350.0)),
            angle=90,
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        info = pdf_doc.pages[0]

        info.ocr_text_blocks[0].text = "Rotated!"
        PdfService.rewrite_text_layer(
            doc,
            pdf_doc,
            0,
            info.ocr_text_blocks,
            info.ocr_preproc_angle,
        )

        # 重写后文字仍在页面内
        page_rect = doc[0].rect
        for layer in PdfService.detect_text_layers(doc, 0):
            lr = fitz.Rect(layer.bbox)
            assert lr.x0 >= -1
            assert lr.y0 >= -1
            assert lr.x1 <= page_rect.width + 1
            assert lr.y1 <= page_rect.height + 1
        doc.close()


class TestPdfServiceDeleteClearsOcrBlocks:
    """删除文字层后必须清空 ocr_text_blocks 缓存。"""

    def test_delete_clears_ocr_blocks(self, tmp_path):
        path = _create_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))

        result = _make_ocr_result(("Hello", (100.0, 100.0, 300.0, 150.0)))
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        assert len(pdf_doc.pages[0].ocr_text_blocks) == 1

        PdfService.delete_text_layers(doc, pdf_doc, 0)
        info = pdf_doc.pages[0]
        assert info.has_text_layer is False
        assert info.ocr_text_blocks == []
        assert info.ocr_preproc_angle == 0
        doc.close()


class TestOpenDocNoRotationRead:
    def test_placeholder_pages_have_zero_rotation(self, tmp_path):
        """open_doc 创建的占位页 rotation=0，不读 doc[i].rotation。"""
        path = tmp_path / "rot.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), "text", fontsize=12)
        page.set_rotation(90)  # 真实 rotation=90
        doc.save(str(path))
        doc.close()

        opened_doc, pdf_doc = PdfService.open_doc(str(path))
        # 占位页 rotation 应为 0（不读真实值），由 LoadWorker 后台覆盖
        assert pdf_doc.pages[0].rotation == 0
        assert opened_doc[0].rotation == 90  # fitz 侧真实值不变
        opened_doc.close()

    def test_placeholder_page_count_matches(self, tmp_path):
        path = tmp_path / "multi.pdf"
        _create_test_pdf(path, num_pages=5)
        opened_doc, pdf_doc = PdfService.open_doc(str(path))
        assert len(pdf_doc.pages) == 5
        opened_doc.close()
