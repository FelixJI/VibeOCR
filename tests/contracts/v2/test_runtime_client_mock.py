from __future__ import annotations

import asyncio
import base64
import json
from importlib import resources

import pytest

from vibeocr.runtime_client.client import (
    MultipartAttachment,
    RuntimeClientError,
    RuntimeHttpClient,
    SupervisorClient,
)
from vibeocr.runtime_client.mock_server import MockRuntimeServer
from vibeocr.runtime_contracts import (
    CancelMode,
    JobCommand,
    JobCommandKind,
    JobKind,
    JobPriority,
    PipelineSelection,
    SubmitItem,
    SubmitRequest,
)
from vibeocr.runtime_contracts.errors import ErrorCode
from vibeocr.runtime_contracts.generated.operations import operation_path


def _runtime_golden() -> dict:
    raw = (
        resources.files("vibeocr.runtime_contracts.golden")
        .joinpath("runtime-api.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(raw)


def _submit_request() -> SubmitRequest:
    return SubmitRequest(
        request_id="request-golden",
        kind=JobKind.RECOGNITION,
        priority=JobPriority.INTERACTIVE,
        pipeline=PipelineSelection(pipeline_id="OCR", options={}),
        items=(
            SubmitItem(
                client_item_key="item-0",
                ordinal=0,
                display_name="page.png",
                source={"type": "upload.v1", "attachment": "attachment-0"},
            ),
        ),
    )


def test_mock_serves_golden_health_and_typed_auth_error() -> None:
    golden = _runtime_golden()
    with MockRuntimeServer() as server:
        anonymous = RuntimeHttpClient(base_url=server.base_url)

        assert anonymous.health() == golden["health"]
        with pytest.raises(RuntimeClientError) as raised:
            anonymous.residency()

    assert raised.value.status_code == 401
    assert raised.value.code is ErrorCode.UNAUTHORIZED
    assert raised.value.detail == golden["unauthorized_error"]["detail"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda health: health.pop("protocol_version"),
        lambda health: health.__setitem__("ready", "yes"),
        lambda health: health.__setitem__("unexpected", True),
    ],
    ids=("missing-required", "wrong-type", "unknown-field"),
)
def test_runtime_client_rejects_response_schema_violations(mutation) -> None:
    with MockRuntimeServer() as server:
        mutation(server._golden["health"])
        client = RuntimeHttpClient(base_url=server.base_url)

        with pytest.raises(RuntimeClientError) as raised:
            client.health()

    assert raised.value.code is ErrorCode.ADAPTER_PROTOCOL_VIOLATION
    assert raised.value.status_code == 200
    assert raised.value.detail["operation_id"] == "getRuntimeHealth"
    assert raised.value.detail["reason"]


def test_multipart_submit_is_recorded_and_matches_manifest() -> None:
    golden = _runtime_golden()
    png = base64.b64decode(golden["png"]["content_base64"], validate=True)
    request = _submit_request()
    with MockRuntimeServer() as server:
        client = RuntimeHttpClient(
            base_url=server.base_url,
            session_token=server.session_token,
        )

        job_ref = client.submit_job(
            request.to_payload(),
            {
                "attachment-0": MultipartAttachment(
                    filename="page.png",
                    media_type="image/png",
                    content=png,
                )
            },
        )

        assert job_ref["schema_version"] == 2
        assert len(server.state.submissions) == 1
        submission = server.state.submissions[0]
        assert submission.manifest == request.to_payload()
        assert submission.attachments["attachment-0"].filename == "page.png"
        assert submission.attachments["attachment-0"].media_type == "image/png"
        assert submission.attachments["attachment-0"].content == png
        assert server.state.requests[-1].path == operation_path("submitJob")


def test_mock_serves_golden_ndjson_and_png() -> None:
    golden = _runtime_golden()
    with MockRuntimeServer() as server:
        client = RuntimeHttpClient(
            base_url=server.base_url,
            session_token=server.session_token,
        )

        progress = client.load_pdf_session("session / golden")
        render = client.render_pdf_page("session / golden", page=0, size=64)

    assert progress == golden["ndjson_progress_lines"]
    assert render.media_type == golden["png"]["media_type"]
    assert render.body == base64.b64decode(
        golden["png"]["content_base64"], validate=True
    )
    assert "%2F" in server.state.requests[0].path


def test_async_supervisor_client_preserves_existing_submit_and_settings_api() -> None:
    request = _submit_request()

    async def scenario(server: MockRuntimeServer) -> None:
        async with SupervisorClient(
            base_url=server.base_url,
            session_token=server.session_token,
            instance_id="sup-golden",
        ) as client:
            health = await client.health()
            job_ref = await client.submit(
                request,
                {"attachment-0": ("image/png", b"png")},
            )
            settings = await client.get_settings()
            observed = await client.observe(job_ref.job_id)
            cancellation = await client.command(
                JobCommand(
                    command_id="command-golden",
                    kind=JobCommandKind.CANCEL,
                    job_id=job_ref.job_id,
                )
            )
            residency = await client.residency()
            released = await client.release_idle()
            preloaded = await client.preload(("OCR",))
            updated_settings = await client.put_settings(settings)
            exported = await client.export_ocr(
                raw_text="text",
                markdown_text="text",
                html_text="<p>text</p>",
                output_path="result.md",
                fmt="markdown",
            )
            decoded = await client.decode_qrcode(b"png")
            generated = await client.generate_qrcode("hello")

        assert health["protocol_version"] == 2
        assert job_ref.schema_version == 2
        assert observed.schema_version == 2
        assert cancellation is CancelMode.COOPERATIVE
        assert residency.schema_version == 2
        assert released == residency
        assert preloaded == residency
        assert settings.schema_version == 2
        assert updated_settings == settings
        assert exported["output_path"] == "result.md"
        assert decoded == []
        assert generated == _runtime_golden()["png"]["content_base64"]

    with MockRuntimeServer() as server:
        asyncio.run(scenario(server))


def test_async_supervisor_client_requires_context_manager() -> None:
    async def scenario(server: MockRuntimeServer) -> None:
        client = SupervisorClient(
            base_url=server.base_url,
            session_token=server.session_token,
        )
        with pytest.raises(RuntimeError, match="async context manager"):
            await client.health()

    with MockRuntimeServer() as server:
        asyncio.run(scenario(server))
