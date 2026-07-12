"""Tests for WorkerHost process lifecycle (Task 1.6 Green).

Verifies the entry point contract: ``--self-test`` emits one line of
machine-readable JSON and exits 0; ``--help`` exits 0; invalid args exit
non-zero. The full pipe round-trip is covered by the named_pipe + handler
tests; here we focus on the process entry point.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

from vibeocr.worker_host.main import main

if TYPE_CHECKING:
    import pytest

# ---------------------------------------------------------------------------
# main() as a library: self-test
# ---------------------------------------------------------------------------


def test_self_test_returns_zero_and_prints_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--self-test"])
    captured = capsys.readouterr()
    assert rc == 0
    # Output is exactly one line of JSON.
    lines = [ln for ln in captured.out.strip().splitlines() if ln.strip()]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["protocol_version"] == 1
    assert "worker_version" in doc
    assert isinstance(doc["capabilities"], list)


def test_help_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--help"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "vibeocr-worker" in (captured.out + captured.err)


def test_no_args_returns_non_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([])
    assert rc != 0


def test_unknown_arg_returns_non_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["--definitely-not-a-flag"])
    assert rc != 0


# ---------------------------------------------------------------------------
# Subprocess: the real entry point behaves the same
# ---------------------------------------------------------------------------


def test_self_test_as_subprocess() -> None:
    """Run the worker as a real subprocess to validate the console entry point."""
    result = subprocess.run(
        [sys.executable, "-m", "vibeocr.worker_host.main", "--self-test"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["protocol_version"] == 1
