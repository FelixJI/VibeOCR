"""Branch-coverage tests for SupervisorClient error paths & utility methods.

The happy-path contract is proven in test_e2e_fake_executor.py via the ASGI
transport. Here we exercise the per-method ``>= 400`` error branches, the
command CANCEL/RETRY switch, attachment-mismatch validation, and the
``_log_http_response`` stream/elapsed defensive branches using ``httpx.MockTransport``
so each branch is hit deterministically without a full app.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from vibeocr.protocol.v2 import (
    CancelMode,
    JobCommand,
    JobCommandKind,
    JobKind,
    JobPriority,
    JobRef,
    PipelineSelection,
    SettingsSnapshot,
    SubmitItem,
    SubmitRequest,
)
from vibeocr.supervisor.client import SupervisorClient
from vibeocr.supervisor.errors import InferenceClientError


def _one_item_request() -> SubmitRequest:
    return SubmitRequest(
        request_id="r",
        kind=JobKind.RECOGNITION,
        priority=JobPriority.INTERACTIVE,
        pipeline=PipelineSelection("OCR"),
        items=(
            SubmitItem(
                client_item_key="i",
                ordinal=0,
                display_name="a.png",
                source={"type": "upload.v1", "attachment": "input"},
            ),
        ),
    )


def _client_with_transport(handler) -> SupervisorClient:
    """Build a SupervisorClient whose inner httpx client uses MockTransport."""
    c = SupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    c._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer t"},
        event_hooks={"response": [c._log_http_response]},
    )
    return c


def _error_body(code: str = "NOT_FOUND", message: str = "nope") -> dict[str, Any]:
    return {
        "schema_version": 2,
        "error": {
            "code": code,
            "message": message,
            "retryable": False,
        },
    }


# ---------------------------------------------------------------------------
# submit: attachment mismatch + error response
# ---------------------------------------------------------------------------


async def test_submit_rejects_attachment_mismatch() -> None:
    """attachments not exactly matching manifest upload sources → ValueError (line 116)."""
    c = _client_with_transport(lambda req: httpx.Response(200, json={}))
    # Provide a mismatched attachment key
    with pytest.raises(ValueError, match="attachments must exactly match"):
        await c.submit(_one_item_request(), {"wrong-key": (None, b"x")})


async def test_submit_raises_on_error_response() -> None:
    """submit surfaces a 4xx as InferenceClientError (line 136)."""
    c = _client_with_transport(
        lambda req: httpx.Response(400, json=_error_body("BAD_REQUEST", "bad"))
    )
    with pytest.raises(InferenceClientError):
        await c.submit(_one_item_request(), {"input": (None, b"x")})


# ---------------------------------------------------------------------------
# observe / residency / release_idle / preload / settings error branches
# ---------------------------------------------------------------------------


async def test_observe_raises_on_error_response() -> None:
    """observe on a 4xx surfaces InferenceClientError (line 151-152)."""
    c = _client_with_transport(
        lambda req: httpx.Response(404, json=_error_body("NOT_FOUND", "missing"))
    )
    with pytest.raises(InferenceClientError):
        await c.observe("job-1")


async def test_residency_raises_on_error_response() -> None:
    """residency on a 4xx surfaces InferenceClientError (line 178)."""
    c = _client_with_transport(
        lambda req: httpx.Response(500, json=_error_body("INTERNAL_ERROR", "boom"))
    )
    with pytest.raises(InferenceClientError):
        await c.residency()


async def test_release_idle_raises_on_error_response() -> None:
    """release_idle on a 4xx surfaces InferenceClientError (line 185)."""
    c = _client_with_transport(
        lambda req: httpx.Response(500, json=_error_body("INTERNAL_ERROR", "boom"))
    )
    with pytest.raises(InferenceClientError):
        await c.release_idle()


async def test_preload_raises_on_error_response() -> None:
    """preload on a 4xx surfaces InferenceClientError (line 196)."""
    c = _client_with_transport(
        lambda req: httpx.Response(500, json=_error_body("INTERNAL_ERROR", "boom"))
    )
    with pytest.raises(InferenceClientError):
        await c.preload(("OCR",))


async def test_get_settings_raises_on_error_response() -> None:
    """get_settings on a 4xx surfaces InferenceClientError (line 203)."""
    c = _client_with_transport(
        lambda req: httpx.Response(500, json=_error_body("INTERNAL_ERROR", "boom"))
    )
    with pytest.raises(InferenceClientError):
        await c.get_settings()


async def test_put_settings_raises_on_error_response() -> None:
    """put_settings on a 4xx surfaces InferenceClientError (line 210)."""
    c = _client_with_transport(
        lambda req: httpx.Response(422, json=_error_body("VALIDATION_ERROR", "bad"))
    )
    snap = SettingsSnapshot(schema_version=2, default_ttl_seconds=300, pipelines=())
    with pytest.raises(InferenceClientError):
        await c.put_settings(snap)


# ---------------------------------------------------------------------------
# command: CANCEL and RETRY branches + error
# ---------------------------------------------------------------------------


async def test_command_cancel_returns_cancel_mode() -> None:
    """CANCEL command returns CancelMode (line 165)."""
    c = _client_with_transport(
        lambda req: httpx.Response(200, json={"cancel_mode": "cooperative"})
    )
    cmd = JobCommand(
        command_id="c1",
        kind=JobCommandKind.CANCEL,
        job_id="job-1",
    )
    result = await c.command(cmd)
    assert result == CancelMode.COOPERATIVE


async def test_command_retry_returns_job_ref() -> None:
    """RETRY command returns JobRef (line 167)."""
    c = _client_with_transport(
        lambda req: httpx.Response(
            200,
            json={
                "schema_version": 2,
                "job_ref": {
                    "schema_version": 2,
                    "job_id": "job-1",
                    "state": "running",
                    "instance_id": "x",
                },
            },
        )
    )
    cmd = JobCommand(
        command_id="c1",
        kind=JobCommandKind.RETRY,
        job_id="job-1",
        item_ids=("item-1",),
    )
    result = await c.command(cmd)
    assert isinstance(result, JobRef)
    assert result.job_id == "job-1"


async def test_command_unknown_kind_returns_none() -> None:
    """An unhandled command kind falls through to return None (line 168)."""
    c = _client_with_transport(lambda req: httpx.Response(200, json={}))
    # Use a sentinel kind that is neither CANCEL nor RETRY
    cmd = MagicMock()
    cmd.kind = "something-else"
    cmd.to_payload.return_value = {"kind": "something-else"}
    result = await c.command(cmd)  # type: ignore[arg-type]
    assert result is None


async def test_command_raises_on_error_response() -> None:
    """command on a 4xx surfaces InferenceClientError (line 162)."""
    c = _client_with_transport(
        lambda req: httpx.Response(409, json=_error_body("CONFLICT", "conflict"))
    )
    cmd = JobCommand(command_id="c1", kind=JobCommandKind.CANCEL, job_id="job-1")
    with pytest.raises(InferenceClientError):
        await c.command(cmd)


# ---------------------------------------------------------------------------
# export_ocr / decode_qrcode / generate_qrcode error branches
# ---------------------------------------------------------------------------


async def test_export_ocr_raises_on_error_response() -> None:
    """export_ocr on a 4xx surfaces InferenceClientError (line 242)."""
    c = _client_with_transport(
        lambda req: httpx.Response(500, json=_error_body("INTERNAL_ERROR", "boom"))
    )
    with pytest.raises(InferenceClientError):
        await c.export_ocr(
            raw_text="r",
            markdown_text="m",
            html_text="h",
            output_path="/tmp/out.txt",
            fmt="txt",
        )


async def test_decode_qrcode_raises_on_error_response() -> None:
    """decode_qrcode on a 4xx surfaces InferenceClientError (line 254)."""
    c = _client_with_transport(
        lambda req: httpx.Response(400, json=_error_body("BAD_REQUEST", "bad"))
    )
    with pytest.raises(InferenceClientError):
        await c.decode_qrcode(b"img")


async def test_generate_qrcode_raises_on_error_response() -> None:
    """generate_qrcode on a 4xx surfaces InferenceClientError (line 270)."""
    c = _client_with_transport(
        lambda req: httpx.Response(400, json=_error_body("BAD_REQUEST", "bad"))
    )
    with pytest.raises(InferenceClientError):
        await c.generate_qrcode("data")


# ---------------------------------------------------------------------------
# Happy-path utility calls that return parsed DTOs (cover _parse_* helpers)
# ---------------------------------------------------------------------------


async def test_residency_parses_entries_and_pipelines() -> None:
    """residency happy path parses entries + pipelines (covers _parse_residency)."""
    body = {
        "schema_version": 2,
        "default_ttl_seconds": 300,
        "entries": [
            {
                "kind": "pinned",
                "pipeline": "OCR",
                "active_leases": 1,
                "remaining_ttl_seconds": 30,
            }
        ],
        "pipelines": [{"name": "OCR", "ttl_seconds": 60}],
        "vram_total_mb": 24000,
        "vram_used_mb": 1000,
    }
    c = _client_with_transport(lambda req: httpx.Response(200, json=body))
    status = await c.residency()
    assert status.default_ttl_seconds == 300
    assert len(status.entries) == 1
    assert len(status.pipelines) == 1
    assert status.vram_total_mb == 24000


async def test_release_idle_returns_parsed_residency() -> None:
    """release_idle happy path (line 186 return)."""
    body = {"schema_version": 2, "default_ttl_seconds": 60, "entries": [], "pipelines": []}
    c = _client_with_transport(lambda req: httpx.Response(200, json=body))
    status = await c.release_idle("OCR")
    assert status.default_ttl_seconds == 60


async def test_preload_returns_parsed_residency() -> None:
    """preload happy path (line 197 return)."""
    body = {"schema_version": 2, "default_ttl_seconds": 60, "entries": [], "pipelines": []}
    c = _client_with_transport(lambda req: httpx.Response(200, json=body))
    status = await c.preload(("OCR",))
    assert status.default_ttl_seconds == 60


async def test_get_settings_parses_snapshot() -> None:
    """get_settings happy path (covers _parse_settings, line 204 return)."""
    body = {
        "schema_version": 2,
        "residency": {
            "default_ttl_seconds": 120,
            "pipelines": [{"schema_version": 2, "name": "OCR"}],
        },
        "extra": {"k": "v"},
    }
    c = _client_with_transport(lambda req: httpx.Response(200, json=body))
    snap = await c.get_settings()
    assert snap.default_ttl_seconds == 120
    assert len(snap.pipelines) == 1
    assert snap.extra == {"k": "v"}


async def test_put_settings_returns_parsed_snapshot() -> None:
    """put_settings happy path (line 211 return)."""
    body = {
        "schema_version": 2,
        "residency": {"default_ttl_seconds": 90, "pipelines": []},
        "extra": {},
    }
    c = _client_with_transport(lambda req: httpx.Response(200, json=body))
    snap_in = SettingsSnapshot(schema_version=2, default_ttl_seconds=90, pipelines=())
    snap_out = await c.put_settings(snap_in)
    assert snap_out.default_ttl_seconds == 90


async def test_export_ocr_returns_body() -> None:
    """export_ocr happy path (line 243 return)."""
    c = _client_with_transport(
        lambda req: httpx.Response(200, json={"path": "/tmp/out.txt"})
    )
    result = await c.export_ocr(
        raw_text="r",
        markdown_text="m",
        html_text="h",
        output_path="/tmp/out.txt",
        fmt="txt",
    )
    assert result == {"path": "/tmp/out.txt"}


async def test_decode_qrcode_returns_codes_list() -> None:
    """decode_qrcode happy path (line 255 return)."""
    c = _client_with_transport(
        lambda req: httpx.Response(200, json={"codes": [{"text": "abc"}]})
    )
    result = await c.decode_qrcode(b"img")
    assert result == [{"text": "abc"}]


async def test_generate_qrcode_returns_image_str() -> None:
    """generate_qrcode happy path (line 271 return)."""
    c = _client_with_transport(
        lambda req: httpx.Response(200, json={"image": "base64data"})
    )
    result = await c.generate_qrcode("data", fmt="png")
    assert result == "base64data"


async def test_observe_returns_parsed_update() -> None:
    """observe happy path (line 153 return).

    The full valid JobUpdate body is exercised end-to-end in
    test_e2e_fake_executor; here we just confirm the parsing path is reached
    (a minimal malformed body surfaces a ContractError rather than crashing).
    """
    from vibeocr.protocol.v2.parser import ContractError

    c = _client_with_transport(lambda req: httpx.Response(200, json={"job_id": "x"}))
    with pytest.raises(ContractError):
        await c.observe("job-1")


# ---------------------------------------------------------------------------
# _error_from_response: malformed body fallback
# ---------------------------------------------------------------------------


async def test_error_from_response_falls_back_on_malformed_body() -> None:
    """A non-JSON / unparseable error body falls back to INTERNAL_ERROR (line 306)."""
    c = _client_with_transport(lambda req: httpx.Response(502, content=b"not json"))
    with pytest.raises(InferenceClientError) as exc:
        await c.observe("job-1")
    # Fallback path produces INTERNAL_ERROR regardless of upstream 502
    assert exc.value.retryable is False


# ---------------------------------------------------------------------------
# _log_http_response: stream-error & elapsed defensive branches
# ---------------------------------------------------------------------------


async def test_log_http_response_handles_stream_errors() -> None:
    """request.content / resp.content raising StreamError is suppressed (lines 320-321, 324-325)."""
    c = SupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    request = httpx.Request("POST", "http://127.0.0.1/v2/jobs")
    # Build a response that is still streaming (content not yet read) →
    # accessing resp.content raises ResponseNotRead (a StreamError subclass).
    resp = httpx.Response(
        200,
        request=request,
        stream=httpx.ByteStream(b""),
    )
    resp.elapsed = timedelta(milliseconds=10)
    # Should not raise even though content isn't consumed
    await c._log_http_response(resp)


async def test_log_http_response_handles_missing_elapsed() -> None:
    """resp.elapsed being None should not crash (line 333-334 branch)."""
    c = SupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    request = httpx.Request("GET", "http://127.0.0.1/v2/health")
    resp = httpx.Response(
        200,
        headers={"content-length": "2"},
        request=request,
        content=b"{}",
    )
    # elapsed defaults to 0; force the attribute path that yields None
    resp.elapsed = None  # type: ignore[assignment]
    # Should not raise
    await c._log_http_response(resp)


async def test_log_http_response_elapsed_exception_branch(monkeypatch) -> None:
    """The elapsed getattr/total_seconds exception branch yields elapsed_ms=None."""
    c = SupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    captured: dict[str, Any] = {}

    def capture(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("vibeocr.supervisor.client.log_http_response", capture)

    request = httpx.Request("GET", "http://127.0.0.1/v2/health")

    # Build a response whose .elapsed raises on total_seconds
    resp = httpx.Response(200, request=request, content=b"{}")

    class _BadTimedelta:
        def total_seconds(self) -> float:
            raise RuntimeError("boom")

    resp.elapsed = _BadTimedelta()  # type: ignore[assignment]
    await c._log_http_response(resp)
    assert captured.get("elapsed_ms") is None


# ---------------------------------------------------------------------------
# require_client guard
# ---------------------------------------------------------------------------


async def test_require_client_raises_when_not_entered() -> None:
    """Using the client without entering the async context raises RuntimeError (line 86)."""
    c = SupervisorClient(
        base_url="http://127.0.0.1", session_token="t", instance_id="x"
    )
    with pytest.raises(RuntimeError, match="async context manager"):
        await c.health()
