"""PDF GUI/IPC 异步边界的精确回归测试。"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QThread

from tests.qt_responsiveness import assert_qt_event_loop_responsive
from vibeocr.ipc.schemas import ModelDiff, PdfDocumentMirror, PdfPageInfoMirror
from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.models.pdf_session import PdfSession
from vibeocr.pyside.pdf_session_manager import PdfSessionManager


def _session(path: str = "C:/fake.pdf", pages: int = 2) -> PdfSession:
    return PdfSession(
        file_path=path,
        session_id="sid",
        pdf_document=PdfDocument(
            file_path=path,
            pages=[PdfPageInfo(page_index=index) for index in range(pages)],
        ),
    )


@pytest.fixture
def manager(qapp):
    mgr = PdfSessionManager()
    mgr._client = MagicMock()
    session = _session()
    mgr._sessions = {session.file_path: session}
    mgr._active_path = session.file_path
    yield mgr
    mgr.request_shutdown()
    mgr.drain(3000)


def test_slow_mineru_preflight_keeps_event_loop_responsive(
    manager, qtbot, monkeypatch
):
    entered = threading.Event()
    release = threading.Event()

    def slow_prepare(*_args, **_kwargs):
        entered.set()
        release.wait(2)
        return True, "ok"

    monkeypatch.setattr("vibeocr.env_manager.ensure_mineru_models", slow_prepare)
    monkeypatch.setattr(manager, "_is_mineru_first_use", lambda _opts: True)
    manager._ocr_service = object()

    assert manager.start_ocr([0], ocr_options=object()) is True
    qtbot.waitUntil(entered.is_set)
    assert_qt_event_loop_responsive(
        qtbot,
        in_flight=lambda: manager._preflight_worker is not None
        and manager._preflight_worker.isRunning(),
    )

    manager.cancel_ocr()
    release.set()
    qtbot.waitUntil(lambda: manager._preflight_worker is None)
    assert manager._ocr_state == "cancelled"
    manager._client.reset_cancel.assert_not_called()


def test_slow_backend_start_for_open_runs_off_gui(manager, qtbot):
    main_thread = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()
    start_threads: list[int] = []

    def slow_start():
        start_threads.append(threading.get_ident())
        entered.set()
        release.wait(2)

    manager._client.start.side_effect = slow_start
    manager._client.open_session.side_effect = RuntimeError("stop after start")

    manager.open_sessions_async(["C:/slow-open.pdf"])
    qtbot.waitUntil(entered.is_set)
    assert_qt_event_loop_responsive(
        qtbot,
        in_flight=lambda: manager._open_worker is not None
        and manager._open_worker.isRunning(),
    )
    release.set()
    qtbot.waitUntil(lambda: manager._open_worker is None)

    assert start_threads and all(ident != main_thread for ident in start_threads)


def test_discarded_doc_opened_closes_orphan_session_in_background(manager, qtbot):
    main_thread = threading.get_ident()
    close_threads: list[int] = []

    def close_session(session_id):
        assert session_id == "orphan-sid"
        close_threads.append(threading.get_ident())

    manager._client.close_session.side_effect = close_session
    manager._open_generation = 8

    manager._on_doc_opened_guarded(
        "C:/orphan.pdf", "orphan-sid", object(), object(), 7
    )
    qtbot.waitUntil(lambda: not manager._close_workers)

    assert "C:/orphan.pdf" not in manager.session_paths
    assert close_threads and all(ident != main_thread for ident in close_threads)


def test_drain_observes_close_worker_added_while_open_worker_finishes(
    manager, qtbot
):
    """GUI poll 不能漏掉旧 open 结束边界新增的 orphan close。"""
    close_entered = threading.Event()
    release_close = threading.Event()

    def slow_close(_session_id):
        close_entered.set()
        release_close.wait(2)

    manager._client.close_session.side_effect = slow_close
    manager._sessions.clear()
    manager._active_path = None
    manager._open_generation = 2

    class LateOpenWorker:
        finished = False

        def isFinished(self):
            return self.finished

    late_open = LateOpenWorker()
    manager._draining_open_workers.add(late_open)
    try:
        manager.request_shutdown()
        assert manager.is_drained() is False

        late_open.finished = True
        manager._draining_open_workers.discard(late_open)
        manager._on_doc_opened_guarded(
            "C:/late.pdf", "late-sid", object(), object(), 1
        )
        qtbot.waitUntil(close_entered.is_set)
        assert manager._close_workers
    finally:
        manager._draining_open_workers.discard(late_open)
        release_close.set()
        for worker in list(manager._close_workers):
            worker.wait(1000)


def test_rapid_second_open_removes_and_closes_partially_loaded_first_session(
    manager, qtbot
):
    first_path = "C:/first.pdf"
    second_path = "C:/second.pdf"
    first_load_entered = threading.Event()
    release_first_load = threading.Event()

    def open_session(path):
        return SimpleNamespace(
            session_id="first-sid" if path == first_path else "second-sid",
            model=PdfDocumentMirror(
                file_path=path,
                pages=[PdfPageInfoMirror(page_index=0)],
            ),
        )

    def load_stream(session_id):
        if session_id == "first-sid":
            first_load_entered.set()
            release_first_load.wait(2)
        return iter(())

    manager._client.open_session.side_effect = open_session
    manager._client.load_stream.side_effect = load_stream

    manager.open_sessions_async([first_path])
    qtbot.waitUntil(first_load_entered.is_set)
    qtbot.waitUntil(lambda: first_path in manager.session_paths)

    manager.open_sessions_async([second_path])
    release_first_load.set()
    qtbot.waitUntil(
        lambda: manager._open_worker is None and not manager._draining_open_workers
    )

    assert first_path not in manager.session_paths
    assert any(
        call.args == ("first-sid",) for call in manager._client.close_session.call_args_list
    )


def test_shutdown_during_open_completion_closes_backend_session_exactly_once(
    manager, qtbot
):
    """取消必须与 open worker 的 incomplete ownership 转移保持原子。"""
    path = "C:/cancel-at-open-completion.pdf"
    load_called = threading.Event()
    allow_load_return = threading.Event()
    worker_before_final_pop = threading.Event()
    allow_final_pop = threading.Event()
    gui_thread_id = threading.get_ident()

    class FinalPopBarrierLock:
        """在 worker 最终 pop 前停住，但允许 GUI 读取 ownership 快照。"""

        def __init__(self):
            self._lock = threading.Lock()

        def __enter__(self):
            if threading.get_ident() != gui_thread_id:
                worker_before_final_pop.set()
                assert allow_final_pop.wait(2)
            self._lock.acquire()
            return self

        def __exit__(self, *_args):
            self._lock.release()

    manager._sessions.clear()
    manager._active_path = None
    manager._client.open_session.return_value = SimpleNamespace(
        session_id="race-sid",
        model=PdfDocumentMirror(
            file_path=path,
            pages=[PdfPageInfoMirror(page_index=0)],
        ),
    )

    def load_stream(_session_id):
        load_called.set()
        assert allow_load_return.wait(2)
        return iter(())

    manager._client.load_stream.side_effect = load_stream
    manager.open_sessions_async([path])
    qtbot.waitUntil(load_called.is_set)
    worker = manager._open_worker
    assert worker is not None
    worker._sessions_lock = FinalPopBarrierLock()

    allow_load_return.set()
    qtbot.waitUntil(worker_before_final_pop.is_set)
    manager.request_shutdown()
    allow_final_pop.set()
    qtbot.waitUntil(manager.is_drained, timeout=3000)

    close_calls = [
        call for call in manager._client.close_session.call_args_list
        if call.args == ("race-sid",)
    ]
    assert len(close_calls) == 1


def test_load_failure_after_doc_opened_removes_and_closes_partial_session(
    manager, qtbot
):
    path = "C:/broken-load.pdf"
    manager._client.open_session.return_value = SimpleNamespace(
        session_id="broken-sid",
        model=PdfDocumentMirror(
            file_path=path,
            pages=[PdfPageInfoMirror(page_index=0)],
        ),
    )
    manager._client.load_stream.side_effect = RuntimeError("stream failed")
    failures: list[tuple[str, str]] = []
    completed: list[str] = []
    manager.open_failed.connect(lambda p, error: failures.append((p, error)))
    manager.load_done.connect(completed.append)

    manager.open_sessions_async([path])
    qtbot.waitUntil(lambda: manager._open_worker is None)

    assert failures and failures[-1][0] == path
    assert path not in manager.session_paths
    assert path not in completed
    assert any(
        call.args == ("broken-sid",)
        for call in manager._client.close_session.call_args_list
    )


def test_export_result_keeps_worker_owned_until_native_finished(manager):
    worker = MagicMock()
    emitted: list[list[str]] = []
    manager.export_done.connect(emitted.append)
    manager._export_worker = worker

    manager._on_export_done(["C:/out.pdf"], worker)

    assert manager._export_worker is worker
    assert emitted == []

    manager._on_export_worker_finished(worker)

    assert manager._export_worker is None
    assert emitted == [["C:/out.pdf"]]
    worker.deleteLater.assert_called_once_with()


def test_unexpected_export_error_emits_terminal_after_native_finished(
    manager, qtbot, tmp_path
):
    """非 PdfBackendError 也必须释放写门并形成业务失败终态。"""
    session = manager.active_session
    assert session is not None
    session.pdf_document.is_modified = True
    manager._client.save.side_effect = MemoryError("export snapshot too large")
    failures: list[str] = []
    completed: list[list[str]] = []
    manager.export_failed.connect(failures.append)
    manager.export_done.connect(completed.append)

    manager.export_all_async(str(tmp_path))
    qtbot.waitUntil(lambda: manager._export_worker is None, timeout=3000)

    assert failures == ["export snapshot too large"]
    assert completed == []
    assert manager._pdf_write_busy() is False


def test_ocr_business_done_keeps_pdf_write_gate_until_thread_finished(
    manager, monkeypatch
):
    import vibeocr.pyside.pdf_session_manager as manager_module

    worker = MagicMock()
    manager._ocr_worker = worker
    manager._ocr_running = True
    manager._ocr_state = "running"
    manager._task_generation = 7
    fake_mutate = MagicMock()
    monkeypatch.setattr(
        manager_module, "PdfIpcMutateWorker", lambda *_args, **_kwargs: fake_mutate
    )

    manager._on_ocr_all_done_signal("sid", 1, 0, task_id=7)

    assert manager._ocr_running is False
    assert manager._start_mutate("rotate", {"pages": [0], "angle": 90}) is False
    fake_mutate.start.assert_not_called()


def test_open_start_failure_reports_every_path_and_finishes(manager, qtbot):
    manager._client.start.side_effect = RuntimeError("backend unavailable")
    failures: list[tuple[str, str]] = []
    done: list[bool] = []
    manager.open_failed.connect(lambda path, error: failures.append((path, error)))
    manager.open_done.connect(lambda: done.append(True))

    manager.open_sessions_async(["C:/a.pdf", "C:/b.pdf"])
    qtbot.waitUntil(lambda: bool(done))

    assert [path for path, _error in failures] == ["C:/a.pdf", "C:/b.pdf"]
    assert all("backend unavailable" in error for _path, error in failures)
    qtbot.waitUntil(lambda: manager._open_worker is None)


def test_preflight_late_success_is_ignored_after_shutdown(manager, qtbot, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def slow_prepare(*_args, **_kwargs):
        entered.set()
        release.wait(2)
        return True, "late"

    monkeypatch.setattr("vibeocr.env_manager.ensure_mineru_models", slow_prepare)
    monkeypatch.setattr(manager, "_is_mineru_first_use", lambda _opts: True)
    manager._ocr_service = object()
    run_ocr = MagicMock()
    monkeypatch.setattr(manager, "_run_ocr", run_ocr)

    assert manager.start_ocr([0], ocr_options=object())
    qtbot.waitUntil(entered.is_set)
    manager.request_shutdown()
    release.set()
    qtbot.waitUntil(lambda: manager._preflight_worker is None)

    run_ocr.assert_not_called()
    assert manager._pending_ocr_request is None


def test_preflight_cancel_defers_business_terminal_until_native_finished(manager):
    path = manager.active_session.file_path
    worker = MagicMock()
    worker.is_cancelled = True
    manager._preflight_worker = worker
    manager._preflight_generation = 11
    manager._pending_ocr_request = (path, [0], object(), object(), False)
    manager._ocr_running = True
    manager._ocr_state = "preflight"
    done: list[tuple[str, int, int]] = []
    manager.ocr_done.connect(lambda p, ok, fail: done.append((p, ok, fail)))

    manager.cancel_ocr()

    assert manager._ocr_running is True
    assert manager.is_ocr_running is True
    assert done == []

    manager._on_preflight_finished(worker, 11)

    assert manager._preflight_worker is None
    assert manager._ocr_running is False
    assert manager._ocr_state == "cancelled"
    assert done == [(path, 0, 0)]
    worker.deleteLater.assert_called_once_with()


def test_pdf_shutdown_request_creates_session_close_workers_on_gui_owner(
    manager, monkeypatch
):
    calls: list[tuple[str, QThread]] = []

    def record_close(session_id, *_args, **_kwargs):
        calls.append((session_id, QThread.currentThread()))
        manager._close_started_session_ids.add(session_id)

    monkeypatch.setattr(
        manager,
        "_start_close_worker",
        record_close,
    )

    manager.request_shutdown()

    assert calls == [("sid", manager.thread())]
    before = list(calls)
    assert manager.is_drained() is False
    assert manager.is_drained() is True
    assert calls == before


def test_quick_page_flip_discards_old_preview_and_detects_off_gui(manager, qtbot):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    main_thread = threading.get_ident()
    page_zero_entered = threading.Event()
    release_page_zero = threading.Event()
    call_threads: list[int] = []
    manager.active_session.pdf_document.pages[1].has_text_layer = True
    image = QImage(1, 1, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    valid_png = bytes(buffer.data())

    def render(_sid, page, *, dpi):
        assert dpi == 150
        call_threads.append(threading.get_ident())
        if page == 0:
            page_zero_entered.set()
            release_page_zero.wait(2)
        return valid_png

    def detect(_sid, page):
        call_threads.append(threading.get_ident())
        assert page == 1
        return SimpleNamespace(text_layers=[])

    manager._client.render_preview.side_effect = render
    manager._client.detect_text_layers.side_effect = detect
    ready: list[tuple[int, int, object]] = []
    manager.preview_ready.connect(
        lambda _path, page, generation, png: ready.append((page, generation, png))
    )

    first = manager.request_preview(0)
    qtbot.waitUntil(page_zero_entered.is_set)
    second = manager.request_preview(1)
    qtbot.waitUntil(lambda: any(item[0] == 1 for item in ready))
    release_page_zero.set()
    qtbot.waitUntil(lambda: not manager._draining_preview_workers)

    assert second > first
    assert [item[0] for item in ready] == [1]
    assert call_threads and all(ident != main_thread for ident in call_threads)


def test_block_edit_revision_and_reset_run_in_mutate_worker(manager, qtbot):
    main_thread = threading.get_ident()
    call_threads: list[int] = []

    def reset(_sid):
        call_threads.append(threading.get_ident())

    def update(*_args):
        call_threads.append(threading.get_ident())
        return SimpleNamespace(diff=ModelDiff(), extra=None)

    manager._client.reset_cancel.side_effect = reset
    manager._client.update_block_text.side_effect = update
    results: list[dict] = []
    manager.mutate_done.connect(lambda _path, result: results.append(result))

    assert manager.update_page_block_text_async(0, 0, "new")
    qtbot.waitUntil(lambda: manager._mutate_worker is None)

    assert results[-1]["op"] == "update_block_text"
    assert results[-1]["revision"] == 1
    assert manager._preview_generation >= results[-1]["revision"]
    assert call_threads and all(ident != main_thread for ident in call_threads)


def test_deskew_get_model_runs_in_worker_and_returns_diff(manager, qtbot):
    main_thread = threading.get_ident()
    get_model_threads: list[int] = []
    manager._sessions[manager._active_path] = _session(pages=1)
    manager._ocr_service = SimpleNamespace(
        recognize_batch=lambda _images, _options: [SimpleNamespace(preproc_angle=0)]
    )
    manager._client.render_preview.return_value = b"png"

    def get_model(_sid):
        get_model_threads.append(threading.get_ident())
        return PdfDocumentMirror(
            file_path="C:/fake.pdf", pages=[PdfPageInfoMirror(page_index=0)]
        )

    manager._client.get_model.side_effect = get_model

    assert manager.auto_deskew_async([0])
    qtbot.waitUntil(lambda: manager._mutate_worker is None)

    assert get_model_threads and all(
        ident != main_thread for ident in get_model_threads
    )


def test_close_session_is_async_and_does_not_block_gui(manager, qtbot):
    entered = threading.Event()
    release = threading.Event()

    def slow_close(_sid):
        entered.set()
        release.wait(2)

    manager._client.close_session.side_effect = slow_close
    path = manager.active_session.file_path

    assert manager.close_session_async(path)
    qtbot.waitUntil(entered.is_set)
    assert path not in manager.session_paths
    assert_qt_event_loop_responsive(
        qtbot,
        in_flight=lambda: bool(manager._close_workers),
    )

    release.set()
    qtbot.waitUntil(lambda: not manager._close_workers)
