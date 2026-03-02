"""SubprocessManager 测试"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from PySide6.QtCore import QThreadPool

from vibeocr.managers.subprocess_manager import (
    SubprocessManager,
    SubprocessStartTask,
    PreloadTask,
    SubprocessStartSignals,
    PreloadSignals,
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
        task = PreloadTask(mock_service, ["OCR", "TABLE_RECOGNITION"])
        assert task._pipelines == ["OCR", "TABLE_RECOGNITION"]
        assert task.signals is not None


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
