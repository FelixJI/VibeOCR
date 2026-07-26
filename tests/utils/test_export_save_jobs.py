"""共享 Export/Save QThread 作业的生命周期与线程边界测试。"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Slot

from tests.qt_responsiveness import assert_qt_event_loop_responsive
from vibeocr.utils.export_jobs import (
    BatchExportReport,
    ExportItem,
    ExportSaveJob,
    export_batch_operation,
)


def test_slow_job_keeps_qt_event_loop_responsive_and_callbacks_on_gui_thread(
    qapp, qtbot
):
    started = threading.Event()
    release = threading.Event()
    operation_threads: list[QThread] = []
    callback_threads: list[QThread] = []

    def slow_operation(_cancel, _progress):
        operation_threads.append(QThread.currentThread())
        started.set()
        release.wait(timeout=2)
        return "ok"

    class Receiver(QObject):
        @Slot(object)
        def completed(self, _result):
            callback_threads.append(QThread.currentThread())

    receiver = Receiver()
    job = ExportSaveJob(slow_operation)
    job.completed.connect(receiver.completed)
    job.start()
    qtbot.waitUntil(started.is_set, timeout=1000)

    assert_qt_event_loop_responsive(qtbot, in_flight=job.isRunning)
    release.set()
    qtbot.waitUntil(lambda: not job.isRunning(), timeout=2000)
    qtbot.waitUntil(lambda: bool(callback_threads), timeout=1000)

    assert operation_threads[0] is not qapp.thread()
    assert callback_threads == [qapp.thread()]
    assert job.drain(0)


def test_batch_n_items_reserves_duplicate_names_and_reports_failures(
    qapp, qtbot, tmp_path
):
    calls: list[Path] = []

    class ExportClient:
        def export_ocr_sync(self, _payload, *, output_path, **_kwargs):
            path = Path(output_path)
            calls.append(path)
            if path.stem.endswith("_1"):
                return {}
            path.write_text("ok", encoding="utf-8")
            return {"output_path": str(path)}

    items = (
        ExportItem("same.png", {}, tmp_path, "txt"),
        ExportItem("same.jpg", {}, tmp_path, "txt"),
        ExportItem("other.png", {}, tmp_path, "txt"),
    )
    progress: list[tuple[int, int, str]] = []
    results: list[BatchExportReport] = []
    job = ExportSaveJob(export_batch_operation(ExportClient(), items))
    job.progress.connect(lambda *args: progress.append(args))
    job.completed.connect(results.append)
    job.start()
    qtbot.waitUntil(lambda: not job.isRunning(), timeout=2000)
    qtbot.waitUntil(lambda: bool(results), timeout=1000)

    assert [path.name for path in calls] == ["same.txt", "same_1.txt", "other.txt"]
    assert progress == [
        (1, 3, "same.png"),
        (2, 3, "same.jpg"),
        (3, 3, "other.png"),
    ]
    assert results[0].success_count == 2
    assert results[0].fail_count == 1
    assert [item.actual_path.name for item in results[0].renamed] == ["same_1.txt"]


def test_cancel_and_bounded_drain_ignore_slow_result(qapp, qtbot):
    started = threading.Event()
    release = threading.Event()
    completed: list[object] = []
    cancelled: list[bool] = []

    def slow_operation(_cancel, _progress):
        started.set()
        release.wait(timeout=2)
        return "late"

    job = ExportSaveJob(slow_operation)
    job.completed.connect(completed.append)
    job.cancelled.connect(lambda: cancelled.append(True))
    job.start()
    qtbot.waitUntil(started.is_set, timeout=1000)
    job.cancel()
    assert not job.drain(1)
    release.set()
    assert job.drain(2000)
    qtbot.waitUntil(lambda: bool(cancelled), timeout=1000)
    assert completed == []
    assert job.status == ExportSaveJob.STATUS_CANCELLED


def test_uncaught_failure_has_error_and_terminal_callback(qapp, qtbot):
    failures: list[str] = []

    def broken(_cancel, _progress):
        raise OSError("disk full")

    job = ExportSaveJob(broken)
    job.failed.connect(failures.append)
    job.start()
    qtbot.waitUntil(lambda: not job.isRunning(), timeout=1000)
    qtbot.waitUntil(lambda: bool(failures), timeout=1000)
    assert failures == ["disk full"]
    assert job.status == ExportSaveJob.STATUS_FAILED
    assert job.error_message == "disk full"
