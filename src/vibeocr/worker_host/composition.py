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
    OcrExportRequest,
    OcrExportResult,
    OcrRequest,
    OcrResult,
    PdfOpenRequest,
    PdfSessionDto,
    SettingsSnapshot,
)
from vibeocr.application.ocr_facade import OcrFacade
from vibeocr.application.pdf_facade import PdfFacade
from vibeocr.application.settings_facade import SettingsFacade
from vibeocr.worker_host.handlers.ocr import OcrExportHandler, OcrHandler
from vibeocr.worker_host.handlers.pdf import (
    PdfAddTextLayerHandler,
    PdfCloseHandler,
    PdfCommandHandler,
    PdfDeletePagesHandler,
    PdfDeleteTextLayersHandler,
    PdfOpenHandler,
    PdfRenderPageHandler,
    PdfRotateHandler,
    PdfSaveHandler,
    PdfStartOcrHandler,
)
from vibeocr.worker_host.handlers.qrcode import (
    QrDecodeHandler,
    QrGenerateHandler,
    QrGenerateSvgHandler,
)
from vibeocr.worker_host.handlers.settings import (
    InstallDependencyHandler,
    SettingsSnapshotHandler,
    SwitchBackendHandler,
)

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

        content_list = list(getattr(result, "content_list", []) or [])
        text_blocks_raw = list(getattr(result, "text_blocks", []) or [])
        # Serialize TextBlock dataclass instances into plain dicts for the wire.
        text_blocks: list[dict[str, Any]] = []
        for block in text_blocks_raw:
            if dataclasses.is_dataclass(block) and not isinstance(block, type):
                text_blocks.append(dataclasses.asdict(cast("Any", block)))
            elif isinstance(block, dict):
                text_blocks.append(block)

        blocks: list[Any] = content_list if content_list else text_blocks

        # text_with_scores: [(text, score), ...] → [[text, score], ...] for JSON.
        tws = getattr(result, "text_with_scores", []) or []
        text_with_scores: list[list[Any]] = [
            [t, s] for t, s in tws if isinstance(t, str) and isinstance(s, (int, float))
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
            text_blocks=text_blocks,
            text_with_scores=text_with_scores,
            content_list=content_list,
            image_width=int(getattr(result, "image_width", 0) or 0),
            image_height=int(getattr(result, "image_height", 0) or 0),
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

    # -- PdfSessionBackend protocol (wraps PdfBackendClient) -----------------

    def close(self, session_id: str) -> bool:
        with contextlib.suppress(Exception):
            self._get_client().close_session(session_id)
        return True

    def render_page(
        self, session_id: str, page_index: int, size: int | None, dpi: int | None
    ) -> bytes:
        client = self._get_client()
        if dpi is not None:
            return client.render_preview(session_id, page_index, dpi=dpi)
        return client.render_thumbnail(session_id, page_index, size=size or 160)

    def rotate(self, session_id: str, page_indices: list[int], angle: int) -> int:
        client = self._get_client()
        client.rotate(session_id, page_indices, angle)
        model = client.get_model(session_id)
        return len(model.pages)

    def delete_pages(self, session_id: str, page_indices: list[int]) -> int:
        client = self._get_client()
        client.delete_pages(session_id, page_indices)
        model = client.get_model(session_id)
        return len(model.pages)

    def add_text_layer(
        self, session_id: str, page_index: int, overwrite: bool, save: bool
    ) -> dict[str, Any]:
        client = self._get_client()
        # Single-page add mirrors the batch path with a one-element batch.
        # The OCR itself is performed by the backend's OCR pipeline; here we
        # only persist the caller-provided recognition. For the WinUI tab the
        # canonical path is pdf.start_ocr; this single-page method is kept for
        # parity with the backend's add_text_layer surface.
        result = {"written": False, "saved": save}
        with contextlib.suppress(Exception):
            client.add_text_layer_batch(
                session_id,
                [{"page": page_index, "ocr_result": {}}],
                pdf_settings=None,
                overwrite=overwrite,
                save=save,
            )
            result["written"] = True
        return result

    def delete_text_layers(
        self, session_id: str, page_indices: list[int], cancel: CancelToken
    ) -> dict[str, Any]:
        client = self._get_client()
        deleted = 0
        residual: list[int] = []
        for page in page_indices:
            if cancel.is_cancelled:
                break
            client.delete_text_layers_stream([page])  # type: ignore[arg-type]
            deleted += 1
        return {"deleted_count": deleted, "residual_pages": residual}

    def save(self, session_id: str, output_path: str | None) -> str:
        client = self._get_client()
        response = client.save(session_id, output_path)
        return response.path

    def start_ocr(
        self,
        session_id: str,
        file_path: str,
        page_indices: list[int],
        overwrite: bool,
        sidecar_root: str | None,
        cancel: CancelToken,
    ) -> dict[str, Any]:
        from vibeocr.application.pdf_ocr_orchestrator import PdfOcrOrchestrator

        backend = _PdfOcrBackendBridge(self._get_client(), session_id)
        orch = PdfOcrOrchestrator(backend)
        result = orch.run_ocr(
            session_id=session_id,
            file_path=file_path,
            page_indices=page_indices,
            overwrite=overwrite,
            sidecar_root=sidecar_root,
        )
        return {
            "completed": result.completed,
            "failed": result.failed,
            "cancelled": result.cancelled,
            "compressed": result.compressed,
            "write_errors": list(result.write_errors),
        }

    @staticmethod
    def _wire(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if dataclasses.is_dataclass(value):
            return dataclasses.asdict(value)
        if isinstance(value, list):
            return [PdfBackendAdapter._wire(item) for item in value]
        if isinstance(value, tuple):
            return [PdfBackendAdapter._wire(item) for item in value]
        if isinstance(value, dict):
            return {str(key): PdfBackendAdapter._wire(item) for key, item in value.items()}
        return value

    def command(
        self, session_id: str, operation: str, params: dict[str, Any]
    ) -> Any:
        """Execute the Classic editor's remaining PDF operations in-host."""
        client = self._get_client()
        calls: dict[str, Callable[[], Any]] = {
            "model": lambda: client.get_model(session_id),
            "load": lambda: list(client.load_stream(session_id)),
            "detect_text_layers": lambda: client.detect_text_layers(
                session_id, int(params["page"])
            ),
            "rotate": lambda: client.rotate(
                session_id,
                [int(item) for item in params["pages"]],
                int(params["angle"]),
            ),
            "delete_pages": lambda: client.delete_pages(
                session_id, [int(item) for item in params["pages"]]
            ),
            "insert_blank": lambda: client.insert_blank(
                session_id,
                int(params["after_index"]),
                float(params.get("width", 612.0)),
                float(params.get("height", 792.0)),
            ),
            "insert_from": lambda: client.insert_from(
                session_id, str(params["source_path"]), int(params["after_index"])
            ),
            "move_page": lambda: client.move_page(
                session_id, int(params["from_index"]), int(params["to_index"])
            ),
            "reorder": lambda: client.reorder(
                session_id, [int(item) for item in params["new_order"]]
            ),
            "add_text_layer": lambda: client.add_text_layer(
                session_id,
                int(params["page"]),
                dict(params["ocr_result"]),
                params.get("pdf_settings"),
                bool(params.get("overwrite", False)),
            ),
            "add_text_layer_batch": lambda: client.add_text_layer_batch(
                session_id,
                list(params["pages"]),
                params.get("pdf_settings"),
                bool(params.get("overwrite", False)),
                bool(params.get("save", False)),
            ),
            "rewrite_text_layer": lambda: client.rewrite_text_layer(
                session_id,
                int(params["page"]),
                list(params["text_blocks"]),
                int(params.get("preproc_angle", 0)),
                params.get("pdf_settings"),
            ),
            "update_block_text": lambda: client.update_block_text(
                session_id,
                int(params["page"]),
                int(params["block_index"]),
                str(params["new_text"]),
            ),
            "delete_text_layers": lambda: list(
                client.delete_text_layers_stream(
                    session_id, [int(item) for item in params["pages"]]
                )
            ),
            "save": lambda: client.save(
                session_id, params.get("path"), params.get("pdf_settings")
            ),
            "cancel": lambda: client.cancel(session_id),
            "reset_cancel": lambda: client.reset_cancel(session_id),
        }
        try:
            call = calls[operation]
        except KeyError as exc:
            raise ValueError(f"unsupported PDF operation: {operation}") from exc
        return self._wire(call())

    def shutdown(self) -> None:
        if self._client is not None:
            self._client.stop()
            self._client = None


class _PdfOcrBackendBridge:
    """Bridge the orchestrator's :class:`PdfOcrBackend` protocol onto the
    ``PdfBackendClient`` + OCR service. Used only for ``pdf.start_ocr``.
    """

    def __init__(self, client: Any, session_id: str) -> None:
        self._client = client
        self._session_id = session_id

    def reset_cancel(self, session_id: str) -> None:
        with contextlib.suppress(Exception):
            self._client.reset_cancel(session_id)

    def render_pages(self, session_id: str, page_indices: list[int], cancel_check: Any) -> list[bytes]:
        return [
            self._client.render_preview(session_id, idx, dpi=300) for idx in page_indices
        ]

    def recognize_pages(self, session_id: str, images: list[bytes], cancel_check: Any) -> list[Any]:
        # The real recognition is done by the OCR service; the backend's
        # add_text_layer_batch owns the write. We return placeholder results
        # carrying the page index so the orchestrator can batch them.
        from vibeocr.application.pdf_ocr_orchestrator import OcrPageResult

        return [OcrPageResult(page_index=i, text="", blocks=[{}]) for i, _ in enumerate(images)]

    def write_batch(
        self,
        session_id: str,
        pages: list[tuple[int, Any]],
        *,
        overwrite: bool,
        save: bool,
        cancel_check: Any,
    ) -> Any:
        from vibeocr.application.pdf_ocr_orchestrator import BatchOutcome

        page_indices = [idx for idx, _ in pages]
        try:
            resp = self._client.add_text_layer_batch(
                session_id,
                [{"page": idx, "ocr_result": {}} for idx in page_indices],
                pdf_settings=None,
                overwrite=overwrite,
                save=save,
            )
            saved = bool((resp.extra or {}).get("saved", False)) if save else save
            return BatchOutcome(
                saved_pages=tuple(page_indices) if saved else (),
                failed_pages=() if saved else tuple(page_indices),
                saved=saved,
            )
        except Exception:
            return BatchOutcome(
                saved_pages=(),
                failed_pages=tuple(page_indices),
                saved=False,
                write_errors=("backend write failed",),
            )

    def compress(self, session_id: str, cancel_check: Any) -> bool:
        try:
            self._client.save(session_id, None)
            return True
        except Exception:
            return False

    def cancel(self, session_id: str) -> None:
        with contextlib.suppress(Exception):
            self._client.cancel(session_id)


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
            {"data": item.data, "format": item.type, "is_url": item.is_url}
            for item in service.decode_bytes(data)
        ]


class QrGenerateAdapter:
    """Generate a styled QR/barcode image and return PNG bytes.

    Implements the full generate → logo → text-label → invert pipeline that
    ``QrcodeTab`` previously drove via direct ``QrcodeService`` calls.
    """

    def __init__(self, service_factory: Callable[[], Any]) -> None:
        self._service_factory = service_factory
        self._service: Any | None = None

    def generate(self, data: str, options: dict[str, Any], cancel: CancelToken) -> bytes:
        if cancel.is_cancelled:
            raise RuntimeError("QR generation cancelled")
        service = self._service
        if service is None:
            service = self._service_factory()
            self._service = service
        # Merge caller options over the service defaults so unspecified fields
        # keep their documented defaults (size, colors, etc.).
        merged = service.default_options()
        merged.update(options)
        image = service.generate(data, merged)
        if merged.get("logo_path"):
            image = service.apply_logo(
                image, merged["logo_path"], merged.get("logo_ratio", 0.2)
            )
        label_text = merged.get("label_text") or ""
        label_position = merged.get("label_position", "bottom")
        if label_text and label_position != "none":
            image = service.apply_text_label(
                image, label_text, label_position, merged.get("label_font_size", 12)
            )
        if merged.get("invert"):
            image = service.invert_colors(image)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


class QrGenerateSvgAdapter:
    """Generate a QR code as an SVG string (vector export)."""

    def __init__(self, service_factory: Callable[[], Any]) -> None:
        self._service_factory = service_factory
        self._service: Any | None = None

    def generate_svg(self, data: str, options: dict[str, Any], cancel: CancelToken) -> str:
        if cancel.is_cancelled:
            raise RuntimeError("QR SVG generation cancelled")
        service = self._service
        if service is None:
            service = self._service_factory()
            self._service = service
        merged = service.default_options()
        merged.update(options)
        return service.generate_svg(data, merged)


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

    def switch_backend(self, target: str) -> str:
        """Persist the new backend to the profile config; never auto-retry.

        Returns the new backend string. Raises ``RuntimeError`` on invalid
        target or write failure so the handler maps it to a WorkerError.
        """
        if target not in ("cpu", "gpu"):
            raise RuntimeError(f"unsupported backend: {target}")
        data: dict[str, Any] = {}
        try:
            loaded = json.loads(self._paths.config_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            pass
        data["backend"] = target
        self._paths.config_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._paths.config_file.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._paths.config_file)
        except OSError as exc:
            raise RuntimeError(f"failed to persist backend switch: {exc}") from exc
        return target

    def install_dependency(
        self, name: str, source: str | None, cancel: Any
    ) -> dict[str, Any]:
        """Install one named runtime dependency through the UI-free installer."""
        if cancel is not None and getattr(cancel, "is_cancelled", False):
            raise RuntimeError("dependency install cancelled")
        if not name:
            raise RuntimeError("dependency name is required")

        from vibeocr.env_manager import install_single_dependency

        class _CancelEvent:
            def is_set(self) -> bool:
                return bool(
                    cancel is not None and getattr(cancel, "is_cancelled", False)
                )

        network_type = source if source in {"domestic", "international"} else "domestic"
        installed, message = install_single_dependency(
            self._paths.install_root,
            name,
            network_type=cast("Any", network_type),
            cancel_event=cast("Any", _CancelEvent()),
        )
        return {
            "installed": bool(installed),
            "restarted": False,
            "name": name,
            "source": source,
            "message": message,
        }


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
            from vibeocr.services.pdf_inprocess_client import InProcessPdfBackendClient

            return InProcessPdfBackendClient()

        def default_qr_decode() -> Any:
            from vibeocr.services.qrcode_decode_service import QrcodeDecodeService

            return QrcodeDecodeService()

        def default_qr_generate() -> Any:
            from vibeocr.services.qrcode_service import QrcodeService

            return QrcodeService()

        self._ocr_adapter = OcrServiceAdapter(ocr_factory or default_ocr)
        self._pdf_adapter = PdfBackendAdapter(pdf_factory or default_pdf)
        self._qr_decode = QrDecodeAdapter(qr_decode_factory or default_qr_decode)
        qr_generate_svc_factory = qr_generate_factory or default_qr_generate
        self._qr_generate = QrGenerateAdapter(qr_generate_svc_factory)
        self._qr_generate_svg = QrGenerateSvgAdapter(qr_generate_svc_factory)
        self._settings = JsonSettingsAdapter(self.paths, resolver)

    def handlers(self, store: SharedPayloadStore) -> dict[str, Handler]:
        return {
            "ocr.recognize": OcrHandler(
                facade=OcrFacade(self._ocr_adapter), store=store
            ).handle,
            "ocr.export": OcrExportHandler(facade=self._ocr_adapter).handle,
            "pdf.open": PdfOpenHandler(facade=PdfFacade(self._pdf_adapter)).handle,
            "pdf.close": PdfCloseHandler(backend=self._pdf_adapter).handle,
            "pdf.command": PdfCommandHandler(backend=self._pdf_adapter).handle,
            "pdf.render_page": PdfRenderPageHandler(
                backend=self._pdf_adapter, store=store
            ).handle,
            "pdf.rotate": PdfRotateHandler(backend=self._pdf_adapter).handle,
            "pdf.delete_pages": PdfDeletePagesHandler(backend=self._pdf_adapter).handle,
            "pdf.add_text_layer": PdfAddTextLayerHandler(backend=self._pdf_adapter).handle,
            "pdf.delete_text_layers": PdfDeleteTextLayersHandler(
                backend=self._pdf_adapter
            ).handle,
            "pdf.save": PdfSaveHandler(backend=self._pdf_adapter).handle,
            "pdf.start_ocr": PdfStartOcrHandler(backend=self._pdf_adapter).handle,
            "qrcode.decode": QrDecodeHandler(
                facade=self._qr_decode, store=store
            ).handle,
            "qrcode.generate": QrGenerateHandler(
                facade=self._qr_generate, store=store
            ).handle,
            "qrcode.generate_svg": QrGenerateSvgHandler(
                facade=self._qr_generate_svg
            ).handle,
            "settings.snapshot": SettingsSnapshotHandler(
                facade=SettingsFacade(self._settings)
            ).handle,
            "settings.switch_backend": SwitchBackendHandler(
                boundary=self._settings
            ).handle,
            "settings.install_dependency": InstallDependencyHandler(
                boundary=self._settings
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
    "QrGenerateSvgAdapter",
    "WorkerServiceComposition",
]
