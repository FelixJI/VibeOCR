"""Async HTTP client for the v2 supervisor.

Built on ``httpx.AsyncClient``. All business requests carry the session
Bearer token; the server also enforces loopback, but the client pins the
base URL to ``http://127.0.0.1:{port}`` so non-loopback never happens.

The client parses every response through the strict v2 parser so a
misbehaving server cannot smuggle unknown fields past the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from vibeocr.protocol.v2 import (
    SCHEMA_VERSION,
    CancelMode,
    ErrorCode,
    JobPriority,
    JobRef,
    JobSnapshot,
    ResidencyStatus,
    ResultEntry,
    SettingsSnapshot,
    StageEvent,
    parse_error_payload,
    parse_job_snapshot,
    parse_pipeline_spec,
    parse_residency_entry,
)

from .errors import InferenceClientError


@dataclass(frozen=True, slots=True)
class _Endpoints:
    base: str

    def health(self) -> str:
        return f"{self.base}/v2/health"

    def submit_recognition(self) -> str:
        return f"{self.base}/v2/jobs/recognition"

    def job(self, job_id: str) -> str:
        return f"{self.base}/v2/jobs/{job_id}"

    def events(self, job_id: str) -> str:
        return f"{self.base}/v2/jobs/{job_id}/events"

    def result(self, job_id: str) -> str:
        return f"{self.base}/v2/jobs/{job_id}/result"

    def cancel(self, job_id: str) -> str:
        return f"{self.base}/v2/jobs/{job_id}/cancel"

    def retry(self, job_id: str) -> str:
        return f"{self.base}/v2/jobs/{job_id}/retry"

    def residency(self) -> str:
        return f"{self.base}/v2/runtime/residency"

    def release(self) -> str:
        return f"{self.base}/v2/runtime/release"

    def settings(self) -> str:
        return f"{self.base}/v2/settings"


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
        self._endpoints = _Endpoints(self._base_url)
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
            timeout=httpx.Timeout(30.0, read=None),
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

    async def submit_recognition(
        self,
        uploads: list[tuple[str, str | None, bytes]],
        *,
        priority: JobPriority = JobPriority.INTERACTIVE,
    ) -> JobRef:
        client = self._require_client()
        files = []
        for display_name, content_type, data in uploads:
            files.append(
                ("files", (display_name, data, content_type or "application/octet-stream"))
            )
        resp = await client.post("/v2/jobs/recognition", files=files)
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        payload = resp.json()
        return JobRef(
            job_id=payload["job_id"],
            schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
            instance_id=payload.get("instance_id"),
        )

    # ------------------------------------------------------------------
    # Status / events / result
    # ------------------------------------------------------------------

    async def status(self, job_id: str) -> JobSnapshot:
        client = self._require_client()
        resp = await client.get(f"/v2/jobs/{job_id}")
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        return parse_job_snapshot(resp.json())

    async def events(self, job_id: str, *, after_sequence: int = 0) -> list[StageEvent]:
        client = self._require_client()
        resp = await client.get(
            f"/v2/jobs/{job_id}/events", params={"after_sequence": after_sequence}
        )
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        body = resp.json()
        events_raw = body.get("events", [])
        return [
            StageEvent(
                sequence=int(e["sequence"]),
                stage=e["stage"],
                item_id=e.get("item_id"),
                timestamp=e.get("timestamp", ""),
                detail=e.get("detail", {}),
            )
            for e in events_raw
        ]

    async def result(self, job_id: str) -> list[ResultEntry]:
        client = self._require_client()
        resp = await client.get(f"/v2/jobs/{job_id}/result")
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        body = resp.json()
        results = body.get("results", [])
        return [
            ResultEntry(
                item_id=r["item_id"],
                display_name=r["display_name"],
                payload=r.get("payload", {}),
                error_code=r.get("error_code"),
            )
            for r in results
        ]

    # ------------------------------------------------------------------
    # Cancel / retry
    # ------------------------------------------------------------------

    async def cancel(self, job_id: str) -> CancelMode:
        client = self._require_client()
        resp = await client.post(f"/v2/jobs/{job_id}/cancel")
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        return CancelMode(resp.json()["cancel_mode"])

    async def retry(self, job_id: str) -> JobRef:
        client = self._require_client()
        resp = await client.post(f"/v2/jobs/{job_id}/retry")
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        payload = resp.json()
        return JobRef(
            job_id=payload["job_id"],
            schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
            instance_id=payload.get("instance_id"),
        )

    async def delete(self, job_id: str) -> None:
        client = self._require_client()
        resp = await client.delete(f"/v2/jobs/{job_id}")
        if resp.status_code >= 400 and resp.status_code != 204:
            raise self._error_from_response(resp)

    # ------------------------------------------------------------------
    # Runtime / settings
    # ------------------------------------------------------------------

    async def residency(self) -> ResidencyStatus:
        client = self._require_client()
        resp = await client.get("/v2/runtime/residency")
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        body = resp.json()
        entries = tuple(parse_residency_entry(e) for e in body.get("entries", []))
        pipelines = tuple(parse_pipeline_spec(p) for p in body.get("pipelines", []))
        from vibeocr.protocol.v2 import ResidencyStatus as _RS

        return _RS(
            schema_version=int(body.get("schema_version", SCHEMA_VERSION)),
            default_ttl_seconds=int(body.get("default_ttl_seconds", 300)),
            entries=entries,
            pipelines=pipelines,
            vram_total_mb=body.get("vram_total_mb"),
            vram_used_mb=body.get("vram_used_mb"),
        )

    async def release_idle(self, pipeline: str | None = None) -> ResidencyStatus:
        client = self._require_client()
        resp = await client.post("/v2/runtime/release", json={"pipeline": pipeline})
        if resp.status_code >= 400:
            raise self._error_from_response(resp)
        body = resp.json()
        entries = tuple(parse_residency_entry(e) for e in body.get("entries", []))
        from vibeocr.protocol.v2 import ResidencyStatus as _RS

        return _RS(
            schema_version=int(body.get("schema_version", SCHEMA_VERSION)),
            default_ttl_seconds=int(body.get("default_ttl_seconds", 300)),
            entries=entries,
        )

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
                f"unexpected response status={resp.status_code}",
                retryable=False,
                detail={"status_code": resp.status_code},
            )


__all__ = ["SupervisorClient"]
