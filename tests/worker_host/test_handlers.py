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
    OcrExportRequest,
    OcrExportResult,
    PdfOpenRequest,
    PdfSessionDto,
)
from vibeocr.worker_host.handlers.ocr import OcrExportHandler, OcrHandler
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


@pytest.mark.asyncio
async def test_ocr_export_handler_preserves_unicode_path_and_overwrite(tmp_path: Path) -> None:
    class _ExportFacade:
        last_request: OcrExportRequest | None = None
        def export(self, request: OcrExportRequest, cancel: CancelToken) -> OcrExportResult:
            self.last_request = request
            request.output_path.write_text(request.markdown_text, encoding="utf-8")
            return OcrExportResult(request.output_path, request.output_path.stat().st_size)

    facade = _ExportFacade()
    destination = tmp_path / "结果 文档.md"
    result = await OcrExportHandler(facade=facade).handle({
        "raw_text": "正文", "markdown_text": "# 标题 ✓", "html_text": "<h1>标题</h1>",
        "raw_blocks": [], "output_path": str(destination), "format": "markdown", "overwrite": False,
    }, CancelToken())
    assert result["output_path"] == str(destination)
    assert destination.read_text(encoding="utf-8") == "# 标题 ✓"


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


@pytest.mark.asyncio
async def test_pdf_open_handler_rejects_relative_path() -> None:
    handler = PdfOpenHandler(facade=_FakePdfFacade())  # type: ignore[arg-type]
    from vibeocr.worker_host.errors import WorkerError

    with pytest.raises(WorkerError, match="absolute"):
        await handler.handle({"file_path": "relative.pdf"}, CancelToken())


# ---------------------------------------------------------------------------
# QR code handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qr_decode_handler_maps_payload_to_result() -> None:
    class _FakeQrDecode:
        def decode(self, data: bytes, cancel: CancelToken) -> list[dict[str, Any]]:
            # The first code advertises is_url; the second omits it so the handler
            # must default it to False rather than dropping or erroring.
            return [
                {"data": "https://example.com", "format": "QR_CODE", "is_url": True},
                {"data": "plain text", "format": "QR_CODE"},
            ]

    store = _FakePayloadStore(b"img")
    handler = QrDecodeHandler(facade=_FakeQrDecode(), store=store)  # type: ignore[arg-type]
    result = await handler.handle({"image": _descriptor(b"img")}, CancelToken())
    assert result["codes"] == [
        {"data": "https://example.com", "format": "QR_CODE", "is_url": True},
        {"data": "plain text", "format": "QR_CODE", "is_url": False},
    ]


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
# PDF session handlers (close/render/rotate/delete/add_text_layer/delete/save/start_ocr)
# ---------------------------------------------------------------------------


class _FakePdfBackend:
    """Records calls and returns canned results for PDF session methods."""

    def __init__(self) -> None:
        self.close_calls: list[str] = []
        self.render_calls: list[tuple[str, int, int | None, int | None]] = []
        self.rotate_calls: list[tuple[str, list[int], int]] = []
        self.delete_calls: list[tuple[str, list[int]]] = []
        self.add_text_layer_calls: list[tuple[str, int, bool, bool]] = []
        self.delete_text_layers_calls: list[tuple[str, list[int]]] = []
        self.save_calls: list[tuple[str, str | None]] = []
        self.start_ocr_calls: list[tuple[str, str, list[int], bool, str | None]] = []

    def close(self, session_id: str) -> bool:
        self.close_calls.append(session_id)
        return True

    def render_page(self, session_id, page_index, size, dpi):
        self.render_calls.append((session_id, page_index, size, dpi))
        return b"\x89PNG fake"

    def rotate(self, session_id, page_indices, angle):
        self.rotate_calls.append((session_id, page_indices, angle))
        return 3

    def delete_pages(self, session_id, page_indices):
        self.delete_calls.append((session_id, page_indices))
        return 2

    def add_text_layer(self, session_id, page_index, overwrite, save):
        self.add_text_layer_calls.append((session_id, page_index, overwrite, save))
        return {"written": True, "saved": save}

    def delete_text_layers(self, session_id, page_indices, cancel):
        self.delete_text_layers_calls.append((session_id, page_indices))
        return {"deleted_count": len(page_indices), "residual_pages": []}

    def save(self, session_id, output_path):
        self.save_calls.append((session_id, output_path))
        return output_path or "C:/data/sample.pdf"

    def start_ocr(self, session_id, file_path, page_indices, overwrite, sidecar_root, cancel):
        self.start_ocr_calls.append((session_id, file_path, page_indices, overwrite, sidecar_root))
        return {
            "completed": len(page_indices),
            "failed": 0,
            "cancelled": False,
            "compressed": True,
            "write_errors": [],
        }


@pytest.mark.asyncio
async def test_pdf_close_handler_returns_closed_flag() -> None:
    from vibeocr.worker_host.handlers.pdf import PdfCloseHandler

    backend = _FakePdfBackend()
    handler = PdfCloseHandler(backend=backend)  # type: ignore[arg-type]
    result = await handler.handle({"session_id": "sess-1"}, CancelToken())
    assert result == {"closed": True}
    assert backend.close_calls == ["sess-1"]


@pytest.mark.asyncio
async def test_pdf_render_page_handler_returns_shared_payload() -> None:
    from vibeocr.worker_host.handlers.pdf import PdfRenderPageHandler

    backend = _FakePdfBackend()
    store = _FakePayloadStore(b"")
    handler = PdfRenderPageHandler(backend=backend, store=store)  # type: ignore[arg-type]
    result = await handler.handle(
        {"session_id": "sess-1", "page_index": 0, "size": 160}, CancelToken()
    )
    assert result["image"]["media_type"] == "image/png"
    assert backend.render_calls == [("sess-1", 0, 160, None)]


@pytest.mark.asyncio
async def test_pdf_rotate_handler_maps_angle_and_returns_page_count() -> None:
    from vibeocr.worker_host.handlers.pdf import PdfRotateHandler

    backend = _FakePdfBackend()
    handler = PdfRotateHandler(backend=backend)  # type: ignore[arg-type]
    result = await handler.handle(
        {"session_id": "sess-1", "page_indices": [0, 1], "angle": 90}, CancelToken()
    )
    assert result == {"page_count": 3}
    assert backend.rotate_calls == [("sess-1", [0, 1], 90)]


@pytest.mark.asyncio
async def test_pdf_rotate_handler_rejects_invalid_angle() -> None:
    from vibeocr.worker_host.handlers.pdf import PdfRotateHandler
    from vibeocr.worker_host.errors import WorkerError

    handler = PdfRotateHandler(backend=_FakePdfBackend())  # type: ignore[arg-type]
    with pytest.raises(WorkerError):
        await handler.handle(
            {"session_id": "sess-1", "page_indices": [0], "angle": 45}, CancelToken()
        )


@pytest.mark.asyncio
async def test_pdf_delete_pages_handler_returns_page_count() -> None:
    from vibeocr.worker_host.handlers.pdf import PdfDeletePagesHandler

    backend = _FakePdfBackend()
    handler = PdfDeletePagesHandler(backend=backend)  # type: ignore[arg-type]
    result = await handler.handle(
        {"session_id": "sess-1", "page_indices": [2]}, CancelToken()
    )
    assert result == {"page_count": 2}


@pytest.mark.asyncio
async def test_pdf_add_text_layer_handler_returns_written_and_saved() -> None:
    from vibeocr.worker_host.handlers.pdf import PdfAddTextLayerHandler

    backend = _FakePdfBackend()
    handler = PdfAddTextLayerHandler(backend=backend)  # type: ignore[arg-type]
    result = await handler.handle(
        {"session_id": "sess-1", "page_index": 0, "overwrite": False, "save": True},
        CancelToken(),
    )
    assert result == {"written": True, "saved": True}


@pytest.mark.asyncio
async def test_pdf_delete_text_layers_handler_returns_deleted_count() -> None:
    from vibeocr.worker_host.handlers.pdf import PdfDeleteTextLayersHandler

    backend = _FakePdfBackend()
    handler = PdfDeleteTextLayersHandler(backend=backend)  # type: ignore[arg-type]
    result = await handler.handle(
        {"session_id": "sess-1", "page_indices": [0, 1]}, CancelToken()
    )
    assert result == {"deleted_count": 2, "residual_pages": []}


@pytest.mark.asyncio
async def test_pdf_save_handler_overwrite_in_place_when_output_path_null() -> None:
    from vibeocr.worker_host.handlers.pdf import PdfSaveHandler

    backend = _FakePdfBackend()
    handler = PdfSaveHandler(backend=backend)  # type: ignore[arg-type]
    result = await handler.handle({"session_id": "sess-1", "output_path": None}, CancelToken())
    assert result == {"saved_path": "C:/data/sample.pdf"}
    assert backend.save_calls == [("sess-1", None)]


@pytest.mark.asyncio
async def test_pdf_start_ocr_handler_maps_payload_to_backend() -> None:
    from vibeocr.worker_host.handlers.pdf import PdfStartOcrHandler

    backend = _FakePdfBackend()
    handler = PdfStartOcrHandler(backend=backend)  # type: ignore[arg-type]
    result = await handler.handle(
        {
            "session_id": "sess-1",
            "file_path": "C:/data/sample.pdf",
            "page_indices": [0, 1, 2],
            "overwrite": False,
        },
        CancelToken(),
    )
    assert result["completed"] == 3
    assert result["compressed"] is True
    assert backend.start_ocr_calls == [("sess-1", "C:/data/sample.pdf", [0, 1, 2], False, None)]


@pytest.mark.asyncio
async def test_pdf_session_handlers_reject_missing_session_id() -> None:
    from vibeocr.worker_host.errors import WorkerError
    from vibeocr.worker_host.handlers.pdf import PdfCloseHandler, PdfSaveHandler

    for handler in (
        PdfCloseHandler(backend=_FakePdfBackend()),  # type: ignore[arg-type]
        PdfSaveHandler(backend=_FakePdfBackend()),  # type: ignore[arg-type]
    ):
        with pytest.raises(WorkerError):
            await handler.handle({}, CancelToken())


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
