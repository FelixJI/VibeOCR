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
    OcrExportRequest,
    OcrExportResult,
    OcrRequest,
    OcrResult,
    PdfOpenRequest,
    PdfSessionDto,
)
from vibeocr.worker_host.handlers.ocr import (
    OcrBatchHandler,
    OcrExportHandler,
    OcrHandler,
)
from vibeocr.worker_host.handlers.pdf import PdfOpenHandler
from vibeocr.worker_host.handlers.pipeline_cache import (
    PipelineCacheStatusHandler,
    PreloadPipelineCacheHandler,
    ReleasePipelineCacheHandler,
    SetPipelineCacheTtlHandler,
    WarmupPipelineCacheHandler,
)
from vibeocr.worker_host.handlers.qrcode import (
    QrDecodeHandler,
    QrGenerateHandler,
    QrGenerateSvgHandler,
)
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
async def test_ocr_handler_returns_enriched_result_fields() -> None:
    """The enriched ocr.recognize response carries text_blocks/scores/dims."""

    class _EnrichedFacade:
        last_request: OcrRequest | None = None

        def recognize(self, request: OcrRequest, cancel: CancelToken) -> OcrResult:
            _EnrichedFacade.last_request = request
            return OcrResult(
                text="Hello",
                raw_blocks=[{"type": "text", "text": "Hello"}],
                pipeline="OCR",
                markdown_text="Hello",
                html_text="<p>Hello</p>",
                raw_text="Hello",
                text_blocks=[
                    {"text": "Hello", "bbox": [0, 0, 100, 100], "score": 0.95, "order": 0}
                ],
                text_with_scores=[["Hello", 0.95]],
                content_list=[{"type": "text", "text": "Hello"}],
                image_width=800,
                image_height=600,
            )

    store = _FakePayloadStore(b"\x89PNG fake")
    handler = OcrHandler(facade=_EnrichedFacade(), store=store)  # type: ignore[arg-type]
    payload = {"image": _descriptor(store.last_bytes), "pipeline": "OCR"}
    result = await handler.handle(payload, CancelToken())
    assert result["text_blocks"] == [
        {"text": "Hello", "bbox": [0, 0, 100, 100], "score": 0.95, "order": 0}
    ]
    assert result["text_with_scores"] == [["Hello", 0.95]]
    assert result["content_list"] == [{"type": "text", "text": "Hello"}]
    assert result["image_width"] == 800
    assert result["image_height"] == 600


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
async def test_ocr_batch_handler_calls_facade_once_and_preserves_failures() -> None:
    class _BatchFacade:
        calls: list[list[OcrRequest]] = []

        def recognize_batch(
            self, requests: list[OcrRequest], cancel: CancelToken
        ) -> list[OcrResult | None]:
            self.calls.append(requests)
            return [
                OcrResult(text="first", pipeline=requests[0].pipeline),
                None,
            ]

    store = _FakePayloadStore(b"png")
    facade = _BatchFacade()
    handler = OcrBatchHandler(facade=facade, store=store)  # type: ignore[arg-type]
    descriptor = _descriptor(store.last_bytes)

    result = await handler.handle(
        {
            "images": [descriptor, descriptor],
            "pipeline": "OCR",
            "language": "ch",
        },
        CancelToken(),
    )

    assert len(facade.calls) == 1
    assert [request.image_data for request in facade.calls[0]] == [b"png", b"png"]
    assert result["results"][0]["text"] == "first"
    assert result["results"][1] is None


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
        last_options: dict[str, Any] = {}

        def generate(self, data: str, options: dict[str, Any], cancel: CancelToken) -> bytes:
            self.last_data = data
            self.last_options = options
            return b"\x89PNG generated"

    gen = _FakeQrGenerate()
    store = _FakePayloadStore(b"")
    handler = QrGenerateHandler(facade=gen, store=store)  # type: ignore[arg-type]
    result = await handler.handle({"data": "https://example.com", "format": "qrcode"}, CancelToken())
    assert gen.last_data == "https://example.com"
    assert gen.last_options["format"] == "qr"
    assert result["image"]["media_type"] == "image/png"


@pytest.mark.asyncio
async def test_qr_generate_handler_passes_full_options_bag() -> None:
    """The enriched request carries styling options through to the facade."""

    class _FakeQrGenerate:
        captured: dict[str, Any] = {}

        def generate(self, data: str, options: dict[str, Any], cancel: CancelToken) -> bytes:
            _FakeQrGenerate.captured = {"data": data, "options": options}
            return b"png-bytes"

    gen = _FakeQrGenerate()
    store = _FakePayloadStore(b"")
    handler = QrGenerateHandler(facade=gen, store=store)  # type: ignore[arg-type]
    await handler.handle(
        {
            "data": "hello",
            "format": "qrcode",
            "size": 800,
            "error_correction": "H",
            "fg_color": "#112233",
            "bg_color": "#ffffff",
            "invert": True,
            "label_text": "caption",
            "label_position": "top",
            "label_font_size": 14,
        },
        CancelToken(),
    )
    opts = _FakeQrGenerate.captured["options"]
    assert opts["format"] == "qr"
    assert opts["size"] == 800
    assert opts["error_correction"] == "H"
    assert opts["fg_color"] == "#112233"
    assert opts["invert"] is True
    assert opts["label_position"] == "top"


@pytest.mark.asyncio
async def test_qr_generate_handler_maps_barcode_format() -> None:
    """format=barcode with barcode_format routes to the named barcode class."""

    class _FakeQrGenerate:
        captured_fmt: str = ""

        def generate(self, data: str, options: dict[str, Any], cancel: CancelToken) -> bytes:
            _FakeQrGenerate.captured_fmt = options["format"]
            return b"png"

    gen = _FakeQrGenerate()
    handler = QrGenerateHandler(facade=gen, store=_FakePayloadStore(b""))  # type: ignore[arg-type]
    await handler.handle(
        {"data": "123456789012", "format": "barcode", "barcode_format": "ean13"},
        CancelToken(),
    )
    assert _FakeQrGenerate.captured_fmt == "ean13"


@pytest.mark.asyncio
async def test_qr_generate_svg_handler_returns_svg_string() -> None:
    class _FakeQrSvg:
        def generate_svg(self, data: str, options: dict[str, Any], cancel: CancelToken) -> str:
            return f"<svg>{data}</svg>"

    handler = QrGenerateSvgHandler(facade=_FakeQrSvg())  # type: ignore[arg-type]
    result = await handler.handle(
        {"data": "https://x.test", "error_correction": "Q"}, CancelToken()
    )
    assert result["svg"] == "<svg>https://x.test</svg>"


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
    from vibeocr.worker_host.errors import WorkerError
    from vibeocr.worker_host.handlers.pdf import PdfRotateHandler

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


class _FakePipelineCache:
    def pipeline_cache_status(self) -> dict[str, Any]:
        return {
            "ready": True,
            "ttl_seconds": 300,
            "max_heavy": 2,
            "loaded_pipelines": ["OCR"],
            "last_used_unix_ms": {"OCR": 1234},
        }

    def set_pipeline_ttl(self, ttl_seconds: int) -> bool:
        self.ttl_seconds = ttl_seconds
        return True

    def release_pipelines(self, heavy_only: bool = True) -> list[str]:
        self.heavy_only = heavy_only
        return ["PP-StructureV3"]

    def preload_pipelines(self, pipelines: list[str]) -> dict[str, bool]:
        self.preloaded = pipelines
        return dict.fromkeys(pipelines, True)

    def warmup_pipelines(self, pipelines: list[str]) -> dict[str, bool]:
        self.warmed = pipelines
        return dict.fromkeys(pipelines, True)


@pytest.mark.asyncio
async def test_pipeline_cache_handlers_reach_boundary() -> None:
    boundary = _FakePipelineCache()
    cancel = CancelToken()

    status = await PipelineCacheStatusHandler(
        boundary=boundary
    ).handle({}, cancel)
    ttl = await SetPipelineCacheTtlHandler(boundary=boundary).handle(
        {"ttl_seconds": 600}, cancel
    )
    released = await ReleasePipelineCacheHandler(boundary=boundary).handle(
        {"heavy_only": False}, cancel
    )
    preloaded = await PreloadPipelineCacheHandler(boundary=boundary).handle(
        {"pipelines": ["OCR"]}, cancel
    )
    warmed = await WarmupPipelineCacheHandler(boundary=boundary).handle(
        {"pipelines": ["OCR"]}, cancel
    )

    assert status["loaded_pipelines"] == ["OCR"]
    assert ttl == {"updated": True, "ttl_seconds": 600}
    assert released == {"released": ["PP-StructureV3"]}
    assert preloaded == {"results": {"OCR": True}}
    assert warmed == {"results": {"OCR": True}}
    assert boundary.ttl_seconds == 600
    assert boundary.heavy_only is False



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


@pytest.mark.asyncio
async def test_switch_backend_handler_persists_and_reports_restart() -> None:
    from vibeocr.worker_host.handlers.settings import SwitchBackendHandler

    class _FakeBackendSwitch:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def switch_backend(self, target: str) -> str:
            self.calls.append(target)
            return target

    boundary = _FakeBackendSwitch()
    handler = SwitchBackendHandler(boundary=boundary)  # type: ignore[arg-type]
    result = await handler.handle({"backend": "gpu"}, CancelToken())
    assert result == {"backend": "gpu", "restart_required": True}
    assert boundary.calls == ["gpu"]


@pytest.mark.asyncio
async def test_switch_backend_handler_rejects_invalid_backend() -> None:
    from vibeocr.worker_host.errors import WorkerError
    from vibeocr.worker_host.handlers.settings import SwitchBackendHandler

    class _Fake:
        def switch_backend(self, target: str) -> str:
            raise AssertionError("should not be called")

    handler = SwitchBackendHandler(boundary=_Fake())  # type: ignore[arg-type]
    with pytest.raises(WorkerError):
        await handler.handle({"backend": "tpu"}, CancelToken())


@pytest.mark.asyncio
async def test_switch_backend_handler_maps_boundary_error_to_worker_error() -> None:
    from vibeocr.worker_host.errors import WorkerError
    from vibeocr.worker_host.handlers.settings import SwitchBackendHandler

    class _FakeFailing:
        def switch_backend(self, target: str) -> str:
            raise OSError("config file locked")

    handler = SwitchBackendHandler(boundary=_FakeFailing())  # type: ignore[arg-type]
    with pytest.raises(WorkerError):
        await handler.handle({"backend": "cpu"}, CancelToken())


@pytest.mark.asyncio
async def test_install_dependency_handler_maps_payload_to_result() -> None:
    from vibeocr.worker_host.handlers.settings import InstallDependencyHandler

    class _FakeInstall:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None]] = []

        def install_dependency(self, name, source, cancel):
            self.calls.append((name, source))
            return {"installed": True, "restarted": False, "name": name, "source": source}

    boundary = _FakeInstall()
    handler = InstallDependencyHandler(boundary=boundary)  # type: ignore[arg-type]
    result = await handler.handle({"name": "runtime", "source": "https://mirror"}, CancelToken())
    assert result["installed"] is True
    assert result["name"] == "runtime"
    assert boundary.calls == [("runtime", "https://mirror")]


@pytest.mark.asyncio
async def test_install_dependency_handler_rejects_missing_name() -> None:
    from vibeocr.worker_host.errors import WorkerError
    from vibeocr.worker_host.handlers.settings import InstallDependencyHandler

    handler = InstallDependencyHandler(boundary=type("F", (), {
        "install_dependency": lambda self, n, s, c: {}
    })())  # type: ignore[arg-type]
    with pytest.raises(WorkerError):
        await handler.handle({}, CancelToken())


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
