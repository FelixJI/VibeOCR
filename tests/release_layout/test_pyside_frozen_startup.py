"""PySide Classic 冻结入口 smoke 门禁测试。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "verify_pyside_artifact.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_pyside_artifact_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_startup_smoke_requires_t3(monkeypatch, tmp_path: Path) -> None:
    verifier = _load_verifier()
    exe = tmp_path / "VibeOCR.exe"
    exe.write_bytes(b"MZ")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        trace = Path(kwargs["env"]["VIBEOCR_STARTUP_TRACE"])
        trace.write_text(
            json.dumps({"T0": 0.0, "T1": 0.1, "T2": 0.2, "T3": 0.3}) + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    verifier._verify_frozen_startup(tmp_path)

    assert captured["command"] == [str(exe)]
    assert captured["env"]["VIBEOCR_SELF_TEST_SMOKE"] == "t3"
    assert captured["env"]["QT_QPA_PLATFORM"] == "offscreen"
    assert not (tmp_path / ".startup-smoke.jsonl").exists()


def test_frozen_startup_smoke_rejects_missing_trace(
    monkeypatch, tmp_path: Path
) -> None:
    verifier = _load_verifier()
    (tmp_path / "VibeOCR.exe").write_bytes(b"MZ")
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=""),
    )

    with pytest.raises(RuntimeError, match="produced no trace"):
        verifier._verify_frozen_startup(tmp_path)
