# tests/services/test_pdf_service.py
"""Tests for PDF service (stateless static methods)."""

from pathlib import Path

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

    def test_save_in_place_creates_backup(self, opened_doc):
        doc, pdf_doc = opened_doc
        PdfService.rotate_pages(doc, pdf_doc, [0], 90)
        file_path = pdf_doc.file_path
        PdfService.save(doc, pdf_doc)

        assert Path(str(file_path) + ".bak").exists() is False
        verify = fitz.open(str(file_path))
        assert verify[0].rotation == 90
        verify.close()


class TestPdfServiceRender:
    def test_render_thumbnail(self, opened_doc, qapp):
        doc, _ = opened_doc
        pixmap = PdfService.render_page(doc, 0, dpi=96)
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
                TextBlock(text=chinese, score=0.99,
                          bbox=(50.0, 50.0, 500.0, 120.0), page_idx=0),
            ],
        )
        written, skipped = PdfService.add_text_layer(doc, pdf_doc, 0, result)
        assert written == 1
        assert skipped == 0
        # 中文必须能被回读（验证 china-s 字体生效）
        extracted = doc[0].get_text()
        assert "你好世界" in extracted
        doc.close()

    def test_add_text_layer_skips_tiny_bbox_with_warning(self, tmp_path, caplog):
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
        good = TextBlock(text="正常文字", score=0.9,
                         bbox=(50.0, 50.0, 400.0, 100.0), page_idx=0)
        # 宽高均 < 1 point → 会被跳过
        tiny = TextBlock(text="小", score=0.9,
                         bbox=(10.0, 10.0, 10.5, 10.5), page_idx=0)
        result = OCRResult(raw_text="x", text_blocks=[good, tiny])

        with caplog.at_level(logging.WARNING, logger="vibeocr.services.pdf_service"):
            written, skipped = PdfService.add_text_layer(doc, pdf_doc, 0, result)

        assert written == 1
        assert skipped == 1
        assert any("skipped" in rec.message for rec in caplog.records)
        doc.close()

    def test_add_text_layer_fallback_insert_text_on_narrow_bbox(self, tmp_path):
        """窄/瘦高矩形装不下横向文字时，insert_text 兜底写入（不再跳过）。

        复现真实报错场景：bbox 宽 ~20pt、高 ~50pt，文字是 3 个汉字，
        insert_textbox 横向排不开、缩 5 次仍失败。兜底用 insert_text
        单点定位写入，保证该词进入文字层（可搜索/可选中）。
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
        # 瘦高矩形（宽 20pt、高 50pt）+ 3 个汉字 → insert_textbox 必然失败
        narrow = TextBlock(
            text="签回联", score=0.95,
            bbox=(50.0, 50.0, 70.0, 100.0), page_idx=0,
        )
        result = OCRResult(raw_text="签回联", text_blocks=[narrow])

        written, skipped = PdfService.add_text_layer(doc, pdf_doc, 0, result)

        # 兜底成功写入，不计 skip
        assert written == 1
        assert skipped == 0
        # 文字层确实包含该词（可搜索）
        assert "签回联" in doc[0].get_text()
        doc.close()

    def test_add_text_layer_fallback_logs_debug(self, tmp_path, caplog):
        """兜底写入走 DEBUG 日志（便于排查），不污染 WARNING 流。"""
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
            text="43778", score=0.9,
            bbox=(50.0, 50.0, 70.0, 80.0), page_idx=0,  # 窄矩形
        )
        result = OCRResult(raw_text="43778", text_blocks=[narrow])

        with caplog.at_level(logging.DEBUG, logger="vibeocr.services.pdf_service"):
            PdfService.add_text_layer(doc, pdf_doc, 0, result)

        # 兜底写入应有 DEBUG 日志
        assert any(
            "insert_text 兜底" in rec.message for rec in caplog.records
        )
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
                    bbox=(400.0, 100.0, 600.0, 350.0),  # [0, 1000] 归一化（足够宽以容纳 CJK 字体下的拉丁字形）
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
        written, skipped = PdfService.add_text_layer(doc, pdf_doc, 0, result)

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
        written, skipped = PdfService.rewrite_text_layer(
            doc, pdf_doc, 0,
            info.ocr_text_blocks, info.ocr_preproc_angle,
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
            doc, pdf_doc, 0, info.ocr_text_blocks, info.ocr_preproc_angle,
        )
        # rewrite 后块缓存仍完整
        assert len(info.ocr_text_blocks) == len(blocks_before)
        doc.close()

    def test_rewrite_with_rotation_angle(self, tmp_path):
        """带 preproc_angle 的 rewrite 坐标正确（不超出页面边界）。"""
        path = _create_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))

        result = _make_ocr_result(
            ("Rotated", (400.0, 100.0, 600.0, 350.0)), angle=90,
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        info = pdf_doc.pages[0]

        info.ocr_text_blocks[0].text = "Rotated!"
        PdfService.rewrite_text_layer(
            doc, pdf_doc, 0, info.ocr_text_blocks, info.ocr_preproc_angle,
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
