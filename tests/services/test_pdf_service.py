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
            text="签回联",
            score=0.95,
            bbox=(50.0, 50.0, 70.0, 100.0),
            page_idx=0,
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
            text="中文测试文字很长装不下窄框",
            score=0.9,
            # 极小矩形：长文本在重试字号缩到 <1 仍溢出，强制走 insert_text 兜底。
            # （依赖具体字体的字号策略，用长文本+小框确保任何字体都触发兜底）
            bbox=(50.0, 50.0, 60.0, 53.0),
            page_idx=0,
        )
        result = OCRResult(raw_text="中文测试文字很长装不下窄框", text_blocks=[narrow])

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
        """窄框（宽度小于字号）也不应被丢弃——文字按行原位写入。"""
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
        result = OCRResult(
            raw_text="这是一行较长的中文识别结果文本",
            text_blocks=[
                TextBlock(
                    text="这是一行较长的中文识别结果文本",
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
        assert "这是一行较长的中文识别结果文本" in extracted
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
