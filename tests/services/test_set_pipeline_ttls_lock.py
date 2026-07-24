"""OCRServiceSubprocess.set_pipeline_ttls 锁等待行为测试。

回归 bug：worker 启动恢复期间（restore_after_start 在 _shm_lock 内预加载未缓存
模型，可达数十秒~数分钟），用户改 TTL 触发的 set_pipeline_ttls 走
execute_control（默认 lock_timeout=15.0），等锁 15s 超时 → 后台任务打 traceback
+ 可能污染 SHM 状态导致后续 preload/OCR 连锁失败。

根因修复：TTL 已持久化（ConfigManager.set_pipeline_ttls），且 worker 恢复时
restore_after_start 会用最新配置重新下发（_send_state_payload）。所以用户态
set_pipeline_ttls 是**尽力而为**：锁被占（恢复中/OCR 中）时快速失败返回 False，
不阻塞 15s。上层 settings_page_controller 把 False 显示为「已保存，重启后生效」。
"""

import time
from unittest.mock import MagicMock

from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess


def _make_service_with_busy_lock():
    """构造一个 _initialized=True、worker 已 ready、但 _shm_lock 被占的 service。

    模拟 worker 恢复预加载期间 _shm_lock 被 restore 线程持有。
    """
    service = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    service._initialized = True

    # 模拟 worker manager：execute_control 走真实锁获取路径
    import threading as _t

    lock = _t.Lock()
    lock.acquire()  # 模拟 restore 线程持有锁

    mock_manager = MagicMock()
    # execute_control 真实尝试 acquire(timeout=lock_timeout)，拿不到就抛错
    mock_manager.execute_control.side_effect = lambda task, lock_timeout=15.0: (
        _acquire_or_raise(lock, lock_timeout, task)
    )
    service._paddlex_manager = mock_manager
    return service, lock


def _acquire_or_raise(lock, lock_timeout, task):
    """复刻 execute_control 的锁获取语义：拿不到就抛 WorkerProcessError。"""
    from vibeocr.services.ocr_worker_process import OCRWorkerProcessError

    if not lock.acquire(timeout=lock_timeout):
        raise OCRWorkerProcessError(
            f"控制 RPC 等待 _shm_lock 超时（{lock_timeout}s）"
        )
    try:
        mock_worker = MagicMock()
        return task(mock_worker)
    finally:
        lock.release()


def test_set_pipeline_ttls_fails_fast_when_lock_busy():
    """锁被占时 set_pipeline_ttls 应快速返回 False，不等满 15s。

    修复前：execute_control 默认 lock_timeout=15.0，恢复期间等满 15s 才失败。
    修复后：set_pipeline_ttls 传短 lock_timeout，~1s 内快速失败返回 False。
    """
    service, lock = _make_service_with_busy_lock()
    try:
        start = time.monotonic()
        result = service.set_pipeline_ttls({"OCR": 300})
        elapsed = time.monotonic() - start
    finally:
        lock.release()

    assert result is False, "锁被占时应返回 False（尽力而为）"
    # 关键：不得等满 15s。短超时应 <3s（留余量给 CI 抖动）。
    assert elapsed < 3.0, f"set_pipeline_ttls 阻塞了 {elapsed:.1f}s，应快速失败"


def test_set_pipeline_ttls_passes_short_lock_timeout():
    """set_pipeline_ttls 应向 execute_control 传一个短的 lock_timeout。"""
    service = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    service._initialized = True
    mock_manager = MagicMock()
    mock_manager.execute_control.return_value = True
    service._paddlex_manager = mock_manager

    service.set_pipeline_ttls({"OCR": 300})

    mock_manager.execute_control.assert_called_once()
    call_kwargs = mock_manager.execute_control.call_args.kwargs
    lock_timeout = call_kwargs.get("lock_timeout")
    assert lock_timeout is not None, "set_pipeline_ttls 未传 lock_timeout"
    assert lock_timeout <= 2.0, (
        f"lock_timeout={lock_timeout} 过长，恢复期间会阻塞；应 ≤2s 快速失败"
    )


def test_set_pipeline_ttls_succeeds_when_lock_free():
    """锁空闲时 set_pipeline_ttls 正常下发，返回 True（不回归）。"""
    service = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    service._initialized = True
    mock_manager = MagicMock()
    mock_manager.execute_control.return_value = True
    service._paddlex_manager = mock_manager

    result = service.set_pipeline_ttls({"OCR": 300})
    assert result is True


def test_set_pipeline_ttls_returns_false_when_not_initialized():
    """服务未初始化时返回 False（既有契约，不回归）。"""
    service = OCRServiceSubprocess.__new__(OCRServiceSubprocess)
    service._initialized = False
    assert service.set_pipeline_ttls({"OCR": 300}) is False
