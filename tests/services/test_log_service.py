import logging
import sys
import time


def test_cleanup_old_logs_deletes_old_files(tmp_path):
    from vibeocr.services.log_service import _cleanup_old_logs

    old_file = tmp_path / "vibeocr.log.3"
    old_file.write_text("old log")

    recent_file = tmp_path / "vibeocr.log.1"
    recent_file.write_text("recent log")

    current_file = tmp_path / "vibeocr.log"
    current_file.write_text("current log")

    # 将旧文件的 mtime 设为 8 天前
    eight_days_ago = time.time() - 8 * 86400
    import os

    os.utime(old_file, (eight_days_ago, eight_days_ago))

    _cleanup_old_logs(tmp_path, max_age_days=7)

    assert not old_file.exists()
    assert recent_file.exists()
    assert current_file.exists()


def test_cleanup_old_logs_skips_current_log(tmp_path):
    from vibeocr.services.log_service import _cleanup_old_logs

    current_file = tmp_path / "vibeocr.log"
    current_file.write_text("current log")

    # 即使 vibeocr.log 很老也不删除
    eight_days_ago = time.time() - 8 * 86400
    import os

    os.utime(current_file, (eight_days_ago, eight_days_ago))

    _cleanup_old_logs(tmp_path, max_age_days=7)

    assert current_file.exists()


def test_cleanup_old_logs_empty_dir(tmp_path):
    from vibeocr.services.log_service import _cleanup_old_logs

    _cleanup_old_logs(tmp_path, max_age_days=7)

    assert list(tmp_path.iterdir()) == []


def test_setup_logging_creates_rotating_handler():
    from logging.handlers import RotatingFileHandler

    from vibeocr.services.log_service import setup_logging

    handler = setup_logging()

    root_logger = logging.getLogger()
    rotating_handlers = [
        h for h in root_logger.handlers if isinstance(h, RotatingFileHandler)
    ]
    assert len(rotating_handlers) == 1
    assert rotating_handlers[0].maxBytes == 10 * 1024 * 1024
    assert rotating_handlers[0].backupCount == 3

    # 清理：移除刚添加的 handlers 避免影响其他测试
    root_logger.removeHandler(handler)
    for h in rotating_handlers:
        h.close()
        root_logger.removeHandler(h)


def test_setup_logging_silences_noisy_third_party_loggers():
    """setup_logging 应将 fontTools/paddle 等噪声库降到 WARNING，避免刷屏。"""
    from vibeocr.services.log_service import setup_logging

    handler = setup_logging()
    try:
        # vibeocr 自身仍可保持 DEBUG
        assert logging.getLogger("vibeocr").getEffectiveLevel() <= logging.DEBUG
        # 已知的噪声库降到 WARNING
        for name in ("fontTools", "paddle", "paddlex", "urllib3"):
            assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING
    finally:
        root_logger = logging.getLogger()
        root_logger.removeHandler(handler)
        for h in [
            h for h in root_logger.handlers if not isinstance(h, logging.RootLogger)
        ]:
            root_logger.removeHandler(h)


def test_qt_log_handler_requires_explicit_ui_status(qapp):
    from vibeocr.services.log_service import QtLogHandler

    handler = QtLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    emitted: list[str] = []
    handler.status_signal.connect(emitted.append)

    keyword_only = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "OCR 服务已就绪", (), None
    )
    handler.emit(keyword_only)
    explicit = logging.LogRecord(
        "test", logging.INFO, __file__, 2, "model ready", (), None
    )
    explicit.ui_status = True
    handler.emit(explicit)

    assert emitted == ["model ready"]


def test_setup_logging_uses_human_readable_file_formatter():
    from logging.handlers import RotatingFileHandler

    from vibeocr.services.log_service import HumanReadableFormatter, setup_logging

    setup_logging()
    root_logger = logging.getLogger()
    try:
        file_handler = next(
            h for h in root_logger.handlers if isinstance(h, RotatingFileHandler)
        )
        assert isinstance(file_handler.formatter, HumanReadableFormatter)
    finally:
        for item in root_logger.handlers[:]:
            root_logger.removeHandler(item)
            item.close()


def test_human_readable_formatter_basic_line():
    from vibeocr.services.log_service import HumanReadableFormatter

    formatter = HumanReadableFormatter()
    record = logging.LogRecord(
        name="vibeocr.views.main_window",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="应用启动",
        args=(),
        exc_info=None,
    )
    record.created = time.mktime(time.strptime("2026-07-22 14:32:01", "%Y-%m-%d %H:%M:%S"))
    record.msecs = 123

    line = formatter.format(record)
    assert line == "2026-07-22 14:32:01.123 INFO  vibeocr.views.main_window: 应用启动"


def test_human_readable_formatter_appends_context_fields():
    from vibeocr.services.log_service import HumanReadableFormatter

    formatter = HumanReadableFormatter()
    record = logging.LogRecord(
        name="vibeocr.workers.ocr_worker",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="OCR 失败",
        args=(),
        exc_info=None,
    )
    record.created = time.mktime(time.strptime("2026-07-22 14:32:02", "%Y-%m-%d %H:%M:%S"))
    record.msecs = 456
    # 模拟 forward_worker_output_line 注入的字段
    record.request_id = "req-1"
    record.task_id = "task-7"
    record.worker_context = {"page": 3, "elapsed_ms": 1200}

    line = formatter.format(record)
    assert line.startswith(
        "2026-07-22 14:32:02.456 ERROR vibeocr.workers.ocr_worker: OCR 失败  ["
    )
    # 上下文字段全部追加在行尾的方括号内
    assert "request_id=req-1" in line
    assert "task_id=task-7" in line
    assert "page=3" in line
    assert "elapsed_ms=1200" in line


def test_human_readable_formatter_appends_traceback():
    from vibeocr.services.log_service import HumanReadableFormatter

    formatter = HumanReadableFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="vibeocr.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="出错了",
        args=(),
        exc_info=exc_info,
    )
    record.created = time.time()

    line = formatter.format(record)
    assert "ValueError: boom" in line
    assert "\nTraceback (most recent call last):" in line
