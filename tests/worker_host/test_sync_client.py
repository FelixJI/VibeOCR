from __future__ import annotations

import os
import sys
from typing import Any

import pytest

from vibeocr.worker_host import sync_client
from vibeocr.worker_host.sync_client import SyncBackendClient


def _install_launch_mocks(monkeypatch, captured: dict[str, Any]) -> None:
    """Wire the launch-time mocks shared by the launcher tests.

    Captures the kwargs passed to ``subprocess.Popen`` into ``captured`` and
    fakes the post-launch coroutine steps so ``_start_async`` returns.
    """

    class FakeProcess:
        stdout = None
        stderr = None

    def fake_popen(*args, **kwargs):
        captured.setdefault("args", args[0] if args else None)
        captured.update(kwargs)
        return FakeProcess()

    class FakeBackendClient:
        async def connect(self, endpoint) -> None:
            self.endpoint = endpoint

        async def call(self, method, payload) -> dict[str, Any]:
            assert method == "system.handshake"
            return {}

    async def fake_ready(self) -> dict[str, Any]:
        return {"event": "worker.ready"}

    monkeypatch.setattr(sync_client.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sync_client, "BackendClient", FakeBackendClient)
    monkeypatch.setattr(SyncBackendClient, "_await_ready", fake_ready)
    monkeypatch.setattr(SyncBackendClient, "_start_output_drains", lambda self: None)


@pytest.mark.asyncio
async def test_worker_stdio_is_forced_to_utf8(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _install_launch_mocks(monkeypatch, captured)
    monkeypatch.setenv("PYTHONIOENCODING", "cp936:strict")

    client = SyncBackendClient()
    await client._start_async("production", "pyside", None)

    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert captured["text"] is True
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8:backslashreplace"
    assert captured["env"] is not os.environ


@pytest.mark.asyncio
async def test_dev_mode_launches_sys_executable(monkeypatch, tmp_path) -> None:
    """In dev (non-frozen), the worker runs under ``sys.executable``.

    Guards against the frozen-only embedded-Python branch accidentally firing
    during local runs / pytest.
    """
    captured: dict[str, Any] = {}
    _install_launch_mocks(monkeypatch, captured)
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)

    # Force the non-frozen branch even if pytest is somehow running frozen.
    monkeypatch.setattr(sync_client.sys, "frozen", False, raising=False)
    monkeypatch.setattr(sync_client.sys, "_MEIPASS", None, raising=False)

    client = SyncBackendClient()
    await client._start_async("production", "pyside", tmp_path)

    cmd = captured["args"]
    assert cmd[0] == sys.executable
    assert "-m" in cmd
    assert captured["env"]["PYTHONUNBUFFERED"] == "1"


@pytest.mark.asyncio
async def test_frozen_build_uses_embedded_python_not_self_exe(
    monkeypatch, tmp_path
) -> None:
    """Regression: packaged build must NOT re-launch ``VibeOCR.exe``.

    In a PyInstaller ``--windowed`` build, ``sys.executable`` *is*
    ``VibeOCR.exe`` and the bootloader ignores ``-m`` — so spawning
    ``[sys.executable, "-m", "vibeocr.worker_host.main"]`` recurses into a
    full GUI instead of the WorkerHost (symptom: ``WorkerHost exited
    (code=None) before ready. stderr:``). The launcher must resolve the
    embedded portable Python and set ``PYTHONPATH`` to ``_MEIPASS`` so the
    bundled-as-datas ``vibeocr`` package is importable.
    """
    captured: dict[str, Any] = {}
    _install_launch_mocks(monkeypatch, captured)

    fake_self_exe = tmp_path / "VibeOCR.exe"
    fake_self_exe.write_bytes(b"MZ")  # placeholder, existence is enough
    fake_meipass = tmp_path / "_internal"
    fake_meipass.mkdir()
    fake_embedded = tmp_path / "python" / "python.exe"
    fake_embedded.parent.mkdir(parents=True)
    fake_embedded.write_bytes(b"python")

    monkeypatch.setattr(sync_client.sys, "frozen", True, raising=False)
    monkeypatch.setattr(sync_client.sys, "executable", str(fake_self_exe))
    monkeypatch.setattr(sync_client.sys, "_MEIPASS", str(fake_meipass), raising=False)

    import vibeocr.env_manager as env_manager

    monkeypatch.setattr(
        env_manager, "get_project_root", lambda: tmp_path, raising=False
    )
    monkeypatch.setattr(
        env_manager,
        "get_embedded_python_executable",
        lambda project_root: fake_embedded,
        raising=False,
    )

    client = SyncBackendClient()
    await client._start_async("production", "pyside", tmp_path)

    cmd = captured["args"]
    # Must launch the embedded interpreter, not the frozen GUI exe.
    assert cmd[0] == str(fake_embedded)
    assert cmd[0] != str(fake_self_exe)
    assert "-m" in cmd
    assert "vibeocr.worker_host.main" in cmd

    env = captured["env"]
    # Embedded interpreter is independent of the PYZ archive; vibeocr source
    # is bundled as datas under _MEIPASS and must be on PYTHONPATH.
    assert str(fake_meipass) in env["PYTHONPATH"]
    # WorkerHost emits worker.ready via stdout; unbuffered so import-time
    # tracebacks and the ready line arrive immediately.
    assert env["PYTHONUNBUFFERED"] == "1"

    # A --windowed parent must not flash a console for the python child.
    if os.name == "nt":
        assert captured["creationflags"] & sync_client.subprocess.CREATE_NO_WINDOW
