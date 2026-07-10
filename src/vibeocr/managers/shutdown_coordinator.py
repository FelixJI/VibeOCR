"""应用级关闭协调器。

按固定顺序 drain 各子系统，避免关闭时后台任务仍在访问已释放的资源。
os._exit 仍保留为 DLL 卸载安全网（规避 QtWebEngine/Paddle DLL 卸载崩溃
0xC0000409），但在其之前由本协调器尽力收拢任务。

使用方式::

    coord = ShutdownCoordinator()
    coord.register("settings", lambda: settings_controller.shutdown())
    coord.register("pdf", lambda: pdf_tab.shutdown())
    coord.register("async_runner", lambda: get_async_runner().cancel_all())
    coord.register("subprocess", lambda: subprocess_manager.shutdown(timeout_ms=2000))
    coord.coordinate(timeout_ms=5000)
"""

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class ShutdownCoordinator:
    """有序关闭协调器。

    注册顺序即为关闭顺序。每个步骤有界执行（总超时均分），超时不阻塞后续。
    """

    def __init__(self) -> None:
        self._steps: list[tuple[str, Callable[[], None]]] = []

    def register(self, name: str, shutdown_fn: Callable[[], None]) -> None:
        """注册一个关闭步骤。

        Args:
            name: 步骤名（日志用）
            shutdown_fn: 无参可调用，执行该子系统关闭
        """
        self._steps.append((name, shutdown_fn))

    def coordinate(self, timeout_ms: int = 5000) -> bool:
        """按注册顺序执行所有关闭步骤。

        每个步骤在独立线程中执行，有独立超时（总超时均分）。
        超时或异常不阻塞后续步骤。

        Returns:
            True 表示全部步骤成功完成，False 表示至少一个超时或异常。
        """
        if not self._steps:
            return True

        all_ok = True
        per_step_timeout = timeout_ms // len(self._steps)

        for name, fn in self._steps:
            done = threading.Event()
            exc_holder: list[BaseException | None] = [None]

            def run(f=fn, h=exc_holder):
                try:
                    f()
                except Exception as e:
                    h[0] = e
                finally:
                    done.set()

            t = threading.Thread(target=run, daemon=True)
            t.start()
            if not done.wait(timeout=per_step_timeout / 1000):
                logger.warning(
                    f"[ShutdownCoordinator] {name} 超时（{per_step_timeout}ms）"
                )
                all_ok = False
            elif exc_holder[0] is not None:
                logger.error(
                    f"[ShutdownCoordinator] {name} 异常: {exc_holder[0]}",
                    exc_info=exc_holder[0],
                )
                all_ok = False
            else:
                logger.debug(f"[ShutdownCoordinator] {name} 已关闭")

        return all_ok
