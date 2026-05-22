"""日志服务模块"""

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from PySide6.QtCore import SignalInstance


class _SignalEmitter(QObject):
    """Qt 信号发射器，与 logging.Handler 分离以避免 emit 方法冲突"""

    status_signal = Signal(str)  # 发射状态栏消息


class QtLogHandler(logging.Handler):
    """将 Python logging 重定向到状态栏的处理器"""

    def __init__(self) -> None:
        super().__init__()
        self._emitter = _SignalEmitter()

    @property
    def status_signal(self) -> "SignalInstance":
        return self._emitter.status_signal

    def emit(self, record: logging.LogRecord) -> None:
        """处理日志记录"""
        try:
            try:
                _ = self.status_signal
            except RuntimeError:
                return

            msg = self.format(record)

            if self._should_show_in_status(msg):
                self.status_signal.emit(msg)
        except RuntimeError:
            pass
        except Exception:
            self.handleError(record)

    def _should_show_in_status(self, msg: str) -> bool:
        """判断日志消息是否应该显示在状态栏"""
        keywords = [
            "[OCR 启动]",
            "READY",
            "OCR 服务已就绪",
            "OCR功能已就绪",
            "OCR 服务启动失败",
            "正在启动 OCR 服务",
        ]
        return any(kw in msg for kw in keywords)


def _cleanup_old_logs(log_dir: Path, max_age_days: int = 7) -> None:
    """删除超过指定天数的旧日志文件"""
    cutoff = time.time() - max_age_days * 86400
    for f in log_dir.iterdir():
        if f.is_file() and f.name != "vibeocr.log" and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def setup_logging() -> QtLogHandler:
    """配置全局日志处理器

    Returns:
        QtLogHandler 实例
    """
    handler = QtLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))

    # 根日志器设为 DEBUG，由各 handler 自行过滤
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 清除已有的 handler（某些库 import 时会调用 basicConfig 添加默认 handler，
    # 导致同一消息被输出两次，格式分别为 LEVEL:name:msg 和 [LEVEL] name: msg）
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)

    root_logger.addHandler(handler)

    # 控制台 handler：仅 WARNING 及以上（不刷屏，只显示需要关注的问题）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(
        logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    )
    root_logger.addHandler(console_handler)

    # 文件 handler：DEBUG 及以上（全量记录，便于排查）
    log_dir = Path(__file__).resolve().parent.parent.parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    _cleanup_old_logs(log_dir)

    file_handler = RotatingFileHandler(
        log_dir / "vibeocr.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root_logger.addHandler(file_handler)

    return handler
