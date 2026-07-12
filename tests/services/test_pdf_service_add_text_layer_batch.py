"""测试 PdfService.add_text_layer_batch 的聚合子集字体行为。

回归任务4：OCR 写层从逐页 add_text_layer（每页独立子集）改为批量
add_text_layer_batch（一批页共享单一聚合子集），复用 save_with_rewrite
已验证的整文档子集模式，避免每页一份字体对象放大体积。
"""

from __future__ import annotations

import fitz
import pytest

from vibeocr.models.ocr_result import OCRResult, TextBlock
from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.services.pdf_service import PdfService


def _ocr_result_to_dict(text: str) -> dict:
    """构造序列化的 OCRResult dict（模拟主进程经 JSON 传给后端的数据）。

    后端 add_text_layer_batch 从 dict 反序列化 text_blocks（b["text"] 等），
    故 text_blocks 必须是纯 dict 列表，而非 TextBlock 对象。
    """
    result = OCRResult(
        raw_text=text,
        text_blocks=[TextBlock(text=text, score=0.9, bbox=(50, 50, 300, 100))],
    )
    return {
        "raw_text": result.raw_text,
        "text_blocks": [
            {
                "text": b.text,
                "score": b.score,
                "bbox": list(b.bbox) if b.bbox else None,
                "page_idx": b.page_idx,
                "is_manually_edited": b.is_manually_edited,
                "label": b.label,
                "order": b.order,
            }
            for b in result.text_blocks
        ],
        "preproc_angle": getattr(result, "preproc_angle", 0),
    }


@pytest.fixture
def scan_pdf(tmp_path):
    """两页扫描 PDF（每页一张图片，无文字层）。"""
    import numpy as np

    path = tmp_path / "scan.pdf"
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
    doc.save(str(path))
    doc.close()
    return path


def _count_embedded_subset_fonts(doc: fitz.Document) -> set[int]:
    """收集全文档嵌入的 TrueType 子集字体 xref（排除内置 CID 字体 china-s）。"""
    embedded = set()
    for i in range(doc.page_count):
        for f in doc.get_page_fonts(i, full=True):
            ftype = f[2] if len(f) > 2 else ""
            ext = f[1] if len(f) > 1 else ""
            if ext in ("ttf", "otf", "n/a") and "Type" in str(ftype) and "CID" not in str(ftype):
                embedded.add(f[0])
    return embedded


class TestAddTextLayerBatchSharesSubsetFont:
    """批量写层应让一批页共享单一聚合子集字体。"""

    def test_batch_shares_single_subset_font(self, scan_pdf, tmp_path):
        """两页不同字符经 add_text_layer_batch 写层后，嵌入子集字体应 ≤ 1。"""
        doc = fitz.open(str(scan_pdf))
        pdf_doc = PdfDocument(file_path=str(scan_pdf))
        pdf_doc.pages = [PdfPageInfo(page_index=i) for i in range(2)]

        pages_data = [
            {"page": 0, "ocr_result": _ocr_result_to_dict("甲乙")},
            {"page": 1, "ocr_result": _ocr_result_to_dict("丙丁")},
        ]

        results = PdfService.add_text_layer_batch(doc, pdf_doc, pages_data)

        # 两页都应成功写入
        assert set(results.keys()) == {0, 1}
        for page_idx, (written, skipped) in results.items():
            assert written >= 1, f"页 {page_idx} 应写入至少 1 块"

        # 保存后验证嵌入字体数
        out_path = tmp_path / "out.pdf"
        doc.save(str(out_path))
        doc.close()

        verify = fitz.open(str(out_path))
        embedded = _count_embedded_subset_fonts(verify)
        verify.close()
        # 批量写层共享单一子集：嵌入的子集字体对象应 ≤ 1
        assert len(embedded) <= 1, (
            f"期望 ≤1 个共享子集字体（批量聚合），实际 {len(embedded)}"
        )

    def test_batch_writes_correct_text(self, scan_pdf, tmp_path):
        """批量写层后，文字层应能被 fitz 正确提取。"""
        doc = fitz.open(str(scan_pdf))
        pdf_doc = PdfDocument(file_path=str(scan_pdf))
        pdf_doc.pages = [PdfPageInfo(page_index=i) for i in range(2)]

        pages_data = [
            {"page": 0, "ocr_result": _ocr_result_to_dict("你好世界")},
            {"page": 1, "ocr_result": _ocr_result_to_dict("测试文本")},
        ]

        PdfService.add_text_layer_batch(doc, pdf_doc, pages_data)

        out_path = tmp_path / "out.pdf"
        doc.save(str(out_path))
        doc.close()

        verify = fitz.open(str(out_path))
        text0 = verify[0].get_text("text")
        text1 = verify[1].get_text("text")
        verify.close()
        assert "你好" in text0
        assert "测试" in text1

    def test_batch_skips_existing_layer_without_overwrite(self, scan_pdf):
        """已有文字层且 overwrite=False 时，该页应被跳过（返回 (0,1)）。"""
        doc = fitz.open(str(scan_pdf))
        pdf_doc = PdfDocument(file_path=str(scan_pdf))
        pdf_doc.pages = [PdfPageInfo(page_index=i) for i in range(2)]

        # 先给 page 0 写一层
        PdfService.add_text_layer(
            doc, pdf_doc, 0,
            OCRResult(raw_text="已有", text_blocks=[
                TextBlock(text="已有", score=0.9, bbox=(50, 50, 300, 100))
            ]),
        )
        assert pdf_doc.pages[0].has_text_layer

        # 批量写层（page 0 已有，page 1 新写），overwrite=False
        pages_data = [
            {"page": 0, "ocr_result": _ocr_result_to_dict("新的")},
            {"page": 1, "ocr_result": _ocr_result_to_dict("丙丁")},
        ]
        results = PdfService.add_text_layer_batch(
            doc, pdf_doc, pages_data, overwrite=False
        )

        # page 0 跳过（skipped=1），page 1 写入
        assert results[0] == (0, 1), "page 0 已有文字层应跳过"
        assert results[1][0] >= 1, "page 1 应写入"
        doc.close()
