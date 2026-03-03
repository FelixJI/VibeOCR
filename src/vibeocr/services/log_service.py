"""日志服务模块"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, Signal


@dataclass
class LogEntry:
    """日志条目"""

    timestamp: datetime
    level: str
    message: str


class QtLogHandler(logging.Handler, QObject):
    """将 Python logging 重定向到 Qt 控件的处理器"""

    log_signal = Signal(object)  # 发射 LogEntry 对象

    def __init__(self) -> None:
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:
        """处理日志记录"""
        try:
            msg = self.format(record)
            entry = LogEntry(
                timestamp=datetime.fromtimestamp(record.created),
                level=record.levelname,
                message=msg,
            )
            self.log_signal.emit(entry)
        except Exception:
            self.handleError(record)


def setup_logging(console_callback: Callable[[object], None]) -> QtLogHandler:
    """配置全局日志处理器

    Args:
        console_callback: 接收 LogEntry 的回调函数

    Returns:
        QtLogHandler 实例
    """
    handler = QtLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.log_signal.connect(console_callback)

    # 配置根日志器
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    return handler
