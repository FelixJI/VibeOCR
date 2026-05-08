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
