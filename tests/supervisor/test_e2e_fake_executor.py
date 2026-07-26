"""End-to-end tests: real FastAPI app + real httpx client over ASGI transport.

This proves the Phase 2 exit criterion: "fake executor E2E can be completed
by the Python client" — submit → events → result → cancel/retry, all over
the HTTP v2 surface with auth enforced.
"""

from __future__ import annotations

import asyncio
import base64
import threading
import time
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
import pytest

from vibeocr.protocol.v2 import (
    TERMINAL_JOB_STATES,
    CancelMode,
    ErrorCode,
    ItemState,
    JobCommand,
    JobCommandKind,
    JobKind,
    JobPriority,
    JobState,
    PipelineSelection,
    ResidencyEntry,
    ResidencyKind,
    ResidencyStatus,
    SettingsSnapshot,
    SubmitItem,
    SubmitRequest,
)
from vibeocr.supervisor.app import create_app
from vibeocr.supervisor.bootstrap import generate_session_token, new_instance_id
from vibeocr.supervisor.client import SupervisorClient
from vibeocr.supervisor.errors import InferenceClientError
from vibeocr.supervisor.jobs.staging import InputExpiredError
from vibeocr.supervisor.module import SupervisorModule, SupervisorOptions

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable
    from pathlib import Path

    from vibeocr.supervisor.jobs.staging import StagedInput


class E2EExecutor:
    """Succeeds every item; respects cancel via the state machine."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.preload_calls: list[tuple[str, ...]] = []

    def execute(self, record, staged: Iterable[StagedInput]) -> None:  # type: ignore[no-untyped-def]
        self.calls.append(record.job_id)
        if record.state is JobState.QUEUED:
            record.transition(JobState.RUNNING)
            record.append_event("running")
        for item in list(record.items):
            if item.state is ItemState.QUEUED:
                record.commit_item_success(
                    item.item_id,
                    payload_type="ocr.v1",
                    payload={"text": f"ocr-{item.display_name}"},
                )
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

    def preload(self, pipelines: tuple[str, ...]) -> ResidencyStatus:
        self.preload_calls.append(pipelines)
        return ResidencyStatus(default_ttl_seconds=300)

    def configure_settings(self, snapshot: SettingsSnapshot) -> ResidencyStatus:
        return ResidencyStatus(
            default_ttl_seconds=snapshot.default_ttl_seconds,
            pipelines=snapshot.pipelines,
        )

    def close(self) -> None:
        return


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
async def client(app, token: str) -> AsyncIterator[SupervisorClient]:
    transport = httpx.ASGITransport(app=app)
    c = SupervisorClient(
        base_url="http://127.0.0.1", session_token=token, instance_id="test"
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {token}"},
        event_hooks={"response": [c._log_http_response]},
    ) as http:
        # Patch the client to use this transport-backed httpx client.
        c._client = http
        yield c


async def test_response_hook_logs_unconsumed_stream(monkeypatch) -> None:
    client = SupervisorClient(
        base_url="http://127.0.0.1:9000",
        session_token="test",
        instance_id="test",
    )
    captured: dict[str, object] = {}

    def capture_log(**kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("vibeocr.supervisor.client.log_http_response", capture_log)
    response = httpx.Response(
        200,
        headers={"content-length": "4"},
        request=httpx.Request("GET", "http://127.0.0.1:9000/v2/events"),
        stream=httpx.ByteStream(b"data"),
    )

    await client._log_http_response(response)

    assert captured["stream"] is True
    assert captured["response_bytes"] == 4
    assert captured["status_code"] == 200


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


async def test_health_does_not_require_token(app) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1"
    ) as http:
        resp = await http.get("/v2/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema_version"] == 2
        assert body["ready"] is True


async def test_business_request_without_token_is_unauthorized(app) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1"
    ) as http:
        resp = await http.get("/v2/runtime/residency")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "UNAUTHORIZED"


async def test_qrcode_generate_supports_png_and_svg(
    client: SupervisorClient,
) -> None:
    png = base64.b64decode(await client.generate_qrcode("vibeocr", fmt="qrcode"))
    svg = base64.b64decode(
        await client.generate_qrcode(
            "vibeocr",
            fmt="svg",
            options={"error_correction": "H"},
        )
    )

    assert png.startswith(b"\x89PNG")
    assert b"<svg" in svg


async def test_business_request_with_wrong_token_is_unauthorized(
    app, token: str
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer wrong-token"},
    ) as http:
        resp = await http.get("/v2/runtime/residency")
        assert resp.status_code == 401


async def test_preload_selected_pipelines_round_trips_through_supervisor(
    client: SupervisorClient,
    module: SupervisorModule,
) -> None:
    status = await client.preload(("OCR", "PP-StructureV3"))

    assert status.default_ttl_seconds == 300
    assert module._executor.preload_calls == [("OCR",), ("PP-StructureV3",)]


async def test_residency_remains_bounded_while_preload_owns_executor_lock(
    client: SupervisorClient,
    module: SupervisorModule,
) -> None:
    """长时间预加载不能让驻留状态请求等待同一个 executor 锁。"""

    class BlockingPreloadExecutor(E2EExecutor):
        def __init__(self) -> None:
            super().__init__()
            self.preload_entered = threading.Event()
            self.preload_release = threading.Event()
            self.runtime_lock = threading.Lock()

        def preload(self, pipelines: tuple[str, ...]) -> ResidencyStatus:
            if pipelines == ("OCR",):
                self.preload_calls.append(pipelines)
                return ResidencyStatus(
                    default_ttl_seconds=300,
                    entries=(
                        ResidencyEntry(
                            pipeline="OCR",
                            kind=ResidencyKind.SOFT_TTL,
                        ),
                    ),
                )
            with self.runtime_lock:
                self.preload_calls.append(pipelines)
                self.preload_entered.set()
                self.preload_release.wait(timeout=1.0)
                return ResidencyStatus(
                    default_ttl_seconds=300,
                    entries=(
                        ResidencyEntry(
                            pipeline="OCR",
                            kind=ResidencyKind.SOFT_TTL,
                        ),
                        ResidencyEntry(
                            pipeline="PP-StructureV3",
                            kind=ResidencyKind.SOFT_TTL,
                        ),
                    ),
                )

        def residency_status(self) -> ResidencyStatus:
            with self.runtime_lock:
                return ResidencyStatus(default_ttl_seconds=300)

    executor = BlockingPreloadExecutor()
    module._executor = executor
    preload_task = asyncio.create_task(client.preload(("OCR", "PP-StructureV3")))
    assert await asyncio.to_thread(executor.preload_entered.wait, 0.5)

    started_at = time.monotonic()
    try:
        status = await asyncio.wait_for(client.residency(), timeout=0.2)
        assert time.monotonic() - started_at < 0.2
        assert status.default_ttl_seconds == 300
        assert [entry.pipeline for entry in status.entries] == ["OCR"]
    finally:
        executor.preload_release.set()
        await preload_task


# ---------------------------------------------------------------------------
# Happy path: submit → observe
# ---------------------------------------------------------------------------


async def test_generic_job_interface_preserves_intent_and_keyed_outcomes(
    client: SupervisorClient,
) -> None:
    request = SubmitRequest(
        request_id="request-e2e-1",
        kind=JobKind.RECOGNITION,
        priority=JobPriority.BACKGROUND,
        pipeline=PipelineSelection(
            pipeline_id="OCR",
            options={"use_doc_orientation_classify": False},
        ),
        items=(
            SubmitItem(
                client_item_key="file-a",
                ordinal=0,
                display_name="a.png",
                source={"type": "upload.v1", "attachment": "input-a"},
            ),
            SubmitItem(
                client_item_key="file-b",
                ordinal=1,
                display_name="b.png",
                source={"type": "upload.v1", "attachment": "input-b"},
            ),
        ),
    )
    ref = await client.submit(
        request,
        {
            "input-a": ("image/png", b"alpha"),
            "input-b": ("image/png", b"beta"),
        },
    )
    assert [item.client_item_key for item in ref.items] == ["file-a", "file-b"]

    deadline = time.time() + 3.0
    update = await client.observe(ref.job_id)
    while time.time() < deadline and update.snapshot.state not in TERMINAL_JOB_STATES:
        await asyncio.sleep(0.02)
        update = await client.observe(ref.job_id)

    assert update.snapshot.state is JobState.COMPLETED
    assert update.snapshot.request_id == "request-e2e-1"
    assert update.snapshot.priority is JobPriority.BACKGROUND
    assert update.snapshot.pipeline == request.pipeline
    item_by_key = {item.client_item_key: item.item_id for item in ref.items}
    assert [outcome.item_id for outcome in update.outcomes] == [
        item_by_key["file-a"],
        item_by_key["file-b"],
    ]
    payloads = [outcome.payload for outcome in update.outcomes]
    assert all(payload is not None for payload in payloads)
    assert [payload["text"] for payload in payloads if payload is not None] == [
        "ocr-a.png",
        "ocr-b.png",
    ]


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


async def test_cancel_returns_cooperative_mode(client: SupervisorClient) -> None:
    ref = await client.submit(
        _one_item_request("cancel"), {"input": ("image/png", b"x")}
    )
    # Give a moment for the job to be created (likely completes fast in tests).
    await asyncio.sleep(0.05)
    snap = (await client.observe(ref.job_id)).snapshot
    command = JobCommand(
        command_id=str(uuid4()),
        kind=JobCommandKind.CANCEL,
        job_id=ref.job_id,
    )
    if snap.state in TERMINAL_JOB_STATES:
        with pytest.raises(InferenceClientError):
            await client.command(command)
        return
    mode = await client.command(command)
    assert mode is CancelMode.COOPERATIVE


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


async def test_retry_rejects_when_no_failed_items(client: SupervisorClient) -> None:
    ref = await client.submit(
        _one_item_request("retry"), {"input": ("image/png", b"x")}
    )
    # Wait for completion (all items succeed).
    deadline = time.time() + 3.0
    snap = (await client.observe(ref.job_id)).snapshot
    while time.time() < deadline and snap.state not in TERMINAL_JOB_STATES:
        await asyncio.sleep(0.02)
        snap = (await client.observe(ref.job_id)).snapshot
    # No failed items → server returns JOB_NOT_RETRYABLE.
    with pytest.raises(InferenceClientError):
        await client.command(
            JobCommand(
                command_id=str(uuid4()),
                kind=JobCommandKind.RETRY,
                job_id=ref.job_id,
            )
        )


async def test_retry_with_expired_retained_input_returns_typed_error(
    client: SupervisorClient,
    module: SupervisorModule,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def expired_retry(_job_id: str) -> None:
        raise InputExpiredError("retry input expired or unavailable: item-1")

    monkeypatch.setattr(module, "retry", expired_retry)

    with pytest.raises(InferenceClientError) as raised:
        await client.command(
            JobCommand(
                command_id=str(uuid4()),
                kind=JobCommandKind.RETRY,
                job_id="expired-job",
            )
        )

    assert raised.value.code is ErrorCode.INPUT_EXPIRED
    assert raised.value.retryable is False


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
    ref = await client.submit(
        _one_item_request("forget"), {"input": ("image/png", b"x")}
    )
    deadline = time.time() + 3.0
    snap = (await client.observe(ref.job_id)).snapshot
    while time.time() < deadline and snap.state not in TERMINAL_JOB_STATES:
        await asyncio.sleep(0.02)
        snap = (await client.observe(ref.job_id)).snapshot
    await client.command(
        JobCommand(
            command_id=str(uuid4()),
            kind=JobCommandKind.FORGET,
            job_id=ref.job_id,
        )
    )
    with pytest.raises(InferenceClientError):
        await client.observe(ref.job_id)


# ---------------------------------------------------------------------------
# Client refuses non-loopback
# ---------------------------------------------------------------------------


def test_client_refuses_non_loopback_base_url() -> None:
    with pytest.raises(InferenceClientError) as exc_info:
        SupervisorClient(base_url="http://10.0.0.5:1234", session_token="x")
    assert exc_info.value.code.value == "FORBIDDEN_LOOPBACK"


def _one_item_request(suffix: str) -> SubmitRequest:
    return SubmitRequest(
        request_id=f"request-{suffix}-{uuid4()}",
        kind=JobKind.RECOGNITION,
        priority=JobPriority.INTERACTIVE,
        pipeline=PipelineSelection("OCR"),
        items=(
            SubmitItem(
                client_item_key=f"item-{suffix}",
                ordinal=0,
                display_name="a.png",
                source={"type": "upload.v1", "attachment": "input"},
            ),
        ),
    )
