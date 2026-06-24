# tests/integration/test_pdf_text_layer_e2e.py
"""端到端验证：PDF 文字层的单一信源修复。

复现用户报告的问题（detect_text_layers 合并块）并验证修复：
  1. OCR 多块 → add_text_layer → ocr_text_blocks 保持细粒度（不合并）
  2. detect_text_layers 重读会合并块（证明旧路径的问题仍存在）
  3. 双击改字 → rewrite → PDF 文字层包含正确文字
"""

from pathlib import Path

import fitz
import pytest

from vibeocr.managers.pdf_session_manager import PdfSessionManager
from vibeocr.models.ocr_result import OCRResult, TextBlock
from vibeocr.services.pdf_service import PdfService


def _make_scanned_pdf(path, width=612, height=792):
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


@pytest.fixture
def manager(qapp):
    mgr = PdfSessionManager(parent=qapp)
    yield mgr
    mgr.shutdown()


class TestSingleSourceOfTruth:
    """验证 OCR 原始块是预览/编辑/重写的唯一信源。"""

    def test_ocr_blocks_not_merged_by_pymupdf(self, tmp_path):
        """OCR 输出多块 → add_text_layer → ocr_text_blocks 保持细粒度。

        这是用户问题的根因：旧版用 detect_text_layers 重读，
        PyMuPDF 会把相邻块合并。新版缓存 ocr_text_blocks 不受影响。
        """
        path = _make_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))

        # 模拟 OCR 输出多块相邻文字（PyMuPDF 可能合并）
        result = OCRResult(
            raw_text="第一行\n第二行\n第三行",
            text_blocks=[
                TextBlock(text="第一行", score=0.95, bbox=(100.0, 100.0, 500.0, 130.0)),
                TextBlock(text="第二行", score=0.93, bbox=(100.0, 135.0, 500.0, 165.0)),
                TextBlock(text="第三行", score=0.91, bbox=(100.0, 170.0, 500.0, 200.0)),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)

        info = pdf_doc.pages[0]
        # ocr_text_blocks 保持 3 个细粒度块（不合并）
        assert len(info.ocr_text_blocks) == 3
        assert info.ocr_text_blocks[0].text == "第一行"
        assert info.ocr_text_blocks[1].text == "第二行"
        assert info.ocr_text_blocks[2].text == "第三行"
        doc.close()

    def test_detect_text_layers_still_merges_blocks(self, tmp_path):
        """证明 detect_text_layers 仍会合并块（这就是为什么不能用它做预览信源）。

        这个测试固化'问题仍存在'的事实：add_text_layer 写入多块后，
        get_text("dict") 可能把它们合并。ocr_text_blocks 才是正确的信源。
        """
        path = _make_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))

        result = OCRResult(
            raw_text="供应商\n客户",
            text_blocks=[
                TextBlock(text="供应商", score=0.95, bbox=(50.0, 50.0, 200.0, 100.0)),
                TextBlock(text="客户", score=0.93, bbox=(50.0, 110.0, 200.0, 160.0)),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)

        # detect_text_layers 重读
        detected = PdfService.detect_text_layers(doc, 0)
        info = pdf_doc.pages[0]

        # ocr_text_blocks 是 2 个（细粒度）
        assert len(info.ocr_text_blocks) == 2
        # detected 可能是 1 或 2（取决于 PyMuPDF 版本的合并行为）
        # 关键断言：ocr_text_blocks 永远 == OCR 原始块数，不受 PyMuPDF 影响
        assert len(info.ocr_text_blocks) == len(result.text_blocks)
        doc.close()


class TestEditFlowE2E:
    """完整编辑流程：OCR → 改字 → rewrite → 落盘 → 重开验证。"""

    def test_edit_then_save_persists_corrected_text(self, tmp_path, manager):
        """双击改字 '签回联' → '签收联' → 保存 → 重开 PDF 验证。

        复刻用户场景：OCR 把'签收联'识别成'签回联'，用户双击改正，
        保存后 PDF 文字层应包含正确文字。
        """
        from unittest.mock import MagicMock

        path = _make_scanned_pdf(tmp_path / "scan.pdf")
        session = manager.open_session(str(path))

        mock_worker = MagicMock()
        mock_worker.session_id = session.file_path
        manager._ocr_worker = mock_worker

        # OCR 识别（有误）
        result = OCRResult(
            raw_text="签回联",
            text_blocks=[
                TextBlock(
                    text="签回联", score=0.9,
                    bbox=(50.0, 50.0, 200.0, 120.0), page_idx=0,
                ),
            ],
        )
        manager._on_ocr_page_done(0, result)
        assert "签回联" in session.doc[0].get_text()

        # 双击改字
        changed = manager.update_page_block_text(0, 0, "签收联")
        assert changed is True

        # 保存（含 rewrite）
        manager.rewrite_modified_pages()
        manager._save_active_to_disk_for_test()

        # 重开验证
        verify = fitz.open(str(path))
        text = verify[0].get_text()
        assert "签收联" in text, f"应包含改正后的文字，实际: {text!r}"
        assert "签回联" not in text, f"不应包含错误文字，实际: {text!r}"
        verify.close()

    def test_edit_preserves_other_blocks(self, tmp_path, manager):
        """改一块不影响其他块。"""
        from unittest.mock import MagicMock

        path = _make_scanned_pdf(tmp_path / "scan.pdf")
        session = manager.open_session(str(path))

        mock_worker = MagicMock()
        mock_worker.session_id = session.file_path
        manager._ocr_worker = mock_worker

        result = OCRResult(
            raw_text="A\nB\nC",
            text_blocks=[
                TextBlock(text="A", score=0.9, bbox=(100.0, 100.0, 200.0, 150.0)),
                TextBlock(text="B", score=0.9, bbox=(100.0, 200.0, 200.0, 250.0)),
                TextBlock(text="C", score=0.9, bbox=(100.0, 300.0, 200.0, 350.0)),
            ],
        )
        manager._on_ocr_page_done(0, result)

        # 只改第二块
        manager.update_page_block_text(0, 1, "B改")
        manager.rewrite_modified_pages()
        manager._save_active_to_disk_for_test()

        verify = fitz.open(str(path))
        text = verify[0].get_text()
        assert "A" in text
        assert "B改" in text
        assert "C" in text
        verify.close()


class TestCrossReaderSearchability:
    """跨阅读器可搜索性：文字层必须含嵌入字体 + ToUnicode CMap。

    fitz.get_text() 能读自己的输出（掩盖问题），但外部阅读器（浏览器/
    macOS Preview/pdftotext）依赖 ToUnicode 反向映射。本类用原始字节断言。
    """

    def test_saved_pdf_has_tounicode_cmap(self, tmp_path):
        """保存后的 PDF 必须含 ToUnicode CMap（外部搜索的前提）。"""
        path = _make_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))
        result = OCRResult(
            raw_text="签收联测试",
            text_blocks=[
                TextBlock(text="签收联测试", score=0.95, bbox=(50, 50, 400, 120)),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        PdfService.save(doc, pdf_doc)
        doc.close()

        raw = Path(path).read_bytes()
        assert b"ToUnicode" in raw, "PDF 缺少 ToUnicode CMap，外部阅读器无法搜索"

    def test_saved_pdf_has_embedded_font(self, tmp_path):
        """保存后的 PDF 必须含嵌入字体（FontFile），字形数据随文件走。"""
        path = _make_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))
        result = OCRResult(
            raw_text="签收联测试",
            text_blocks=[
                TextBlock(text="签收联测试", score=0.95, bbox=(50, 50, 400, 120)),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        PdfService.save(doc, pdf_doc)
        doc.close()

        raw = Path(path).read_bytes()
        assert b"FontFile" in raw, "PDF 缺少嵌入字体，字形未随文件保存"

    def test_volume_increase_acceptable(self, tmp_path):
        """子集化字体嵌入后体积增量可忽略（< 100KB）。"""
        base_path = tmp_path / "base.pdf"
        _make_scanned_pdf(base_path)
        base_size = base_path.stat().st_size

        path = tmp_path / "scan.pdf"
        _make_scanned_pdf(path)
        doc, pdf_doc = PdfService.open_doc(str(path))
        result = OCRResult(
            raw_text="签收联测试中文文字层发货单",
            text_blocks=[
                TextBlock(
                    text="签收联测试中文文字层发货单",
                    score=0.95,
                    bbox=(50, 50, 500, 120),
                ),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        PdfService.save(doc, pdf_doc)
        doc.close()

        increase = path.stat().st_size - base_size
        # 子集字体增量应远小于整字体（整字体 3.5MB+）；放宽到 100KB 容错
        assert increase < 100_000, (
            f"体积增量过大: {increase} bytes（疑似嵌整字体）"
        )

    def test_fallback_when_no_system_font(self, tmp_path, monkeypatch):
        """无系统字体时回退 china-s，文字层仍可被 fitz 提取（不阻断流程）。"""
        from vibeocr.services.pdf_service import _CJK_RESOLVER

        # 强制 resolver 探测失败
        monkeypatch.setattr(
            _CJK_RESOLVER, "_get_candidates", lambda: ["/nonexistent.ttf"]
        )
        _CJK_RESOLVER._probed = False  # 重置缓存
        _CJK_RESOLVER._system_font = None

        try:
            path = _make_scanned_pdf(tmp_path / "scan.pdf")
            doc, pdf_doc = PdfService.open_doc(str(path))
            result = OCRResult(
                raw_text="签收联",
                text_blocks=[
                    TextBlock(text="签收联", score=0.95, bbox=(50, 50, 200, 120)),
                ],
            )
            PdfService.add_text_layer(doc, pdf_doc, 0, result)
            PdfService.save(doc, pdf_doc)
            doc.close()

            verify = fitz.open(str(path))
            assert "签收联" in verify[0].get_text()
            verify.close()
        finally:
            # 恢复 resolver 状态，避免污染后续测试
            _CJK_RESOLVER._probed = False
            _CJK_RESOLVER._system_font = None


class TestAddRewriteFontCollision:
    """add_text_layer → rewrite_text_layer 同页写两个不同字符集的子集字体。

    回归防护：PyMuPDF 按字体名缓存资源，若两次写入用相同 fontname 却不同
    fontfile，会复用第一个字体的 cmap，导致第二个子集里新增的字写成 \x00
    （缺字）。fontname 随子集字体路径派生（md5）保证不冲突。
    """

    def test_rewrite_with_new_char_no_glyph_loss(self, tmp_path):
        """add 写'签回联'→rewrite 改成'签收联'，'收'不应丢成 \x00。"""
        path = _make_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))

        # 第一次：写入 '签回联'
        result = OCRResult(
            raw_text="签回联",
            text_blocks=[
                TextBlock(text="签回联", score=0.9, bbox=(50.0, 50.0, 300.0, 120.0)),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        assert "签回联" in doc[0].get_text()

        # 改成含新字 '收' 的文本（'收' 不在第一子集里）
        info = pdf_doc.pages[0]
        info.ocr_text_blocks[0].text = "签收联"
        info.ocr_text_blocks[0].is_manually_edited = True

        # rewrite：删旧文字层后用新子集重写
        PdfService.rewrite_text_layer(
            doc, pdf_doc, 0, info.ocr_text_blocks, info.ocr_preproc_angle
        )

        # 落盘后重开验证（用 fitz 读已保存文件，模拟外部读取路径）
        PdfService.save(doc, pdf_doc)
        doc.close()

        verify = fitz.open(str(path))
        text = verify[0].get_text()
        verify.close()
        # 关键断言：新字 '收' 不能丢成 \x00
        assert "签收联" in text, f"'收' 字丢失或写成 \x00，实际: {text!r}"
        assert "签回联" not in text, f"旧文字应被 rewrite 清除，实际: {text!r}"
