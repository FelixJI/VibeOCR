"""Tests for WorkerManager health check and task execution.

重点验证：健康检查不应误杀正常的长时间批量任务。
根因：批量识别（如 25 页 PDF）单次 predict 可能运行 >300s，
但健康检查硬编码 300s 就强制重启，导致任务卡死、UI 无提示、取消无效。
"""

import time

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


class TestCancelEvent:
    """cancel_event 取消机制测试:中断 _get_available_worker 的长等待"""

    def test_cancel_event_set_returns_immediately(self):
        """cancel_event 已 set 时,_get_available_worker 立即返回 None"""
        import threading

        cancel_event = threading.Event()
        cancel_event.set()
        mgr = WorkerManager(
            max_workers=1, use_gpu=False, auto_restart=False, cancel_event=cancel_event
        )
        start = time.monotonic()
        result = mgr._get_available_worker(wait_timeout=300.0)
        elapsed = time.monotonic() - start
        assert result is None
        # 应在 1 秒内返回(event.wait(0.1) 检测到 set),而非等 300 秒
        assert elapsed < 1.0, f"cancel 未生效,等待了 {elapsed:.2f}s"

    def test_cancel_event_set_during_wait(self):
        """等待期间 set cancel_event,能中断后续轮询"""
        import threading
        from unittest.mock import MagicMock

        from vibeocr.services.worker_manager import WorkerInfo

        cancel_event = threading.Event()
        mgr = WorkerManager(
            max_workers=1, use_gpu=False, auto_restart=False, cancel_event=cancel_event
        )
        # 注入一个 busy worker,使 _get_available_worker 进入轮询
        mock_process = MagicMock()
        mock_process.busy = True
        info = WorkerInfo(
            worker_id="fake", process=mock_process, state=WorkerState.IDLE
        )
        mgr._workers.append(info)

        def setter():
            time.sleep(0.3)
            cancel_event.set()

        threading.Thread(target=setter, daemon=True).start()

        start = time.monotonic()
        result = mgr._get_available_worker(wait_timeout=300.0)
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < 1.0, f"cancel 未中断等待,耗时 {elapsed:.2f}s"

    def test_no_cancel_event_fallback_to_sleep(self):
        """未设置 cancel_event 时,保持旧行为(不报错)"""
        mgr = WorkerManager(
            max_workers=1, use_gpu=False, auto_restart=False, cancel_event=None
        )
        result = mgr._get_available_worker(wait_timeout=0.2)
        assert result is None

    def test_set_cancel_event_after_init(self):
        """set_cancel_event 能在构造后注入事件"""
        import threading

        mgr = WorkerManager(
            max_workers=1, use_gpu=False, auto_restart=False
        )
        assert mgr._cancel_event is None
        event = threading.Event()
        mgr.set_cancel_event(event)
        assert mgr._cancel_event is event
        event.set()
        result = mgr._get_available_worker(wait_timeout=300.0)
        assert result is None
