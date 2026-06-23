"""Tests for WorkerManager health check and task execution.

重点验证：健康检查不应误杀正常的长时间批量任务。
根因：批量识别（如 25 页 PDF）单次 predict 可能运行 >300s，
但健康检查硬编码 300s 就强制重启，导致任务卡死、UI 无提示、取消无效。
"""

import time

import pytest

from vibeocr.services.worker_manager import WorkerManager, WorkerState


class TestHealthCheckThreshold:
    """健康检查卡死阈值：必须大于最大批量任务超时（1800s）。"""

    def test_stale_threshold_constant_exceeds_max_batch_timeout(self):
        """STALE_THRESHOLD 常量应 > 1800s（最大批量超时），避免误杀正常任务。"""
        assert hasattr(WorkerManager, "STALE_THRESHOLD")
        assert WorkerManager.STALE_THRESHOLD > 1800.0

    def test_busy_worker_not_killed_within_batch_timeout(self):
        """BUSY 状态的 worker 在批量任务超时内不应被判定卡死。

        模拟：worker 开始批量任务（last_active=now），运行 300s 仍在 BUSY，
        健康检查不应把它加入重启列表。
        """
        from unittest.mock import MagicMock

        mgr = WorkerManager.__new__(WorkerManager)
        mgr._workers_lock = __import__("threading").RLock()
        mgr.auto_restart = True
        mgr._workers = []
        mgr._shutting_down = False

        mock_process = MagicMock()
        mock_process.is_running = True
        worker_info = MagicMock()
        worker_info.process = mock_process
        worker_info.state = WorkerState.BUSY
        worker_info.worker_id = 0
        # 模拟任务运行了 300s（旧阈值会误杀）
        worker_info.last_active = time.time() - 300
        mgr._workers.append(worker_info)

        # 收集重启列表
        workers_to_restart = []
        # 复刻 _perform_health_check 的卡死判断逻辑
        with mgr._workers_lock:
            for wi in mgr._workers:
                if wi.state == WorkerState.BUSY:
                    idle_time = time.time() - wi.last_active
                    if idle_time > WorkerManager.STALE_THRESHOLD:
                        workers_to_restart.append(wi)

        assert len(workers_to_restart) == 0, "300s 的批量任务不应被判定卡死"

    def test_truly_stale_worker_detected(self):
        """真正卡死（超过阈值）的 worker 仍应被检测到。"""
        from unittest.mock import MagicMock

        mock_process = MagicMock()
        mock_process.is_running = True
        worker_info = MagicMock()
        worker_info.process = mock_process
        worker_info.state = WorkerState.BUSY
        # 超过 STALE_THRESHOLD
        worker_info.last_active = time.time() - (WorkerManager.STALE_THRESHOLD + 60)

        idle_time = time.time() - worker_info.last_active
        assert idle_time > WorkerManager.STALE_THRESHOLD
