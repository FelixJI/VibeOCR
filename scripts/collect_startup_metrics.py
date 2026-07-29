#!/usr/bin/env python3
"""Collect cold-start metrics for the perf gate.

Launches the target (Python main.py or WinUI exe) N times as fresh
processes, measures wall-clock time to process exit (the apps self-exit
in smoke mode), and emits the flat JSON shape that
``scripts/compare_release_metrics.py`` consumes:

    {name, fingerprint, samples, zip_bytes, unzipped_bytes,
     t0_t3_p95_ms, t0_t6_p95_ms, rss_idle_mb, handle_count_idle}

For the Python target we set VIBEOCR_STARTUP_PROFILE_MODE=1 so main.py
exits after first-window; for the WinUI target we pass --self-test-smoke
which exits after the window renders. T0-T3 = launch-to-exit wall clock;
T0-T6 mirrors it (the smoke run reaches interactive before exit).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, round(pct / 100.0 * (len(s) - 1)))
    return s[idx]


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _read_trace(path: Path) -> tuple[float, float]:
    """Return real T0-T3/T0-T6 milliseconds from the last JSONL record."""
    if not path.exists():
        raise ValueError("startup trace was not created")
    lines = [line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not lines:
        raise ValueError("startup trace is empty")
    data = json.loads(lines[-1])
    try:
        t0 = float(data["T0"])
        t3 = float(data["T3"])
        t6 = float(data["T6"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("startup trace must contain numeric T0, T3 and T6") from error
    if not t0 <= t3 <= t6:
        raise ValueError(f"startup milestones are not monotonic: T0={t0}, T3={t3}, T6={t6}")
    return (t3 - t0) * 1000.0, (t6 - t0) * 1000.0


def collect(
    target: str,
    runs: int,
    name: str,
    fingerprint: str,
    *,
    timeout_seconds: float = 120.0,
) -> dict:
    """Launch ``target`` and collect its real T0/T3/T6 trace milestones."""
    if runs < 1:
        raise ValueError("runs must be at least 1")
    env = os.environ.copy()
    env["VIBEOCR_REPOSITORY_ROOT"] = str(REPO)
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    env["VIBEOCR_SELF_TEST_SMOKE"] = "t6"
    samples_t03: list[float] = []  # ms
    samples_t06: list[float] = []

    for i in range(runs):
        with tempfile.TemporaryDirectory(prefix="vibeocr-startup-") as temp_dir:
            trace_path = Path(temp_dir) / "trace.jsonl"
            env["VIBEOCR_STARTUP_TRACE"] = str(trace_path)
            command = [target] if target.lower().endswith(".exe") else [sys.executable, target]
            if not target.lower().endswith(".exe"):
                env["QT_QPA_PLATFORM"] = "offscreen"
            try:
                proc = subprocess.run(
                    command,
                    env=env,
                    cwd=str(REPO),
                    timeout=timeout_seconds,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if proc.returncode != 0:
                    raise ValueError(f"process exited with {proc.returncode}")
                t03, t06 = _read_trace(trace_path)
            except (subprocess.TimeoutExpired, OSError, ValueError, json.JSONDecodeError) as error:
                print(f"  run {i + 1}: INVALID ({error})", file=sys.stderr)
                continue
            samples_t03.append(t03)
            samples_t06.append(t06)
            print(f"  run {i + 1}: T0-T3={t03:.0f} ms, T0-T6={t06:.0f} ms")

    # Unzipped size: Python uses the venv + src; WinUI uses its bin dir.
    if target.endswith(".exe"):
        unzipped = _dir_size(Path(target).parent)
        zip_bytes = 0
    else:
        unzipped = _dir_size(REPO / "src") + _dir_size(REPO / ".venv/Lib/site-packages")
        zip_bytes = 0

    return {
        "name": name,
        "fingerprint": fingerprint,
        "samples": len(samples_t03),
        "zip_bytes": int(zip_bytes),
        "unzipped_bytes": int(unzipped),
        "t0_t3_p95_ms": round(_percentile(samples_t03, 95), 1),
        "t0_t6_p95_ms": round(_percentile(samples_t06, 95), 1),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target", required=True, help="Path to exe or 'python'")
    p.add_argument("--runs", type=int, default=30)
    p.add_argument("--name", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--zip-bytes", type=int, default=0,
                   help="Release ZIP size in bytes (measured separately).")
    p.add_argument("--timeout-seconds", type=float, default=120.0)
    args = p.parse_args(argv)

    fingerprint = f"{os.environ.get('COMPUTERNAME','host')}|{os.environ.get('PROCESSOR_ARCHITECTURE','x64')}"

    target = args.target
    if target == "python":
        target = str(
            REPO
            / "apps/vibeocr-pyside/src/vibeocr/classic/main.py"
        )
    target = str(Path(target).resolve())

    print(f"Collecting {args.runs} cold-start samples for {args.name} ({target})...")
    try:
        metrics = collect(
            target,
            args.runs,
            args.name,
            fingerprint,
            timeout_seconds=args.timeout_seconds,
        )
    except ValueError as error:
        p.error(str(error))
    if args.zip_bytes:
        metrics["zip_bytes"] = int(args.zip_bytes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    print(f"Wrote {args.output}: {metrics['samples']} samples, "
          f"t0-t3 p95={metrics['t0_t3_p95_ms']}ms, "
          f"unzipped={metrics['unzipped_bytes']}")
    return 0 if metrics["samples"] == args.runs else 2


if __name__ == "__main__":
    raise SystemExit(main())
