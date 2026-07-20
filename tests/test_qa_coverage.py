from __future__ import annotations

import subprocess
import sys

from qa import coverage


def _completed(returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode)


def test_run_coverage_uses_current_python_and_workspace_package(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(
        coverage,
        "run_command",
        lambda command: commands.append(command) or _completed(),
    )

    result = coverage.run_coverage(
        html=True,
        xml=True,
        min_coverage=80,
        verbose=False,
    )

    assert result == 0
    assert commands == [
        [
            sys.executable,
            "-m",
            "pytest",
            "--cov=vibeocr",
            "--cov-report=term-missing",
            "--cov-report=html:htmlcov",
            "--cov-report=xml:coverage.xml",
            "--cov-fail-under=80",
            *(f"--ignore={path}" for path in coverage.PYTHON_COVERAGE_IGNORES),
            "tests/",
        ]
    ]


def test_run_quick_coverage_uses_same_source_target(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(
        coverage,
        "run_command",
        lambda command: commands.append(command) or _completed(),
    )

    result = coverage.run_quick_coverage()

    assert result == 0
    assert commands == [
        [
            sys.executable,
            "-m",
            "pytest",
            "--cov=vibeocr",
            "--cov-report=term",
            "-q",
            *(f"--ignore={path}" for path in coverage.PYTHON_COVERAGE_IGNORES),
            "tests/",
        ]
    ]
