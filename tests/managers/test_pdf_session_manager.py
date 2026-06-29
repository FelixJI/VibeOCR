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
        manager.ocr_stats_ready.connect(lambda sid, w, s: emitted.append((sid, w, s)))

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

    def test_start_ocr_propagates_overwrite_to_page_done(
        self, manager, test_pdf_a, monkeypatch
    ):
        """start_ocr(overwrite=True) 应设置 _overwrite_text_layer，
        使随后的 _on_ocr_page_done 对已有文字层页执行先删后写。

        回归锁定 start_ocr → _overwrite_text_layer → add_text_layer 的端到端契约。
        """
        from unittest.mock import MagicMock

        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.services.pdf_service import PdfService

        session = manager.open_session(str(test_pdf_a))
        with session.doc_lock:
            PdfService.build_page_infos(session.doc, session.pdf_document)
        # 每页都有文字层，overwrite=False 会全跳过，True 才会先删后写

        # stub 掉 worker 构造与 ocr_service，避免真实 OCR 运行
        fake_worker = MagicMock()
        fake_worker.session_id = session.file_path
        monkeypatch.setattr(
            "vibeocr.managers.pdf_session_manager.PdfOcrWorker",
            lambda *a, **k: fake_worker,
        )
        manager._ocr_service = MagicMock()

        manager.start_ocr([0], overwrite=True)
        # start_ocr 应已把 overwrite 透传到 _overwrite_text_layer
        assert manager._overwrite_text_layer is True
        # 模拟 worker 回调一页
        manager._ocr_worker = fake_worker
        result = OCRResult(
            raw_text="替换文字",
            text_blocks=[
                TextBlock(
                    text="替换文字",
                    score=0.9,
                    bbox=(50.0, 50.0, 300.0, 100.0),
                    page_idx=0,
                ),
            ],
        )
        manager._on_ocr_page_done(0, result)
        assert session.ocr_stats["written"] == 1
        assert session.ocr_stats["skipped"] == 0


class TestMinerUFirstUseGuard:
    """start_ocr 在 MinerU 文档解析首用时触发模型下载"""

    def test_triggers_model_download_on_first_use(self, manager, test_pdf_a, monkeypatch):
        """MinerU 管道 + 模型未下载 → 应调用 ensure_mineru_models"""
        from unittest.mock import MagicMock

        from vibeocr.core.pipelines import OCRPipeline
        from vibeocr.models.ocr_options import OCROptions
        from vibeocr.services.pdf_service import PdfService

        session = manager.open_session(str(test_pdf_a))
        with session.doc_lock:
            PdfService.build_page_infos(session.doc, session.pdf_document)

        fake_worker = MagicMock()
        fake_worker.session_id = session.file_path
        monkeypatch.setattr(
            "vibeocr.managers.pdf_session_manager.PdfOcrWorker",
            lambda *a, **k: fake_worker,
        )
        manager._ocr_service = MagicMock()

        # 模型未下载（首用）
        monkeypatch.setattr(
            "vibeocr.pipeline_status.is_pipeline_ever_succeeded", lambda *a, **k: False
        )
        download_calls = []
        monkeypatch.setattr(
            "vibeocr.env_manager.ensure_mineru_models",
            lambda *a, **k: (download_calls.append(1) or (True, "ok")),
        )

        opts = OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING)
        manager.start_ocr([0], ocr_options=opts)

        assert len(download_calls) == 1, "首用应触发模型下载"

    def test_skips_download_when_already_succeeded(self, manager, test_pdf_a, monkeypatch):
        """MinerU 已成功过 → 跳过模型下载"""
        from unittest.mock import MagicMock

        from vibeocr.core.pipelines import OCRPipeline
        from vibeocr.models.ocr_options import OCROptions
        from vibeocr.services.pdf_service import PdfService

        session = manager.open_session(str(test_pdf_a))
        with session.doc_lock:
            PdfService.build_page_infos(session.doc, session.pdf_document)

        fake_worker = MagicMock()
        fake_worker.session_id = session.file_path
        monkeypatch.setattr(
            "vibeocr.managers.pdf_session_manager.PdfOcrWorker",
            lambda *a, **k: fake_worker,
        )
        manager._ocr_service = MagicMock()

        # 模型已下载（已成功过）
        monkeypatch.setattr(
            "vibeocr.pipeline_status.is_pipeline_ever_succeeded", lambda *a, **k: True
        )
        download_calls = []
        monkeypatch.setattr(
            "vibeocr.env_manager.ensure_mineru_models",
            lambda *a, **k: (download_calls.append(1) or (True, "ok")),
        )

        opts = OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING)
        manager.start_ocr([0], ocr_options=opts)

        assert len(download_calls) == 0, "已成功过应跳过下载"

    def test_download_failure_aborts_ocr(self, manager, test_pdf_a, monkeypatch):
        """模型下载失败时应终止本次 OCR（不启动 worker）"""
        from unittest.mock import MagicMock

        from vibeocr.core.pipelines import OCRPipeline
        from vibeocr.models.ocr_options import OCROptions
        from vibeocr.services.pdf_service import PdfService

        session = manager.open_session(str(test_pdf_a))
        with session.doc_lock:
            PdfService.build_page_infos(session.doc, session.pdf_document)

        worker_created = []
        monkeypatch.setattr(
            "vibeocr.managers.pdf_session_manager.PdfOcrWorker",
            lambda *a, **k: worker_created.append(1) or MagicMock(),
        )
        manager._ocr_service = MagicMock()

        monkeypatch.setattr(
            "vibeocr.pipeline_status.is_pipeline_ever_succeeded", lambda *a, **k: False
        )
        monkeypatch.setattr(
            "vibeocr.env_manager.ensure_mineru_models",
            lambda *a, **k: (False, "下载失败"),
        )

        opts = OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING)
        manager.start_ocr([0], ocr_options=opts)

        assert len(worker_created) == 0, "下载失败不应启动 OCR worker"

    def test_non_mineru_pipeline_skips_check(self, manager, test_pdf_a, monkeypatch):
        """非 MinerU 管道不应触发模型检查"""
        from unittest.mock import MagicMock

        from vibeocr.core.pipelines import OCRPipeline
        from vibeocr.models.ocr_options import OCROptions
        from vibeocr.services.pdf_service import PdfService

        session = manager.open_session(str(test_pdf_a))
        with session.doc_lock:
            PdfService.build_page_infos(session.doc, session.pdf_document)

        fake_worker = MagicMock()
        fake_worker.session_id = session.file_path
        monkeypatch.setattr(
            "vibeocr.managers.pdf_session_manager.PdfOcrWorker",
            lambda *a, **k: fake_worker,
        )
        manager._ocr_service = MagicMock()

        download_calls = []
        monkeypatch.setattr(
            "vibeocr.env_manager.ensure_mineru_models",
            lambda *a, **k: (download_calls.append(1) or (True, "ok")),
        )

        # 普通 OCR 管道（非文档解析）
        opts = OCROptions(pipeline=OCRPipeline.OCR)
        manager.start_ocr([0], ocr_options=opts)

        assert len(download_calls) == 0, "非 MinerU 管道不应触发模型下载"


class TestPdfSessionManagerBlockEdit:
    """双击改字 → 内存模型更新（update_page_block_text）。"""

    def test_update_page_block_text_changes_memory(self, manager, test_pdf_a):
        """改字后 PdfPageInfo.ocr_text_blocks[idx].text 更新，标记手动修改。"""
        from unittest.mock import MagicMock

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        session = manager.open_session(str(test_pdf_a))
        mock_worker = MagicMock()
        mock_worker.session_id = session.file_path
        manager._ocr_worker = mock_worker

        result = OCRResult(
            raw_text="签回联",
            text_blocks=[
                TextBlock(
                    text="签回联",
                    score=0.9,
                    bbox=(50.0, 50.0, 200.0, 120.0),
                    page_idx=0,
                ),
            ],
        )
        manager._on_ocr_page_done(0, result)

        changed = manager.update_page_block_text(0, 0, "签收联")
        assert changed is True
        info = session.pdf_document.pages[0]
        assert info.ocr_text_blocks[0].text == "签收联"
        assert info.ocr_text_blocks[0].is_manually_edited is True
        assert session.is_modified is True

    def test_update_page_block_text_noop_when_unchanged(self, manager, test_pdf_a):
        """文字未变时返回 False，不触发 is_modified。"""
        from unittest.mock import MagicMock

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        session = manager.open_session(str(test_pdf_a))
        mock_worker = MagicMock()
        mock_worker.session_id = session.file_path
        manager._ocr_worker = mock_worker

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
        session.pdf_document.is_modified = False  # 重置

        changed = manager.update_page_block_text(0, 0, "Hello")
        assert changed is False
        assert session.is_modified is False

    def test_update_page_block_text_invalid_index(self, manager, test_pdf_a):
        """无效 page/block 索引返回 False，不报错。"""
        manager.open_session(str(test_pdf_a))
        assert manager.update_page_block_text(0, 0, "x") is False
        assert manager.update_page_block_text(99, 0, "x") is False


class TestPdfSessionManagerRewritePages:
    """保存时重写已编辑页（rewrite_modified_pages）。"""

    def test_rewrite_persists_edited_text_to_pdf(self, manager, tmp_path):
        """改字后 rewrite_modified_pages 把编辑写回 PDF 文字层。"""
        from unittest.mock import MagicMock

        # 扫描件单页 PDF
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

        session = manager.open_session(str(path))
        mock_worker = MagicMock()
        mock_worker.session_id = session.file_path
        manager._ocr_worker = mock_worker

        result = OCRResult(
            raw_text="签回联",
            text_blocks=[
                TextBlock(
                    text="签回联",
                    score=0.9,
                    bbox=(50.0, 50.0, 200.0, 120.0),
                    page_idx=0,
                ),
            ],
        )
        manager._on_ocr_page_done(0, result)

        # 改字
        manager.update_page_block_text(0, 0, "签收联")

        # rewrite + save
        manager.rewrite_modified_pages()
        manager._save_active_to_disk_for_test()

        # 重新打开验证
        verify = fitz.open(str(path))
        text = verify[0].get_text()
        assert "签收联" in text
        assert "签回联" not in text
        verify.close()

    def test_rewrite_skips_unedited_pages(self, manager, test_pdf_a):
        """未编辑的页不重写（无 ocr_text_blocks 时跳过）。"""
        manager.open_session(str(test_pdf_a))
        # 没有 OCR 过，ocr_text_blocks 为空，rewrite 应安全跳过
        manager.rewrite_modified_pages()  # 不应报错


class TestStartOcrStreaming:
    def test_uses_render_worker_and_queue(self, manager, test_pdf_a, monkeypatch):
        """start_ocr 应启动 PdfRenderWorker + PdfOcrWorker(queue 模式)。"""
        from queue import Queue
        from unittest.mock import MagicMock

        from vibeocr.services.pdf_service import PdfService

        session = manager.open_session(str(test_pdf_a))
        with session.doc_lock:
            PdfService.build_page_infos(session.doc, session.pdf_document)

        render_created = []
        ocr_created = []
        monkeypatch.setattr(
            "vibeocr.managers.pdf_session_manager.PdfRenderWorker",
            lambda *a, **k: render_created.append(k) or MagicMock(),
        )
        monkeypatch.setattr(
            "vibeocr.managers.pdf_session_manager.PdfOcrWorker",
            lambda *a, **k: ocr_created.append(k) or MagicMock(),
        )
        manager._ocr_service = MagicMock()

        manager.start_ocr([0])
        assert len(render_created) == 1
        assert len(ocr_created) == 1
        # ocr worker 应以 render_queue 参数构造（流式模式）
        assert "render_queue" in ocr_created[0]
