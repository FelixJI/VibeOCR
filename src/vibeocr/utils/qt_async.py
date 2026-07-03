"""Qt-asyncio 集成工具

使用 qasync 库实现 Qt 事件循环与 asyncio 事件循环的集成。
"""

import asyncio
import functools
import logging
import warnings
import weakref
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# 存储异步任务引用，防止垃圾回收
_async_tasks: weakref.WeakSet[asyncio.Task] = weakref.WeakSet()


def _get_running_or_set_loop() -> asyncio.AbstractEventLoop:
    """获取当前线程的事件循环，必要时创建并设为当前循环。

    Python 3.13 起，无参 ``asyncio.get_event_loop()`` 在当前线程没有已设置
    的事件循环时会抛出 ``RuntimeError``（3.10+ 已对其 DeprecationWarning，
    3.12+ 在无 running loop 且无 set 过 loop 时直接报错）。

    生产环境由 ``create_qasync_event_loop`` 提前 ``set_event_loop``，这里仅在
    单元测试等未经过 main.py 初始化的场景兜底：若无当前循环则新建一个，避免
    ``run_coroutine`` / ``AsyncTaskRunner`` 在这些环境下崩溃。
    """
    # 复用当前线程已设置的事件循环（生产环境的 qasync 循环通过
    # ``set_event_loop`` 注册）。3.13 起无循环时 ``get_event_loop()`` 既发
    # DeprecationWarning 又抛 RuntimeError；用 catch_warnings 屏蔽该 warning，
    # 再在 RuntimeError 时新建循环，避免在未经 ``create_qasync_event_loop``
    # 初始化的场景（如单元测试）下崩溃或留下噪音日志。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop


def create_qasync_event_loop(app) -> asyncio.AbstractEventLoop:
    """创建 qasync 事件循环

    Args:
        app: QApplication 实例

    Returns:
        QEventLoop 事件循环
    """
    try:
        import qasync  # type: ignore[import-untyped]

        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
        logger.debug("qasync 事件循环已创建")
        return loop
    except ImportError:
        logger.warning("qasync 未安装，使用标准 asyncio 事件循环")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def run_coroutine(coro: Coroutine, callback: Callable | None = None) -> None:
    """在 Qt 环境中运行协程

    将协程添加到事件循环中执行，可选提供完成回调。

    Args:
        coro: 要执行的协程
        callback: 可选的完成回调函数，接收协程返回值作为参数
    """
    logger.debug("[run_coroutine] 开始执行协程...")
    loop = _get_running_or_set_loop()
    logger.debug(
        f"[run_coroutine] 事件循环: {type(loop).__name__}, running={loop.is_running()}"
    )

    async def wrapped():
        logger.debug("[run_coroutine] wrapped 协程开始执行")
        try:
            result = await coro
            logger.debug("[run_coroutine] 协程执行完成")
            if callback:
                callback(result)
            return result
        except Exception as e:
            logger.error(f"[run_coroutine] 协程执行失败: {e}", exc_info=True)
            raise

    future = asyncio.ensure_future(wrapped(), loop=loop)
    logger.debug(f"[run_coroutine] Future 已创建: {future}")


def async_slot(*types):
    """将异步函数转换为 Qt 槽的装饰器

    使用示例:
        @async_slot()
        async def on_button_clicked(self):
            result = await some_async_operation()
            self.label.setText(result)

    Args:
        *types: 可选的槽参数类型（与 PySide6.Slot 相同）

    Returns:
        装饰后的函数，可作为 Qt 槽使用
    """

    def decorator(async_func: Callable[..., Coroutine]) -> Callable:
        @functools.wraps(async_func)
        def wrapper(*args, **kwargs):
            coro = async_func(*args, **kwargs)
            task = asyncio.ensure_future(coro)
            # 存储引用以防止垃圾回收
            _async_tasks.add(task)
            task.add_done_callback(_async_tasks.discard)

        # 添加 Qt 槽信息（用于 PySide6 元对象系统）
        wrapper.__signature__ = getattr(async_func, "__signature__", None)  # type: ignore[attr-defined]
        wrapper.__annotations__ = getattr(async_func, "__annotations__", {})

        return wrapper

    return decorator


class AsyncTaskRunner:
    """异步任务运行器

    提供便捷的方式来管理和执行异步任务，支持取消和超时。

    使用示例:
        runner = AsyncTaskRunner()
        runner.run(some_async_func(), on_complete=handle_result)
        runner.cancel_all()  # 取消所有运行中的任务
    """

    def __init__(self):
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    def run(
        self,
        coro: Coroutine,
        on_complete: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        timeout: float | None = None,
    ) -> asyncio.Task:
        """运行异步任务

        Args:
            coro: 要执行的协程
            on_complete: 成功完成回调
            on_error: 错误回调
            timeout: 可选超时时间（秒）

        Returns:
            asyncio.Task 对象
        """

        async def wrapped():
            try:
                if timeout:
                    result = await asyncio.wait_for(coro, timeout=timeout)
                else:
                    result = await coro

                if on_complete:
                    on_complete(result)
                return result

            except TimeoutError as e:
                logger.error(f"任务超时: {timeout}s")
                if on_error:
                    on_error(e)
                raise

            except asyncio.CancelledError:
                logger.debug("任务已取消")
                raise

            except Exception as e:
                logger.error(f"任务失败: {e}")
                if on_error:
                    on_error(e)
                raise

            finally:
                # 从任务列表中移除
                task = asyncio.current_task()
                if task in self._tasks:
                    self._tasks.remove(task)

        loop = _get_running_or_set_loop()
        task = loop.create_task(wrapped())
        self._tasks.append(task)
        return task

    def cancel_all(self) -> None:
        """取消所有运行中的任务"""
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()

    @property
    def active_count(self) -> int:
        """获取活动任务数量"""
        return sum(1 for t in self._tasks if not t.done())


# 全局任务运行器实例
_global_runner: AsyncTaskRunner | None = None


def get_async_runner() -> AsyncTaskRunner:
    """获取全局异步任务运行器"""
    global _global_runner
    if _global_runner is None:
        _global_runner = AsyncTaskRunner()
    return _global_runner
