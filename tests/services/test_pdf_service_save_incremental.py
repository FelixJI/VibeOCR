import fitz
from vibeocr.services.pdf_service import PdfService


def test_save_incremental_persists_and_keeps_doc_usable(tmp_path):
    pdf = tmp_path / "a.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), "hello")
    doc.save(str(pdf))
    doc.close()
    doc = fitz.open(str(pdf))
    # 再加一层文字
    doc[0].insert_text((50, 100), "world")

    ok = PdfService.save_incremental(doc, str(pdf))
    assert ok is True
    # doc 仍可用（不重开，不 close）
    assert doc.page_count == 1
    # 重开验证内容落盘
    doc.close()
    doc2 = fitz.open(str(pdf))
    text = doc2[0].get_text()
    assert "hello" in text and "world" in text
    doc2.close()


def test_save_incremental_returns_false_and_keeps_doc_usable_on_failure(
    tmp_path, monkeypatch
):
    """失败时 doc 保持可用（不 close），文件从备份回滚。调用方据此不写 sidecar。"""
    pdf = tmp_path / "a.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(pdf))
    doc.close()
    doc = fitz.open(str(pdf))
    doc[0].insert_text((50, 100), "world")  # 内存改动

    # 模拟 save 抛异常（incremental 写文件失败）
    def boom(self, *a, **kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(fitz.Document, "save", boom)

    ok = PdfService.save_incremental(doc, str(pdf))
    assert ok is False
    # 关键：doc 仍可用（未 close），内存文字层保留，可继续后续操作
    assert doc.page_count == 1
    assert "world" in doc[0].get_text()  # 内存改动还在
    doc.close()
    # 文件从备份回滚（只剩最初的 new_page，无 world）
    monkeypatch.undo()
    doc2 = fitz.open(str(pdf))
    assert doc2.page_count == 1
    assert "world" not in doc2[0].get_text()
    doc2.close()
