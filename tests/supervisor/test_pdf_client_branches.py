"""Branch-coverage tests for PdfSupervisorClient + SyncPdfSupervisorClient.

The happy-path contract is in test_pdf_supervisor_client.py. Here we cover:
- PdfBackendError legacy single-string constructor form.
- health() happy path (parses HealthResponse).
- load_stream / delete_text_layers_stream empty-line skip + HTTPError branch.
- SyncPdfSupervisorClient: _ensure_entered via real bg loop, base_url property,
  close() after start(), close() before start() no-op, start() idempotency.
- _log_http_response elapsed exception branch.
- __aexit__ when _client already None (no-op).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from vibeocr.protocol.v2 import ErrorCode
from vibeocr.supervisor.pdf_client import (
    PdfBackendError,
    PdfSupervisorClient,
    SyncPdfSupervisorClient,
)

# ---------------------------------------------------------------------------
# PdfBackendError legacy single-string form
# ---------------------------------------------------------------------------


def test_pdf_backend_error_single_string_form() -> None:
    """Legacy PdfBackendError("boom") maps to INTERNAL_ERROR (line 89)."""
    err = PdfBackendError("boom")
    assert err.code is ErrorCode.INTERNAL_ERROR
    # Message preserved
    assert "boom" in str(err)


def test_pdf_backend_error_typed_form() -> None:
    """Typed form PdfBackendError(ErrorCode.X, "boom") keeps the code (line 91)."""
    err = PdfBackendError(ErrorCode.INTERNAL_ERROR, "boom", retryable=False)
    assert err.code is ErrorCode.INTERNAL_ERROR


# ---------------------------------------------------------------------------
# health() happy path + __aexit__ no-op when client already None
# ---------------------------------------------------------------------------


async def test_health_returns_health_response() -> None:
    """health() parses HealthResponse (line 218)."""
    c = PdfSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})),
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer t"},
    )
    try:
        resp = await c.health()
        assert resp is not None
    finally:
        await c._client.aclose()


async def test_aexit_when_client_already_none_is_noop() -> None:
    """__aexit__ with _client=None should not raise (line 140->exit branch)."""
    c = PdfSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    # _client is None initially
    assert c._client is None
    # Should not raise
    await c.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# load_stream / delete_text_layers_stream: empty-line skip + HTTPError
# ---------------------------------------------------------------------------


async def test_load_stream_skips_empty_lines() -> None:
    """load_stream skips blank lines (line 250 continue)."""

    async def handler(req: httpx.Request) -> httpx.Response:
        # NDJSON with blank lines between events
        body = "\n".join(
            [
                "",
                '{"phase":"load","current":0,"total":1,"page_index":0,"message":"ok"}',
                "",
                "",
            ]
        )
        return httpx.Response(200, content=body.encode("utf-8"))

    c = PdfSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer t"},
    )
    try:
        events = [e async for e in c.load_stream("sid-1")]
    finally:
        await c._client.aclose()
    # Blank lines skipped → only one real event
    assert len(events) == 1
    assert events[0].message == "ok"


async def test_load_stream_http_error_raises_pdf_backend_error() -> None:
    """load_stream transport error wraps in PdfBackendError (line 252-253)."""

    async def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    c = PdfSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer t"},
    )
    try:
        with pytest.raises(PdfBackendError, match="流式调用失败"):
            async for _ in c.load_stream("sid-1"):
                pass
    finally:
        await c._client.aclose()


async def test_delete_text_layers_stream_http_error_raises() -> None:
    """delete_text_layers_stream transport error wraps in PdfBackendError (line 447-448)."""

    async def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    c = PdfSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer t"},
    )
    try:
        with pytest.raises(PdfBackendError, match="流式调用失败"):
            async for _ in c.delete_text_layers_stream("sid-1", [0]):
                pass
    finally:
        await c._client.aclose()


async def test_delete_text_layers_stream_skips_empty_lines() -> None:
    """delete_text_layers_stream skips blank lines (line 445 continue)."""

    async def handler(req: httpx.Request) -> httpx.Response:
        body = "\n".join(
            [
                "",
                '{"phase":"delete","current":1,"total":1,"page_payload":{"residual_pages":[]}}',
                "",
            ]
        )
        return httpx.Response(200, content=body.encode("utf-8"))

    c = PdfSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer t"},
    )
    try:
        events = [e async for e in c.delete_text_layers_stream("sid-1", [0])]
    finally:
        await c._client.aclose()
    assert len(events) == 1


# ---------------------------------------------------------------------------
# _log_http_response: elapsed exception branch (line 156->161)
# ---------------------------------------------------------------------------


async def test_log_http_response_elapsed_exception(monkeypatch) -> None:
    """elapsed.total_seconds() raising is suppressed (line 156->161 branch)."""
    c = PdfSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    captured: dict[str, Any] = {}

    def capture(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("vibeocr.supervisor.pdf_client.log_http_response", capture)

    request = httpx.Request("GET", "http://127.0.0.1/v2/pdf/health")

    class _BadTimedelta:
        def total_seconds(self) -> float:
            raise RuntimeError("boom")

    resp = httpx.Response(200, request=request, content=b"{}")
    resp.elapsed = _BadTimedelta()  # type: ignore[assignment]
    await c._log_http_response(resp)
    assert captured.get("elapsed_ms") is None


# ---------------------------------------------------------------------------
# SyncPdfSupervisorClient lifecycle via real bg loop
# ---------------------------------------------------------------------------


def test_sync_base_url_property() -> None:
    """SyncPdfSupervisorClient.base_url delegates to inner client (line 592)."""
    sync = SyncPdfSupervisorClient(
        base_url="http://127.0.0.1:9999", session_token="t", instance_id="x"
    )
    assert sync.base_url == "http://127.0.0.1:9999"


def test_sync_close_before_start_is_noop() -> None:
    """close() before start() does nothing (line 595 _entered False branch)."""
    sync = SyncPdfSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    # Should not raise and should not touch the bg loop
    sync.close()
    assert sync._entered is False


def test_sync_ensure_entered_drives_bg_loop_then_idempotent(monkeypatch) -> None:
    """start() runs __aenter__ on the bg loop once; second start() is a no-op (line 586-587)."""
    import asyncio

    sync = SyncPdfSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    aenter_calls = {"n": 0}

    async def fake_aenter() -> PdfSupervisorClient:
        aenter_calls["n"] += 1
        return sync._async

    monkeypatch.setattr(sync._async, "__aenter__", fake_aenter)
    # Replace the bg loop with one that actually drives coroutines
    loop = MagicMock()

    def _drive(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    loop.run.side_effect = _drive
    monkeypatch.setattr("vibeocr.supervisor.pdf_client._get_bg_loop", lambda: loop)

    sync.start()
    assert sync._entered is True
    assert aenter_calls["n"] == 1
    # Idempotent: second start() doesn't re-enter
    sync.start()
    assert aenter_calls["n"] == 1


def test_sync_close_after_start_drives_aexit(monkeypatch) -> None:
    """close() after start() drives __aexit__ on the bg loop (line 597)."""
    import asyncio

    sync = SyncPdfSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    aexit_calls = {"n": 0}

    async def fake_aenter() -> PdfSupervisorClient:
        sync._entered = True
        return sync._async

    async def fake_aexit(exc_type, exc, tb) -> None:
        aexit_calls["n"] += 1

    monkeypatch.setattr(sync._async, "__aenter__", fake_aenter)
    monkeypatch.setattr(sync._async, "__aexit__", fake_aexit)
    loop = MagicMock()

    def _drive(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    loop.run.side_effect = _drive
    monkeypatch.setattr("vibeocr.supervisor.pdf_client._get_bg_loop", lambda: loop)

    sync.start()
    sync.close()
    assert aexit_calls["n"] == 1
    assert sync._entered is False


# ---------------------------------------------------------------------------
# _error_from_response: malformed body fallback
# ---------------------------------------------------------------------------


async def test_pdf_error_from_response_malformed_body() -> None:
    """A non-JSON error body falls back to INTERNAL_ERROR PdfBackendError."""
    c = PdfSupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(502, content=b"not json")),
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer t"},
    )
    try:
        with pytest.raises(PdfBackendError) as exc:
            await c.health()
        assert exc.value.code is ErrorCode.INTERNAL_ERROR
    finally:
        await c._client.aclose()
