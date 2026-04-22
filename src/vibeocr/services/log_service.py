"""日志服务模块"""

import logging
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
            "[Worker",
            "[主进程]",
            "OCR 服务初始化",
            "连接数据共享内存",
            "READY",
        ]
        return any(kw in msg for kw in keywords)


def setup_logging() -> QtLogHandler:
    """配置全局日志处理器

    Returns:
        QtLogHandler 实例
    """
    handler = QtLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))

    # 配置根日志器
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    # 添加文件日志
    log_dir = Path(__file__).resolve().parent.parent.parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(
        log_dir / "vibeocr.log", encoding="utf-8", delay=True
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root_logger.addHandler(file_handler)

    return handler
