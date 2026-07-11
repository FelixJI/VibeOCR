"""单实例守卫（QLocalServer / QLocalSocket 实现）

确保同一时刻只有一个 VibeOCR 主进程在运行。第二个实例启动时通过本地 socket
通知已运行实例"把主窗口提到前台"，随后自身静默退出；避免重复进程各自拉起
OCR 子进程、WebEngine、nvidia-smi 探测等重资源。

实现要点：
- Windows 下 QLocalServer 由 Qt 用命名管道实现，无残留 socket 文件；
  ``QLocalServer.removeServer`` 仍调用作跨平台清理（Unix 下清理残留文件）。
- 必须在 ``QApplication`` 创建之后调用（QLocalServer 依赖 Qt 事件循环分发
  ``newConnection``）。
- socket 名固定为 ``VibeOCR``（不绑版本），保证升级后新旧版本互认同为同一应用。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

# 第二实例通知主实例的指令载荷：要求把主窗口提到前台。
# 预留为字节常量，便于将来扩展（如带文件路径打开）。
_CMD_RAISE = b"RAISE"

# 服务端确认字节：读完客户端指令后回写，客户端据此确认服务端已收到再退出，
# 避免客户端提前断开导致服务端读不到数据（不依赖时间猜测）。
_ACK = b"K"

# 连接/读写等待超时（毫秒）。第二实例退出路径不应长时间阻塞。
_TIMEOUT_MS = 1000


class SingleInstanceGuard(QObject):
    """QLocalServer/QLocalSocket 单实例守卫。

    用法::

        guard = SingleInstanceGuard("VibeOCR")
        if not guard.try_lock():
            # 已有实例在运行，本实例退出
            return 0
        # 本实例为主，连接 raise_requested 到窗口恢复逻辑
        guard.raise_requested.connect(window.bring_to_front)
    """

    # 收到第二实例的"提到前台"请求时发射（主线程）。
    raise_requested = Signal()

    def __init__(self, app_id: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app_id = app_id
        self._server: QLocalServer | None = None

    def try_lock(self) -> bool:
        """尝试成为主实例。

        Returns:
            True  —— 本实例成功占位（成为主实例），应继续启动；
            False —— 已有实例在运行（本实例已通知其提到前台），应静默退出。
        """
        # 1) 先尝试连接已运行实例。能连上说明已有主实例。
        socket = QLocalSocket()
        socket.connectToServer(self._app_id)
        if socket.waitForConnected(_TIMEOUT_MS):
            # 已有实例：发送 RAISE 指令，等服务端回写 ACK 确认收到后再退出。
            # 用 ACK 闭环而非定时等待——避免客户端提前断开导致服务端读不到数据。
            socket.write(_CMD_RAISE)
            socket.flush()
            socket.waitForBytesWritten(_TIMEOUT_MS)
            # 阻塞等待服务端 ACK（最多 _TIMEOUT_MS）；超时也直接退出，
            # 不阻断第二实例退出（服务端可能在忙，但指令字节已入 OS 缓冲）。
            socket.waitForReadyRead(_TIMEOUT_MS)
            socket.disconnectFromServer()
            logger.debug("[SingleInstance] 检测到已运行实例，已通知其提到前台，本实例退出")
            return False

        # 2) 无运行实例：清理可能残留的 socket（上次崩溃未释放），再创建 server。
        #    Windows 用命名管道，removeServer 为空操作；Unix 清理 socket 文件。
        QLocalServer.removeServer(self._app_id)
        self._server = QLocalServer()
        if not self._server.listen(self._app_id):
            logger.warning(
                f"[SingleInstance] 创建本地服务失败: {self._server.errorString()}"
            )
            # 监听失败不阻断启动——退化为允许多实例（宁可重复启动也不启动不了）。
            self._server = None
            return True

        self._server.newConnection.connect(self._on_new_connection)
        logger.debug("[SingleInstance] 已成为主实例，监听本地服务")
        return True

    def _on_new_connection(self) -> None:
        """主实例收到第二实例连接：读取指令并处理。"""
        if self._server is None:
            return
        conn = self._server.nextPendingConnection()
        if conn is None:
            return
        # 读取指令（带超时，避免恶意/异常连接挂起主线程）。
        # 持有 conn 引用防止过早 GC，连接在本回调结束后由 Qt 回收。
        if conn.waitForReadyRead(_TIMEOUT_MS):
            data = bytes(conn.readAll())  # type: ignore[arg-type]
            # 回写 ACK 让客户端确认已收到，再断开。
            conn.write(_ACK)
            conn.flush()
            conn.waitForBytesWritten(_TIMEOUT_MS)
            if data == _CMD_RAISE:
                self.raise_requested.emit()
        conn.disconnectFromServer()
