from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import soak_winui


def test_process_snapshot_parses_process_and_handle_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        soak_winui.subprocess,
        "check_output",
        lambda *args, **kwargs: '{"processes":2,"handles":120}',
    )

    assert soak_winui._process_snapshot() == (2, 120)


def test_process_snapshot_targets_supervisor_not_worker_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def fake_check_output(command, **kwargs):
        del kwargs
        captured.extend(command)
        return '{"processes":0,"handles":0}'

    monkeypatch.setattr(
        soak_winui.subprocess,
        "check_output",
        fake_check_output,
    )

    assert soak_winui._process_snapshot() == (0, 0)
    command_line = " ".join(captured)
    assert "vibeocr\\.supervisor\\.main" in command_line
    assert "worker_host" not in command_line


def test_process_snapshot_failure_is_not_reported_as_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args, **kwargs):
        del args, kwargs
        raise OSError("monitor unavailable")

    monkeypatch.setattr(soak_winui.subprocess, "check_output", fail)

    assert soak_winui._process_snapshot() is None


def test_crash_iteration_requires_explicit_recovery_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "VibeOCR.WinUI.exe"
    app.write_bytes(b"MZ")

    observed_command: list[str] = []

    def fake_run(command, *, env, **kwargs):
        observed_command.extend(command)
        del kwargs
        Path(env["VIBEOCR_SOAK_RESULT"]).write_text(
            json.dumps({"crash_requested": True, "recovered": True, "error": None}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(soak_winui.subprocess, "run", fake_run)

    code, _, result = soak_winui.run_iteration(str(app), crash_inject=True)

    assert code == 0
    assert result == {"crash_requested": True, "recovered": True, "error": None}
    assert observed_command[-2:] == ["--profile", "winui-dev"]


def test_zero_duration_is_rejected(tmp_path: Path) -> None:
    app = tmp_path / "VibeOCR.WinUI.exe"
    app.write_bytes(b"MZ")

    with pytest.raises(SystemExit):
        soak_winui.main(["--winui-exe", str(app), "--duration-hours", "0"])
