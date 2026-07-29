"""Reusable mock server for Runtime Protocol v2 consumers."""

from __future__ import annotations

import base64
import json
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from vibeocr.runtime_contracts.generated.operations import operation_path

if TYPE_CHECKING:
    from collections.abc import Mapping

JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class MockUploadedFile:
    filename: str
    media_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class MockSubmission:
    manifest: JsonObject
    attachments: dict[str, MockUploadedFile]


@dataclass(frozen=True, slots=True)
class MockRequest:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes

    def json(self) -> JsonObject:
        value = json.loads(self.body)
        if not isinstance(value, dict):
            raise ValueError("request body is not a JSON object")
        return value


@dataclass(slots=True)
class MockRuntimeState:
    requests: list[MockRequest] = field(default_factory=list)
    submissions: list[MockSubmission] = field(default_factory=list)


class MockRuntimeServer:
    """Threaded loopback mock backed by the committed protocol goldens."""

    def __init__(
        self,
        *,
        session_token: str = "golden-token",
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("mock runtime must bind to 127.0.0.1")
        self.session_token = session_token
        self.state = MockRuntimeState()
        self._golden = _load_json_resource("golden/runtime-api.json")
        self._contract_golden = _load_json_resource("golden/golden.json")
        handler = _handler_type(self)
        self._server = ThreadingHTTPServer((host, port), handler)
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> MockRuntimeServer:
        if self._thread is not None:
            raise RuntimeError("mock runtime is already started")
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vibeocr-runtime-mock",
            daemon=True,
        )
        self._thread.start()
        return self

    def close(self) -> None:
        if self._thread is None:
            self._server.server_close()
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._thread = None

    def __enter__(self) -> MockRuntimeServer:
        return self.start()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def dispatch(self, request: MockRequest) -> tuple[int, str, bytes]:
        """Return a deterministic response for one recorded request."""

        health_path = operation_path("getRuntimeHealth")
        if request.path == health_path and request.method == "GET":
            return _json_response(200, self._golden["health"])
        expected_authorization = f"Bearer {self.session_token}"
        if request.headers.get("Authorization") != expected_authorization:
            return _json_response(401, self._golden["unauthorized_error"])

        if request.path == operation_path("submitJob") and request.method == "POST":
            try:
                submission = _parse_submission(request)
            except (ValueError, json.JSONDecodeError):
                return _json_response(
                    400, self._validation_error("invalid multipart submission")
                )
            self.state.submissions.append(submission)
            return _json_response(200, self._contract_golden["job_ref"])

        if request.path.endswith("/observe") and request.method == "GET":
            snapshot = deepcopy(self._contract_golden["job_snapshot_running"])
            update = {
                "schema_version": 2,
                "snapshot": snapshot,
                "events": [],
                "outcomes": [],
                "through_sequence": snapshot.get("event_sequence", 0),
                "more": False,
            }
            return _json_response(200, update)

        if request.path == operation_path("commandJob") and request.method == "POST":
            command = request.json()
            kind = command.get("kind")
            return _json_response(
                200,
                {
                    "schema_version": 2,
                    "instance_id": "sup-golden",
                    "command_id": command.get("command_id", "command-golden"),
                    "kind": kind,
                    "cancel_mode": "cooperative" if kind == "cancel" else None,
                    "job_ref": (
                        deepcopy(self._contract_golden["job_ref"])
                        if kind == "retry"
                        else None
                    ),
                },
            )

        residency_operations = {
            ("GET", operation_path("getRuntimeResidency")),
            ("POST", operation_path("releaseRuntime")),
            ("POST", operation_path("preloadRuntime")),
        }
        if (request.method, request.path) in residency_operations:
            return _json_response(200, self._contract_golden["residency_status"])

        if request.path == operation_path("getSettings") and request.method == "GET":
            return _json_response(200, self._contract_golden["settings_snapshot"])
        if request.path == operation_path("putSettings") and request.method == "PUT":
            return _json_response(200, request.json())
        if request.path == operation_path("exportOcr") and request.method == "POST":
            body = request.json()
            return _json_response(
                200,
                {
                    "schema_version": 2,
                    "instance_id": "sup-golden",
                    "output_path": body["output_path"],
                    "bytes_written": 0,
                },
            )
        if request.path == operation_path("decodeQrCode") and request.method == "POST":
            return _json_response(
                200,
                {
                    "schema_version": 2,
                    "instance_id": "sup-golden",
                    "codes": [],
                },
            )
        if (
            request.path == operation_path("generateQrCode")
            and request.method == "POST"
        ):
            png = self._golden["png"]
            return _json_response(
                200,
                {
                    "schema_version": 2,
                    "instance_id": "sup-golden",
                    "image": png["content_base64"],
                    "format": "png",
                    "media_type": png["media_type"],
                },
            )

        load_prefix, load_suffix = _split_template(operation_path("loadPdfSession"))
        if (
            request.method == "POST"
            and request.path.startswith(load_prefix)
            and request.path.endswith(load_suffix)
        ):
            lines = b"".join(
                json.dumps(row, separators=(",", ":")).encode() + b"\n"
                for row in self._golden["ndjson_progress_lines"]
            )
            return 200, "application/x-ndjson", lines

        render_prefix, render_suffix = _split_template(operation_path("renderPdfPage"))
        if (
            request.method == "GET"
            and request.path.startswith(render_prefix)
            and request.path.endswith(render_suffix)
        ):
            png = self._golden["png"]
            return (
                200,
                png["media_type"],
                base64.b64decode(png["content_base64"], validate=True),
            )

        return _json_response(404, self._not_found_error(request.path))

    def _validation_error(self, message: str) -> JsonObject:
        payload = deepcopy(self._golden["unauthorized_error"])
        payload.update(
            {
                "code": "VALIDATION_ERROR",
                "message": message,
                "category": "validation",
            }
        )
        return payload

    def _not_found_error(self, path: str) -> JsonObject:
        payload = deepcopy(self._golden["unauthorized_error"])
        payload.update(
            {
                "code": "RESOURCE_NOT_FOUND",
                "message": f"Mock route not found: {path}",
                "category": "not_found",
                "detail": {"path": path},
            }
        )
        return payload


def _handler_type(runtime: MockRuntimeServer) -> type[BaseHTTPRequestHandler]:
    class RuntimeRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._dispatch()

        def do_PUT(self) -> None:
            self._dispatch()

        def _dispatch(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length)
            target = urlsplit(self.path)
            request = MockRequest(
                method=self.command,
                path=target.path,
                query=parse_qs(target.query, keep_blank_values=True),
                headers=dict(self.headers.items()),
                body=body,
            )
            runtime.state.requests.append(request)
            try:
                status, media_type, response_body = runtime.dispatch(request)
            except (KeyError, TypeError, ValueError):
                status, media_type, response_body = _json_response(
                    400,
                    runtime._validation_error("invalid request body"),
                )
            self.send_response(status)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            del format, args

    return RuntimeRequestHandler


def _parse_submission(request: MockRequest) -> MockSubmission:
    content_type = request.headers.get("Content-Type", "")
    if not content_type.lower().startswith("multipart/form-data;"):
        raise ValueError("submit request is not multipart/form-data")
    envelope = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
        + request.body
    )
    message = BytesParser(policy=policy.default).parsebytes(envelope)
    if not message.is_multipart():
        raise ValueError("multipart body has no parts")

    manifest: JsonObject | None = None
    attachments: dict[str, MockUploadedFile] = {}
    for part in message.iter_parts():
        field_name = part.get_param("name", header="content-disposition")
        if not isinstance(field_name, str):
            raise ValueError("multipart part has no name")
        content = part.get_payload(decode=True)
        if content is None:
            content = b""
        if not isinstance(content, bytes):
            raise ValueError("multipart payload is not bytes")
        content_bytes: bytes = content
        if field_name == "manifest":
            value = json.loads(content_bytes)
            if not isinstance(value, dict):
                raise ValueError("manifest is not an object")
            manifest = value
            continue
        attachments[field_name] = MockUploadedFile(
            filename=part.get_filename() or field_name,
            media_type=part.get_content_type(),
            content=content_bytes,
        )
    if manifest is None:
        raise ValueError("multipart body has no manifest")
    expected = {
        str(item["source"]["attachment"])
        for item in manifest.get("items", [])
        if item.get("source", {}).get("type") == "upload.v1"
    }
    if expected != set(attachments):
        raise ValueError("attachments do not match manifest")
    return MockSubmission(manifest=manifest, attachments=attachments)


def _split_template(path: str) -> tuple[str, str]:
    start = path.index("{")
    end = path.index("}", start)
    return path[:start], path[end + 1 :]


def _json_response(status: int, payload: Mapping[str, Any]) -> tuple[int, str, bytes]:
    return (
        status,
        "application/json",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
    )


def _load_json_resource(relative_path: str) -> JsonObject:
    raw = (
        resources.files("vibeocr.runtime_contracts")
        .joinpath(relative_path)
        .read_text(encoding="utf-8")
    )
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"protocol resource is not an object: {relative_path}")
    return value


__all__ = [
    "MockRequest",
    "MockRuntimeServer",
    "MockRuntimeState",
    "MockSubmission",
    "MockUploadedFile",
]
