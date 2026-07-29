"""Verify the PySide Classic ZIP and exact backend-wheel binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def _verify_frozen_startup(root: Path, timeout_seconds: float = 45.0) -> None:
    """真实启动冻结入口并要求它完成 Supervisor 就绪握手。"""
    exe = root / "VibeOCR.exe"
    trace = root / ".startup-smoke.jsonl"
    result_file = root / ".startup-smoke-result.json"
    stdout_log = root / ".startup-smoke.stdout.log"
    stderr_log = root / ".startup-smoke.stderr.log"
    trace.unlink(missing_ok=True)
    result_file.unlink(missing_ok=True)
    stdout_log.unlink(missing_ok=True)
    stderr_log.unlink(missing_ok=True)
    env = os.environ.copy()
    env["VIBEOCR_SELF_TEST_SMOKE"] = "t6"
    env["VIBEOCR_STARTUP_TRACE"] = str(trace)
    env["VIBEOCR_SELF_TEST_RESULT"] = str(result_file)
    env["VIBEOCR_SELF_TEST_PYTHON"] = sys.executable
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    for variable in (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "VIBEOCR_REPOSITORY_ROOT",
    ):
        env.pop(variable, None)
    try:
        # 不使用 PIPE：启动阶段的后台清理子进程可能继承 stdout/stderr，
        # 即使主进程已 os._exit，communicate() 仍会等待继承的管道关闭并误报超时。
        with (
            stdout_log.open("wb") as stdout_handle,
            stderr_log.open("wb") as stderr_handle,
        ):
            process_result = subprocess.run(
                [str(exe)],
                cwd=root,
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                timeout=timeout_seconds,
                check=False,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        if process_result.returncode != 0:
            stderr = stderr_log.read_text(encoding="utf-8", errors="replace").strip()
            raise RuntimeError(
                f"frozen PySide startup smoke exited with {process_result.returncode}: "
                f"{stderr}"
            )
        if not trace.is_file():
            raise RuntimeError("frozen PySide startup smoke produced no trace")
        records = [
            json.loads(line)
            for line in trace.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        required_events = {f"T{index}" for index in range(7)}
        if not records or not required_events.issubset(records[-1]):
            raise RuntimeError(
                f"frozen PySide startup smoke did not reach T6: "
                f"{records[-1:] or 'empty'}"
            )
        if not result_file.is_file():
            raise RuntimeError(
                "frozen PySide startup smoke produced no result evidence"
            )
        smoke_result = json.loads(result_file.read_text(encoding="utf-8"))
        if smoke_result.get("supervisor_ready") is not True:
            raise RuntimeError(
                "frozen PySide startup smoke did not prove Supervisor ready"
            )
        module_file = Path(str(smoke_result.get("module_file", ""))).resolve()
        try:
            module_file.relative_to(root.resolve())
        except ValueError as error:
            raise RuntimeError(
                f"Supervisor module loaded outside extracted artifact: {module_file}"
            ) from error
        if not module_file.is_file():
            raise RuntimeError(
                f"Supervisor module evidence does not exist in artifact: {module_file}"
            )
    except subprocess.TimeoutExpired as error:
        stderr = stderr_log.read_text(encoding="utf-8", errors="replace").strip()
        raise RuntimeError(
            f"frozen PySide startup smoke timed out after {timeout_seconds:.0f}s: "
            f"{stderr}"
        ) from error
    finally:
        trace.unlink(missing_ok=True)
        result_file.unlink(missing_ok=True)
        stdout_log.unlink(missing_ok=True)
        stderr_log.unlink(missing_ok=True)


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
        missing = [
            str(path.relative_to(root)) for path in required if not path.is_file()
        ]
        if missing:
            raise RuntimeError(f"required PySide files missing: {missing}")
        if (root / "VibeOCR.WinUI.exe").exists():
            raise RuntimeError("WinUI executable present in PySide artifact")
        manifest = json.loads(
            (root / "product-manifest.json").read_text(encoding="utf-8-sig")
        )
        if manifest.get("frontend") != "pyside":
            raise RuntimeError("product manifest frontend is not pyside")
        if manifest.get("protocol_major") != 2:
            raise RuntimeError("product manifest protocol_major is not 2")
        records = manifest.get("python_wheels", [])
        expected = {
            "vibeocr",
            "vibeocr-backend",
            "vibeocr-client-py",
            "vibeocr-contracts-py",
            "vibeocr-pyside",
            "vibeocr-runtime-client",
        }
        if {record.get("distribution") for record in records} != expected:
            raise RuntimeError("bound Python wheel set is incomplete")
        records_by_distribution = {
            str(record["distribution"]): record for record in records
        }
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
        required_members = {
            "vibeocr-backend": "vibeocr/supervisor/main.py",
            "vibeocr-contracts-py": "vibeocr/protocol/v2/golden/golden.json",
            "vibeocr-runtime-client": "vibeocr/protocol/v2/client.py",
        }
        for distribution, required_member in required_members.items():
            record = records_by_distribution[distribution]
            bound = root / "backend" / str(record["file"])
            with zipfile.ZipFile(bound) as archive:
                members = set(archive.namelist())
            if required_member not in members:
                raise RuntimeError(f"{distribution} wheel is missing {required_member}")
            legacy = sorted(
                member
                for member in members
                if member.startswith(("vibeocr/worker_host/", "vibeocr/protocol/v1/"))
            )
            if legacy:
                raise RuntimeError(
                    f"{distribution} wheel contains legacy runtime paths: {legacy}"
                )
        if os.name == "nt":
            _verify_frozen_startup(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
