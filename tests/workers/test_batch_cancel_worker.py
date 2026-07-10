"""测试 worker 端后台轮询 SHM cancel flag 并调用 mgr.cancel()。

worker 主循环在 mgr.commit() 内同步阻塞，MSG_BATCH_CANCEL 无法在 commit
期间被读取。修复方案：commit 期间启动守护线程轮询 SHM cancel flag 字节，
检测到后调用 mgr.cancel()（在子批次边界生效）。
"""

import threading
import time
from unittest.mock import MagicMock


class TestPollCancelFlag:
    """_poll_cancel_flag：后台轮询 cancel flag 并触发 mgr.cancel()。"""

    def test_cancel_flag_triggers_mgr_cancel(self):
        """cancel flag 被 set 后，轮询线程调用 mgr.cancel()"""
        from vibeocr.workers.ocr_worker import _poll_cancel_flag

        mgr = MagicMock()
        mgr._cancelled = False
        proto = MagicMock()
        proto.is_cancelled.side_effect = [False, False, True]  # 第三次检测到

        stop_event = threading.Event()
        _poll_cancel_flag(proto, mgr, stop_event, poll_interval=0.01)

        assert proto.is_cancelled.call_count == 3
        assert mgr.cancel.call_count == 1
        assert stop_event.is_set()

    def test_no_cancel_no_mgr_cancel(self):
        """未 set cancel flag 时不调用 mgr.cancel()"""
        from vibeocr.workers.ocr_worker import _poll_cancel_flag

        mgr = MagicMock()
        proto = MagicMock()
        proto.is_cancelled.return_value = False

        stop_event = threading.Event()
        # 0.1s 后由外部停止（模拟 commit 完成）
        timer = threading.Timer(0.1, stop_event.set)
        timer.daemon = True
        timer.start()

        _poll_cancel_flag(proto, mgr, stop_event, poll_interval=0.01)
        assert mgr.cancel.call_count == 0

    def test_poll_stops_when_stop_event_set(self):
        """stop_event 被 set 后轮询立即退出"""
        from vibeocr.workers.ocr_worker import _poll_cancel_flag

        mgr = MagicMock()
        proto = MagicMock()
        proto.is_cancelled.return_value = False

        stop_event = threading.Event()
        stop_event.set()  # 预先 set

        start = time.monotonic()
        _poll_cancel_flag(proto, mgr, stop_event, poll_interval=0.01)
        elapsed = time.monotonic() - start

        # 应立即退出，不轮询
        assert elapsed < 0.05
        assert mgr.cancel.call_count == 0

    def test_poll_exception_does_not_crash(self):
        """is_cancelled 抛异常时轮询不崩溃，继续直到 stop"""
        from vibeocr.workers.ocr_worker import _poll_cancel_flag

        mgr = MagicMock()
        proto = MagicMock()
        proto.is_cancelled.side_effect = [Exception("boom"), False, True]

        stop_event = threading.Event()
        _poll_cancel_flag(proto, mgr, stop_event, poll_interval=0.01)

        # 异常被吞掉，最终仍检测到 cancel
        assert mgr.cancel.call_count == 1
