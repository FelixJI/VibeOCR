"""HTTP clients for the formal Runtime Protocol v2 API."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from vibeocr.runtime_contracts import (
    CancelMode,
    JobCommand,
    JobCommandKind,
    JobRef,
    JobUpdate,
    ResidencyStatus,
    SettingsSnapshot,
    SubmitRequest,
)
from vibeocr.runtime_contracts.errors import ErrorCode, ErrorPayload
from vibeocr.runtime_contracts.generated.operations import (
    OPERATION_IDS_BY_NAME,
    operation_path,
)
from vibeocr.runtime_contracts.generated.server import RESPONSE_JSON_SCHEMAS
from vibeocr.runtime_contracts.parser import (
    parse_error_payload,
    parse_job_ref,
    parse_job_update,
    parse_pipeline_spec,
    parse_residency_entry,
)
from vibeocr.runtime_contracts.utils.http_log import (
    guess_request_size,
    guess_response_size,
    log_http_response,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from vibeocr.runtime_contracts.generated.wire_types import (
        CommandResult,
        ExportRequest,
        ExportResponse,
        Health,
        PdfOpenResponse,
        QrDecodeRequest,
        QrDecodeResponse,
        QrGenerateRequest,
        QrGenerateResponse,
    )
    from vibeocr.runtime_contracts.generated.wire_types import (
        JobRef as WireJobRef,
    )
    from vibeocr.runtime_contracts.generated.wire_types import (
        JobUpdate as WireJobUpdate,
    )
    from vibeocr.runtime_contracts.generated.wire_types import (
        ResidencyStatus as WireResidencyStatus,
    )
    from vibeocr.runtime_contracts.generated.wire_types import (
        SettingsSnapshot as WireSettingsSnapshot,
    )

JsonObject = dict[str, Any]


@cache
def _response_validator(operation_id: str) -> Draft202012Validator:
    try:
        schema = RESPONSE_JSON_SCHEMAS[operation_id]
    except KeyError as exc:
        raise RuntimeClientError(
            ErrorCode.ADAPTER_PROTOCOL_VIOLATION,
            f"no JSON response schema is generated for {operation_id}",
            detail={"operation_id": operation_id},
        ) from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_response_object(
    operation_id: str,
    value: JsonObject,
    *,
    status_code: int | None = None,
) -> JsonObject:
    try:
        _response_validator(operation_id).validate(value)
    except ValidationError as exc:
        raise RuntimeClientError(
            ErrorCode.ADAPTER_PROTOCOL_VIOLATION,
            f"runtime response violates the {operation_id} schema",
            detail={
                "operation_id": operation_id,
                "instance_path": "/" + "/".join(str(item) for item in exc.path),
                "schema_path": "/" + "/".join(str(item) for item in exc.schema_path),
                "reason": exc.message,
            },
            status_code=status_code,
        ) from exc
    return value


class RuntimeClientError(Exception):
    """A transport or formal Runtime API error safe to expose to products."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        detail: Mapping[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.detail = dict(detail or {})
        self.status_code = status_code

    @classmethod
    def from_payload(
        cls,
        payload: ErrorPayload,
        *,
        status_code: int | None = None,
    ) -> RuntimeClientError:
        return cls(
            payload.code,
            payload.message,
            retryable=payload.retryable,
            detail=payload.detail,
            status_code=status_code,
        )


@dataclass(frozen=True, slots=True)
class MultipartAttachment:
    """One multipart upload part keyed by its manifest attachment name."""

    filename: str
    content: bytes
    media_type: str = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class RuntimeHttpResponse:
    """Materialized response returned by the dependency-free transport."""

    status_code: int
    media_type: str
    body: bytes

    def json(self) -> JsonObject:
        value = json.loads(self.body)
        if not isinstance(value, dict):
            raise RuntimeClientError(
                ErrorCode.ADAPTER_PROTOCOL_VIOLATION,
                "runtime response must be a JSON object",
                detail={"media_type": self.media_type},
                status_code=self.status_code,
            )
        return value

    def ndjson(self) -> list[JsonObject]:
        rows: list[JsonObject] = []
        for line_number, raw_line in enumerate(self.body.splitlines(), start=1):
            if not raw_line.strip():
                continue
            value = json.loads(raw_line)
            if not isinstance(value, dict):
                raise RuntimeClientError(
                    ErrorCode.ADAPTER_PROTOCOL_VIOLATION,
                    "runtime NDJSON line must be an object",
                    detail={"line_number": line_number},
                    status_code=self.status_code,
                )
            rows.append(value)
        return rows


class RuntimeHttpClient:
    """Small synchronous stdlib transport keyed by generated operation IDs."""

    def __init__(
        self,
        *,
        base_url: str,
        session_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            raise RuntimeClientError(
                ErrorCode.FORBIDDEN_LOOPBACK,
                "runtime client refuses non-loopback base url",
            )
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain credentials")
        self._base_url = base_url.rstrip("/")
        self._token = session_token
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        return self._base_url

    def request(
        self,
        operation_id: str,
        *,
        path_parameters: Mapping[str, str | int] | None = None,
        query: Mapping[str, str | int | bool | None] | None = None,
        json_body: Mapping[str, Any] | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> RuntimeHttpResponse:
        """Invoke one generated operation and materialize its response."""

        try:
            operation = OPERATION_IDS_BY_NAME[operation_id]
        except KeyError as exc:
            raise ValueError(f"unknown runtime operation: {operation_id}") from exc
        path = operation_path(operation_id)
        for name, value in (path_parameters or {}).items():
            path = path.replace(f"{{{name}}}", quote(str(value), safe=""))
        if "{" in path or "}" in path:
            raise ValueError(f"missing path parameter for operation: {operation_id}")

        filtered_query = {
            key: value for key, value in (query or {}).items() if value is not None
        }
        url = f"{self._base_url}{path}"
        if filtered_query:
            url = f"{url}?{urlencode(filtered_query)}"

        request_headers = {"Accept": "application/json"}
        if self._token is not None:
            request_headers["Authorization"] = f"Bearer {self._token}"
        if json_body is not None:
            if body is not None:
                raise ValueError("json_body and body are mutually exclusive")
            body = json.dumps(
                json_body, ensure_ascii=False, separators=(",", ":")
            ).encode()
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        request = Request(
            url,
            data=body,
            headers=request_headers,
            method=operation.method,
        )
        try:
            with urlopen(request, timeout=timeout or self._timeout) as response:
                response_body = response.read()
                media_type = response.headers.get_content_type()
                return RuntimeHttpResponse(response.status, media_type, response_body)
        except HTTPError as exc:
            response_body = exc.read()
            self._raise_response_error(
                exc.code,
                exc.headers.get_content_type(),
                response_body,
            )
        except (TimeoutError, URLError, OSError) as exc:
            raise RuntimeClientError(
                ErrorCode.BACKEND_UNAVAILABLE,
                f"runtime request failed: {exc}",
                retryable=True,
                detail={"operation_id": operation_id},
            ) from exc
        raise AssertionError("unreachable")

    def request_json(self, operation_id: str, **kwargs: Any) -> JsonObject:
        response = self.request(operation_id, **kwargs)
        return _validate_response_object(
            operation_id,
            response.json(),
            status_code=response.status_code,
        )

    def request_ndjson(self, operation_id: str, **kwargs: Any) -> list[JsonObject]:
        response = self.request(operation_id, **kwargs)
        if response.media_type not in {"application/x-ndjson", "application/ndjson"}:
            raise RuntimeClientError(
                ErrorCode.ADAPTER_PROTOCOL_VIOLATION,
                "runtime response is not NDJSON",
                detail={"media_type": response.media_type},
                status_code=response.status_code,
            )
        return response.ndjson()

    def health(self) -> Health:
        return cast("Health", self.request_json("getRuntimeHealth"))

    def submit_job(
        self,
        manifest: Mapping[str, Any],
        attachments: Mapping[str, MultipartAttachment],
    ) -> WireJobRef:
        body, content_type = _encode_multipart(manifest, attachments)
        return cast(
            "WireJobRef",
            self.request_json(
                "submitJob",
                body=body,
                headers={"Content-Type": content_type},
            ),
        )

    def observe_job(self, job_id: str, *, after_sequence: int = 0) -> WireJobUpdate:
        return cast(
            "WireJobUpdate",
            self.request_json(
                "observeJob",
                path_parameters={"job_id": job_id},
                query={"after_sequence": after_sequence},
            ),
        )

    def command_job(self, command: Mapping[str, Any]) -> CommandResult:
        return cast("CommandResult", self.request_json("commandJob", json_body=command))

    def residency(self) -> WireResidencyStatus:
        return cast("WireResidencyStatus", self.request_json("getRuntimeResidency"))

    def release_idle(self, pipeline: str | None = None) -> WireResidencyStatus:
        return cast(
            "WireResidencyStatus",
            self.request_json("releaseRuntime", json_body={"pipeline": pipeline}),
        )

    def preload(self, pipelines: Iterable[str]) -> WireResidencyStatus:
        return cast(
            "WireResidencyStatus",
            self.request_json(
                "preloadRuntime",
                json_body={"pipelines": list(pipelines)},
                timeout=600.0,
            ),
        )

    def get_settings(self) -> WireSettingsSnapshot:
        return cast("WireSettingsSnapshot", self.request_json("getSettings"))

    def put_settings(self, snapshot: Mapping[str, Any]) -> WireSettingsSnapshot:
        return cast(
            "WireSettingsSnapshot",
            self.request_json("putSettings", json_body=snapshot),
        )

    def export_ocr(self, request: ExportRequest) -> ExportResponse:
        return cast("ExportResponse", self.request_json("exportOcr", json_body=request))

    def decode_qrcode(self, request: QrDecodeRequest) -> QrDecodeResponse:
        return cast(
            "QrDecodeResponse", self.request_json("decodeQrCode", json_body=request)
        )

    def generate_qrcode(self, request: QrGenerateRequest) -> QrGenerateResponse:
        return cast(
            "QrGenerateResponse",
            self.request_json("generateQrCode", json_body=request),
        )

    def open_pdf_session(self, path: str) -> PdfOpenResponse:
        return cast(
            "PdfOpenResponse",
            self.request_json("openPdfSession", json_body={"path": path}),
        )

    def load_pdf_session(self, session_id: str) -> list[JsonObject]:
        return self.request_ndjson(
            "loadPdfSession",
            path_parameters={"session_id": session_id},
        )

    def render_pdf_page(
        self,
        session_id: str,
        *,
        page: int,
        size: int | None = None,
    ) -> RuntimeHttpResponse:
        response = self.request(
            "renderPdfPage",
            path_parameters={"session_id": session_id},
            query={"page": page, "size": size},
            headers={"Accept": "image/png"},
        )
        if response.media_type != "image/png":
            raise RuntimeClientError(
                ErrorCode.ADAPTER_PROTOCOL_VIOLATION,
                "runtime response is not PNG",
                detail={"media_type": response.media_type},
                status_code=response.status_code,
            )
        return response

    @staticmethod
    def _raise_response_error(status_code: int, media_type: str, body: bytes) -> None:
        try:
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError
            payload = parse_error_payload(value)
        except Exception as exc:
            raise RuntimeClientError(
                ErrorCode.INTERNAL_ERROR,
                f"unexpected runtime response status={status_code}",
                detail={"media_type": media_type},
                status_code=status_code,
            ) from exc
        raise RuntimeClientError.from_payload(payload, status_code=status_code)


class AsyncRuntimeTransport:
    """Protocol-owned async loopback transport for product adapters.

    Product packages may translate domain DTOs, but authentication, connection
    lifecycle, host pinning and actual HTTP dispatch stay in this SDK.
    """

    def __init__(
        self,
        *,
        base_url: str,
        session_token: str | None = None,
        timeout: httpx.Timeout | float = 30.0,
        response_hook: Any | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            raise RuntimeClientError(
                ErrorCode.FORBIDDEN_LOOPBACK,
                "runtime client refuses non-loopback base url",
            )
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain credentials")
        self._base_url = base_url.rstrip("/")
        self._token = session_token
        self._timeout = timeout
        self._response_hook = response_hook
        self._client: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def client(self) -> httpx.AsyncClient | None:
        """Compatibility seam for in-process ASGI/mock transports."""
        return self._client

    @client.setter
    def client(self, value: httpx.AsyncClient | None) -> None:
        self._client = value

    async def open(self) -> AsyncRuntimeTransport:
        if self._client is None:
            headers = (
                {"Authorization": f"Bearer {self._token}"} if self._token else None
            )
            event_hooks = (
                {"response": [self._response_hook]} if self._response_hook else None
            )
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
                event_hooks=event_hooks,
            )
        return self

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "AsyncRuntimeTransport must be opened before sending requests"
            )
        return self._client

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._require_client().get(
            _require_relative_runtime_path(path), **kwargs
        )

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._require_client().post(
            _require_relative_runtime_path(path), **kwargs
        )

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._require_client().put(
            _require_relative_runtime_path(path), **kwargs
        )

    def stream(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._require_client().stream(
            method,
            _require_relative_runtime_path(path),
            **kwargs,
        )


class SupervisorClient:
    """Async compatibility facade over :class:`RuntimeHttpClient`."""

    def __init__(
        self,
        *,
        base_url: str,
        session_token: str,
        instance_id: str | None = None,
    ) -> None:
        self._transport = RuntimeHttpClient(
            base_url=base_url,
            session_token=session_token,
        )
        self._entered = False
        # Compatibility seam for in-process HTTPX/ASGI adapters. Production
        # traffic uses the Protocol-owned stdlib transport above.
        self._client: Any | None = None
        self.instance_id = instance_id

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    async def __aenter__(self) -> SupervisorClient:
        self._entered = True
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._entered = False
        client = self._client
        if client is not None and hasattr(client, "aclose"):
            await client.aclose()
            self._client = None

    def _require_transport(self) -> RuntimeHttpClient:
        if not self._entered:
            raise RuntimeError(
                "SupervisorClient must be used as an async context manager"
            )
        return self._transport

    def _require_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._entered:
            return self._transport
        raise RuntimeError("SupervisorClient must be used as an async context manager")

    async def health(self) -> JsonObject:
        if self._client is not None:
            response = await self._client.get(operation_path("getRuntimeHealth"))
            return self._async_response_object(response, "getRuntimeHealth")
        return dict(await asyncio.to_thread(self._require_transport().health))

    async def submit(
        self,
        request: SubmitRequest,
        attachments: dict[str, tuple[str | None, bytes]],
    ) -> JobRef:
        expected = {
            str(item.source["attachment"]): item
            for item in request.items
            if item.source.get("type") == "upload.v1"
        }
        if set(expected) != set(attachments):
            raise ValueError("attachments must exactly match manifest upload sources")
        if self._client is not None:
            files = [
                (
                    name,
                    (
                        expected[name].display_name,
                        content,
                        media_type or "application/octet-stream",
                    ),
                )
                for name, (media_type, content) in attachments.items()
            ]
            response = await self._client.post(
                operation_path("submitJob"),
                data={
                    "manifest": json.dumps(
                        request.to_payload(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                },
                files=files,
            )
            return parse_job_ref(self._async_response_object(response, "submitJob"))
        parts = {
            name: MultipartAttachment(
                filename=expected[name].display_name,
                content=content,
                media_type=media_type or "application/octet-stream",
            )
            for name, (media_type, content) in attachments.items()
        }
        transport = self._require_transport()
        value = await asyncio.to_thread(
            transport.submit_job,
            request.to_payload(),
            parts,
        )
        return parse_job_ref(cast("JsonObject", value))

    async def observe(self, job_id: str, *, after_sequence: int = 0) -> JobUpdate:
        if self._client is not None:
            response = await self._client.get(
                _bind_path("observeJob", job_id=job_id),
                params={"after_sequence": after_sequence},
            )
            return parse_job_update(self._async_response_object(response, "observeJob"))
        transport = self._require_transport()
        value = await asyncio.to_thread(
            transport.observe_job,
            job_id,
            after_sequence=after_sequence,
        )
        return parse_job_update(cast("JsonObject", value))

    async def command(self, command: JobCommand) -> JobRef | CancelMode | None:
        if self._client is not None:
            response = await self._client.post(
                operation_path("commandJob"),
                json=command.to_payload(),
            )
            value = self._async_response_object(response, "commandJob")
        else:
            transport = self._require_transport()
            value = await asyncio.to_thread(
                transport.command_job,
                command.to_payload(),
            )
        if command.kind is JobCommandKind.CANCEL:
            return CancelMode(value["cancel_mode"])
        if command.kind is JobCommandKind.RETRY:
            job_ref = value["job_ref"]
            if job_ref is None:
                raise RuntimeClientError(
                    ErrorCode.ADAPTER_PROTOCOL_VIOLATION,
                    "retry command response is missing job_ref",
                )
            return parse_job_ref(cast("JsonObject", job_ref))
        return None

    async def residency(self) -> ResidencyStatus:
        if self._client is not None:
            response = await self._client.get(operation_path("getRuntimeResidency"))
            return _parse_residency(
                self._async_response_object(response, "getRuntimeResidency")
            )
        value = await asyncio.to_thread(self._require_transport().residency)
        return _parse_residency(value)

    async def release_idle(self, pipeline: str | None = None) -> ResidencyStatus:
        if self._client is not None:
            response = await self._client.post(
                operation_path("releaseRuntime"),
                json={"pipeline": pipeline},
            )
            return _parse_residency(
                self._async_response_object(response, "releaseRuntime")
            )
        value = await asyncio.to_thread(
            self._require_transport().release_idle, pipeline
        )
        return _parse_residency(value)

    async def preload(self, pipelines: tuple[str, ...]) -> ResidencyStatus:
        if self._client is not None:
            response = await self._client.post(
                operation_path("preloadRuntime"),
                json={"pipelines": list(pipelines)},
                timeout=600.0,
            )
            return _parse_residency(
                self._async_response_object(response, "preloadRuntime")
            )
        value = await asyncio.to_thread(self._require_transport().preload, pipelines)
        return _parse_residency(value)

    async def get_settings(self) -> SettingsSnapshot:
        if self._client is not None:
            response = await self._client.get(operation_path("getSettings"))
            return _parse_settings(self._async_response_object(response, "getSettings"))
        value = await asyncio.to_thread(self._require_transport().get_settings)
        return _parse_settings(value)

    async def put_settings(self, snapshot: SettingsSnapshot) -> SettingsSnapshot:
        if self._client is not None:
            response = await self._client.put(
                operation_path("putSettings"),
                json=snapshot.to_payload(),
            )
            return _parse_settings(self._async_response_object(response, "putSettings"))
        transport = self._require_transport()
        value = await asyncio.to_thread(
            transport.put_settings,
            snapshot.to_payload(),
        )
        return _parse_settings(value)

    async def export_ocr(
        self,
        *,
        raw_text: str,
        markdown_text: str,
        html_text: str,
        raw_blocks: list[JsonObject] | None = None,
        output_path: str,
        fmt: str,
        overwrite: bool = False,
    ) -> JsonObject:
        request: ExportRequest = {
            "raw_text": raw_text,
            "markdown_text": markdown_text,
            "html_text": html_text,
            "raw_blocks": raw_blocks or [],
            "output_path": output_path,
            "format": fmt,
            "overwrite": overwrite,
        }
        if self._client is not None:
            response = await self._client.post(
                operation_path("exportOcr"),
                json=request,
            )
            return self._async_response_object(response, "exportOcr")
        return dict(
            await asyncio.to_thread(self._require_transport().export_ocr, request)
        )

    async def decode_qrcode(self, image_bytes: bytes) -> list[JsonObject]:
        request: QrDecodeRequest = {
            "image": base64.b64encode(image_bytes).decode("ascii")
        }
        if self._client is not None:
            response = await self._client.post(
                operation_path("decodeQrCode"),
                json=request,
            )
            value = self._async_response_object(response, "decodeQrCode")
            return [dict(code) for code in value.get("codes", [])]
        value = await asyncio.to_thread(
            self._require_transport().decode_qrcode, request
        )
        return [dict(code) for code in value.get("codes", [])]

    async def generate_qrcode(
        self,
        data: str,
        *,
        fmt: str = "qrcode",
        options: JsonObject | None = None,
    ) -> str:
        request: QrGenerateRequest = {
            "data": data,
            "format": fmt,
            "options": options or {},
        }
        if self._client is not None:
            response = await self._client.post(
                operation_path("generateQrCode"),
                json=request,
            )
            return str(
                self._async_response_object(response, "generateQrCode").get("image", "")
            )
        value = await asyncio.to_thread(
            self._require_transport().generate_qrcode, request
        )
        return str(value.get("image", ""))

    @staticmethod
    def _error_from_response(response: Any) -> RuntimeClientError:
        try:
            return RuntimeClientError.from_payload(
                parse_error_payload(response.json()),
                status_code=response.status_code,
            )
        except Exception:
            return RuntimeClientError(
                ErrorCode.INTERNAL_ERROR,
                f"unexpected runtime response status={response.status_code}",
                detail={
                    "status_code": response.status_code,
                    "status_detail": response.reason_phrase,
                },
                status_code=response.status_code,
            )

    def _async_response_object(
        self,
        response: Any,
        operation_id: str,
    ) -> JsonObject:
        if response.status_code >= 400:
            raise self._error_from_response(response)
        value = response.json()
        if not isinstance(value, dict):
            raise RuntimeClientError(
                ErrorCode.ADAPTER_PROTOCOL_VIOLATION,
                "runtime response must be a JSON object",
                status_code=response.status_code,
            )
        return _validate_response_object(
            operation_id,
            value,
            status_code=response.status_code,
        )

    async def _log_http_response(self, response: Any) -> None:
        """Log transport metadata without consuming streaming responses."""
        request = response.request
        try:
            request_content = request.content
        except Exception:
            request_content = None
        try:
            response_content = response.content
        except Exception:
            response_content = None
        elapsed_ms = None
        try:
            elapsed = getattr(response, "elapsed", None)
            if elapsed is not None:
                elapsed_ms = elapsed.total_seconds() * 1000.0
        except Exception:
            elapsed_ms = None
        log_http_response(
            logger=logger,
            method=request.method,
            url=str(request.url),
            status_code=response.status_code,
            reason=response.reason_phrase,
            elapsed_ms=elapsed_ms,
            request_bytes=guess_request_size(request_content),
            response_bytes=guess_response_size(
                dict(response.headers),
                response_content,
            ),
            stream=not response.is_stream_consumed,
        )


def bind_operation_path(operation_id: str, **parameters: str | int) -> str:
    """Bind and percent-encode one generated operation path."""
    path = operation_path(operation_id)
    for name, value in parameters.items():
        path = path.replace(f"{{{name}}}", quote(str(value), safe=""))
    if "{" in path or "}" in path:
        raise ValueError(f"missing path parameter for operation: {operation_id}")
    return path


_bind_path = bind_operation_path


def _require_relative_runtime_path(path: str) -> str:
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or any(ord(character) < 32 for character in path)
    ):
        raise RuntimeClientError(
            ErrorCode.FORBIDDEN_LOOPBACK,
            "runtime request path must stay on the configured loopback authority",
            detail={"path": path},
        )
    return path


def _encode_multipart(
    manifest: Mapping[str, Any],
    attachments: Mapping[str, MultipartAttachment],
) -> tuple[bytes, str]:
    boundary = f"vibeocr-{secrets.token_hex(16)}"
    chunks: list[bytes] = []

    def add_part(headers: Iterable[str], content: bytes) -> None:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.extend(f"{header}\r\n".encode() for header in headers)
        chunks.append(b"\r\n")
        chunks.append(content)
        chunks.append(b"\r\n")

    add_part(
        (
            'Content-Disposition: form-data; name="manifest"',
            "Content-Type: application/json; charset=utf-8",
        ),
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode(),
    )
    for field_name, attachment in attachments.items():
        safe_name = _multipart_token(field_name)
        safe_filename = _multipart_token(attachment.filename)
        add_part(
            (
                f'Content-Disposition: form-data; name="{safe_name}"; '
                f'filename="{safe_filename}"',
                f"Content-Type: {attachment.media_type}",
            ),
            attachment.content,
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _multipart_token(value: str) -> str:
    if any(character in value for character in {'"', "\r", "\n"}):
        raise ValueError(
            "multipart names and filenames cannot contain quotes or newlines"
        )
    return value


def _parse_residency(body: Mapping[str, Any]) -> ResidencyStatus:
    entries = tuple(parse_residency_entry(entry) for entry in body.get("entries", []))
    pipelines = tuple(
        parse_pipeline_spec(pipeline) for pipeline in body.get("pipelines", [])
    )
    return ResidencyStatus(
        schema_version=int(body.get("schema_version", 2)),
        default_ttl_seconds=int(body.get("default_ttl_seconds", 300)),
        entries=entries,
        pipelines=pipelines,
        vram_total_mb=body.get("vram_total_mb"),
        vram_used_mb=body.get("vram_used_mb"),
    )


def _parse_settings(body: Mapping[str, Any]) -> SettingsSnapshot:
    residency = body.get("residency", {})
    if not isinstance(residency, Mapping):
        raise RuntimeClientError(
            ErrorCode.ADAPTER_PROTOCOL_VIOLATION,
            "settings residency must be an object",
        )
    pipelines = tuple(
        parse_pipeline_spec(pipeline) for pipeline in residency.get("pipelines", [])
    )
    extra = body.get("extra", {})
    if not isinstance(extra, dict):
        raise RuntimeClientError(
            ErrorCode.ADAPTER_PROTOCOL_VIOLATION,
            "settings extra must be an object",
        )
    return SettingsSnapshot(
        schema_version=int(body.get("schema_version", 2)),
        default_ttl_seconds=int(residency.get("default_ttl_seconds", 300)),
        pipelines=pipelines,
        extra=extra,
    )


__all__ = [
    "AsyncRuntimeTransport",
    "MultipartAttachment",
    "RuntimeClientError",
    "RuntimeHttpClient",
    "RuntimeHttpResponse",
    "SupervisorClient",
    "bind_operation_path",
]
