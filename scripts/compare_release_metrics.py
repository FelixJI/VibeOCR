#!/usr/bin/env python3
"""Compare old (Python/PySide6) vs new (WinUI) release metrics and enforce the cutover gate.

Gate (pass requires ALL):
- Each side has at least 30 samples.
- Both sides come from the same machine fingerprint.
- Both sides report ZIP size, unzipped size, and T0-T3/T0-T6 startup p95.
- At least one of (ZIP size, cold-start T0-T3 p95) improved by >= 30%.
- No unapproved significant regression in the other dimension (> 10%).

Usage:
    python scripts/compare_release_metrics.py --old a.json --new b.json --require-gate
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

MIN_SAMPLES = 30
MIN_IMPROVEMENT = 0.30  # 30%
MAX_REGRESSION = 0.10  # 10% unapproved regression tolerated on the non-primary metric


@dataclass
class Metrics:
    name: str
    fingerprint: str
    samples: int
    zip_bytes: int
    unzipped_bytes: int
    t0_t3_p95_ms: float
    t0_t6_p95_ms: float
    rss_idle_mb: float | None = None
    handle_count_idle: int | None = None


def _load(path: Path) -> Metrics:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Metrics(
        name=data.get("name", path.stem),
        fingerprint=data["fingerprint"],
        samples=int(data["samples"]),
        zip_bytes=int(data["zip_bytes"]),
        unzipped_bytes=int(data["unzipped_bytes"]),
        t0_t3_p95_ms=float(data["t0_t3_p95_ms"]),
        t0_t6_p95_ms=float(data["t0_t6_p95_ms"]),
        rss_idle_mb=data.get("rss_idle_mb"),
        handle_count_idle=data.get("handle_count_idle"),
    )


def _pct_change(old: float, new: float) -> float:
    """Negative = improvement (smaller new). Positive = regression."""
    if old == 0:
        return 0.0
    return (new - old) / old


def compare(old: Metrics, new: Metrics, *, require_gate: bool) -> tuple[bool, list[str]]:
    errors: list[str] = []

    if old.samples < MIN_SAMPLES:
        errors.append(f"old has {old.samples} samples (< {MIN_SAMPLES})")
    if new.samples < MIN_SAMPLES:
        errors.append(f"new has {new.samples} samples (< {MIN_SAMPLES})")
    if old.fingerprint != new.fingerprint:
        errors.append(
            f"machine fingerprint differs: old={old.fingerprint} new={new.fingerprint}"
        )
    for label, m in (("old", old), ("new", new)):
        if m.zip_bytes <= 0 or m.unzipped_bytes <= 0:
            errors.append(f"{label} missing size data")
        if m.t0_t3_p95_ms <= 0 or m.t0_t6_p95_ms <= 0:
            errors.append(f"{label} missing startup p95 data")

    if errors:
        return False, errors

    zip_change = _pct_change(old.zip_bytes, new.zip_bytes)
    t03_change = _pct_change(old.t0_t3_p95_ms, new.t0_t3_p95_ms)
    t06_change = _pct_change(old.t0_t6_p95_ms, new.t0_t6_p95_ms)

    if require_gate:
        zip_improved = zip_change <= -MIN_IMPROVEMENT
        t03_improved = t03_change <= -MIN_IMPROVEMENT
        if not (zip_improved or t03_improved):
            errors.append(
                f"gate not met: need >={int(MIN_IMPROVEMENT*100)}% improvement in ZIP or "
                f"T0-T3 p95; got zip {zip_change*100:+.1f}%, t0-t3 {t03_change*100:+.1f}%"
            )
        # The other (non-primary) dimension must not regress beyond MAX_REGRESSION.
        if zip_improved and t03_change > MAX_REGRESSION:
            errors.append(
                f"unapproved T0-T3 regression: {t03_change*100:+.1f}% (> +{int(MAX_REGRESSION*100)}%)"
            )
        if t03_improved and zip_change > MAX_REGRESSION:
            errors.append(
                f"unapproved ZIP regression: {zip_change*100:+.1f}% (> +{int(MAX_REGRESSION*100)}%)"
            )
        if t06_change > MAX_REGRESSION:
            errors.append(
                f"unapproved T0-T6 regression: {t06_change*100:+.1f}% "
                f"(> +{int(MAX_REGRESSION*100)}%)"
            )
        if old.rss_idle_mb and new.rss_idle_mb:
            rss_change = _pct_change(old.rss_idle_mb, new.rss_idle_mb)
            if rss_change > MAX_REGRESSION:
                errors.append(
                    f"unapproved idle RSS regression: {rss_change*100:+.1f}% "
                    f"(> +{int(MAX_REGRESSION*100)}%)"
                )
        if old.handle_count_idle and new.handle_count_idle:
            handle_change = _pct_change(
                float(old.handle_count_idle),
                float(new.handle_count_idle),
            )
            if handle_change > MAX_REGRESSION:
                errors.append(
                    f"unapproved handle-count regression: {handle_change*100:+.1f}% "
                    f"(> +{int(MAX_REGRESSION*100)}%)"
                )

    return len(errors) == 0, errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old", type=Path, required=True)
    p.add_argument("--new", type=Path, required=True)
    p.add_argument("--require-gate", action="store_true")
    args = p.parse_args(argv)

    old = _load(args.old)
    new = _load(args.new)
    ok, errors = compare(old, new, require_gate=args.require_gate)
    if errors:
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
    zip_change = _pct_change(old.zip_bytes, new.zip_bytes)
    t03_change = _pct_change(old.t0_t3_p95_ms, new.t0_t3_p95_ms)
    print(
        f"{old.name}->{new.name}: zip {zip_change*100:+.1f}%, "
        f"t0-t3 {t03_change*100:+.1f}%, samples old={old.samples} new={new.samples}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
