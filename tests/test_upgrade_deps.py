"""Tests for the optional .NET lock update integration in qa/upgrade_deps.py."""

from __future__ import annotations

from qa import upgrade_deps


def test_dotnet_lock_update_dry_run_does_not_require_powershell(
    monkeypatch, capsys
):
    monkeypatch.setattr(upgrade_deps.shutil, "which", lambda _name: None)

    assert upgrade_deps.run_dotnet_lock_update(dry_run=True) == 0
    output = capsys.readouterr().out
    assert "scripts/update_dotnet_locks.ps1" in output


def test_dotnet_lock_update_prefers_pwsh(monkeypatch):
    calls: list[list[str]] = []

    monkeypatch.setattr(
        upgrade_deps.shutil,
        "which",
        lambda name: "C:/Tools/pwsh.exe" if name == "pwsh" else None,
    )
    monkeypatch.setattr(
        upgrade_deps,
        "run_command_streaming",
        lambda command: calls.append(command) or 0,
    )

    assert upgrade_deps.run_dotnet_lock_update() == 0
    assert calls == [
        [
            "C:/Tools/pwsh.exe",
            "-NoProfile",
            "-File",
            str(upgrade_deps.DOTNET_LOCK_UPDATE_SCRIPT),
        ]
    ]


def test_powershell_fallback_bypasses_execution_policy(monkeypatch):
    monkeypatch.setattr(
        upgrade_deps.shutil,
        "which",
        lambda name: "C:/Windows/powershell.exe"
        if name == "powershell"
        else None,
    )

    assert upgrade_deps._powershell_prefix() == [
        "C:/Windows/powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
    ]
