"""Verify the PySide Classic ZIP and exact backend-wheel binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path


def _verify_frozen_startup(root: Path, timeout_seconds: float = 45.0) -> None:
    """真实启动冻结入口并要求它到达 T3 首窗里程碑。"""
    exe = root / "VibeOCR.exe"
    trace = root / ".startup-smoke.jsonl"
    trace.unlink(missing_ok=True)
    env = os.environ.copy()
    env["VIBEOCR_SELF_TEST_SMOKE"] = "t3"
    env["VIBEOCR_STARTUP_TRACE"] = str(trace)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("VIBEOCR_REPOSITORY_ROOT", None)
    try:
        result = subprocess.run(
            [str(exe)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            ),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"frozen PySide startup smoke exited with {result.returncode}: "
                f"{result.stderr.strip()}"
            )
        if not trace.is_file():
            raise RuntimeError("frozen PySide startup smoke produced no trace")
        records = [
            json.loads(line)
            for line in trace.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not records or not {"T0", "T1", "T2", "T3"}.issubset(records[-1]):
            raise RuntimeError(
                f"frozen PySide startup smoke did not reach T3: {records[-1:] or 'empty'}"
            )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"frozen PySide startup smoke timed out after {timeout_seconds:.0f}s"
        ) from error
    finally:
        trace.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="vibeocr-pyside-verify-") as temp:
        with zipfile.ZipFile(args.artifact) as archive:
            archive.extractall(temp)
        roots = list(Path(temp).iterdir())
        root = roots[0] if len(roots) == 1 and roots[0].is_dir() else Path(temp)
        required = [root / "VibeOCR.exe", root / "product-manifest.json"]
        missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
        if missing:
            raise RuntimeError(f"required PySide files missing: {missing}")
        if (root / "VibeOCR.WinUI.exe").exists():
            raise RuntimeError("WinUI executable present in PySide artifact")
        manifest = json.loads((root / "product-manifest.json").read_text(encoding="utf-8-sig"))
        if manifest.get("frontend") != "pyside":
            raise RuntimeError("product manifest frontend is not pyside")
        records = manifest.get("python_wheels", [])
        expected = {
            "vibeocr",
            "vibeocr-backend",
            "vibeocr-client-py",
            "vibeocr-contracts-py",
            "vibeocr-pyside",
        }
        if {record.get("distribution") for record in records} != expected:
            raise RuntimeError("bound Python wheel set is incomplete")
        for record in records:
            bound = root / "backend" / str(record.get("file", ""))
            if not bound.is_file():
                raise RuntimeError(f"bound wheel is missing: {bound.name}")
            if hashlib.sha256(bound.read_bytes()).hexdigest() != record.get("sha256"):
                raise RuntimeError(f"bound wheel hash mismatch: {bound.name}")
        wheel = root / "backend" / str(manifest.get("backend_wheel", ""))
        if not wheel.is_file():
            raise RuntimeError("bound backend wheel is missing")
        actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if actual != manifest.get("backend_sha256"):
            raise RuntimeError("bound backend wheel hash mismatch")
        if os.name == "nt":
            _verify_frozen_startup(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
