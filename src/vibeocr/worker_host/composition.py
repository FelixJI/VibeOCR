"""UI-free production composition for WorkerHost protocol-v1 handlers."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from vibeocr.app_paths import AppPaths, resolve_app_paths
from vibeocr.application.contracts import (
    CancelToken,
    OcrRequest,
    OcrResult,
    OcrExportRequest,
    OcrExportResult,
    PdfOpenRequest,
    PdfSessionDto,
    SettingsSnapshot,
)
from vibeocr.application.ocr_facade import OcrFacade
from vibeocr.application.pdf_facade import PdfFacade
from vibeocr.application.settings_facade import SettingsFacade
from vibeocr.worker_host.handlers.ocr import OcrExportHandler, OcrHandler
from vibeocr.worker_host.handlers.pdf import PdfOpenHandler
from vibeocr.worker_host.handlers.qrcode import QrDecodeHandler, QrGenerateHandler
from vibeocr.worker_host.handlers.settings import SettingsSnapshotHandler

if TYPE_CHECKING:
    from collections.abc import Callable

    from vibeocr.worker_host.dispatcher import Handler
    from vibeocr.worker_host.shared_payload import SharedPayloadStore


class OcrServiceAdapter:
    """Map application OCR DTOs onto the existing subprocess OCR service."""

    def __init__(self, service_factory: Callable[[], Any]) -> None:
        self._service_factory = service_factory
        self._service: Any | None = None
        self._lock = threading.Lock()

    def _get_service(self) -> Any:
        if self._service is None:
            with self._lock:
                if self._service is None:
                    self._service = self._service_factory()
        return self._service

    def recognize(self, request: OcrRequest, cancel: CancelToken) -> OcrResult:
        if cancel.is_cancelled:
            raise RuntimeError("OCR request cancelled")
        options: dict[str, Any] = {"pipeline": request.pipeline}
        if request.language is not None:
            options["language"] = request.language
        result = self._get_service().recognize(request.image_data, options)
        if cancel.is_cancelled:
            raise RuntimeError("OCR request cancelled")

        blocks: list[Any] = list(getattr(result, "content_list", []) or [])
        if not blocks:
            blocks = [
                dataclasses.asdict(cast("Any", block))
                if dataclasses.is_dataclass(block) and not isinstance(block, type)
                else block
                for block in (getattr(result, "text_blocks", []) or [])
            ]
        text = str(
            getattr(result, "copy_text", None) or getattr(result, "raw_text", "")
        )
        pipeline = str(getattr(result, "pipeline_type", request.pipeline))
        return OcrResult(
            text=text,
            raw_blocks=blocks,
            pipeline=pipeline,
            markdown_text=str(getattr(result, "markdown_text", "") or text),
            html_text=str(getattr(result, "html_text", "") or ""),
            raw_text=str(getattr(result, "raw_text", "") or text),
        )

    def export(self, request: OcrExportRequest, cancel: CancelToken) -> OcrExportResult:
        if cancel.is_cancelled:
            raise RuntimeError("OCR export cancelled")
        from vibeocr.models.ocr_result import OCRResult
        from vibeocr.services.export_service import ExportService

        result = OCRResult(
            raw_text=request.raw_text,
            markdown_text=request.markdown_text,
            html_text=request.html_text,
            content_list=request.raw_blocks,
        )
        if not ExportService.export(result, request.output_path, request.format):
            raise RuntimeError("OCR export failed")
        if cancel.is_cancelled:
            raise RuntimeError("OCR export cancelled")
        return OcrExportResult(request.output_path, request.output_path.stat().st_size)

    def shutdown(self) -> None:
        if self._service is not None:
            self._service.shutdown()
            self._service = None


class PdfBackendAdapter:
    """Map application PDF DTOs onto ``PdfBackendClient``."""

    def __init__(self, client_factory: Callable[[], Any]) -> None:
        self._client_factory = client_factory
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def open(self, request: PdfOpenRequest, cancel: CancelToken) -> PdfSessionDto:
        if cancel.is_cancelled:
            raise RuntimeError("PDF request cancelled")
        path = request.file_path.expanduser().resolve()
        response = self._get_client().open_session(str(path))
        if cancel.is_cancelled:
            with contextlib.suppress(Exception):
                self._get_client().close_session(response.session_id)
            raise RuntimeError("PDF request cancelled")
        model_path = response.model.file_path or str(path)
        return PdfSessionDto(
            session_id=response.session_id,
            file_path=Path(model_path),
            page_count=len(response.model.pages),
        )

    def shutdown(self) -> None:
        if self._client is not None:
            self._client.stop()
            self._client = None


class QrDecodeAdapter:
    def __init__(self, service_factory: Callable[[], Any]) -> None:
        self._service_factory = service_factory
        self._service: Any | None = None

    def decode(self, data: bytes, cancel: CancelToken) -> list[dict[str, Any]]:
        if cancel.is_cancelled:
            raise RuntimeError("QR decode cancelled")
        service = self._service
        if service is None:
            service = self._service_factory()
            self._service = service
        return [
            {"data": item.data, "format": item.type}
            for item in service.decode_bytes(data)
        ]


class QrGenerateAdapter:
    def __init__(self, service_factory: Callable[[], Any]) -> None:
        self._service_factory = service_factory
        self._service: Any | None = None

    def generate(self, data: str, fmt: str, cancel: CancelToken) -> bytes:
        if cancel.is_cancelled:
            raise RuntimeError("QR generation cancelled")
        service = self._service
        if service is None:
            service = self._service_factory()
            self._service = service
        options = service.default_options()
        options["format"] = "qr" if fmt == "qrcode" else "code128"
        image = service.generate(data, options)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


class JsonSettingsAdapter:
    """Read the side-by-side profile settings without importing Qt."""

    def __init__(
        self,
        paths: AppPaths,
        backend_resolver: Callable[[], str],
    ) -> None:
        self._paths = paths
        self._backend_resolver = backend_resolver

    def get_snapshot(self) -> SettingsSnapshot:
        data: dict[str, Any] = {}
        try:
            loaded = json.loads(self._paths.config_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            pass
        pipelines = data.get("preload_pipelines", [])
        if not isinstance(pipelines, list):
            pipelines = []
        normalized = tuple(str(item) for item in pipelines if isinstance(item, str))
        ttl = data.get("pipeline_ttl_seconds", 300)
        if isinstance(ttl, bool) or not isinstance(ttl, int):
            ttl = 300
        backend = data.get("backend")
        if backend not in ("cpu", "gpu"):
            backend = self._backend_resolver()
        return SettingsSnapshot(
            backend=str(backend),
            preload_pipelines=normalized,
            ttl_seconds=max(0, ttl),
        )


class WorkerServiceComposition:
    """Own the production adapters and expose the protocol handler table."""

    def __init__(
        self,
        *,
        project_root: Path,
        profile: str,
        ocr_factory: Callable[[], Any] | None = None,
        pdf_factory: Callable[[], Any] | None = None,
        qr_decode_factory: Callable[[], Any] | None = None,
        qr_generate_factory: Callable[[], Any] | None = None,
        backend_resolver: Callable[[], str] | None = None,
    ) -> None:
        self.paths = resolve_app_paths(project_root, profile=profile)

        def default_backend() -> str:
            from vibeocr.env_manager import resolve_use_gpu

            return "gpu" if resolve_use_gpu(project_root) else "cpu"

        resolver = backend_resolver or default_backend

        def default_ocr() -> Any:
            from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess

            return OCRServiceSubprocess(
                use_gpu=self._settings.get_snapshot().backend == "gpu",
                auto_start=True,
            )

        def default_pdf() -> Any:
            from vibeocr.services.pdf_backend_client import PdfBackendClient

            return PdfBackendClient.instance()

        def default_qr_decode() -> Any:
            from vibeocr.services.qrcode_decode_service import QrcodeDecodeService

            return QrcodeDecodeService()

        def default_qr_generate() -> Any:
            from vibeocr.services.qrcode_service import QrcodeService

            return QrcodeService()

        self._ocr_adapter = OcrServiceAdapter(ocr_factory or default_ocr)
        self._pdf_adapter = PdfBackendAdapter(pdf_factory or default_pdf)
        self._qr_decode = QrDecodeAdapter(qr_decode_factory or default_qr_decode)
        self._qr_generate = QrGenerateAdapter(qr_generate_factory or default_qr_generate)
        self._settings = JsonSettingsAdapter(self.paths, resolver)

    def handlers(self, store: SharedPayloadStore) -> dict[str, Handler]:
        return {
            "ocr.recognize": OcrHandler(
                facade=OcrFacade(self._ocr_adapter), store=store
            ).handle,
            "ocr.export": OcrExportHandler(facade=self._ocr_adapter).handle,
            "pdf.open": PdfOpenHandler(facade=PdfFacade(self._pdf_adapter)).handle,
            "qrcode.decode": QrDecodeHandler(
                facade=self._qr_decode, store=store
            ).handle,
            "qrcode.generate": QrGenerateHandler(
                facade=self._qr_generate, store=store
            ).handle,
            "settings.snapshot": SettingsSnapshotHandler(
                facade=SettingsFacade(self._settings)
            ).handle,
        }

    def backend(self) -> str:
        return self._settings.get_snapshot().backend

    def shutdown(self) -> None:
        self._ocr_adapter.shutdown()
        self._pdf_adapter.shutdown()


__all__ = [
    "JsonSettingsAdapter",
    "OcrServiceAdapter",
    "PdfBackendAdapter",
    "QrDecodeAdapter",
    "QrGenerateAdapter",
    "WorkerServiceComposition",
]
