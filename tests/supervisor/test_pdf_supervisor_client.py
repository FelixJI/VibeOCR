"""Tests for the PdfSupervisorClient + SyncPdfSupervisorClient.

Drives the same fake adapter surface as test_pdf_routes.py via the real
FastAPI app + httpx ASGI transport, but goes through the typed client
methods (async and sync). This proves the GUI-side transport swap is a
faithful drop-in for the legacy PdfBackendClient surface.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import httpx
import pytest
from conftest import NullExecutor

from vibeocr.ipc.schemas import ProgressPhase
from vibeocr.supervisor.app import create_app
from vibeocr.supervisor.bootstrap import generate_session_token, new_instance_id
from vibeocr.supervisor.module import SupervisorModule, SupervisorOptions
from vibeocr.supervisor.pdf_client import (
    PdfBackendError,
    PdfSupervisorClient,
    SyncPdfSupervisorClient,
)

if TYPE_CHECKING:
    from pathlib import Path

    from conftest import FakePdfAdapter
    from fastapi import FastAPI


def _build_async_client(app, token: str) -> PdfSupervisorClient:
    """Build a PdfSupervisorClient backed by an ASGI transport, pre-entered."""
    client = PdfSupervisorClient(
        base_url="http://127.0.0.1", session_token=token, instance_id="test"
    )
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {token}"},
        event_hooks={"response": [client._log_http_response]},
    )
    return client


async def test_response_hook_logs_unconsumed_stream(monkeypatch) -> None:
    client = PdfSupervisorClient(
        base_url="http://127.0.0.1",
        session_token="test",
        instance_id="test",
    )
    captured: dict[str, object] = {}

    def capture_log(**kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("vibeocr.supervisor.pdf_client.log_http_response", capture_log)
    response = httpx.Response(
        206,
        headers={"content-length": "4"},
        request=httpx.Request(
            "POST",
            "http://127.0.0.1/v2/pdf/preview",
            stream=httpx.ByteStream(b"request"),
        ),
        stream=httpx.ByteStream(b"data"),
    )
    response.elapsed = timedelta(milliseconds=50)

    await client._log_http_response(response)

    assert captured["stream"] is True
    assert captured["response_bytes"] == 4
    assert captured["status_code"] == 206
    assert captured["elapsed_ms"] == 50


async def test_async_client_lifecycle_and_guards() -> None:
    with pytest.raises(PdfBackendError):
        PdfSupervisorClient(
            base_url="http://example.com",
            session_token="test",
            instance_id="test",
        )

    client = PdfSupervisorClient(
        base_url="http://127.0.0.1/",
        session_token="test",
        instance_id="test",
    )
    assert client.base_url == "http://127.0.0.1"
    with pytest.raises(RuntimeError, match="async context manager"):
        client._require_client()

    async with client as entered:
        assert entered is client
        assert client._require_client() is not None
    assert client._client is None


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


async def test_async_open_returns_session_id(
    pdf_app: FastAPI, supervisor_token: str
) -> None:
    client = _build_async_client(pdf_app, supervisor_token)
    resp = await client.open_session("doc.pdf")
    assert resp.session_id == "sid-1"
    assert resp.model.file_path == "doc.pdf"


async def test_async_render_preview_returns_png(
    pdf_app: FastAPI, supervisor_token: str
) -> None:
    client = _build_async_client(pdf_app, supervisor_token)
    data = await client.render_preview("sid-1", 0, dpi=300)
    assert data.startswith(b"\x89PNG")


async def test_async_rotate_returns_mutate_response(
    pdf_app: FastAPI,
    supervisor_token: str,
    fake_pdf_adapter: FakePdfAdapter,
) -> None:
    client = _build_async_client(pdf_app, supervisor_token)
    resp = await client.rotate("sid-1", [0, 1], 90)
    assert resp.diff is not None
    # The adapter recorded the verbatim args.
    assert fake_pdf_adapter.calls[-1] == ("rotate", ("sid-1", [0, 1], 90), {})


async def test_async_remaining_session_and_mutation_routes(
    pdf_app: FastAPI,
    supervisor_token: str,
) -> None:
    client = _build_async_client(pdf_app, supervisor_token)

    with pytest.raises(PdfBackendError):
        await client.health()
    assert (await client.get_model("sid-1")).file_path == "doc.pdf"
    assert (await client.detect_text_layers("sid-1", 0)).text_layers == []
    assert (await client.delete_pages("sid-1", [0])).diff.structural_change is True
    assert (await client.insert_blank("sid-1", 0)).diff.structural_change is True
    assert (
        await client.insert_from("sid-1", "source.pdf", 0)
    ).diff.structural_change is True
    assert (await client.move_page("sid-1", 0, 1)).diff.structural_change is True
    assert (await client.reorder("sid-1", [1, 0])).diff.structural_change is True
    assert (await client.add_text_layer("sid-1", 0, {"text": "x"})).diff is not None
    assert (await client.rewrite_text_layer("sid-1", 0, [])).diff is not None
    assert (await client.update_block_text("sid-1", 0, 0, "new")).diff is not None

    events = [event async for event in client.delete_text_layers_stream("sid-1", [0])]
    assert len(events) == 1
    assert events[0].phase is ProgressPhase.DELETE

    await client.close_session("sid-1")


async def test_async_load_stream_yields_progress_events(
    pdf_app: FastAPI, supervisor_token: str
) -> None:
    client = _build_async_client(pdf_app, supervisor_token)
    events = []
    async for event in client.load_stream("sid-1"):
        events.append(event)
    assert len(events) == 1
    assert events[0].phase is ProgressPhase.LOAD
    assert events[0].message == "done"


async def test_async_save_returns_path(pdf_app: FastAPI, supervisor_token: str) -> None:
    client = _build_async_client(pdf_app, supervisor_token)
    resp = await client.save("sid-1", "out.pdf", rewrite_text_layers=False)
    assert resp.path == "out.pdf"


async def test_async_error_raises_typed_pdf_backend_error(
    tmp_path: Path,
) -> None:
    """A 500 from a missing-adapter build surfaces as PdfBackendError."""
    opts = SupervisorOptions(instance_id=new_instance_id())
    module = SupervisorModule(
        options=opts,
        stager_root=tmp_path / "staging",
        executor=NullExecutor(),
        pdf_adapter=None,
    )
    token = generate_session_token()
    app = create_app(module, token)
    client = _build_async_client(app, token)
    with pytest.raises(PdfBackendError):
        await client.open_session("doc.pdf")


# ---------------------------------------------------------------------------
# Sync wrapper
# ---------------------------------------------------------------------------


@pytest.fixture()
def sync_client(pdf_app: FastAPI, supervisor_token: str):
    """A SyncPdfSupervisorClient whose inner async client uses ASGI transport.

    We bypass the background-loop ``__aenter__`` by pre-seeding ``_client`` so
    the test does not depend on the daemon-thread loop lifecycle.
    """
    sync = SyncPdfSupervisorClient(
        base_url="http://127.0.0.1", session_token=supervisor_token, instance_id="test"
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


def test_sync_open_matches_async_contract(sync_client: SyncPdfSupervisorClient) -> None:
    resp = sync_client.open_session("doc.pdf")
    assert resp.session_id == "sid-1"


def test_sync_render_thumbnail_returns_bytes(
    sync_client: SyncPdfSupervisorClient,
) -> None:
    data = sync_client.render_thumbnail("sid-1", 0, size=128)
    assert data.startswith(b"\x89PNG")


def test_sync_add_text_layer_batch_returns_saved_flag(
    sync_client: SyncPdfSupervisorClient,
) -> None:
    resp = sync_client.add_text_layer_batch(
        "sid-1",
        [{"page": 0, "ocr_result": {"text": "hi"}}],
        save=True,
    )
    assert resp.extra == {"saved": True}


def test_sync_load_stream_is_iterable(
    sync_client: SyncPdfSupervisorClient,
) -> None:
    events = list(sync_client.load_stream("sid-1"))
    assert len(events) == 1
    assert events[0].phase is ProgressPhase.LOAD


def test_sync_cancel_and_reset_cancel_run_without_error(
    sync_client: SyncPdfSupervisorClient,
) -> None:
    sync_client.cancel("sid-1")
    sync_client.reset_cancel("sid-1")


def test_sync_save_round_trip(sync_client: SyncPdfSupervisorClient) -> None:
    resp = sync_client.save("sid-1", "out.pdf")
    assert resp.path == "out.pdf"


def test_sync_wrapper_delegates_remaining_operations(monkeypatch) -> None:
    import vibeocr.supervisor.pdf_client as module

    sync = SyncPdfSupervisorClient(
        base_url="http://127.0.0.1",
        session_token="test",
        instance_id="test",
    )
    client = MagicMock()
    expected = object()
    for name in (
        "health",
        "close_session",
        "get_model",
        "render_preview",
        "detect_text_layers",
        "rotate",
        "delete_pages",
        "insert_blank",
        "insert_from",
        "move_page",
        "reorder",
        "add_text_layer",
        "rewrite_text_layer",
        "update_block_text",
    ):
        getattr(client, name).return_value = expected
    client.delete_text_layers_stream.return_value = iter(())
    sync._ensure_entered = lambda: client  # type: ignore[method-assign]

    loop = MagicMock()
    loop.run.side_effect = lambda value: value
    loop.iterate_stream.side_effect = lambda factory: factory()
    monkeypatch.setattr(module, "_get_bg_loop", lambda: loop)

    sync.start()
    assert sync.health() is expected
    assert sync.close_session("sid") is None
    assert sync.get_model("sid") is expected
    assert sync.render_preview("sid", 0, dpi=200) is expected
    assert sync.detect_text_layers("sid", 0) is expected
    assert sync.rotate("sid", [0], 90) is expected
    assert sync.delete_pages("sid", [0]) is expected
    assert sync.insert_blank("sid", 0) is expected
    assert sync.insert_from("sid", "source.pdf", 0) is expected
    assert sync.move_page("sid", 0, 1) is expected
    assert sync.reorder("sid", [1, 0]) is expected
    assert sync.add_text_layer("sid", 0, {"text": "x"}) is expected
    assert sync.rewrite_text_layer("sid", 0, []) is expected
    assert sync.update_block_text("sid", 0, 0, "new") is expected
    assert list(sync.delete_text_layers_stream("sid", [0])) == []
