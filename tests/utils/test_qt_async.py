"""测试 qt_async 模块的 run_coroutine / AsyncTaskRunner 超时与任务管理"""

import asyncio

import pytest

from vibeocr.utils.qt_async import (
    AsyncTaskRunner,
    get_async_runner,
    run_coroutine,
)


def _run_loop_until_complete(coro, timeout=2.0):
    """在新事件循环上同步运行协程,带整体超时保护(测试辅助)"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
    finally:
        loop.close()


class TestAsyncTaskRunner:
    """AsyncTaskRunner 行为测试"""

    def test_run_with_result(self):
        """无超时:正常完成返回结果,触发 on_complete"""

        async def coro():
            await asyncio.sleep(0.01)
            return 42

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runner = AsyncTaskRunner()
            captured = []

            async def driver():
                task = runner.run(
                    coro(), on_complete=lambda r: captured.append(r)
                )
                await task

            loop.run_until_complete(asyncio.wait_for(driver(), timeout=2.0))
            assert captured == [42]
            assert runner.active_count == 0
        finally:
            loop.close()

    def test_run_with_timeout_raises(self):
        """有超时:协程慢于 timeout 时抛 TimeoutError,触发 on_error"""

        async def slow_coro():
            await asyncio.sleep(10)
            return "done"

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runner = AsyncTaskRunner()
            errors = []

            async def driver():
                task = runner.run(
                    slow_coro(),
                    on_error=lambda e: errors.append(e),
                    timeout=0.05,
                )
                with pytest.raises(TimeoutError):
                    await task

            loop.run_until_complete(asyncio.wait_for(driver(), timeout=2.0))
            assert len(errors) == 1
            assert isinstance(errors[0], TimeoutError)
        finally:
            loop.close()

    def test_cancel_all_clears_tasks(self):
        """cancel_all 取消所有运行中任务"""

        async def long_coro():
            await asyncio.sleep(10)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            runner = AsyncTaskRunner()

            async def driver():
                t1 = runner.run(long_coro())
                t2 = runner.run(long_coro())
                assert runner.active_count == 2
                runner.cancel_all()
                # cancel 是异步的,await 让取消真正生效(会抛 CancelledError,忽略)
                import contextlib

                for t in (t1, t2):
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await t
                assert all(t.done() for t in (t1, t2))

            loop.run_until_complete(asyncio.wait_for(driver(), timeout=2.0))
        finally:
            loop.close()


class TestRunCoroutine:
    """run_coroutine 函数测试"""

    def test_run_coroutine_accepts_timeout_param(self):
        """run_coroutine 接受 timeout 关键字参数(委托 AsyncTaskRunner)"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # run_coroutine 不阻塞,仅调度;在循环上跑一下让它完成
            ran = []

            async def quick():
                ran.append(True)
                return "ok"

            run_coroutine(quick(), timeout=1.0)

            # 让循环处理一个 tick
            async def pump():
                await asyncio.sleep(0.05)

            loop.run_until_complete(asyncio.wait_for(pump(), timeout=2.0))
            assert ran == [True]
        finally:
            loop.close()

    def test_run_coroutine_timeout_triggers(self):
        """run_coroutine 的 timeout 真的能让慢协程超时"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            timed_out = []

            async def slow():
                await asyncio.sleep(10)

            # 用 on_error 捕获超时(run_coroutine 无返回值)
            run_coroutine(slow(), timeout=0.05)

            # 注入一个 on_error 不便(签名限制),改为直接观察全局 runner 的任务
            runner = get_async_runner()

            async def pump():
                # 等足够久让 timeout 触发
                await asyncio.sleep(0.2)
                # 等所有 pending 任务完成(它们应因 timeout 而完成)
                for t in list(runner._tasks):
                    with contextlib_suppress():
                        await t

            # 由于 run_coroutine 默认无 on_error,超时会 log + raise(在 task 上下文)
            # 我们只验证任务最终 done 且 active_count 归零
            loop.run_until_complete(asyncio.wait_for(pump(), timeout=2.0))
            # 任务应已完成(无论成功或异常)
            for t in runner._tasks:
                assert t.done()
        finally:
            loop.close()


def contextlib_suppress():
    """返回 contextlib.suppress(Exception),避免顶部 import 污染"""
    import contextlib

    return contextlib.suppress(Exception)
