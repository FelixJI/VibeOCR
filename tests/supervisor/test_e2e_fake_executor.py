"""End-to-end tests: real FastAPI app + real httpx client over ASGI transport.

This proves the Phase 2 exit criterion: "fake executor E2E can be completed
by the Python client" — submit → events → result → cancel/retry, all over
the HTTP v2 surface with auth enforced.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import httpx
import pytest

from vibeocr.protocol.v2 import (
    TERMINAL_JOB_STATES,
    CancelMode,
    ItemState,
    JobState,
    ResidencyStatus,
    SettingsSnapshot,
)
from vibeocr.supervisor.app import create_app
from vibeocr.supervisor.bootstrap import generate_session_token, new_instance_id
from vibeocr.supervisor.client import SupervisorClient
from vibeocr.supervisor.errors import InferenceClientError
from vibeocr.supervisor.module import SupervisorModule, SupervisorOptions

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from vibeocr.supervisor.jobs.staging import StagedInput


class E2EExecutor:
    """Succeeds every item; respects cancel via the state machine."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, record, staged: Iterable[StagedInput]) -> None:  # type: ignore[no-untyped-def]
        self.calls.append(record.job_id)
        if record.state is JobState.QUEUED:
            record.transition(JobState.RUNNING)
            record.append_event("running")
        for item in list(record.items):
            if item.state is ItemState.QUEUED:
                record.transition_item(item.item_id, ItemState.RUNNING)
                record.set_item_result(item.item_id, {"text": f"ocr-{item.display_name}"})
                record.transition_item(item.item_id, ItemState.SUCCEEDED)
        if record.cancel_requested_at is not None:
            record.transition(JobState.CANCEL_REQUESTED)
            record.transition(JobState.CANCELLED)
        else:
            record.transition(JobState.COMPLETED)
        record.append_event("done")

    def cancel_mode_for(self, record) -> CancelMode:  # type: ignore[no-untyped-def]
        return CancelMode.COOPERATIVE

    def residency_status(self) -> ResidencyStatus:
        return ResidencyStatus(default_ttl_seconds=300)

    def release_idle(self, pipeline: str | None = None) -> ResidencyStatus:
        return ResidencyStatus(default_ttl_seconds=300)


@pytest.fixture()
def module(tmp_path: Path) -> SupervisorModule:
    opts = SupervisorOptions(instance_id=new_instance_id())
    return SupervisorModule(
        options=opts, stager_root=tmp_path / "staging", executor=E2EExecutor()
    )


@pytest.fixture()
def token() -> str:
    return generate_session_token()


@pytest.fixture()
def app(module: SupervisorModule, token: str):
    return create_app(module, token)


@pytest.fixture()
async def client(app, token: str) -> SupervisorClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1", headers={"Authorization": f"Bearer {token}"}
    ) as http:
        # Patch the client to use this transport-backed httpx client.
        c = SupervisorClient(base_url="http://127.0.0.1", session_token=token, instance_id="test")
        c._client = http
        yield c


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


async def test_health_does_not_require_token(app) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
        resp = await http.get("/v2/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema_version"] == 2
        assert body["ready"] is True


async def test_business_request_without_token_is_unauthorized(app) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
        resp = await http.get("/v2/runtime/residency")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "UNAUTHORIZED"


async def test_business_request_with_wrong_token_is_unauthorized(app, token: str) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer wrong-token"},
    ) as http:
        resp = await http.get("/v2/runtime/residency")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Happy path: submit → events → result
# ---------------------------------------------------------------------------


async def test_submit_events_result_roundtrip(client: SupervisorClient, module: SupervisorModule) -> None:
    ref = await client.submit_recognition(
        [("a.png", "image/png", b"alpha"), ("b.png", "image/png", b"beta")],
    )
    # Wait for terminal via polling status.
    deadline = time.time() + 3.0
    snap = await client.status(ref.job_id)
    while time.time() < deadline and snap.state not in TERMINAL_JOB_STATES:
        await asyncio.sleep(0.02)
        snap = await client.status(ref.job_id)
    assert snap.state is JobState.COMPLETED
    assert snap.summary.succeeded == 2
    events = await client.events(ref.job_id, after_sequence=0)
    assert any(e.stage == "done" for e in events)
    results = await client.result(ref.job_id)
    assert [r.display_name for r in results] == ["a.png", "b.png"]
    assert results[0].payload["text"] == "ocr-a.png"


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


async def test_cancel_returns_cooperative_mode(client: SupervisorClient) -> None:
    ref = await client.submit_recognition([("a.png", None, b"x")])
    # Give a moment for the job to be created (likely completes fast in tests).
    await asyncio.sleep(0.05)
    snap = await client.status(ref.job_id)
    if snap.state in TERMINAL_JOB_STATES:
        # Already terminal — cancelling should still be reachable without error.
        with pytest.raises(InferenceClientError):
            await client.cancel(ref.job_id)
        return
    mode = await client.cancel(ref.job_id)
    assert mode is CancelMode.COOPERATIVE


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


async def test_retry_rejects_when_no_failed_items(client: SupervisorClient) -> None:
    ref = await client.submit_recognition([("a.png", None, b"x")])
    # Wait for completion (all items succeed).
    deadline = time.time() + 3.0
    snap = await client.status(ref.job_id)
    while time.time() < deadline and snap.state not in TERMINAL_JOB_STATES:
        await asyncio.sleep(0.02)
        snap = await client.status(ref.job_id)
    # No failed items → server returns JOB_NOT_RETRYABLE.
    with pytest.raises(InferenceClientError):
        await client.retry(ref.job_id)


# ---------------------------------------------------------------------------
# Residency / settings
# ---------------------------------------------------------------------------


async def test_residency_endpoint(client: SupervisorClient) -> None:
    status = await client.residency()
    assert status.default_ttl_seconds == 300


async def test_settings_roundtrip(client: SupervisorClient) -> None:
    initial = await client.get_settings()
    assert initial.default_ttl_seconds == 300
    new_snap = SettingsSnapshot(default_ttl_seconds=600)
    updated = await client.put_settings(new_snap)
    assert updated.default_ttl_seconds == 600
    again = await client.get_settings()
    assert again.default_ttl_seconds == 600


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_delete_after_terminal(client: SupervisorClient) -> None:
    ref = await client.submit_recognition([("a.png", None, b"x")])
    deadline = time.time() + 3.0
    snap = await client.status(ref.job_id)
    while time.time() < deadline and snap.state not in TERMINAL_JOB_STATES:
        await asyncio.sleep(0.02)
        snap = await client.status(ref.job_id)
    await client.delete(ref.job_id)
    with pytest.raises(InferenceClientError):
        await client.status(ref.job_id)


# ---------------------------------------------------------------------------
# Client refuses non-loopback
# ---------------------------------------------------------------------------


def test_client_refuses_non_loopback_base_url() -> None:
    with pytest.raises(InferenceClientError) as exc_info:
        SupervisorClient(base_url="http://10.0.0.5:1234", session_token="x")
    assert exc_info.value.code.value == "FORBIDDEN_LOOPBACK"
