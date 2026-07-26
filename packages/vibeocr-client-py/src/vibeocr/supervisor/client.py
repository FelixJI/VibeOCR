"""Async HTTP client for the v2 supervisor.

Built on ``httpx.AsyncClient``. All business requests carry the session
Bearer token; the server also enforces loopback, but the client pins the
base URL to ``http://127.0.0.1:{port}`` so non-loopback never happens.

The client parses every response through the strict v2 parser so a
misbehaving server cannot smuggle unknown fields past the UI.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from vibeocr.protocol.v2 import (
    SCHEMA_VERSION,
    CancelMode,
    ErrorCode,
    JobCommand,
    JobCommandKind,
    JobRef,
    JobUpdate,
    ResidencyStatus,
    SettingsSnapshot,
    SubmitRequest,
    parse_error_payload,
    parse_job_ref,
    parse_job_update,
    parse_pipeline_spec,
    parse_residency_entry,
)
from vibeocr.utils.http_log import (
    guess_request_size,
    guess_response_size,
    log_http_response,
)

from .errors import InferenceClientError

logger = logging.getLogger(__name__)


class SupervisorClient:
    """Async HTTP v2 client. Use as an async context manager."""

    def __init__(self, *, base_url: str, session_token: str, instance_id: str | None = None) -> None:
        if not base_url.startswith("http://127.0.0.1"):
            # Pin loopback in the client too; defence in depth.
            raise InferenceClientError(
                ErrorCode.FORBIDDEN_LOOPBACK,
                "supervisor client refuses non-loopback base url",
            )
        self._base_url = base_url.rstrip("/")
        self._token = session_token
        self.instance_id = instance_id
        self._client: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> SupervisorClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=httpx.Timeout(30.0),
            event_hooks={"response": [self._log_http_response]},
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("SupervisorClient must be used as an async context manager")
        return self._client

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        client = self._require_client()
        resp = await client.get("/v2/health")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    async def submit(
        self,
        request: SubmitRequest,
        attachments: dict[str, tuple[str | None, bytes]],
    ) -> JobRef:
        """Submit one logical manifest; attachments are keyed by source name."""
        client = self._require_client()
        expected = {
            str(item.source["attachment"]): item
            for item in request.items
            if item.source.get("type") == "upload.v1"
        }
        if set(expected) != set(attachments):
            raise ValueError("attachments must exactly match manifest upload sources")
        files = []
        for attachment, item in expected.items():
            content_type, data = attachments[attachment]
            files.append(
                (
                    attachment,
                    (
                        item.display_name,
                        data,
                        content_type or "application/octet-stream",
                    ),
                )
            )
        resp = await client.post(
            "/v2/jobs",
            data={"manifest": json.dumps(request.to_payload(), ensure_ascii=False)},
            files=files,
        )
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        return parse_job_ref(resp.json())

    # ------------------------------------------------------------------
    # Atomic observation
    # ------------------------------------------------------------------

    async def observe(
        self, job_id: str, *, after_sequence: int = 0
    ) -> JobUpdate:
        client = self._require_client()
        resp = await client.get(
            f"/v2/jobs/{job_id}/observe",
            params={"after_sequence": after_sequence},
        )
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        return parse_job_update(resp.json())

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    async def command(self, command: JobCommand) -> JobRef | CancelMode | None:
        client = self._require_client()
        resp = await client.post("/v2/jobs/command", json=command.to_payload())
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        body = resp.json()
        if command.kind is JobCommandKind.CANCEL:
            return CancelMode(body["cancel_mode"])
        if command.kind is JobCommandKind.RETRY:
            return parse_job_ref(body["job_ref"])
        return None

    # ------------------------------------------------------------------
    # Runtime / settings
    # ------------------------------------------------------------------

    async def residency(self) -> ResidencyStatus:
        client = self._require_client()
        resp = await client.get("/v2/runtime/residency")
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        return self._parse_residency(resp.json())

    async def release_idle(self, pipeline: str | None = None) -> ResidencyStatus:
        client = self._require_client()
        resp = await client.post("/v2/runtime/release", json={"pipeline": pipeline})
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        return self._parse_residency(resp.json())

    async def preload(self, pipelines: tuple[str, ...]) -> ResidencyStatus:
        client = self._require_client()
        resp = await client.post(
            "/v2/runtime/preload",
            json={"pipelines": list(pipelines)},
            timeout=httpx.Timeout(600.0),
        )
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        return self._parse_residency(resp.json())

    async def get_settings(self) -> SettingsSnapshot:
        client = self._require_client()
        resp = await client.get("/v2/settings")
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        return self._parse_settings(resp.json())

    async def put_settings(self, snapshot: SettingsSnapshot) -> SettingsSnapshot:
        client = self._require_client()
        resp = await client.put("/v2/settings", json=snapshot.to_payload())
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        return self._parse_settings(resp.json())

    # ------------------------------------------------------------------
    # Bounded utility operations
    # ------------------------------------------------------------------

    async def export_ocr(
        self,
        *,
        raw_text: str,
        markdown_text: str,
        html_text: str,
        raw_blocks: list[dict[str, Any]] | None = None,
        output_path: str,
        fmt: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        client = self._require_client()
        resp = await client.post(
            "/v2/export",
            json={
                "raw_text": raw_text,
                "markdown_text": markdown_text,
                "html_text": html_text,
                "raw_blocks": raw_blocks or [],
                "output_path": output_path,
                "format": fmt,
                "overwrite": overwrite,
            },
        )
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        return resp.json()

    async def decode_qrcode(self, image_bytes: bytes) -> list[dict[str, Any]]:
        import base64

        client = self._require_client()
        resp = await client.post(
            "/v2/qrcode/decode",
            json={"image": base64.b64encode(image_bytes).decode("ascii")},
        )
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        return list(resp.json().get("codes", []))

    async def generate_qrcode(
        self,
        data: str,
        *,
        fmt: str = "qrcode",
        options: dict[str, Any] | None = None,
    ) -> str:
        client = self._require_client()
        resp = await client.post(
            "/v2/qrcode/generate",
            json={"data": data, "format": fmt, "options": options or {}},
        )
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        return str(resp.json().get("image", ""))

    @staticmethod
    def _parse_residency(body: dict[str, Any]) -> ResidencyStatus:
        entries = tuple(parse_residency_entry(e) for e in body.get("entries", []))
        pipelines = tuple(parse_pipeline_spec(p) for p in body.get("pipelines", []))
        return ResidencyStatus(
            schema_version=int(body.get("schema_version", SCHEMA_VERSION)),
            default_ttl_seconds=int(body.get("default_ttl_seconds", 300)),
            entries=entries,
            pipelines=pipelines,
            vram_total_mb=body.get("vram_total_mb"),
            vram_used_mb=body.get("vram_used_mb"),
        )

    def _parse_settings(self, body: dict[str, Any]) -> SettingsSnapshot:
        residency = body.get("residency", {})
        pipelines = tuple(parse_pipeline_spec(p) for p in residency.get("pipelines", []))
        return SettingsSnapshot(
            schema_version=int(body.get("schema_version", SCHEMA_VERSION)),
            default_ttl_seconds=int(residency.get("default_ttl_seconds", 300)),
            pipelines=pipelines,
            extra=body.get("extra", {}),
        )

    # ------------------------------------------------------------------
    # Error decoding
    # ------------------------------------------------------------------

    def _error_from_response(self, resp: httpx.Response) -> InferenceClientError:
        try:
            body = resp.json()
            payload = parse_error_payload(body)
            return InferenceClientError.from_payload(payload)
        except Exception:
            return InferenceClientError(
                ErrorCode.INTERNAL_ERROR,
                f"unexpected response status={resp.status_code} ({resp.reason_phrase})",
                retryable=False,
                detail={
                    "status_code": resp.status_code,
                    "status_detail": resp.reason_phrase,
                },
            )

    def _log_http_response(self, resp: httpx.Response) -> None:
        request = resp.request
        req_size = guess_request_size(getattr(request, "content", None))
        resp_size = guess_response_size(
            dict(resp.headers),
            resp.content if getattr(resp, "num_bytes_downloaded", None) is not None else None,
        )
        elapsed_ms = None
        try:
            raw_elapsed = getattr(resp, "elapsed", None)
            if raw_elapsed is not None:
                elapsed_ms = raw_elapsed.total_seconds() * 1000.0
        except Exception:
            elapsed_ms = None
        log_http_response(
            logger=logger,
            method=request.method,
            url=str(request.url),
            status_code=resp.status_code,
            reason=resp.reason_phrase,
            elapsed_ms=elapsed_ms,
            request_bytes=req_size,
            response_bytes=resp_size,
        )


__all__ = ["SupervisorClient"]
