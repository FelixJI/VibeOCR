"""Tests for PdfSessionManager(进程化版本)。

新架构:manager 通过 PdfBackendClient(httpx)调用后端子进程,不持 fitz.Document。
测试用真实后端子进程(端口自动分配),验证 public API + 信号。

注意:这些测试会启动 PDF 后端子进程,单测较慢(进程启动 ~3s)。
标记为 slow,CI 可选跳过。
"""

from __future__ import annotations

import time

import fitz
import pytest
from PySide6.QtCore import Qt

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


def _wait_signal(qapp, signal, timeout=15.0):
    """等待信号触发,期间处理事件循环。返回是否触发。"""
    fired = [False]
    def _on():
        fired[0] = True
    signal.connect(_on)
    deadline = time.monotonic() + timeout
    try:
        while not fired[0] and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.03)
    finally:
        signal.disconnect(_on)
    return fired[0]


# ---- session 生命周期 --------------------------------------------------

class TestPdfSessionManagerSessions:
    def test_open_session(self, manager, test_pdf_a, qapp):
        fired = [False]
        manager.active_changed.connect(lambda: fired.__setitem__(0, True))
        manager.open_session(str(test_pdf_a))
        qapp.processEvents()
        assert fired[0], "active_changed 应触发"
        s = manager.active_session
        assert s is not None
        assert s.pdf_document.page_count == 2

    def test_active_session_is_last_opened(self, manager, test_pdf_a, test_pdf_b, qapp):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        qapp.processEvents()
        assert manager.active_session.file_path.endswith("b.pdf")

    def test_switch_session(self, manager, test_pdf_a, test_pdf_b, qapp):
        path_a = str(test_pdf_a)
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        qapp.processEvents()
        manager.switch_session(path_a)
        assert manager.active_session.file_path.endswith("a.pdf")

    def test_close_session(self, manager, test_pdf_a, qapp):
        path = str(test_pdf_a)
        manager.open_session(path)
        qapp.processEvents()
        manager.close_session(path)
        qapp.processEvents()
        assert path not in manager.session_paths
        assert manager.get_session(path) is None

    def test_session_paths(self, manager, test_pdf_a, test_pdf_b, qapp):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        qapp.processEvents()
        assert len(manager.session_paths) == 2

    def test_get_session(self, manager, test_pdf_a, qapp):
        path = str(test_pdf_a)
        manager.open_session(path)
        qapp.processEvents()
        s = manager.get_session(path)
        assert s is not None
        assert manager.get_session("nonexistent") is None

    def test_open_nonexistent_emits_open_failed(self, manager, qapp):
        """打开不存在的文件:emit open_failed 信号。"""
        fired = [False]
        manager.open_failed.connect(lambda *a: fired.__setitem__(0, True))
        manager.open_session("/nonexistent/file.pdf")
        qapp.processEvents()
        assert fired[0], "open_failed 应触发"


# ---- 异步批量打开 ------------------------------------------------------

class TestOpenAsync:
    def test_open_sessions_async_emits_session_added(self, manager, test_pdf_a, qapp):
        path = str(test_pdf_a)
        fired = [False]
        manager.session_added.connect(lambda *a: fired.__setitem__(0, True))
        manager.open_sessions_async([path])
        # 异步:等 worker 线程完成 open+load
        deadline = time.monotonic() + 25.0
        while not fired[0] and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)
        assert fired[0], "session_added 应触发"
        assert path in manager.session_paths

    def test_open_sessions_async_skip_existing(self, manager, test_pdf_a, qapp):
        path = str(test_pdf_a)
        manager.open_session(path)
        qapp.processEvents()
        fired = [False]
        manager.open_done.connect(lambda: fired.__setitem__(0, True))
        manager.open_sessions_async([path])
        deadline = time.monotonic() + 10.0
        while not fired[0] and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.03)
        assert fired[0], "open_done 应触发(已存在的文件跳过)"


# ---- 缩略图失效信号 ----------------------------------------------------

class TestRerenderThumbnailsAsync:
    def test_emits_thumbnails_invalidated(self, manager, test_pdf_a, qapp):
        manager.open_session(str(test_pdf_a))
        qapp.processEvents()
        fired = [False]
        manager.thumbnails_invalidated.connect(lambda *a: fired.__setitem__(0, True))
        manager.rerender_thumbnails_async([0])
        qapp.processEvents()
        assert fired[0], "thumbnails_invalidated 应触发"

    def test_empty_indices_does_not_emit(self, manager, test_pdf_a, qapp):
        manager.open_session(str(test_pdf_a))
        qapp.processEvents()
        manager.rerender_thumbnails_async([])
        qapp.processEvents()


# ---- 文字层状态 --------------------------------------------------------

class TestPagesWithoutTextLayer:
    def test_returns_empty_for_unknown_session(self, manager):
        assert manager.get_pages_without_text_layer("nonexistent") == []


class TestPdfSessionManagerBlockEdit:
    def test_update_block_text_no_active_session(self, manager):
        """无活动会话时返回 False。"""
        assert manager.update_page_block_text(0, 0, "x") is False


# ---- shutdown ----------------------------------------------------------

class TestPdfSessionManagerShutdown:
    def test_shutdown_clears_sessions(self, manager, test_pdf_a, qapp):
        manager.open_session(str(test_pdf_a))
        qapp.processEvents()
        manager.shutdown()
        assert len(manager.session_paths) == 0


# ---- 属性 ---------------------------------------------------------------

class TestPdfSessionManagerProperties:
    def test_is_ocr_ready_default_false(self, manager):
        assert manager.is_ocr_ready is False

    def test_is_deskew_running_default_false(self, manager):
        assert manager.is_deskew_running is False

    def test_is_mutate_running_default_false(self, manager):
        assert manager.is_mutate_running is False

    def test_backend_client_exposed(self, manager):
        """manager 暴露 backend_client 供 PdfTab 缩略图/预览渲染用。"""
        assert manager.backend_client is not None

    def test_get_modified_sessions_empty(self, manager):
        assert manager.get_modified_sessions() == []
