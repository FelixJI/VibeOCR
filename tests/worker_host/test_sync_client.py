from __future__ import annotations

import os
from typing import Any

import pytest

from vibeocr.worker_host import sync_client
from vibeocr.worker_host.sync_client import SyncBackendClient


@pytest.mark.asyncio
async def test_worker_stdio_is_forced_to_utf8(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeProcess:
        stdout = None
        stderr = None

    def fake_popen(*args, **kwargs):
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
    monkeypatch.setenv("PYTHONIOENCODING", "cp936:strict")

    client = SyncBackendClient()
    await client._start_async("production", "pyside", None)

    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert captured["text"] is True
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8:backslashreplace"
    assert captured["env"] is not os.environ
