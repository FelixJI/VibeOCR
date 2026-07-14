from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.collect_startup_metrics import _read_trace, collect


def test_read_trace_uses_real_distinct_milestones(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        json.dumps({"T0": 0.0, "T3": 0.25, "T6": 0.9}) + "\n",
        encoding="utf-8",
    )

    assert _read_trace(trace) == pytest.approx((250.0, 900.0))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"T0": 0.0, "T3": 0.2},
        {"T0": 0.0, "T3": 0.5, "T6": 0.4},
        {"T0": "bad", "T3": 0.5, "T6": 0.8},
    ],
)
def test_read_trace_rejects_missing_invalid_or_non_monotonic_data(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        _read_trace(trace)


def test_collect_counts_only_valid_trace_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "VibeOCR.WinUI.exe"
    target.write_bytes(b"MZ")

    def fake_run(command, *, env, **kwargs):
        del command, kwargs
        Path(env["VIBEOCR_STARTUP_TRACE"]).write_text(
            json.dumps({"T0": 0.0, "T3": 0.1, "T6": 0.4}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("scripts.collect_startup_metrics.subprocess.run", fake_run)

    metrics = collect(str(target), 2, "winui", "host|x64")

    assert metrics["samples"] == 2
    assert metrics["t0_t3_p95_ms"] == pytest.approx(100.0)
    assert metrics["t0_t6_p95_ms"] == pytest.approx(400.0)


def test_collect_rejects_zero_runs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="runs"):
        collect(str(tmp_path / "app.exe"), 0, "winui", "host|x64")
