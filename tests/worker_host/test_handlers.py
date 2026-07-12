"""Tests for WorkerHost RPC handlers (Task 1.6 Green).

Each handler is exercised against a fake application facade to verify DTO
mapping: the wire payload is translated into the correct facade request, and
the facade result is translated back into the wire result payload. Handlers
must never import PySide6.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from vibeocr.application.contracts import (
    CancelToken,
    OcrError,
    OcrRequest,
    OcrResult,
    PdfOpenRequest,
    PdfSessionDto,
)
from vibeocr.worker_host.handlers.ocr import OcrHandler
from vibeocr.worker_host.handlers.pdf import PdfOpenHandler
from vibeocr.worker_host.handlers.qrcode import QrDecodeHandler, QrGenerateHandler
from vibeocr.worker_host.handlers.settings import SettingsSnapshotHandler
from vibeocr.worker_host.shared_payload import SharedPayloadRef

# ---------------------------------------------------------------------------
# Import boundary: handlers must not pull in PySide6
# ---------------------------------------------------------------------------


def test_handlers_do_not_import_pyside6() -> None:
    import importlib
    import sys

    # Remove any cached PySide6 so the import check is meaningful.
    for mod in list(sys.modules):
        if mod == "PySide6" or mod.startswith("PySide6."):
            sys.modules.pop(mod, None)
    for mod_name in (
        "vibeocr.worker_host.handlers.ocr",
        "vibeocr.worker_host.handlers.pdf",
        "vibeocr.worker_host.handlers.qrcode",
        "vibeocr.worker_host.handlers.settings",
    ):
        importlib.import_module(mod_name)

    assert "PySide6" not in sys.modules, "handlers must not import PySide6"


# ---------------------------------------------------------------------------
# OCR handler
# ---------------------------------------------------------------------------


class _FakeOcrFacade:
    def __init__(self, text: str = "hello") -> None:
        self._text = text
        self.last_request: OcrRequest | None = None

    def recognize(self, request: OcrRequest, cancel: CancelToken) -> OcrResult:
        self.last_request = request
        return OcrResult(text=self._text, raw_blocks=[], pipeline=request.pipeline)


@pytest.mark.asyncio
async def test_ocr_handler_maps_payload_to_result(tmp_path: Path) -> None:
    facade = _FakeOcrFacade("recognized text")
    store = _FakePayloadStore(b"\x89PNG fake image bytes")
    handler = OcrHandler(facade=facade, store=store)  # type: ignore[arg-type]
    payload = {
        "image": _descriptor(store.last_bytes),
        "pipeline": "OCR",
        "language": None,
    }
    result = await handler.handle(payload, CancelToken())
    assert result["text"] == "recognized text"
    assert result["pipeline"] == "OCR"
    assert facade.last_request is not None
    assert facade.last_request.pipeline == "OCR"
    assert facade.last_request.image_data == store.last_bytes


@pytest.mark.asyncio
async def test_ocr_handler_maps_ocr_error() -> None:
    class _FailingFacade:
        def recognize(self, request: OcrRequest, cancel: CancelToken) -> OcrResult:
            raise OcrError("nope")

    store = _FakePayloadStore(b"img")
    handler = OcrHandler(facade=_FailingFacade(), store=store)  # type: ignore[arg-type]
    from vibeocr.worker_host.errors import WorkerError

    with pytest.raises(WorkerError):
        await handler.handle(
            {"image": _descriptor(b"img"), "pipeline": "OCR"}, CancelToken()
        )


# ---------------------------------------------------------------------------
# PDF handler
# ---------------------------------------------------------------------------


class _FakePdfFacade:
    def __init__(self, page_count: int = 3) -> None:
        self._page_count = page_count

    def open(self, request: PdfOpenRequest, cancel: CancelToken) -> PdfSessionDto:
        return PdfSessionDto(
            session_id="sess-1",
            file_path=request.file_path,
            page_count=self._page_count,
        )


@pytest.mark.asyncio
async def test_pdf_open_handler_maps_payload_to_result() -> None:
    handler = PdfOpenHandler(facade=_FakePdfFacade(page_count=5))  # type: ignore[arg-type]
    result = await handler.handle({"file_path": "C:/data/sample.pdf"}, CancelToken())
    assert result["session_id"] == "sess-1"
    assert result["page_count"] == 5
    # Path normalizes separators on Windows; accept the platform's form.
    assert Path(result["file_path"]).as_posix() == "C:/data/sample.pdf"


# ---------------------------------------------------------------------------
# QR code handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qr_decode_handler_maps_payload_to_result() -> None:
    class _FakeQrDecode:
        def decode(self, data: bytes, cancel: CancelToken) -> list[dict[str, Any]]:
            return [{"data": "https://example.com", "format": "QR_CODE"}]

    store = _FakePayloadStore(b"img")
    handler = QrDecodeHandler(facade=_FakeQrDecode(), store=store)  # type: ignore[arg-type]
    result = await handler.handle({"image": _descriptor(b"img")}, CancelToken())
    assert result["codes"] == [{"data": "https://example.com", "format": "QR_CODE"}]


@pytest.mark.asyncio
async def test_qr_generate_handler_maps_payload_to_result() -> None:
    class _FakeQrGenerate:
        last_data: str = ""

        def generate(self, data: str, fmt: str, cancel: CancelToken) -> bytes:
            self.last_data = data
            return b"\x89PNG generated"

    gen = _FakeQrGenerate()
    store = _FakePayloadStore(b"")
    handler = QrGenerateHandler(facade=gen, store=store)  # type: ignore[arg-type]
    result = await handler.handle({"data": "https://example.com", "format": "qrcode"}, CancelToken())
    assert gen.last_data == "https://example.com"
    assert result["image"]["media_type"] == "image/png"


# ---------------------------------------------------------------------------
# Settings handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_snapshot_handler_maps_payload_to_result() -> None:
    from vibeocr.application.contracts import SettingsSnapshot

    class _FakeSettings:
        def get_snapshot(self) -> SettingsSnapshot:
            return SettingsSnapshot(
                backend="gpu", preload_pipelines=("OCR",), ttl_seconds=7200
            )

    handler = SettingsSnapshotHandler(facade=_FakeSettings())  # type: ignore[arg-type]
    result = await handler.handle({}, CancelToken())
    assert result["backend"] == "gpu"
    assert result["preload_pipelines"] == ["OCR"]
    assert result["ttl_seconds"] == 7200


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _FakePayloadStore:
    """A fake SharedPayloadStore that returns canned bytes for any ref."""

    def __init__(self, payload_bytes: bytes) -> None:
        self.last_bytes = payload_bytes

    async def read(self, ref: Any) -> bytes:
        return self.last_bytes

    async def put(self, data: bytes, *, media_type: str, ttl_seconds: int | None = None) -> SharedPayloadRef:
        self.last_bytes = data
        return SharedPayloadRef(
            name="Local\\VibeOCR-00000000-0000-4000-8000-000000000000-00000000-0000-4000-8000-000000000001",
            size=len(data),
            media_type=media_type,
            sha256=hashlib.sha256(data).hexdigest(),
            owner="worker",
            expires_unix_ms=1,
        )


def _descriptor(data: bytes) -> dict[str, Any]:
    return {
        "name": "Local\\VibeOCR-00000000-0000-4000-8000-000000000000-00000000-0000-4000-8000-000000000001",
        "size": len(data),
        "media_type": "image/png",
        "sha256": hashlib.sha256(data).hexdigest(),
        "owner": "client",
        "expires_unix_ms": 1,
    }
