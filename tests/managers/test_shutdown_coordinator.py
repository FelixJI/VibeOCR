"""测试 ShutdownCoordinator 有序 drain。

按固定顺序 drain 各子系统，避免关闭时后台任务仍在访问已释放的资源。
os._exit 仍保留为 DLL 卸载安全网，但在其之前由本协调器尽力收拢任务。
"""

import time


class TestShutdownCoordinator:
    def test_coordinate_calls_in_order(self):
        """coordinator 按注册顺序调用各子系统的 shutdown"""
        from vibeocr.managers.shutdown_coordinator import ShutdownCoordinator

        coord = ShutdownCoordinator()
        order = []

        coord.register("settings", lambda: order.append("settings"))
        coord.register("pdf", lambda: order.append("pdf"))
        coord.register("subprocess", lambda: order.append("subprocess"))
        coord.register("async_runner", lambda: order.append("async_runner"))

        result = coord.coordinate(timeout_ms=3000)

        assert result is True
        assert order == ["settings", "pdf", "subprocess", "async_runner"]

    def test_coordinate_returns_false_on_timeout(self):
        """某子系统超时，coordinator 返回 False 但继续后续"""
        from vibeocr.managers.shutdown_coordinator import ShutdownCoordinator

        coord = ShutdownCoordinator()
        coord.register("slow", lambda: time.sleep(2))
        coord.register("fast", lambda: None)

        result = coord.coordinate(timeout_ms=100)

        # 即使 slow 超时，也返回 False（非完全成功）
        assert result is False

    def test_coordinate_continues_after_exception(self):
        """某子系统抛异常，coordinator 记录但继续后续"""
        from vibeocr.managers.shutdown_coordinator import ShutdownCoordinator

        coord = ShutdownCoordinator()
        order = []

        def boom():
            raise RuntimeError("boom")

        coord.register("crash", boom)
        coord.register("after", lambda: order.append("after"))

        result = coord.coordinate(timeout_ms=1000)

        assert result is False  # 有异常
        assert order == ["after"]  # 后续仍执行

    def test_coordinate_empty_returns_true(self):
        """无注册步骤时返回 True"""
        from vibeocr.managers.shutdown_coordinator import ShutdownCoordinator

        coord = ShutdownCoordinator()
        assert coord.coordinate(timeout_ms=1000) is True

    def test_coordinate_per_step_timeout(self):
        """每个步骤有独立超时（总超时均分）"""
        from vibeocr.managers.shutdown_coordinator import ShutdownCoordinator

        coord = ShutdownCoordinator()
        coord.register("a", lambda: None)
        coord.register("b", lambda: None)
        coord.register("c", lambda: None)

        # 总超时 300ms，3 步各 100ms
        result = coord.coordinate(timeout_ms=300)
        assert result is True
