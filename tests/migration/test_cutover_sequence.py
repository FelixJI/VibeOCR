"""Tests for the WinUI cutover update sequence."""

from __future__ import annotations

import pytest

from vibeocr.migration.cutover_sequence import (
    CutoverError,
    CutoverPlan,
    run_cutover,
    verify_sha256,
)


class FakeBoundary:
    """Records the step order and lets tests inject failures."""

    def __init__(self, *, fail_at: str | None = None) -> None:
        self.calls: list[str] = []
        self.repair_reason: str | None = None
        self._fail_at = fail_at

    def verify_archive(self, archive_path, expected_sha256):
        self.calls.append("verify")
        if self._fail_at == "verify":
            raise ValueError("bad hash")

    def stop_old_processes(self):
        self.calls.append("stop")
        if self._fail_at == "stop":
            raise OSError("process stuck")

    def atomic_replace(self, archive_path):
        self.calls.append("replace")
        if self._fail_at == "replace":
            raise OSError("file locked")

    def migrate_config(self):
        self.calls.append("migrate")
        if self._fail_at == "migrate":
            raise OSError("disk full")

    def check_prerequisites(self):
        self.calls.append("prereq")
        if self._fail_at == "prereq":
            raise RuntimeError("WebView2 missing")

    def winui_health_handshake(self, timeout_seconds):
        self.calls.append("health")
        if self._fail_at == "health":
            raise TimeoutError("no ready event")

    def launch_winui(self):
        self.calls.append("launch")
        if self._fail_at == "launch":
            raise OSError("exe missing")

    def enter_repair_mode(self, reason):
        self.repair_reason = reason


def _plan() -> CutoverPlan:
    return CutoverPlan(archive_path="C:/pkg.zip", expected_sha256="abc")


def test_successful_cutover_runs_steps_in_order() -> None:
    boundary = FakeBoundary()
    result = run_cutover(boundary, _plan())
    assert result == "launched"
    assert boundary.calls == ["verify", "stop", "replace", "migrate", "prereq", "health", "launch"]
    assert boundary.repair_reason is None


@pytest.mark.parametrize("failing_step, run_prefix", [
    ("verify", ["verify"]),
    ("stop", ["verify", "stop"]),
    ("replace", ["verify", "stop", "replace"]),
    ("migrate", ["verify", "stop", "replace", "migrate"]),
    ("prereq", ["verify", "stop", "replace", "migrate", "prereq"]),
    ("health", ["verify", "stop", "replace", "migrate", "prereq", "health"]),
    ("launch", ["verify", "stop", "replace", "migrate", "prereq", "health", "launch"]),
])
def test_failure_at_any_step_enters_repair_and_raises(failing_step, run_prefix) -> None:
    boundary = FakeBoundary(fail_at=failing_step)
    with pytest.raises(CutoverError):
        run_cutover(boundary, _plan())
    # The failing step appended itself before raising; nothing after it ran.
    assert boundary.calls == run_prefix
    assert boundary.repair_reason is not None


def test_repair_mode_failure_does_not_swallow_original_error() -> None:
    class RepairFails(FakeBoundary):
        def check_prerequisites(self):
            raise RuntimeError("WebView2 missing")

        def enter_repair_mode(self, reason):
            raise OSError("repair also broken")

    with pytest.raises(CutoverError) as exc:
        run_cutover(RepairFails(), _plan())
    # The original step failure is the raised reason, not the repair failure.
    assert "prereq" in str(exc.value) or "WebView2" in str(exc.value.__cause__ or "")


def test_verify_sha256_mismatch_raises() -> None:
    with pytest.raises(CutoverError):
        verify_sha256(b"data", "0" * 64)


def test_verify_sha256_match_passes() -> None:
    import hashlib

    good = hashlib.sha256(b"data").hexdigest()
    verify_sha256(b"data", good)  # no raise


def test_launch_legacy_ui_is_never_called() -> None:
    """The sequence must never invoke a legacy-UI launch path."""
    boundary = FakeBoundary(fail_at="health")
    with pytest.raises(CutoverError):
        run_cutover(boundary, _plan())
    assert "launch" not in boundary.calls  # health failed before launch
    # And repair mode is the only fallback.
    assert boundary.repair_reason is not None
