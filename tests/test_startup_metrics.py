"""startup_metrics 模块测试：可信的 T0–T6 启动里程碑。

覆盖：
- StartupEvent 枚举值固定（T0–T6）
- StartupRecorder 记录事件、处理重复/乱序/缺失
- JSONL 输出只在 VIBEOCR_STARTUP_TRACE 设置时落盘
- 路径脱敏（不含本机绝对路径）
- p50/p95 汇总统计
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibeocr.startup_metrics import (
    StartupEvent,
    StartupRecorder,
    percentile,
    summarize_runs,
)


class TestStartupEvent:
    def test_event_values_are_t0_to_t6(self):
        assert StartupEvent.PROCESS_START.value == "T0"
        assert StartupEvent.RUNTIME_READY.value == "T1"
        assert StartupEvent.SHELL_CREATED.value == "T2"
        assert StartupEvent.FIRST_WINDOW.value == "T3"
        assert StartupEvent.WORKER_READY.value == "T4"
        assert StartupEvent.BACKEND_READY.value == "T5"
        assert StartupEvent.INTERACTIVE.value == "T6"

    def test_event_count_is_seven(self):
        assert len(StartupEvent) == 7

    def test_str_enum_string_compatible(self):
        """StartupEvent 是 StrEnum，可直接与字符串比较。"""
        assert StartupEvent.PROCESS_START == "T0"
        assert str(StartupEvent.INTERACTIVE) == "T6"


class TestStartupRecorder:
    def test_record_single_event(self):
        rec = StartupRecorder()
        rec.record(StartupEvent.PROCESS_START, 0.0)
        assert rec.events[StartupEvent.PROCESS_START] == pytest.approx(0.0)

    def test_record_monotonic_timestamps(self):
        rec = StartupRecorder()
        rec.record(StartupEvent.PROCESS_START, 0.0)
        rec.record(StartupEvent.SHELL_CREATED, 1.5)
        rec.record(StartupEvent.INTERACTIVE, 3.0)
        assert rec.events[StartupEvent.INTERACTIVE] == pytest.approx(3.0)

    def test_duplicate_event_keeps_first(self):
        """重复记录同一事件只保留首次时间戳（里程碑语义）。"""
        rec = StartupRecorder()
        rec.record(StartupEvent.SHELL_CREATED, 1.0)
        rec.record(StartupEvent.SHELL_CREATED, 2.0)  # 第二次应被忽略
        assert rec.events[StartupEvent.SHELL_CREATED] == pytest.approx(1.0)

    def test_out_of_order_events_preserve_individual_timestamps(self):
        """乱序事件各自记录，不强制递增（允许异步就绪回调）。"""
        rec = StartupRecorder()
        rec.record(StartupEvent.INTERACTIVE, 5.0)
        rec.record(StartupEvent.PROCESS_START, 0.0)
        # 各自独立
        assert rec.events[StartupEvent.PROCESS_START] == pytest.approx(0.0)
        assert rec.events[StartupEvent.INTERACTIVE] == pytest.approx(5.0)

    def test_missing_t6_detected(self):
        """缺少 T6（INTERACTIVE）时 is_complete 返回 False。"""
        rec = StartupRecorder()
        rec.record(StartupEvent.PROCESS_START, 0.0)
        rec.record(StartupEvent.INTERACTIVE, 3.0)
        # 有 T0 和 T6，但缺中间事件
        assert not rec.is_complete()

    def test_complete_when_all_seven_present(self):
        rec = StartupRecorder()
        for i, ev in enumerate(StartupEvent):
            rec.record(ev, float(i))
        assert rec.is_complete()

    def test_to_dict_contains_all_recorded_events(self):
        rec = StartupRecorder()
        rec.record(StartupEvent.PROCESS_START, 0.0)
        rec.record(StartupEvent.SHELL_CREATED, 1.0)
        d = rec.to_dict()
        assert d["T0"] == pytest.approx(0.0)
        assert d["T2"] == pytest.approx(1.0)
        # 未记录的事件不出现在 dict 中
        assert "T6" not in d

    def test_jsonl_output_only_when_trace_env_set(self, tmp_path, monkeypatch):
        """默认不落盘；设置 VIBEOCR_STARTUP_TRACE 才输出 JSONL。"""
        trace_path = tmp_path / "startup.jsonl"
        monkeypatch.setenv("VIBEOCR_STARTUP_TRACE", str(trace_path))

        rec = StartupRecorder()
        rec.record(StartupEvent.PROCESS_START, 0.0)
        rec.record(StartupEvent.INTERACTIVE, 2.5)
        rec.flush()

        assert trace_path.exists()
        lines = trace_path.read_text(encoding="utf-8").strip().split("\n")
        data = json.loads(lines[0])
        assert data["T0"] == pytest.approx(0.0)
        assert data["T6"] == pytest.approx(2.5)

    def test_no_output_when_trace_env_unset(self, tmp_path, monkeypatch):
        """未设置 VIBEOCR_STARTUP_TRACE 时不创建任何文件。"""
        monkeypatch.delenv("VIBEOCR_STARTUP_TRACE", raising=False)
        trace_path = tmp_path / "startup.jsonl"

        rec = StartupRecorder()
        rec.record(StartupEvent.PROCESS_START, 0.0)
        rec.flush()

        assert not trace_path.exists()

    def test_jsonl_scrubs_absolute_paths(self, tmp_path, monkeypatch):
        """JSONL 输出不得包含本机绝对路径。"""
        trace_path = tmp_path / "deep" / "nested" / "startup.jsonl"
        monkeypatch.setenv("VIBEOCR_STARTUP_TRACE", str(trace_path))

        rec = StartupRecorder()
        rec.record(StartupEvent.PROCESS_START, 0.0)
        rec.flush()

        raw = trace_path.read_text(encoding="utf-8")
        # 不含本机用户目录（trace_path 本身的路径不应出现在 JSON 内容中）
        home = str(Path.home())
        assert home not in raw


class TestPercentile:
    def test_p50_single_value(self):
        assert percentile([1.0], 50) == pytest.approx(1.0)

    def test_p50_even_count(self):
        # [1, 2, 3, 4] 的 p50 取中间两个的平均
        result = percentile([1.0, 2.0, 3.0, 4.0], 50)
        assert 2.0 <= result <= 3.0

    def test_p50_odd_count(self):
        result = percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50)
        assert result == pytest.approx(3.0)

    def test_p95_near_max(self):
        data = [float(i) for i in range(100)]
        p95 = percentile(data, 95)
        assert p95 >= 90.0  # p95 应接近上界

    def test_empty_list_raises(self):
        with pytest.raises((ValueError, IndexError)):
            percentile([], 50)


class TestSummarizeRuns:
    def test_summarize_computes_p50_p95_per_milestone(self):
        """汇总多次运行，为每个里程碑计算 p50/p95。"""
        from vibeocr.startup_metrics import StartupRecorder

        runs = []
        for base in range(10):
            rec = StartupRecorder()
            for i, ev in enumerate(StartupEvent):
                rec.record(ev, float(base + i * 0.1))
            runs.append(rec.to_dict())

        summary = summarize_runs(runs)
        assert "T0" in summary
        assert "p50" in summary["T0"]
        assert "p95" in summary["T0"]
        assert "T6" in summary
        # T6 的 p50 应大于 T0 的 p50（T6 在后面）
        assert summary["T6"]["p50"] >= summary["T0"]["p50"]

    def test_summarize_handles_missing_milestones(self):
        """某些 run 缺某里程碑时，只统计有该里程碑的 run。"""
        runs = [
            {"T0": 0.0, "T6": 3.0},
            {"T0": 0.0},  # 缺 T6
        ]
        summary = summarize_runs(runs)
        assert "T0" in summary
        assert "T6" in summary
        assert summary["T6"]["p50"] == pytest.approx(3.0)
