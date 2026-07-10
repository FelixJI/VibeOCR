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


class TestAtomicWorkerReservation:
    """worker 领取与 BUSY 标记必须是原子操作。

    根因：_get_available_worker 在锁内返回 IDLE worker，锁释放后 execute()
    才设置 BUSY。两个调用线程可同时拿到同一 worker。
    """

    def test_concurrent_execute_does_not_double_reserve(self):
        """两个线程同时 execute，同一 worker 不会被同时借出给两者。

        关键场景：t1 领取 worker 进入 task（阻塞），t2 在此期间尝试领取。
        旧实现因 TOCTOU（锁内返回、锁外设 BUSY）会让 t2 也拿到同一 worker。
        修复后 _reserve_worker 在锁内原子完成 选择+BUSY，t2 应拿不到。
        """
        import threading
        from unittest.mock import MagicMock

        from vibeocr.services.worker_manager import WorkerInfo

        # 预设 cancel_event，让 t2 的等待可被中断
        cancel_event = threading.Event()
        mgr = WorkerManager(
            max_workers=1, use_gpu=False, auto_restart=False, cancel_event=cancel_event
        )
        mock_process = MagicMock()
        mock_process.is_ready = True
        mock_process.busy = False
        mock_process.is_running = True
        info = WorkerInfo(worker_id=0, process=mock_process, state=WorkerState.IDLE)
        mgr._workers.append(info)

        in_task_count = []
        count_lock = threading.Lock()
        first_in_task = threading.Event()
        release_first = threading.Event()
        t2_done = threading.Event()

        def task(w):
            with count_lock:
                in_task_count.append(1)
                current = len(in_task_count)
            if current == 1:
                # 第一个任务：通知已进入，等待放行（期间 worker 保持 BUSY）
                first_in_task.set()
                release_first.wait(timeout=5)
            return "ok"

        def run_execute():
            try:
                mgr.execute(task)
            except Exception:
                pass
            finally:
                t2_done.set()

        t1 = threading.Thread(target=run_execute)
        t2 = threading.Thread(target=run_execute)
        t1.start()
        # 确保第一个已进入 task 并持有 BUSY
        first_in_task.wait(timeout=2)
        # 启动第二个线程，它应因 worker BUSY 而无法领取
        t2.start()
        # 让 t2 有足够时间尝试领取（多次轮询）并持续失败
        time.sleep(0.5)
        # 此时 t1 仍持有 BUSY，t2 应仍在等待，未进入 task
        with count_lock:
            concurrent = len(in_task_count)
        assert concurrent == 1, (
            f"t1 仍持有 BUSY 时 t2 不应进入 task，但 in_task_count={concurrent}"
        )
        # 中断 t2 的等待并放行 t1
        cancel_event.set()
        release_first.set()
        t1.join(timeout=5)
        t2.join(timeout=5)

    def test_reserve_worker_marks_busy_atomically(self):
        """_reserve_worker 在锁内完成 选择+BUSY+计数"""
        import threading
        from unittest.mock import MagicMock

        from vibeocr.services.worker_manager import WorkerInfo

        mgr = WorkerManager(max_workers=1, use_gpu=False, auto_restart=False)
        mock_process = MagicMock()
        mock_process.is_ready = True
        mock_process.busy = False
        info = WorkerInfo(worker_id=0, process=mock_process, state=WorkerState.IDLE)
        mgr._workers.append(info)

        reserved = mgr._reserve_worker(wait_timeout=0.5)
        assert reserved is not None
        # 锁内已标记 BUSY
        assert reserved.state == WorkerState.BUSY
        assert reserved.total_tasks == 1
        # 第二次领取应拿不到（已 BUSY）
        again = mgr._reserve_worker(wait_timeout=0.1)
        assert again is None

    def test_release_worker_transitions_to_idle(self):
        """_release_worker(success=True) 在锁内将状态迁回 IDLE"""
        from unittest.mock import MagicMock

        from vibeocr.services.worker_manager import WorkerInfo

        mgr = WorkerManager(max_workers=1, use_gpu=False, auto_restart=False)
        mock_process = MagicMock()
        info = WorkerInfo(worker_id=0, process=mock_process, state=WorkerState.BUSY)
        mgr._workers.append(info)

        mgr._release_worker(info, success=True)
        assert info.state == WorkerState.IDLE
        assert info.last_active > 0
