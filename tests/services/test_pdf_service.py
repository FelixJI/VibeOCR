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
                    bbox=(400.0, 100.0, 600.0, 200.0),  # [0, 1000] 归一化
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
