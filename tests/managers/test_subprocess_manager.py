"""SubprocessManager 测试"""

import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from vibeocr.managers.subprocess_manager import (
    PreloadSignals,
    PreloadTask,
    SubprocessManager,
    SubprocessStartSignals,
    SubprocessStartTask,
    WorkerHostStartTask,
)


class TestSubprocessStartSignals:
    """SubprocessStartSignals 测试"""

    def test_signals_exist(self, qapp):
        """测试信号存在"""
        signals = SubprocessStartSignals()
        assert hasattr(signals, "started")
        assert hasattr(signals, "progress")


class TestSubprocessStartTask:
    """SubprocessStartTask 测试"""

    def test_task_creation(self):
        """测试任务创建"""
        task = SubprocessStartTask(Path("/tmp"), use_gpu=True)
        assert task._use_gpu is True
        assert task._cancelled is False
        assert task.signals is not None

    def test_task_cancel(self):
        """测试任务取消"""
        task = SubprocessStartTask(Path("/tmp"))
        task.cancel()
        assert task._cancelled is True


class TestWorkerHostStartTask:
    def test_cancel_uses_thread_safe_event(self):
        task = WorkerHostStartTask()
        task.cancel()
        assert task._cancelled.is_set()


class TestPreloadSignals:
    """PreloadSignals 测试"""

    def test_signals_exist(self, qapp):
        """测试信号存在"""
        signals = PreloadSignals()
        assert hasattr(signals, "finished")


class TestPreloadTask:
    """PreloadTask 测试"""

    def test_task_creation(self):
        """测试任务创建"""
        mock_service = Mock()
        task = PreloadTask(mock_service, ["OCR", "PP-StructureV3"])
        assert task._pipelines == ["OCR", "PP-StructureV3"]
        assert task.signals is not None

    def test_task_has_cancel_event(self):
        """PreloadTask 应有协作取消事件（threading.Event）。"""
        import threading

        mock_service = Mock()
        task = PreloadTask(mock_service, ["OCR"])
        assert hasattr(task, "_cancelled")
        assert isinstance(task._cancelled, threading.Event)
        assert task._cancelled.is_set() is False

    def test_task_cancel_sets_event(self):
        """cancel() 应设置取消事件。"""
        mock_service = Mock()
        task = PreloadTask(mock_service, ["OCR"])
        task.cancel()
        assert task._cancelled.is_set() is True

    def test_task_run_checks_cancel_between_pipelines(self):
        """run() 应在每个管道之间检查取消事件，取消后跳过剩余管道。"""
        mock_service = Mock()
        # 让 preload_pipelines 模拟耗时（但不实际阻塞）
        mock_service.preload_pipelines.return_value = {"OCR": True}
        task = PreloadTask(mock_service, ["OCR", "Table", "Formula"])
        # 预先取消
        task.cancel()
        task.run()
        # 取消后不应调用任何 preload（在第一个管道前就退出）
        mock_service.preload_pipelines.assert_not_called()

    def test_task_run_clears_service_ref_after_finish(self):
        """任务结束后应清零 service 引用（避免延迟 signal 访问已销毁 UI）。"""
        mock_service = Mock()
        mock_service.preload_pipelines.return_value = {"OCR": True}
        mock_service.warmup_pipelines.return_value = {"OCR": True}
        task = PreloadTask(mock_service, ["OCR"])
        task.run()
        # run 完成后引用清零
        assert task._service is None


class TestSubprocessManager:
    """SubprocessManager 测试"""

    @pytest.fixture
    def manager(self, qapp, tmp_path):
        """创建 SubprocessManager 实例"""
        mgr = SubprocessManager(tmp_path)
        yield mgr
        # 清理：断开信号连接并关闭
        try:
            if mgr._start_task is not None:
                mgr._start_task.signals.started.disconnect()
                mgr._start_task.signals.progress.disconnect()
                mgr._start_task = None
            mgr.shutdown(timeout_ms=100)
        except RuntimeError:
            pass  # 信号已断开

    def test_manager_creation(self, manager):
        """测试管理器创建"""
        assert manager._service is None
        assert manager.is_ready is False

    def test_manager_has_required_signals(self, manager):
        """测试管理器有必需的信号"""
        assert hasattr(manager, "service_ready")
        assert hasattr(manager, "progress_update")
        assert hasattr(manager, "preload_finished")
        assert hasattr(manager, "preload_progress")

    def test_service_property(self, manager):
        """测试服务属性"""
        assert manager.service is None

    def test_is_ready_property(self, manager):
        """测试就绪状态属性"""
        assert manager.is_ready is False

    def test_start_creates_task(self, manager):
        """测试启动创建任务"""
        manager.start(use_gpu=False)

        assert manager._start_task is not None
        assert manager._start_task._use_gpu is False

        # 清理：取消任务并断开信号
        manager.shutdown(timeout_ms=100)

    def test_start_skips_if_already_ready(self, manager):
        """测试已就绪时跳过启动"""
        manager._is_ready = True

        manager.start()

        assert manager._start_task is None

    def test_start_skips_if_already_starting(self, manager):
        """测试正在启动时跳过重复启动"""
        manager._start_task = Mock()

        manager.start()

        # 应该还是原来的任务
        assert manager._start_task is not None

        # 清理
        manager._start_task = None

    def test_worker_host_start_keeps_qt_event_loop_responsive(
        self, manager, qtbot, monkeypatch
    ):
        """慢 ready 握手在后台运行时，GUI 事件仍能被处理。"""
        entered = threading.Event()
        release = threading.Event()
        fake_client = Mock()

        def slow_get_backend_client():
            entered.set()
            assert release.wait(5)
            return fake_client

        monkeypatch.setattr(
            "vibeocr.client.session.get_backend_client", slow_get_backend_client
        )
        ready: list[bool] = []
        manager.service_ready.connect(ready.append)

        manager.start_worker_host()
        qtbot.waitUntil(entered.is_set, timeout=2000)

        gui_tick: list[bool] = []
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, lambda: gui_tick.append(True))
        qtbot.waitUntil(lambda: bool(gui_tick), timeout=1000)

        release.set()
        qtbot.waitUntil(lambda: ready == [True], timeout=3000)
        assert manager.is_ready is True
        assert manager.service is not None

    def test_preload_pipelines_skips_if_not_ready(self, manager):
        """测试未就绪时跳过预加载"""
        manager.preload_pipelines(["OCR"])

        # 不应该有任务添加到线程池
        # 由于服务未就绪，直接返回

    def test_preload_pipelines_skips_if_empty(self, manager):
        """测试空管道列表时跳过预加载"""
        manager._service = Mock()

        manager.preload_pipelines([])

        # 不应该有任务添加到线程池

    def test_shutdown_resets_state(self, manager):
        """测试关闭重置状态"""
        manager._is_ready = True
        manager._service = Mock()

        result = manager.shutdown(timeout_ms=100)

        assert result is True
        assert manager._service is None
        assert manager._is_ready is False

    def test_shutdown_cancels_start_task(self, manager):
        """测试关闭取消启动任务"""
        mock_task = Mock()
        manager._start_task = mock_task

        manager.shutdown(timeout_ms=100)

        mock_task.cancel.assert_called_once()

    def test_shutdown_cancels_preload_task(self, manager):
        """shutdown 应先取消 _preload_task 再关闭 service。"""
        mock_preload = Mock()
        manager._preload_task = mock_preload
        manager._service = Mock()

        manager.shutdown(timeout_ms=100)

        # preload_task.cancel() 必须被调用
        mock_preload.cancel.assert_called_once()

    def test_shutdown_cancels_preload_before_service(self, manager):
        """取消 preload 必须在关闭 service 之前（顺序验证）。"""
        call_order: list[str] = []

        mock_preload = Mock()
        mock_preload.cancel.side_effect = lambda: call_order.append("cancel_preload")
        manager._preload_task = mock_preload

        mock_service = Mock()
        mock_service.shutdown.side_effect = lambda: call_order.append("service_shutdown")
        manager._service = mock_service

        manager.shutdown(timeout_ms=100)

        assert call_order[0] == "cancel_preload"
        assert "service_shutdown" in call_order
        assert call_order.index("cancel_preload") < call_order.index("service_shutdown")

    def test_shutdown_clears_preload_task_ref(self, manager):
        """shutdown 后 _preload_task 引用应清零。"""
        mock_preload = Mock()
        manager._preload_task = mock_preload

        manager.shutdown(timeout_ms=100)

        assert manager._preload_task is None


class TestSubprocessManagerIntegration:
    """SubprocessManager 集成测试"""

    @pytest.fixture
    def manager(self, qapp, tmp_path):
        """创建 SubprocessManager 实例"""
        mgr = SubprocessManager(tmp_path)
        yield mgr
        # 清理：断开信号连接并关闭
        try:
            if mgr._start_task is not None:
                mgr._start_task.signals.started.disconnect()
                mgr._start_task.signals.progress.disconnect()
                mgr._start_task = None
            mgr.shutdown(timeout_ms=100)
        except RuntimeError:
            pass  # 信号已断开

    def test_on_started_success(self, manager):
        """测试启动成功回调"""
        # 模拟启动任务
        mock_task = Mock()
        mock_service = Mock()
        mock_task.service = mock_service
        manager._start_task = mock_task

        # 模拟信号回调
        manager._on_started(True)

        assert manager._is_ready is True
        assert manager._service is mock_service
        assert manager._start_task is None

    def test_on_started_failure(self, manager):
        """测试启动失败回调"""
        manager._start_task = Mock()
        manager._start_task.service = None

        manager._on_started(False)

        assert manager._is_ready is False
        assert manager._service is None
        assert manager._start_task is None
