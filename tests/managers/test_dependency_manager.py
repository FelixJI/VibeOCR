"""测试 DependencyManager"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from vibeocr.managers.dependency_manager import (
    DependencyManager,
    DependencyCheckTask,
    DependencyCheckSignals,
)


class TestDependencyCheckSignals:
    """测试依赖检查信号"""

    def test_signals_exist(self, qapp):
        """测试信号存在"""
        signals = DependencyCheckSignals()
        assert hasattr(signals, 'finished')


class TestDependencyCheckTask:
    """测试依赖检查任务"""

    def test_task_creation(self, tmp_path):
        """测试任务创建"""
        task = DependencyCheckTask(tmp_path)
        assert task._project_root == tmp_path
        assert hasattr(task, 'signals')

    @patch("vibeocr.managers.dependency_manager.env_manager")
    def test_task_run_ready(self, mock_env_manager, tmp_path, qapp):
        """测试任务运行（依赖就绪）"""
        mock_env_manager.get_environment_mode.return_value = "embedded"
        mock_env_manager.get_embedded_python_executable.return_value = "python.exe"
        mock_env_manager.is_embedded_environment_ready.return_value = (True, [])

        task = DependencyCheckTask(tmp_path)

        # 连接信号以捕获结果
        finished_mock = Mock()
        task.signals.finished.connect(finished_mock)

        # 运行任务
        task.run()

        # 验证结果
        finished_mock.assert_called_once_with(True, [])
        mock_env_manager.is_embedded_environment_ready.assert_called_once_with(tmp_path)

    @patch("vibeocr.managers.dependency_manager.env_manager")
    def test_task_run_not_ready(self, mock_env_manager, tmp_path, qapp):
        """测试任务运行（依赖未就绪）"""
        mock_env_manager.get_environment_mode.return_value = "embedded"
        mock_env_manager.get_embedded_python_executable.return_value = "python.exe"
        mock_env_manager.is_embedded_environment_ready.return_value = (False, ["paddlepaddle"])

        task = DependencyCheckTask(tmp_path)

        finished_mock = Mock()
        task.signals.finished.connect(finished_mock)

        task.run()

        finished_mock.assert_called_once_with(False, ["paddlepaddle"])


class TestDependencyManager:
    """测试依赖管理器"""

    def test_manager_creation(self, qapp):
        """测试管理器创建"""
        manager = DependencyManager()
        assert manager is not None
        assert not manager.is_checking()
        assert not manager.is_ready()

    def test_manager_with_project_root(self, tmp_path, qapp):
        """测试指定项目根目录"""
        manager = DependencyManager(project_root=tmp_path)
        assert manager._project_root == tmp_path

    @patch("vibeocr.managers.dependency_manager.DependencyCheckTask")
    def test_check_dependencies(self, mock_task_class, tmp_path, qapp):
        """测试检查依赖"""
        manager = DependencyManager(project_root=tmp_path)

        # 模拟任务
        mock_task = MagicMock()
        mock_task_class.return_value = mock_task

        # 连接信号
        started_mock = Mock()
        manager.check_started.connect(started_mock)

        # 检查依赖
        manager.check_dependencies()

        # 验证
        assert manager.is_checking()
        started_mock.assert_called_once()
        mock_task_class.assert_called_once_with(tmp_path)

    def test_check_dependencies_prevents_duplicate(self, tmp_path, qapp):
        """测试防止重复检查"""
        manager = DependencyManager(project_root=tmp_path)
        manager._is_checking = True

        started_mock = Mock()
        manager.check_started.connect(started_mock)

        manager.check_dependencies()

        # 不应发出 started 信号
        started_mock.assert_not_called()

    def test_on_check_finished(self, qapp):
        """测试检查完成回调"""
        manager = DependencyManager()

        completed_mock = Mock()
        manager.check_completed.connect(completed_mock)

        # 模拟检查完成
        manager._on_check_finished(True, [])

        assert not manager.is_checking()
        assert manager.is_ready()
        completed_mock.assert_called_once_with(True, [])

    def test_get_missing_dependencies(self, qapp):
        """测试获取缺失依赖"""
        manager = DependencyManager()
        manager._missing_dependencies = ["paddlepaddle", "paddlex"]

        missing = manager.get_missing_dependencies()
        assert missing == ["paddlepaddle", "paddlex"]

        # 验证返回的是副本
        missing.append("other")
        assert manager._missing_dependencies == ["paddlepaddle", "paddlex"]

    def test_reset(self, qapp):
        """测试重置状态"""
        manager = DependencyManager()
        manager._is_checking = True
        manager._is_ready = True
        manager._missing_dependencies = ["paddlepaddle"]

        manager.reset()

        assert not manager.is_checking()
        assert not manager.is_ready()
        assert manager._missing_dependencies == []

    def test_signals_exist(self, qapp):
        """测试信号存在"""
        manager = DependencyManager()
        assert hasattr(manager, 'check_completed')
        assert hasattr(manager, 'check_started')
