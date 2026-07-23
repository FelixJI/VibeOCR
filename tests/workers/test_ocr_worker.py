"""ocr_worker 模块的单元测试。

聚焦 _poll_cancel_flag（此前完全未覆盖，ocr_worker.py 仅 12% 覆盖）。
run_worker 是 600 行的集成主循环（需 mock 整个 SHM/管道协议），不在本文件
范围；_poll_cancel_flag 是可独立测试的纯线程循环函数。
"""

import threading
from unittest.mock import MagicMock

from vibeocr.workers.ocr_worker import _poll_cancel_flag


class TestPollCancelFlag:
    """_poll_cancel_flag：批量 commit 期间的 SHM cancel flag 轮询。"""

    def test_detects_cancel_calls_mgr_cancel_and_sets_stop(self):
        """检测到 cancel flag 时应调用 mgr.cancel() 并 set stop_event。"""
        protocol = MagicMock()
        protocol.is_cancelled.return_value = True
        mgr = MagicMock()
        stop_event = threading.Event()

        _poll_cancel_flag(protocol, mgr, stop_event, poll_interval=0.01)

        mgr.cancel.assert_called_once()
        assert stop_event.is_set()

    def test_no_cancel_exits_when_stop_event_set(self):
        """stop_event 被外部 set（commit 完成）时正常退出，不调 cancel。"""
        protocol = MagicMock()
        protocol.is_cancelled.return_value = False
        mgr = MagicMock()
        stop_event = threading.Event()

        # 立即 set stop_event 模拟 commit 完成
        stop_event.set()
        _poll_cancel_flag(protocol, mgr, stop_event, poll_interval=0.01)

        mgr.cancel.assert_not_called()

    def test_is_cancelled_exception_does_not_crash_loop(self):
        """is_cancelled 抛异常时不应崩溃轮询线程，应继续直到 stop_event。"""
        call_count = {"n": 0}

        class FlakyProtocol:
            def is_cancelled(self):
                call_count["n"] += 1
                if call_count["n"] < 3:
                    raise OSError("SHM read error")
                return False

        mgr = MagicMock()
        stop_event = threading.Event()

        # 短暂后 set stop_event 让循环退出
        timer = threading.Timer(0.05, stop_event.set)
        timer.start()
        # 不应抛异常
        _poll_cancel_flag(FlakyProtocol(), mgr, stop_event, poll_interval=0.01)
        timer.join()

        mgr.cancel.assert_not_called()
        # 应至少轮询了几次（异常被吞掉后继续）
        assert call_count["n"] >= 3

    def test_polls_until_cancel_detected(self):
        """前几次未 cancel，第 N 次检测到 cancel → 调 cancel。"""
        protocol = MagicMock()
        # 前 4 次返回 False，第 5 次 True
        protocol.is_cancelled.side_effect = [False, False, False, False, True]
        mgr = MagicMock()
        stop_event = threading.Event()

        _poll_cancel_flag(protocol, mgr, stop_event, poll_interval=0.001)

        mgr.cancel.assert_called_once()
        assert stop_event.is_set()
        assert protocol.is_cancelled.call_count == 5
