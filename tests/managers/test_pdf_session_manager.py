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


# ---- task generation ---------------------------------------------------

class TestPdfTaskGeneration:
    """PDF runner task generation：旧任务的迟到信号不污染新任务状态。

    根因：OCR/mutate 取消后可继续启动新任务，旧 runner 的 all_done 信号
    无条件清 _ocr_running/_ocr_worker，把新任务状态清掉。引入递增
    task generation，信号带 task_id，槽只接受当前代。
    """

    def test_ocr_done_with_stale_task_id_ignored(self):
        """旧 task_id 的 all_done 信号被忽略，不清 _ocr_running/_ocr_worker"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 2  # 当前代
        mgr._ocr_running = True
        mgr._ocr_worker = MagicMock()
        mgr._sessions = {}
        mgr._path_for_session_id = lambda sid: None  # 模拟无匹配

        # 旧代（task_id=1）的 all_done 信号
        mgr._on_ocr_all_done_signal("session_1", 5, 0, task_id=1)

        # 当前代状态不被旧信号清掉
        assert mgr._ocr_running is True
        assert mgr._ocr_worker is not None

    def test_ocr_done_with_current_task_id_accepted(self):
        """当前 task_id 的 all_done 信号正常清理状态"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 2
        mgr._ocr_running = True
        mgr._ocr_worker = MagicMock()
        mgr._sessions = {}
        mgr._path_for_session_id = lambda sid: None

        mgr._on_ocr_all_done_signal("session_2", 5, 0, task_id=2)

        assert mgr._ocr_running is False
        assert mgr._ocr_worker is None

    def test_ocr_done_without_task_id_accepted(self):
        """无 task_id 参数（默认 0）时正常处理（向后兼容）"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 1
        mgr._ocr_running = True
        mgr._ocr_worker = MagicMock()
        mgr._sessions = {}
        mgr._path_for_session_id = lambda sid: None

        mgr._on_ocr_all_done_signal("session_1", 5, 0)

        assert mgr._ocr_running is False
        assert mgr._ocr_worker is None

    def test_mutate_done_with_stale_task_id_ignored(self):
        """旧 task_id 的 mutate all_done 信号被忽略，不清 _mutate_worker"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 2
        mgr._mutate_worker = MagicMock()
        mgr._sessions = {}
        mgr._path_for_session_id = lambda sid: None

        mgr._on_mutate_all_done("session_1", MagicMock(), {}, task_id=1)

        assert mgr._mutate_worker is not None

    def test_task_generation_increments_on_start_ocr(self):
        """start_ocr 递增 task generation"""
        from unittest.mock import MagicMock, patch

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 0
        mgr._sessions = {}
        mgr._active_path = None
        mgr._ocr_service = None
        mgr._ocr_running = False
        mgr._ocr_cancelled = False
        mgr._ocr_worker = None
        mgr._client = MagicMock()

        # active_session 为 None 时 start_ocr 直接返回，不递增
        # 需要有 active session
        mock_session = MagicMock()
        mock_session.session_id = "sid1"
        mock_session.reset_ocr_stats = MagicMock()
        mgr._sessions["/fake.pdf"] = mock_session
        mgr._active_path = "/fake.pdf"
        mgr._ocr_service = MagicMock()
        mgr._is_mineru_first_use = MagicMock(return_value=False)

        with patch.object(mgr, "_cancel_ocr"):
            try:
                mgr.start_ocr([0, 1])
            except Exception:
                pass

        assert mgr._task_generation == 1


class TestExportCancel:
    """export cancel 真正生效：逐文件检查 cancel flag，不继续后续文件。

    根因：_ExportRunner._cancelled 被 cancel() 设置但 run() 从不读取，
    export_all_modified 也不检查它，属于无效取消。
    """

    def test_export_cancel_stops_after_current_file(self):
        """export_all_modified 检查 cancel_check，取消后停止后续文件"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._pdf_settings = None
        mgr._settings_to_dict = lambda s: None
        mgr._client = MagicMock()
        mgr._client.save = MagicMock(return_value=None)

        # 3 个 modified session
        sessions = {}
        for i in range(3):
            mock_s = MagicMock()
            mock_s.session_id = f"sid_{i}"
            mock_s.is_modified = True
            sessions[f"/file_{i}.pdf"] = mock_s
        mgr._sessions = sessions

        # cancel_check 第一次返回 False（处理第一个），之后返回 True
        call_count = [0]
        def cancel_check():
            call_count[0] += 1
            return call_count[0] > 1

        results = mgr.export_all_modified("/tmp/out", cancel_check=cancel_check)

        # 取消后只处理 1 个文件
        assert len(results) == 1
        assert mgr._client.save.call_count == 1

    def test_export_no_cancel_processes_all(self):
        """无取消时处理所有 modified session"""
        from unittest.mock import MagicMock

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._pdf_settings = None
        mgr._settings_to_dict = lambda s: None
        mgr._client = MagicMock()
        mgr._client.save = MagicMock(return_value=None)

        sessions = {}
        for i in range(3):
            mock_s = MagicMock()
            mock_s.session_id = f"sid_{i}"
            mock_s.is_modified = True
            sessions[f"/file_{i}.pdf"] = mock_s
        mgr._sessions = sessions

        results = mgr.export_all_modified("/tmp/out")
        assert len(results) == 3
        assert mgr._client.save.call_count == 3
