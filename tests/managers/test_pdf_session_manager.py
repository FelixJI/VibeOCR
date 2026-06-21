"""Tests for PdfSessionManager."""

import fitz
import pytest

from vibeocr.managers.pdf_session_manager import PdfSessionManager


def _create_test_pdf(path, num_pages=2):
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Page {i + 1}", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def manager(qapp):
    mgr = PdfSessionManager(parent=qapp)
    yield mgr
    mgr.shutdown()


@pytest.fixture
def test_pdf_a(tmp_path):
    return _create_test_pdf(tmp_path / "a.pdf", num_pages=2)


@pytest.fixture
def test_pdf_b(tmp_path):
    return _create_test_pdf(tmp_path / "b.pdf", num_pages=3)


class TestPdfSessionManagerSessions:
    def test_open_session(self, manager, test_pdf_a):
        session = manager.open_session(str(test_pdf_a))
        assert session is not None
        assert session.file_path == str(test_pdf_a)
        assert session.pdf_document.page_count == 2

    def test_active_session_is_last_opened(self, manager, test_pdf_a, test_pdf_b):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        assert manager.active_session is not None
        assert manager.active_session.file_path == str(test_pdf_b)

    def test_switch_session(self, manager, test_pdf_a, test_pdf_b):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        manager.switch_session(str(test_pdf_a))
        assert manager.active_session.file_path == str(test_pdf_a)

    def test_close_session(self, manager, test_pdf_a):
        manager.open_session(str(test_pdf_a))
        manager.close_session(str(test_pdf_a))
        assert manager.active_session is None
        assert len(manager.session_paths) == 0

    def test_close_active_switches_to_remaining(self, manager, test_pdf_a, test_pdf_b):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        manager.close_session(str(test_pdf_b))
        assert manager.active_session is not None
        assert manager.active_session.file_path == str(test_pdf_a)

    def test_session_paths(self, manager, test_pdf_a, test_pdf_b):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        paths = manager.session_paths
        assert str(test_pdf_a) in paths
        assert str(test_pdf_b) in paths

    def test_get_session(self, manager, test_pdf_a):
        manager.open_session(str(test_pdf_a))
        session = manager.get_session(str(test_pdf_a))
        assert session is not None
        assert session.file_path == str(test_pdf_a)

    def test_open_nonexistent_raises(self, manager):
        with pytest.raises(FileNotFoundError):
            manager.open_session("/nonexistent/file.pdf")


class TestPdfSessionManagerShutdown:
    def test_shutdown_closes_all_docs(self, manager, test_pdf_a, test_pdf_b):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        manager.shutdown()
        assert manager.active_session is None
        assert len(manager.session_paths) == 0


class TestPdfSessionManagerOcrStats:
    def test_ocr_stats_accumulate_and_signal(self, manager, test_pdf_a):
        """模拟 OCR worker 回调，验证 stats 累加与 ocr_stats_ready 信号。

        _on_ocr_page_done/_on_ocr_all_done 从 self._ocr_worker.session_id 取会话，
        因此注入一个 mock worker 指向活动会话。
        """
        from unittest.mock import MagicMock

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        session = manager.open_session(str(test_pdf_a))

        mock_worker = MagicMock()
        mock_worker.session_id = session.file_path
        manager._ocr_worker = mock_worker

        emitted = []
        manager.ocr_stats_ready.connect(
            lambda sid, w, s: emitted.append((sid, w, s))
        )

        # 第一页写入 1 块
        result = OCRResult(
            raw_text="Hello",
            text_blocks=[
                TextBlock(
                    text="Hello",
                    score=0.9,
                    bbox=(50.0, 50.0, 300.0, 100.0),
                    page_idx=0,
                ),
            ],
        )
        manager._on_ocr_page_done(0, result)
        # 第二页 result=None（模拟失败页）
        manager._on_ocr_page_done(1, None)

        assert session.ocr_stats["written"] == 1
        assert session.ocr_stats["skipped"] == 0

        manager._on_ocr_all_done(session.file_path, 1, 1)
        assert len(emitted) == 1
        sid, w, s = emitted[0]
        assert sid == session.file_path
        assert w == 1
        assert s == 0


def _create_mixed_pdf(path, num_pages=3):
    """第 0 页有文字层，其余页无。"""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)
        if i == 0:
            page.insert_text((72, 72), "已有文字", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


class TestPagesWithoutTextLayer:
    def test_returns_only_pages_without_layer(self, manager, tmp_path):
        """混合页（有/无文字层）只返回无文字层的索引。"""
        from vibeocr.services.pdf_service import PdfService

        path = _create_mixed_pdf(tmp_path / "mixed.pdf", num_pages=3)
        session = manager.open_session(str(path))
        # open_session 不分析文字层；模拟 PdfLoadWorker 的后台分析
        with session.doc_lock:
            PdfService.build_page_infos(session.doc, session.pdf_document)

        result = manager.get_pages_without_text_layer(session.file_path)
        assert result == [1, 2]

    def test_returns_empty_when_all_have_layer(self, manager, test_pdf_a):
        """所有页都有文字层时返回空列表。"""
        from vibeocr.services.pdf_service import PdfService

        session = manager.open_session(str(test_pdf_a))
        with session.doc_lock:
            PdfService.build_page_infos(session.doc, session.pdf_document)
        result = manager.get_pages_without_text_layer(session.file_path)
        assert result == []

    def test_returns_empty_for_unknown_session(self, manager):
        result = manager.get_pages_without_text_layer("/nonexistent/path.pdf")
        assert result == []


class TestOcrOverwritePassThrough:
    def test_overwrite_false_default(self, manager, test_pdf_a):
        """start_ocr 默认 overwrite=False，_on_ocr_page_done 写入时用 False。"""
        from unittest.mock import MagicMock

        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.services.pdf_service import PdfService

        session = manager.open_session(str(test_pdf_a))
        with session.doc_lock:
            PdfService.build_page_infos(session.doc, session.pdf_document)
        manager._ocr_worker = MagicMock()
        manager._ocr_worker.session_id = session.file_path
        manager._overwrite_text_layer = False

        # 已有文字层，overwrite=False → 跳过 (0,1)
        result = OCRResult(
            raw_text="x",
            text_blocks=[
                TextBlock(
                    text="x",
                    score=0.9,
                    bbox=(50.0, 50.0, 300.0, 100.0),
                    page_idx=0,
                ),
            ],
        )
        manager._on_ocr_page_done(0, result)
        assert session.ocr_stats["written"] == 0
        assert session.ocr_stats["skipped"] == 1

    def test_overwrite_true_deletes_then_writes(self, manager, test_pdf_a):
        """overwrite=True 时已有文字层页被先删后写。"""
        from unittest.mock import MagicMock

        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.services.pdf_service import PdfService

        session = manager.open_session(str(test_pdf_a))
        with session.doc_lock:
            PdfService.build_page_infos(session.doc, session.pdf_document)
        manager._ocr_worker = MagicMock()
        manager._ocr_worker.session_id = session.file_path
        manager._overwrite_text_layer = True

        result = OCRResult(
            raw_text="新文字",
            text_blocks=[
                TextBlock(
                    text="新文字",
                    score=0.9,
                    bbox=(50.0, 50.0, 300.0, 100.0),
                    page_idx=0,
                ),
            ],
        )
        manager._on_ocr_page_done(0, result)
        assert session.ocr_stats["written"] == 1
        assert session.ocr_stats["skipped"] == 0
