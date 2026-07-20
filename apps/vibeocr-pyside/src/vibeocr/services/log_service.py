"""日志服务模块"""

import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from vibeocr.logging_context import JsonLogFormatter, ui_status_extra

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
            if self._should_show_in_status(record):
                self.status_signal.emit(msg)
        except RuntimeError:
            pass
        except Exception:
            self.handleError(record)

    def _should_show_in_status(self, record: logging.LogRecord) -> bool:
        """状态栏只接收调用方显式标记的日志。"""
        return bool(getattr(record, "ui_status", False))


def log_ui_status(
    logger: logging.Logger,
    message: str,
    *args,
    level: int = logging.INFO,
    **context,
) -> None:
    """记录一条显式的状态栏消息，不依赖文本关键词。"""
    logger.log(level, message, *args, extra=ui_status_extra(**context))


def _cleanup_old_logs(log_dir: Path, max_age_days: int = 7) -> None:
    """删除超过指定天数的旧日志文件"""
    cutoff = time.time() - max_age_days * 86400
    for f in log_dir.iterdir():
        if f.is_file() and f.name != "vibeocr.log" and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def _coerce_level(level: int | str) -> int:
    if isinstance(level, int):
        return level
    value = logging.getLevelNamesMapping().get(str(level).upper())
    return value if isinstance(value, int) else logging.INFO


def apply_log_level(level: int | str) -> int:
    """立即调整本进程日志级别，并传递给后续启动的 WorkerHost。"""
    effective_level = _coerce_level(level)
    os.environ["VIBEOCR_LOG_LEVEL"] = logging.getLevelName(effective_level)
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            handler.setLevel(effective_level)
    return effective_level


def setup_logging(level: int | str = logging.INFO) -> QtLogHandler:
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

    # 控制台 handler：开发环境 DEBUG，打包环境 WARNING
    console_handler = logging.StreamHandler()
    console_handler.setLevel(
        logging.DEBUG if not getattr(sys, "frozen", False) else logging.WARNING
    )
    console_handler.setFormatter(
        logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    )
    root_logger.addHandler(console_handler)

    # 文件 handler：DEBUG 及以上（全量记录，便于排查）
    # 日志目录跟随 project_root（打包态在 exe 同级，开发态在仓库根），
    # 与 env_manager.get_project_root 保持一致，避免硬编码层级偏差。
    from vibeocr.env_manager import get_project_root

    log_dir = get_project_root() / "logs"
    log_dir.mkdir(exist_ok=True)
    _cleanup_old_logs(log_dir)

    file_handler = RotatingFileHandler(
        log_dir / "vibeocr.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setLevel(_coerce_level(level))
    file_handler.setFormatter(JsonLogFormatter(frontend="pyside", profile="production"))
    root_logger.addHandler(file_handler)

    # 第三方库降噪：根日志器是 DEBUG，若不显式降级，fontTools/paddle/urllib3 等
    # 库的 INFO/DEBUG 会大量混入（如 PDF 渲染时 fontTools.subset 的逐字形日志）。
    # 仅 vibeocr.* 保持 DEBUG 全量记录；以下库降到 WARNING。
    _noisy_loggers = (
        "fontTools",
        "PIL",
        "paddle",
        "paddlex",
        "paddleocr",
        "urllib3",
        "matplotlib",
        "huggingface_hub",
        "filelock",
        "asyncio",
        # 更新检查走 qasync+httpx/httpcore，DEBUG 级会刷出大量 IO 轮询日志
        # （每读一个 64KB chunk 打两行 poll/event），把真正有用的 INFO 淹没。
        "qasync",
        "httpcore",
        "httpx",
    )
    for name in _noisy_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)

    apply_log_level(level)

    return handler
