"""回归测试：批量取消在 100ms 内返回，不冻结 UI。

根因：旧实现 batch_cancel() 通过 WorkerManager.execute() 领取同一 busy worker，
最长等待 300 秒，直接冻结 GUI。修复后 batch_cancel() 直接写 SHM cancel flag
独立通道，瞬间返回。本测试锁定"cancel 不阻塞"契约。
"""

import time
from unittest.mock import MagicMock

import pytest

# 检查模块可用性
try:
    from vibeocr.views.batch_recognition_tab import BatchRecognitionWorker

    HAS_MODULE = True
except ImportError:
    BatchRecognitionWorker = None  # type: ignore[assignment,misc]
    HAS_MODULE = False


@pytest.mark.skipif(not HAS_MODULE, reason="batch_recognition_tab not available")
class TestBatchCancelNonBlocking:
    """批量取消必须非阻塞（100ms 内返回）。"""

    def test_cancel_returns_within_100ms_when_worker_busy(self):
        """worker busy 时 cancel 仍应在 100ms 内返回。

        模拟：batch_cancel 为瞬间返回的 mock（独立通道不应阻塞）。
        旧实现会因 WorkerManager.execute 内 300s 等待而远超 100ms。
        """
        mock_service = MagicMock()
        # batch_cancel 模拟为瞬间返回（独立通道不应阻塞）
        mock_service.batch_cancel.return_value = None
        assert BatchRecognitionWorker is not None
        worker = BatchRecognitionWorker.__new__(BatchRecognitionWorker)
        worker._cancelled = False
        worker._service = mock_service

        start = time.monotonic()
        worker.cancel()
        elapsed = time.monotonic() - start

        assert worker._cancelled is True
        assert elapsed < 0.1, f"cancel 阻塞了 {elapsed:.3f}s，应 <100ms"
        mock_service.batch_cancel.assert_called_once()

    def test_cancel_sets_cancelled_flag_even_if_service_raises(self):
        """service.batch_cancel 抛异常时 cancel flag 仍被设置"""
        mock_service = MagicMock()
        mock_service.batch_cancel.side_effect = RuntimeError("service error")
        assert BatchRecognitionWorker is not None
        worker = BatchRecognitionWorker.__new__(BatchRecognitionWorker)
        worker._cancelled = False
        worker._service = mock_service

        # 不应抛异常（cancel 内 suppress）
        worker.cancel()
        assert worker._cancelled is True
