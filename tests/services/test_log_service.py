import logging
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


def test_setup_logging_uses_jsonl_file_formatter():
    from logging.handlers import RotatingFileHandler

    from vibeocr.logging_context import JsonLogFormatter
    from vibeocr.services.log_service import setup_logging

    setup_logging()
    root_logger = logging.getLogger()
    try:
        file_handler = next(
            h for h in root_logger.handlers if isinstance(h, RotatingFileHandler)
        )
        assert isinstance(file_handler.formatter, JsonLogFormatter)
    finally:
        for item in root_logger.handlers[:]:
            root_logger.removeHandler(item)
            item.close()
