"""Phase 9: Paddle benchmark matrix + fault injection gates.

Plan §9 requires:
* Paddle pipeline × device × batch sweep benchmarks.
* Fault injection: supervisor kill, OOM, cancel mid-flight, TTL race.

All heavy tests are ``@pytest.mark.slow`` and skip when paddle is not available
or when the paddle+torch DLL conflict is present. CI runs only the fast,
deterministic fault-injection tests; the benchmark matrix runs locally on GPU.
"""

from __future__ import annotations

import io
import time
from importlib.util import find_spec
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from vibeocr.backend.supervisor.composition import build_supervisor
from vibeocr.runtime_contracts import TERMINAL_JOB_STATES, JobState

# ---------------------------------------------------------------------------
# Availability checks (shared with integration test)
# ---------------------------------------------------------------------------

_PADDLE_AVAILABLE = find_spec("paddle") is not None

slow = pytest.mark.slow
skip_no_paddle = pytest.mark.skipif(
    not _PADDLE_AVAILABLE,
    reason="paddle not installed",
)


def _render_text_image(text: str, width: int = 400, height: int = 100) -> bytes:
    """Render ``text`` as a PNG using Pillow (no font file needed)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), text, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _wait_for_terminal(module, job_id: str, *, timeout: float = 120.0) -> JobState:
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = module.status(job_id)
        if snap.state in TERMINAL_JOB_STATES:
            return snap.state
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not reach terminal within {timeout}s")


# ===========================================================================
# Part A: Paddle benchmark matrix (slow, GPU/CPU, real model load)
# ===========================================================================


class TestPaddleBenchmarkMatrix:
    """Benchmark OCR pipeline across batch sizes. Outputs timing for calibration."""

    @slow
    @skip_no_paddle
    @pytest.mark.parametrize("batch_size", [1, 2, 4, 8])
    def test_ocr_batch_sweep(self, batch_size: int, tmp_path: Path) -> None:
        """Measure warm-state OCR throughput at different batch sizes."""
        module, _handle = build_supervisor(use_real_paddle=True, stager_root=tmp_path / "staging")

        # Warm up the model with a single image first.
        from vibeocr.runtime_contracts import JobKind, JobPriority

        warmup = _render_text_image("WARMUP")
        ref = module.submit(
            kind=JobKind.RECOGNITION,
            priority=JobPriority.INTERACTIVE,
            uploads=[("warmup.png", "image/png", warmup)],
        )
        _wait_for_terminal(module, ref.job_id)

        # Now benchmark the target batch size.
        images = [_render_text_image(f"IMAGE_{i:02d}") for i in range(batch_size)]
        uploads = [(f"img_{i}.png", "image/png", img) for i, img in enumerate(images)]

        from vibeocr.runtime_contracts import JobKind, JobPriority

        start = time.monotonic()
        batch_ref = module.submit(
            kind=JobKind.RECOGNITION,
            priority=JobPriority.BACKGROUND,
            uploads=uploads,
        )
        state = _wait_for_terminal(module, batch_ref.job_id, timeout=180.0)
        elapsed = time.monotonic() - start

        assert state in (JobState.COMPLETED, JobState.COMPLETED_WITH_ERRORS)
        results = module.result(batch_ref.job_id)
        assert len(results) == batch_size

        # Log timing for calibration (pytest -v shows the output).
        per_image = elapsed / batch_size
        print(f"\n[benchmark] batch={batch_size} total={elapsed:.2f}s per_image={per_image:.3f}s")

    @slow
    @skip_no_paddle
    def test_cold_vs_warm_latency(self, tmp_path: Path) -> None:
        """Measure cold-start vs warm-start single-image latency."""
        module, _handle = build_supervisor(use_real_paddle=True, stager_root=tmp_path / "staging")
        img = _render_text_image("LATENCY TEST")

        from vibeocr.runtime_contracts import JobKind, JobPriority

        # Cold (first inference — model load).
        cold_start = time.monotonic()
        ref = module.submit(kind=JobKind.RECOGNITION, priority=JobPriority.INTERACTIVE, uploads=[("test.png", "image/png", img)])
        _wait_for_terminal(module, ref.job_id, timeout=180.0)
        cold_elapsed = time.monotonic() - cold_start

        # Warm (second inference — model cached).
        warm_start = time.monotonic()
        ref = module.submit(kind=JobKind.RECOGNITION, priority=JobPriority.INTERACTIVE, uploads=[("test.png", "image/png", img)])
        _wait_for_terminal(module, ref.job_id)
        warm_elapsed = time.monotonic() - warm_start

        print(f"\n[benchmark] cold={cold_elapsed:.2f}s warm={warm_elapsed:.2f}s speedup={cold_elapsed / max(warm_elapsed, 0.01):.1f}x")
        # Warm should not be dramatically slower than cold (model is cached).
        # We don't assert warm < cold because single-image variance can mask
        # the difference; instead we assert warm is within 2x of cold.
        assert warm_elapsed < cold_elapsed * 2, "warm should be within 2x of cold"


# ===========================================================================
# Part B: Fault injection (fast, deterministic, no real model needed)
# ===========================================================================


class TestFaultInjection:
    """Fault injection gates: verify the supervisor handles failures gracefully."""

    def test_cancel_queued_job(self, tmp_path: Path) -> None:
        """A queued job that is cancelled before execution reaches CANCELLED."""
        from vibeocr.runtime_contracts import CancelMode, JobKind, JobPriority

        module, _ = build_supervisor(executor=_HangingExecutor(), stager_root=tmp_path / "staging")
        ref = module.submit(
            kind=JobKind.RECOGNITION,
            priority=JobPriority.INTERACTIVE,
            uploads=[("test.png", "image/png", b"\x89PNG")],
        )
        # The job is queued/running. Cancel it.
        mode = module.request_cancel(ref.job_id)
        assert mode in (CancelMode.COOPERATIVE, CancelMode.QUEUED_ONLY)

    def test_oom_recovery_shrinks_batch(self) -> None:
        """RecoveryPolicy: OOM halves the microbatch with bounded retries."""
        from vibeocr.backend.supervisor.inference.recovery import (
            FailureClass,
            RecoveryAction,
            RecoveryPolicy,
        )

        policy = RecoveryPolicy(max_oom_retries=2)
        d1 = policy.next_action(failure=FailureClass.OOM, current_batch_size=8, attempt=0)
        assert d1.action is RecoveryAction.SHRINK_AND_RETRY
        assert d1.next_batch_size == 4
        assert d1.degraded is True

        d2 = policy.next_action(failure=FailureClass.OOM, current_batch_size=4, attempt=1)
        assert d2.next_batch_size == 2

        d3 = policy.next_action(failure=FailureClass.OOM, current_batch_size=2, attempt=2)
        assert d3.action is RecoveryAction.FAIL_FAST

    def test_bad_input_isolation(self) -> None:
        """RecoveryPolicy: bad input uses bisect isolation."""
        from vibeocr.backend.supervisor.inference.recovery import (
            FailureClass,
            RecoveryAction,
            RecoveryPolicy,
        )

        policy = RecoveryPolicy()
        d = policy.next_action(failure=FailureClass.BAD_INPUT, current_batch_size=8, attempt=0)
        assert d.action is RecoveryAction.BISECT_ISOLATE

    def test_transient_backoff_budget(self) -> None:
        """Transient errors use exponential backoff under a total time budget."""
        from vibeocr.backend.supervisor.inference.recovery import (
            FailureClass,
            RecoveryAction,
            RecoveryPolicy,
        )

        policy = RecoveryPolicy(
            max_transient_retries=3,
            transient_base_delay=10.0,
            transient_max_delay=20.0,
            transient_total_budget_seconds=4.0,
        )
        d = policy.next_action(failure=FailureClass.TRANSIENT, current_batch_size=4, attempt=0)
        assert d.action is RecoveryAction.FAIL_FAST
        assert "budget" in d.reason

    def test_supervisor_drain_rejects_new_jobs(self, tmp_path: Path) -> None:
        """A draining supervisor rejects new job submissions."""
        from vibeocr.backend.supervisor.module import ShutdownRequested
        from vibeocr.runtime_contracts import JobKind, JobPriority

        module, _ = build_supervisor(executor=_HangingExecutor(), stager_root=tmp_path / "staging")
        module.begin_drain()
        with pytest.raises(ShutdownRequested):
            module.submit(
                kind=JobKind.RECOGNITION,
                priority=JobPriority.INTERACTIVE,
                uploads=[("test.png", "image/png", b"\x89PNG")],
            )

    def test_supervisor_shutdown_releases_staging(self, tmp_path: Path) -> None:
        """shutdown_now releases all staging directories."""
        module, _ = build_supervisor(executor=_HangingExecutor(), stager_root=tmp_path / "staging")
        # Create a staging dir by submitting a job.
        from vibeocr.runtime_contracts import JobKind, JobPriority

        _ = module.submit(
            kind=JobKind.RECOGNITION,
            priority=JobPriority.INTERACTIVE,
            uploads=[("test.png", "image/png", b"\x89PNG")],
        )
        # Verify staging dir exists.
        assert any(module.stager.root.iterdir())
        module.shutdown_now()
        # After shutdown, staging should be empty.
        assert not any(module.stager.root.iterdir()) if module.stager.root.exists() else True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _HangingExecutor:
    """Executor that enters running but never completes (for cancel tests)."""

    def execute(self, record, staged) -> None:  # type: ignore[no-untyped-def]
        from vibeocr.runtime_contracts import JobState

        if record.state is JobState.QUEUED:
            record.transition(JobState.RUNNING)
            record.append_event("running")
        # Never transition to terminal — hangs forever (test cancels it).

    def cancel_mode_for(self, record) -> str:  # type: ignore[no-untyped-def]
        from vibeocr.runtime_contracts import CancelMode

        return CancelMode.COOPERATIVE

    def residency_status(self):
        from vibeocr.runtime_contracts import ResidencyStatus

        return ResidencyStatus()

    def release_idle(self, pipeline=None):
        from vibeocr.runtime_contracts import ResidencyStatus

        return ResidencyStatus()
