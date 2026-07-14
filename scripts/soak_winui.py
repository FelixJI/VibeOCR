#!/usr/bin/env python3
"""WinUI stability soak harness.

Loops the WinUI app launch -> worker handshake -> smoke-exit cycle,
injecting a worker crash mid-run, and samples residual process / handle
growth. Run for the desired duration (e.g. 8h for the Phase 5.5
soak gate); a non-growing baseline across the run is the pass criterion.

Usage:
    python scripts/soak_winui.py --winui-exe <path> --duration-hours 8 \\
        --report reports/local/soak-report.json

Pass criteria (the script exits non-zero on violation):
- No upward trend in residual VibeOCR processes between iterations.
- Injected crash iterations must emit an explicit successful recovery result.
- Process/handle monitoring failures fail the gate instead of becoming zeros.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _process_snapshot() -> tuple[int, int] | None:
    """Return residual VibeOCR process count and aggregate handle count."""
    script = (
        "$all=@(Get-CimInstance Win32_Process); "
        "$p=@($all | Where-Object { "
        "$_.Name -like 'VibeOCR*.exe' -or "
        "($_.Name -like 'python*.exe' -and $_.CommandLine -match 'vibeocr\\.worker_host') }); "
        "$h=0; foreach($item in $p){ try { $h += (Get-Process -Id $item.ProcessId).HandleCount } catch {} }; "
        "[pscustomobject]@{processes=$p.Count;handles=$h} | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            text=True, timeout=20,
        )
        data = json.loads(out)
        return int(data["processes"]), int(data["handles"])
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def run_iteration(
    winui_exe: str,
    crash_inject: bool,
) -> tuple[int, float, dict[str, object] | None]:
    """One launch cycle; return exit code, elapsed ms, and verified app result."""
    env = os.environ.copy()
    env["VIBEOCR_REPOSITORY_ROOT"] = str(REPO)
    env["VIBEOCR_SELF_TEST_SMOKE"] = "t6"
    if crash_inject:
        # Signal the worker supervisor to inject a crash on this run.
        env["VIBEOCR_SOAK_INJECT_CRASH"] = "1"
    with tempfile.TemporaryDirectory(prefix="vibeocr-soak-") as temp_dir:
        result_path = Path(temp_dir) / "result.json"
        env["VIBEOCR_SOAK_RESULT"] = str(result_path)
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                [winui_exe], env=env, timeout=120,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = -1
        elapsed = (time.perf_counter() - start) * 1000.0
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            result = None
        return code, elapsed, result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--winui-exe", required=True)
    p.add_argument("--duration-hours", type=float, default=8.0)
    p.add_argument("--report", type=Path, default=Path("reports/local/soak-report.json"))
    p.add_argument("--max-iterations", type=int, default=0, help="0 = unlimited by duration")
    p.add_argument("--pause-seconds", type=float, default=1.0)
    p.add_argument("--max-process-drift", type=int, default=2)
    p.add_argument("--max-handle-drift", type=int, default=100)
    args = p.parse_args(argv)

    if args.duration_hours <= 0:
        p.error("--duration-hours must be positive")
    if args.max_iterations < 0 or args.pause_seconds < 0:
        p.error("iteration and pause values must be non-negative")
    if not Path(args.winui_exe).is_file():
        p.error(f"WinUI app not found: {args.winui_exe}")

    deadline = time.perf_counter() + args.duration_hours * 3600.0
    iterations = 0
    failures = 0
    process_samples: list[int] = []
    handle_samples: list[int] = []
    elapsed_samples: list[float] = []
    start_snapshot = _process_snapshot()
    monitoring_errors = 0
    if start_snapshot is None:
        start_procs, start_handles = 0, 0
        monitoring_errors += 1
    else:
        start_procs, start_handles = start_snapshot

    print(f"Soak: {args.duration_hours}h, app={args.winui_exe}, baseline procs={start_procs}")
    while time.perf_counter() < deadline:
        if args.max_iterations and iterations >= args.max_iterations:
            break
        # Inject a crash every 10th iteration to exercise recovery.
        crash = (iterations % 10 == 9)
        code, elapsed, result = run_iteration(args.winui_exe, crash)
        iterations += 1
        elapsed_samples.append(elapsed)
        snapshot = _process_snapshot()
        if snapshot is None:
            procs, handles = 0, 0
            monitoring_errors += 1
        else:
            procs, handles = snapshot
            process_samples.append(procs)
            handle_samples.append(handles)
        result_ok = (
            code == 0
            and result is not None
            and result.get("crash_requested") is crash
            and result.get("recovered") is True
        )
        if not result_ok:
            failures += 1
            print(f"  iter {iterations}: FAIL exit={code} result={result} ({elapsed:.0f}ms)")
        else:
            tag = " (crash-injected)" if crash else ""
            print(f"  iter {iterations}: exit={code}{tag} ({elapsed:.0f}ms, procs={procs})")
        # Short pause between iterations.
        time.sleep(args.pause_seconds)

    end_snapshot = _process_snapshot()
    if end_snapshot is None:
        end_procs, end_handles = 0, 0
        monitoring_errors += 1
    else:
        end_procs, end_handles = end_snapshot
    proc_drift = end_procs - start_procs
    handle_drift = end_handles - start_handles
    report = {
        "duration_hours": args.duration_hours,
        "iterations": iterations,
        "failures": failures,
        "baseline_processes": start_procs,
        "final_processes": end_procs,
        "process_drift": proc_drift,
        "baseline_handles": start_handles,
        "final_handles": end_handles,
        "handle_drift": handle_drift,
        "monitoring_errors": monitoring_errors,
        "shared_memory_check": "covered indirectly by recovered worker/process lifetime; no fake counter",
        "max_processes_observed": max(process_samples) if process_samples else 0,
        "max_handles_observed": max(handle_samples) if handle_samples else 0,
        "median_elapsed_ms": sorted(elapsed_samples)[len(elapsed_samples) // 2] if elapsed_samples else 0,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSoak complete: {iterations} iterations, {failures} non-injected failures")
    print(f"Process drift: {proc_drift} (baseline {start_procs} -> final {end_procs})")
    print(f"Report: {args.report}")

    # Pass criterion: no failures beyond injected crashes, and process count
    # did not grow without bound (allow small noise).
    ok = (
        iterations > 0
        and failures == 0
        and monitoring_errors == 0
        and proc_drift <= args.max_process_drift
        and handle_drift <= args.max_handle_drift
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
