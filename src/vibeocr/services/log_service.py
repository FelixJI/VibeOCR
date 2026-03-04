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
    status_signal = Signal(str)  # 发射状态栏消息

    def __init__(self) -> None:
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:
        """处理日志记录"""
        try:
            # 检查信号源是否已被删除（应用程序关闭时可能发生）
            try:
                # 使用信号前检查 QObject 是否有效
                # 如果底层 C++ 对象已被删除，访问任何属性都会失败
                _ = self.log_signal  # noqa: F841
            except RuntimeError:
                # Signal source has been deleted - 静默忽略
                return

            msg = self.format(record)
            entry = LogEntry(
                timestamp=datetime.fromtimestamp(record.created),
                level=record.levelname,
                message=msg,
            )
            self.log_signal.emit(entry)

            # 发射状态栏消息（用于显示 Worker 节点输出）
            # 过滤出与 Worker 启动/预加载相关的日志
            if self._should_show_in_status(msg):
                self.status_signal.emit(msg)
        except RuntimeError:
            # 捕获 "Signal source has been deleted" 错误
            # 这通常发生在应用程序关闭时后台线程仍在记录日志
            pass
        except Exception:
            self.handleError(record)

    def _should_show_in_status(self, msg: str) -> bool:
        """判断日志消息是否应该显示在状态栏"""
        # 只显示与 Worker 启动/初始化相关的日志
        keywords = ["[Worker", "[主进程]", "OCR 服务初始化", "连接数据共享内存", "READY"]
        return any(kw in msg for kw in keywords)


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
