"""Tests for SyncSupervisorClient — the sync façade over SupervisorClient.

The async SupervisorClient is exercised end-to-end via the ASGI transport in
test_e2e_fake_executor.py; here we focus on the sync wrapper's delegation,
lifecycle (start/close/idempotent enter) and the loop-driving mechanics that
``SyncSupervisorClient`` adds on top.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import httpx
import pytest

from vibeocr.supervisor.sync_client import SyncSupervisorClient

if TYPE_CHECKING:
    from fastapi import FastAPI

    from vibeocr.supervisor.client import SupervisorClient


# ---------------------------------------------------------------------------
# Construction & lifecycle
# ---------------------------------------------------------------------------


def test_constructor_pins_loopback_via_inner_client() -> None:
    """Non-loopback base_url surfaces InferenceClientError from the inner client."""
    from vibeocr.protocol.v2 import ErrorCode
    from vibeocr.supervisor.errors import InferenceClientError

    with pytest.raises(InferenceClientError) as exc:
        SyncSupervisorClient(base_url="http://example.com", session_token="t")
    assert exc.value.code is ErrorCode.FORBIDDEN_LOOPBACK


def test_base_url_strips_trailing_slash() -> None:
    sync = SyncSupervisorClient(
        base_url="http://127.0.0.1:5000/", session_token="t"
    )
    assert sync._async.base_url == "http://127.0.0.1:5000"


def test_close_without_start_is_noop() -> None:
    """close() before start()/enter must be a safe no-op."""
    sync = SyncSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="i"
    )
    # Should not raise
    sync.close()
    assert sync._entered is False


def test_start_drives_aenter_on_background_loop(monkeypatch) -> None:
    """start() calls _ensure_entered → runs __aenter__ on the bg loop once."""
    import asyncio

    sync = SyncSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="i"
    )
    aenter_called = {"n": 0}

    async def fake_aenter() -> SupervisorClient:
        aenter_called["n"] += 1
        return sync._async

    monkeypatch.setattr(sync._async, "__aenter__", fake_aenter)

    loop = MagicMock()

    def _drive(coro):
        # Actually run the coroutine to completion on a fresh loop
        return asyncio.new_event_loop().run_until_complete(coro)

    loop.run.side_effect = _drive
    monkeypatch.setattr("vibeocr.supervisor.sync_client._get_bg_loop", lambda: loop)

    sync.start()
    assert sync._entered is True
    assert aenter_called["n"] == 1

    # Calling start() again should NOT re-enter (idempotent)
    sync.start()
    assert aenter_called["n"] == 1


def test_close_drives_aexit_and_resets_entered(monkeypatch) -> None:
    """close() after start() runs __aexit__ and clears _entered."""
    import asyncio

    sync = SyncSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="i"
    )
    aexit_called = {"n": 0}

    async def fake_aenter() -> SupervisorClient:
        sync._entered = True
        return sync._async

    async def fake_aexit(exc_type, exc, tb) -> None:
        aexit_called["n"] += 1

    monkeypatch.setattr(sync._async, "__aenter__", fake_aenter)
    monkeypatch.setattr(sync._async, "__aexit__", fake_aexit)

    loop = MagicMock()

    def _drive(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    loop.run.side_effect = _drive
    monkeypatch.setattr("vibeocr.supervisor.sync_client._get_bg_loop", lambda: loop)

    sync.start()
    sync.close()
    assert aexit_called["n"] == 1
    assert sync._entered is False
    # Second close is a no-op (already closed)
    sync.close()
    assert aexit_called["n"] == 1


# ---------------------------------------------------------------------------
# Delegation — every public method drives _ensure_entered() + bg loop run
# ---------------------------------------------------------------------------


def test_delegates_all_business_methods(monkeypatch) -> None:
    """submit/observe/command/export_ocr/decode_qrcode/generate_qrcode all delegate."""
    sync = SyncSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="i"
    )

    sentinel = object()
    inner = MagicMock()
    # Each async method returns a sentinel; _ensure_entered returns inner
    for name in (
        "submit",
        "observe",
        "command",
        "export_ocr",
        "decode_qrcode",
        "generate_qrcode",
    ):
        getattr(inner, name).return_value = sentinel

    # Replace _ensure_entered to skip bg-loop __aenter__ and return the mock
    sync._ensure_entered = lambda: inner  # type: ignore[method-assign]
    # Mark entered so close() at the end doesn't try to drive aexit on the mock
    sync._entered = True

    loop = MagicMock()
    loop.run.side_effect = lambda coro: coro

    monkeypatch.setattr("vibeocr.supervisor.sync_client._get_bg_loop", lambda: loop)

    # Build minimal protocol objects that .to_payload() etc. aren't called on
    # (submit/observe/command receive request objects; we just check the return
    # value passes through and the inner method is invoked).
    assert sync.observe("job-1") is sentinel
    inner.observe.assert_called_once_with("job-1", after_sequence=0)

    assert sync.observe("job-2", after_sequence=5) is sentinel
    inner.observe.assert_called_with("job-2", after_sequence=5)

    assert sync.export_ocr(
        raw_text="r",
        markdown_text="m",
        html_text="h",
        output_path="/tmp/out.txt",
        fmt="txt",
    ) is sentinel
    inner.export_ocr.assert_called_once()

    assert sync.decode_qrcode(b"img") is sentinel
    inner.decode_qrcode.assert_called_once_with(b"img")

    assert sync.generate_qrcode("data", fmt="png") is sentinel
    inner.generate_qrcode.assert_called_once_with("data", fmt="png", options=None)

    sync.close()


def test_submit_passes_attachments_through(monkeypatch) -> None:
    sync = SyncSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="i"
    )
    inner = MagicMock()
    sync._ensure_entered = lambda: inner  # type: ignore[method-assign]
    sync._entered = True

    loop = MagicMock()
    loop.run.side_effect = lambda coro: coro

    monkeypatch.setattr("vibeocr.supervisor.sync_client._get_bg_loop", lambda: loop)

    sentinel = object()
    inner.submit.return_value = sentinel

    result = sync.submit(MagicMock(), {"a": (None, b"data")})  # type: ignore[arg-type]
    assert result is sentinel
    inner.submit.assert_called_once()
    sync.close()


def test_command_default_args(monkeypatch) -> None:
    sync = SyncSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="i"
    )
    inner = MagicMock()
    sync._ensure_entered = lambda: inner  # type: ignore[method-assign]
    sync._entered = True

    loop = MagicMock()
    loop.run.side_effect = lambda coro: coro

    monkeypatch.setattr("vibeocr.supervisor.sync_client._get_bg_loop", lambda: loop)

    sentinel = object()
    inner.command.return_value = sentinel
    cmd = MagicMock()
    assert sync.command(cmd) is sentinel
    inner.command.assert_called_once_with(cmd)
    sync.close()


# ---------------------------------------------------------------------------
# End-to-end via ASGI transport (real SupervisorClient + fake job executor)
# ---------------------------------------------------------------------------


@pytest.fixture()
def sync_supervisor_client(
    pdf_app: FastAPI, supervisor_token: str
) -> SyncSupervisorClient:
    """Pre-seed the inner async client with an ASGI transport, bypassing start()."""
    sync = SyncSupervisorClient(
        base_url="http://127.0.0.1", session_token=supervisor_token, instance_id="t"
    )
    sync._async._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=pdf_app),
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        event_hooks={"response": [sync._async._log_http_response]},
    )
    sync._entered = True
    yield sync
    sync.close()


def test_sync_health_via_asgi(sync_supervisor_client: SyncSupervisorClient) -> None:
    """observe on a nonexistent job surfaces the typed error path through the wrapper."""
    from vibeocr.protocol.v2 import JobRef
    from vibeocr.supervisor.errors import InferenceClientError

    # Submit is covered in e2e; here we exercise observe on a missing job → error
    # surfaces through the sync wrapper's loop.run.
    with pytest.raises(InferenceClientError):
        sync_supervisor_client.observe("nonexistent-job")
    # Sanity: JobRef is the expected return type for successful paths.
    assert JobRef is not None
