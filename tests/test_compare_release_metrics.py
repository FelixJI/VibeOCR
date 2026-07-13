"""Tests for the release-metrics comparison gate."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _REPO_ROOT / "scripts" / "compare_release_metrics.py"
_spec = importlib.util.spec_from_file_location("compare_release_metrics", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["compare_release_metrics"] = _mod
_spec.loader.exec_module(_mod)

Metrics = _mod.Metrics
_pct_change = _mod._pct_change
compare = _mod.compare


def _metrics(**over) -> Metrics:
    base = dict(
        name="x", fingerprint="machine|x64", samples=30,
        zip_bytes=160_000_000, unzipped_bytes=400_000_000,
        t0_t3_p95_ms=3000.0, t0_t6_p95_ms=5000.0,
    )
    base.update(over)
    return Metrics(**base)


def test_pct_change_negative_is_improvement() -> None:
    assert _pct_change(100.0, 60.0) == pytest.approx(-0.4)


def test_gate_passes_when_zip_improves_30pct() -> None:
    old = _metrics()
    new = _metrics(zip_bytes=100_000_000)  # -37.5%
    ok, errors = compare(old, new, require_gate=True)
    assert ok, errors
    assert errors == []


def test_gate_passes_when_startup_improves_30pct() -> None:
    old = _metrics()
    new = _metrics(t0_t3_p95_ms=1800.0)  # -40%
    ok, errors = compare(old, new, require_gate=True)
    assert ok, errors


def test_gate_fails_when_neither_improves() -> None:
    old = _metrics()
    new = _metrics(zip_bytes=150_000_000, t0_t3_p95_ms=2900.0)  # both < 30%
    ok, errors = compare(old, new, require_gate=True)
    assert not ok
    assert any("gate not met" in e for e in errors)


def test_gate_fails_on_unapproved_secondary_regression() -> None:
    old = _metrics()
    # Startup improves 40% (primary), but ZIP regresses 20% (beyond 10% tolerance).
    new = _metrics(zip_bytes=192_000_000, t0_t3_p95_ms=1800.0)
    ok, errors = compare(old, new, require_gate=True)
    assert not ok
    assert any("ZIP regression" in e for e in errors)


def test_too_few_samples_rejected() -> None:
    old = _metrics(samples=10)
    new = _metrics()
    ok, errors = compare(old, new, require_gate=False)
    assert not ok
    assert any("samples" in e for e in errors)


def test_different_fingerprint_rejected() -> None:
    old = _metrics(fingerprint="a|x64")
    new = _metrics(fingerprint="b|x64")
    ok, errors = compare(old, new, require_gate=False)
    assert not ok
    assert any("fingerprint" in e for e in errors)


def test_missing_size_data_rejected() -> None:
    old = _metrics(zip_bytes=0)
    new = _metrics()
    ok, errors = compare(old, new, require_gate=False)
    assert not ok
    assert any("missing size" in e for e in errors)


def test_no_require_gate_allows_small_changes() -> None:
    old = _metrics()
    new = _metrics(zip_bytes=155_000_000, t0_t3_p95_ms=2950.0)
    ok, errors = compare(old, new, require_gate=False)
    assert ok, errors


def test_main_exits_nonzero_on_gate_failure(tmp_path: Path) -> None:
    main = _mod.main
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(json.dumps({
        "name": "python", "fingerprint": "m|x64", "samples": 30,
        "zip_bytes": 160_000_000, "unzipped_bytes": 400_000_000,
        "t0_t3_p95_ms": 3000.0, "t0_t6_p95_ms": 5000.0,
    }))
    new_path.write_text(json.dumps({
        "name": "winui", "fingerprint": "m|x64", "samples": 30,
        "zip_bytes": 150_000_000, "unzipped_bytes": 400_000_000,
        "t0_t3_p95_ms": 2900.0, "t0_t6_p95_ms": 5000.0,
    }))
    assert main([str("--old=" + str(old_path)), str("--new=" + str(new_path)), "--require-gate"]) == 1
