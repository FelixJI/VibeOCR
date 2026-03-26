"""日志服务模块"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from PySide6.QtCore import SignalInstance


@dataclass
class LogEntry:
    """日志条目"""

    timestamp: datetime
    level: str
    message: str
    source: str = "主进程"  # 日志来源：主进程 / Worker-0 / Worker-1 等


class _SignalEmitter(QObject):
    """Qt 信号发射器，与 logging.Handler 分离以避免 emit 方法冲突"""

    log_signal = Signal(object)  # 发射 LogEntry 对象
    status_signal = Signal(str)  # 发射状态栏消息


class QtLogHandler(logging.Handler):
    """将 Python logging 重定向到 Qt 控件的处理器"""

    def __init__(self) -> None:
        super().__init__()
        self._emitter = _SignalEmitter()

    @property
    def log_signal(self) -> "SignalInstance":
        return self._emitter.log_signal

    @property
    def status_signal(self) -> "SignalInstance":
        return self._emitter.status_signal

    def emit(self, record: logging.LogRecord) -> None:
        """处理日志记录"""
        try:
            # 检查信号源是否已被删除（应用程序关闭时可能发生）
            try:
                # 使用信号前检查 QObject 是否有效
                # 如果底层 C++ 对象已被删除，访问任何属性都会失败
                _ = self.log_signal
            except RuntimeError:
                # Signal source has been deleted - 静默忽略
                return

            msg = self.format(record)

            # 解析日志来源
            source = self._parse_source(msg)

            entry = LogEntry(
                timestamp=datetime.fromtimestamp(record.created),
                level=record.levelname,
                message=msg,
                source=source,
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

    def _parse_source(self, msg: str) -> str:
        """解析日志来源

        从日志消息中提取来源标识：
        - [Worker X] -> Worker-X
        - [主进程] -> 主进程
        - 其他 -> 主进程
        """
        import re

        # 匹配 [Worker X] 格式
        worker_match = re.search(r"\[Worker\s+(\d+)\]", msg)
        if worker_match:
            return f"Worker-{worker_match.group(1)}"

        # 匹配 [主进程] 格式
        if "[主进程]" in msg:
            return "主进程"

        # 默认为主进程
        return "主进程"

    def _should_show_in_status(self, msg: str) -> bool:
        """判断日志消息是否应该显示在状态栏"""
        # 只显示与 Worker 启动/初始化相关的日志
        keywords = [
            "[Worker",
            "[主进程]",
            "OCR 服务初始化",
            "连接数据共享内存",
            "READY",
        ]
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
