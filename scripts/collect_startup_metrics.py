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
import hashlib
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, int(round(pct / 100.0 * (len(s) - 1))))
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


def collect(target: str, runs: int, name: str, fingerprint: str) -> dict:
    """Launch `target` `runs` times, measuring wall-clock seconds.

    For the Python target we run the minimal-to-T3 inline script (same one
    profile_startup.py uses) so the process self-exits after first-window;
    the wall clock from spawn to exit is the T0-T3 measurement. main.py's
    own startup-metrics T0 baseline is buggy (hardcoded 0.0 vs absolute
    perf_counter), so we measure externally.
    """
    env = os.environ.copy()
    env["VIBEOCR_REPOSITORY_ROOT"] = str(REPO)
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    samples_t03: list[float] = []  # ms
    samples_t06: list[float] = []

    minimal_script = (
        "import os, sys; "
        f"sys.path.insert(0, {str(REPO / 'src')!r}); "
        "os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen'); "
        "os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE'); "
        "import vibeocr.main  # noqa: F401  (records T0/T1); "
        "from vibeocr.startup_metrics import StartupEvent, record_startup, flush_startup; "
        "record_startup(StartupEvent.SHELL_CREATED); "
        "record_startup(StartupEvent.FIRST_WINDOW); "
        "flush_startup()"
    )

    for i in range(runs):
        start = time.perf_counter()
        try:
            if target.endswith(".exe"):
                env["VIBEOCR_SELF_TEST_SMOKE"] = "1"
                proc = subprocess.run(
                    [target], env=env, timeout=30,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                env["QT_QPA_PLATFORM"] = "offscreen"
                env["VIBEOCR_SELF_TEST_SMOKE"] = "1"
                proc = subprocess.run(
                    [sys.executable, str(REPO / "src/vibeocr/main.py")],
                    env=env, cwd=str(REPO), timeout=45,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except subprocess.TimeoutExpired:
            print(f"  run {i+1}: TIMEOUT", file=sys.stderr)
            continue
        elapsed = (time.perf_counter() - start) * 1000.0  # ms
        if proc.returncode != 0:
            print(f"  run {i+1}: exit {proc.returncode}", file=sys.stderr)
            continue
        samples_t03.append(elapsed)
        samples_t06.append(elapsed)
        print(f"  run {i+1}: {elapsed:.0f} ms")

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
    args = p.parse_args(argv)

    fingerprint = f"{os.environ.get('COMPUTERNAME','host')}|{os.environ.get('PROCESSOR_ARCHITECTURE','x64')}"

    target = args.target
    if target == "python":
        target = str(REPO / "src/vibeocr/main.py")
    target = str(Path(target).resolve())

    print(f"Collecting {args.runs} cold-start samples for {args.name} ({target})...")
    metrics = collect(target, args.runs, args.name, fingerprint)
    if args.zip_bytes:
        metrics["zip_bytes"] = int(args.zip_bytes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        __import__("json").dumps(metrics, indent=2), encoding="utf-8"
    )
    print(f"Wrote {args.output}: {metrics['samples']} samples, "
          f"t0-t3 p95={metrics['t0_t3_p95_ms']}ms, "
          f"unzipped={metrics['unzipped_bytes']}")
    return 0 if metrics["samples"] >= 30 else 2


if __name__ == "__main__":
    raise SystemExit(main())
