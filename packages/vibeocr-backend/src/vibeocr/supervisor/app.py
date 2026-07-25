"""FastAPI application for the v2 supervisor.

Routes map 1:1 to plan §4.1. The app is constructed from a
:class:`~vibeocr.supervisor.module.SupervisorModule` and a session token;
both are injected so tests can drive the full surface with a fake executor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from vibeocr.ipc.schemas import ProgressEvent, ProgressPhase

if TYPE_CHECKING:
    from collections.abc import Iterator

from vibeocr.protocol.v2 import (
    SCHEMA_VERSION,
    ErrorCode,
    ErrorPayload,
    JobKind,
    JobPriority,
    SettingsSnapshot,
    parse_pipeline_spec,
)
from vibeocr.protocol.v2.errors import error_registry

from .auth import check_bearer_token, check_loopback, is_bootstrap_path
from .jobs.registry import JobNotFoundError
from .jobs.staging import StagingQuotaError
from .module import ShutdownRequested, SupervisorModule


def _error_response(
    code: ErrorCode,
    instance_id: str,
    *,
    detail: dict | None = None,
    job_id: str | None = None,
) -> JSONResponse:
    entry = error_registry[code]
    payload = ErrorPayload(
        schema_version=SCHEMA_VERSION,
        instance_id=instance_id,
        code=code,
        message=entry.message,
        category=entry.category,
        retryable=entry.retryable,
        detail=detail or {},
        job_id=job_id,
    )
    body = payload.to_payload()
    return JSONResponse(status_code=entry.http_status, content=body)


class _PdfUnavailable(Exception):
    """Raised when the supervisor was built without a PDF adapter."""


class _PdfBadRequest(Exception):
    """Raised when a PDF route receives an unparseable/invalid body."""


def create_app(module: SupervisorModule, session_token: str) -> FastAPI:
    """Build a FastAPI app bound to ``module`` guarded by ``session_token``."""
    instance_id = module.options.instance_id
    app = FastAPI(title="VibeOCR Inference Supervisor", version="2.0.0")

    @app.middleware("http")
    async def _guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        client_host = request.client.host if request.client else None
        loop = check_loopback(client_host, instance_id=instance_id)
        if not loop.ok:
            return _error_response(loop.error.code, instance_id)  # type: ignore[arg-type]
        if not is_bootstrap_path(path):
            auth = check_bearer_token(
                request.headers.get("authorization"), session_token, instance_id=instance_id
            )
            if not auth.ok:
                return _error_response(auth.error.code, instance_id)  # type: ignore[arg-type]
        return await call_next(request)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.get("/v2/health")
    async def health() -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "instance_id": instance_id,
            "protocol_version": 2,
            "ready": not module.shutdown,
            "draining": module.draining,
            "capabilities": ["recognition", "pdf_ocr", "mineru_parse", "qrcode", "settings"],
        }

    # ------------------------------------------------------------------
    # Submit recognition
    # ------------------------------------------------------------------

    @app.post("/v2/jobs/recognition")
    async def submit_recognition(request: Request) -> dict[str, Any]:
        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" not in content_type:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id, detail={"field": "content-type"})
        try:
            form = await request.form()
        except Exception:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id)
        uploads: list[tuple[str, str | None, bytes]] = []
        files = form.getlist("files")
        for upload in files:
            data = await upload.read()  # type: ignore[union-attr]
            uploads.append((upload.filename or "input", upload.content_type, data))  # type: ignore[union-attr]
        try:
            ref = module.submit(
                kind=JobKind.RECOGNITION,
                priority=JobPriority.INTERACTIVE,
                uploads=uploads,
            )
        except ShutdownRequested:
            return _error_response(ErrorCode.SUPERVISOR_DRAINING, instance_id)
        except StagingQuotaError as exc:
            return _error_response(
                ErrorCode.QUOTA_EXCEEDED, instance_id, detail={"reason": str(exc)}
            )
        return ref.to_payload()

    # ------------------------------------------------------------------
    # Job status / events / result
    # ------------------------------------------------------------------

    @app.get("/v2/jobs/{job_id}")
    async def job_status(job_id: str) -> dict[str, Any]:
        try:
            return module.status(job_id).to_payload()
        except JobNotFoundError:
            return _error_response(ErrorCode.JOB_NOT_FOUND, instance_id, job_id=job_id)

    @app.get("/v2/jobs/{job_id}/events")
    async def job_events(job_id: str, after_sequence: int = 0) -> dict[str, Any]:
        try:
            events = module.events(job_id, after_sequence)
        except JobNotFoundError:
            return _error_response(ErrorCode.JOB_NOT_FOUND, instance_id, job_id=job_id)
        return {
            "schema_version": SCHEMA_VERSION,
            "instance_id": instance_id,
            "job_id": job_id,
            "events": [e.to_payload() for e in events],
        }

    @app.get("/v2/jobs/{job_id}/result")
    async def job_result(job_id: str) -> dict[str, Any]:
        try:
            entries = module.result(job_id)
        except JobNotFoundError:
            return _error_response(ErrorCode.JOB_NOT_FOUND, instance_id, job_id=job_id)
        return {
            "schema_version": SCHEMA_VERSION,
            "instance_id": instance_id,
            "job_id": job_id,
            "results": [e.to_payload() for e in entries],
        }

    # ------------------------------------------------------------------
    # Cancel / retry / delete
    # ------------------------------------------------------------------

    @app.post("/v2/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict[str, Any]:
        try:
            mode = module.request_cancel(job_id)
        except JobNotFoundError:
            return _error_response(ErrorCode.JOB_NOT_FOUND, instance_id, job_id=job_id)
        except ShutdownRequested:
            return _error_response(
                ErrorCode.JOB_NOT_CANCELLABLE, instance_id, job_id=job_id
            )
        return {
            "schema_version": SCHEMA_VERSION,
            "instance_id": instance_id,
            "job_id": job_id,
            "cancel_mode": mode.value,
        }

    @app.post("/v2/jobs/{job_id}/retry")
    async def retry_job(job_id: str) -> dict[str, Any]:
        try:
            ref = module.retry(job_id)
        except JobNotFoundError:
            return _error_response(ErrorCode.JOB_NOT_FOUND, instance_id, job_id=job_id)
        except Exception:
            return _error_response(ErrorCode.JOB_NOT_RETRYABLE, instance_id, job_id=job_id)
        return ref.to_payload()

    @app.delete("/v2/jobs/{job_id}")
    async def delete_job(job_id: str) -> JSONResponse:
        try:
            module.delete(job_id)
        except JobNotFoundError:
            return _error_response(ErrorCode.JOB_NOT_FOUND, instance_id, job_id=job_id)
        except ShutdownRequested:
            return _error_response(
                ErrorCode.JOB_NOT_CANCELLABLE, instance_id, job_id=job_id
            )
        return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)

    # ------------------------------------------------------------------
    # Runtime / settings
    # ------------------------------------------------------------------

    @app.get("/v2/runtime/residency")
    async def residency() -> dict[str, Any]:
        return module.residency().to_payload()

    @app.post("/v2/runtime/release")
    async def release_runtime(request: Request) -> dict[str, Any]:
        body: dict[str, Any] = {}
        try:
            parsed = await request.json()
            if isinstance(parsed, dict):
                body = parsed
        except Exception:
            body = {}
        pipeline = body.get("pipeline")
        return module.release_idle(pipeline).to_payload()

    @app.get("/v2/settings")
    async def get_settings() -> dict[str, Any]:
        return module.settings().to_payload()

    @app.put("/v2/settings")
    async def put_settings(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id)
        if not isinstance(body, dict):
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id)
        residency = body.get("residency", {})
        default_ttl = int(residency.get("default_ttl_seconds", 300))
        raw_pipelines = residency.get("pipelines", [])
        try:
            pipelines = tuple(parse_pipeline_spec(p) for p in raw_pipelines)
        except Exception:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id)
        snapshot = SettingsSnapshot(
            default_ttl_seconds=default_ttl,
            pipelines=pipelines,
            extra=body.get("extra", {}),
        )
        return module.update_settings(snapshot).to_payload()

    # ------------------------------------------------------------------
    # Export (plan §4.1 — bounded export capability)
    # ------------------------------------------------------------------

    @app.post("/v2/export")
    async def export_ocr(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id)
        if not isinstance(body, dict):
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id)
        from pathlib import Path

        from vibeocr.application.contracts import OcrExportRequest

        try:
            req = OcrExportRequest(
                raw_text=str(body.get("raw_text", "")),
                markdown_text=str(body.get("markdown_text", "")),
                html_text=str(body.get("html_text", "")),
                raw_blocks=list(body.get("raw_blocks", [])),
                output_path=Path(str(body.get("output_path", ""))),
                format=str(body.get("format", "")),
                overwrite=bool(body.get("overwrite", False)),
            )
        except Exception:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id, detail={"reason": "invalid export request"})
        try:
            from vibeocr.models.ocr_result import OCRResult
            from vibeocr.services.export_service import ExportService

            ocr_result = OCRResult(
                raw_text=req.raw_text,
                markdown_text=req.markdown_text,
                html_text=req.html_text,
                content_list=req.raw_blocks,
            )
            success = ExportService.export(ocr_result, req.output_path, req.format)
            if not success:
                return _error_response(
                    ErrorCode.INTERNAL_ERROR, instance_id, detail={"reason": "export failed"}
                )
            bytes_written = req.output_path.stat().st_size if req.output_path.exists() else 0
        except Exception as exc:
            return _error_response(ErrorCode.INTERNAL_ERROR, instance_id, detail={"error": str(exc)})
        return {
            "schema_version": SCHEMA_VERSION,
            "instance_id": instance_id,
            "output_path": str(req.output_path),
            "bytes_written": bytes_written,
        }

    # ------------------------------------------------------------------
    # PDF session operations (plan §6 — bounded proxy to PDF child)
    # The supervisor owns the PDF child process via ``module.pdf_adapter``;
    # these endpoints proxy the full PdfBackendClient surface so the UI
    # never talks to the PDF child directly. DTOs come from
    # ``vibeocr.ipc.schemas`` (shared with the legacy client) so the
    # GUI-side transport swap is a drop-in.
    # ------------------------------------------------------------------

    def _pdf_adapter() -> Any:
        """Return the PDF adapter or raise to produce a 503 error response."""
        adapter = module.pdf_adapter
        if adapter is None:
            raise _PdfUnavailable()
        return adapter

    async def _pdf_body(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception as e:
            raise _PdfBadRequest(str(e)) from e
        if not isinstance(body, dict):
            raise _PdfBadRequest("body must be a JSON object")
        return body

    def _pdf_response(payload: Any) -> dict[str, Any]:
        """Serialise a pydantic DTO (or pass through a dict) with envelope."""
        if hasattr(payload, "model_dump"):
            data = payload.model_dump(mode="json")
        elif isinstance(payload, dict):
            data = payload
        else:
            data = {"value": payload}
        return {"schema_version": SCHEMA_VERSION, "instance_id": instance_id, **data}

    def _pdf_error(exc: Exception) -> JSONResponse:
        if isinstance(exc, _PdfUnavailable):
            return _error_response(
                ErrorCode.INTERNAL_ERROR,
                instance_id,
                detail={"reason": "pdf_adapter not configured"},
            )
        if isinstance(exc, _PdfBadRequest):
            return _error_response(
                ErrorCode.VALIDATION_ERROR, instance_id, detail={"reason": str(exc)}
            )
        return _error_response(
            ErrorCode.INTERNAL_ERROR, instance_id, detail={"error": str(exc)}
        )

    @app.post("/v2/pdf/sessions/open")
    async def pdf_open(request: Request) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            path = body.get("path", "")
            if not path:
                raise _PdfBadRequest("missing path")
            adapter = _pdf_adapter()
            result = adapter.open_session(path)
            return _pdf_response(result)
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/close")
    async def pdf_close(session_id: str) -> dict[str, Any]:
        try:
            _pdf_adapter().close_session(session_id)
            return _pdf_response({"closed": True})
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/model")
    async def pdf_model(session_id: str) -> dict[str, Any]:
        try:
            return _pdf_response(_pdf_adapter().get_model(session_id))
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/load")
    async def pdf_load(session_id: str) -> StreamingResponse:
        """Stream per-page text-layer detection (NDJSON, one ProgressEvent per line)."""
        try:
            adapter = _pdf_adapter()
        except Exception as exc:
            return _pdf_error(exc)  # type: ignore[return-value]

        def gen() -> Iterator[bytes]:
            try:
                for event in adapter.load_stream(session_id):
                    yield event.model_dump_json().encode("utf-8") + b"\n"
            except Exception as exc:
                # Emit a typed error line so the client can raise rather than hang.
                err = ProgressEvent(phase=ProgressPhase.LOAD, message=f"error: {exc}")
                yield err.model_dump_json().encode("utf-8") + b"\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/v2/pdf/sessions/{session_id}/render_thumbnail")
    async def pdf_render_thumbnail(
        session_id: str, request: Request
    ) -> Response:
        try:
            body = await _pdf_body(request)
            page = int(body.get("page", 0))
            size = int(body.get("size", 160))
            data = _pdf_adapter().render_thumbnail(session_id, page, size=size)
            return Response(content=data, media_type="image/png")
        except Exception as exc:
            return _pdf_error(exc)  # type: ignore[return-value]

    @app.post("/v2/pdf/sessions/{session_id}/render_preview")
    async def pdf_render_preview(
        session_id: str, request: Request
    ) -> Response:
        try:
            body = await _pdf_body(request)
            page = int(body.get("page", 0))
            dpi = int(body.get("dpi", 150))
            data = _pdf_adapter().render_preview(session_id, page, dpi=dpi)
            return Response(content=data, media_type="image/png")
        except Exception as exc:
            return _pdf_error(exc)  # type: ignore[return-value]

    @app.get("/v2/pdf/sessions/{session_id}/render")
    async def pdf_render(
        session_id: str, request: Request
    ) -> Response:
        """Render a page thumbnail via GET (quick-preview contract).

        The .NET ``InferenceHttpClient.RenderPdfPageAsync`` issues a GET to
        ``/v2/pdf/sessions/{id}/render?page=&size=`` for fast page previews.
        This delegates to the adapter's ``render_thumbnail`` (same pixels as
        the POST ``render_thumbnail`` route); the GET form is for callers that
        embed the URL directly (e.g. an image source).
        """
        try:
            page = int(request.query_params.get("page", 0))
            size = int(request.query_params.get("size", 160))
            data = _pdf_adapter().render_thumbnail(session_id, page, size=size)
            return Response(content=data, media_type="image/png")
        except Exception as exc:
            return _pdf_error(exc)  # type: ignore[return-value]

    @app.post("/v2/pdf/sessions/{session_id}/detect_text_layers")
    async def pdf_detect_text_layers(
        session_id: str, request: Request
    ) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            page = int(body.get("page", 0))
            return _pdf_response(
                _pdf_adapter().detect_text_layers(session_id, page)
            )
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/rotate")
    async def pdf_rotate(session_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            pages = body.get("pages", [])
            angle = int(body.get("angle", 90))
            return _pdf_response(_pdf_adapter().rotate(session_id, pages, angle))
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/delete_pages")
    async def pdf_delete_pages(session_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            pages = body.get("pages", [])
            return _pdf_response(_pdf_adapter().delete_pages(session_id, pages))
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/insert_blank")
    async def pdf_insert_blank(session_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            after_index = int(body.get("after_index", -1))
            width = float(body.get("width", 612.0))
            height = float(body.get("height", 792.0))
            return _pdf_response(
                _pdf_adapter().insert_blank(session_id, after_index, width, height)
            )
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/insert_from")
    async def pdf_insert_from(session_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            source_path = body.get("source_path", "")
            after_index = int(body.get("after_index", -1))
            if not source_path:
                raise _PdfBadRequest("missing source_path")
            return _pdf_response(
                _pdf_adapter().insert_from(session_id, source_path, after_index)
            )
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/move_page")
    async def pdf_move_page(session_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            from_index = int(body.get("from_index", -1))
            to_index = int(body.get("to_index", -1))
            return _pdf_response(
                _pdf_adapter().move_page(session_id, from_index, to_index)
            )
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/reorder")
    async def pdf_reorder(session_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            new_order = body.get("new_order", [])
            return _pdf_response(_pdf_adapter().reorder(session_id, new_order))
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/add_text_layer")
    async def pdf_add_text_layer(session_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            page = int(body.get("page", 0))
            ocr_result = body.get("ocr_result", {})
            pdf_settings = body.get("pdf_settings")
            overwrite = bool(body.get("overwrite", False))
            return _pdf_response(
                _pdf_adapter().add_text_layer(
                    session_id, page, ocr_result, pdf_settings, overwrite
                )
            )
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/add_text_layer_batch")
    async def pdf_add_text_layer_batch(
        session_id: str, request: Request
    ) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            pages_data = body.get("pages", [])
            pdf_settings = body.get("pdf_settings")
            overwrite = bool(body.get("overwrite", False))
            save = bool(body.get("save", False))
            return _pdf_response(
                _pdf_adapter().add_text_layer_batch(
                    session_id, pages_data, pdf_settings, overwrite, save
                )
            )
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/rewrite_text_layer")
    async def pdf_rewrite_text_layer(
        session_id: str, request: Request
    ) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            page = int(body.get("page", 0))
            text_blocks = body.get("text_blocks", [])
            preproc_angle = int(body.get("preproc_angle", 0))
            pdf_settings = body.get("pdf_settings")
            return _pdf_response(
                _pdf_adapter().rewrite_text_layer(
                    session_id, page, text_blocks, preproc_angle, pdf_settings
                )
            )
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/update_block_text")
    async def pdf_update_block_text(
        session_id: str, request: Request
    ) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            page = int(body.get("page", 0))
            block_index = int(body.get("block_index", 0))
            new_text = body.get("new_text", "")
            return _pdf_response(
                _pdf_adapter().update_block_text(
                    session_id, page, block_index, new_text
                )
            )
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/delete_text_layers")
    async def pdf_delete_text_layers(
        session_id: str, request: Request
    ) -> StreamingResponse:
        """Stream per-page text-layer deletion (NDJSON)."""
        try:
            body = await _pdf_body(request)
            pages = body.get("pages", [])
            adapter = _pdf_adapter()
        except Exception as exc:
            return _pdf_error(exc)  # type: ignore[return-value]

        def gen() -> Iterator[bytes]:
            try:
                for event in adapter.delete_text_layers_stream(session_id, pages):
                    yield event.model_dump_json().encode("utf-8") + b"\n"
            except Exception as exc:
                err = ProgressEvent(phase=ProgressPhase.DELETE, message=f"error: {exc}")
                yield err.model_dump_json().encode("utf-8") + b"\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/v2/pdf/sessions/{session_id}/save")
    async def pdf_save(session_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await _pdf_body(request)
            path = body.get("path")
            pdf_settings = body.get("pdf_settings")
            rewrite_text_layers = bool(body.get("rewrite_text_layers", True))
            return _pdf_response(
                _pdf_adapter().save(
                    session_id,
                    path,
                    pdf_settings,
                    rewrite_text_layers=rewrite_text_layers,
                )
            )
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/save_transactional")
    async def pdf_save_transactional(
        session_id: str, request: Request
    ) -> dict[str, Any]:
        """Atomic save: write to a temp file in the target's parent dir, fsync,
        then ``Path.replace`` onto the target path. Returns ``{path}``.

        This is the transactional publish path required by plan §6 ("PDF 最终
        save 使用临时文件 + fsync/replace 的事务式发布") so a crash mid-save
        never overwrites the original with a half-written file.
        """
        try:
            body = await _pdf_body(request)
            path = body.get("path")
            if not path:
                return _error_response(
                    ErrorCode.VALIDATION_ERROR,
                    instance_id,
                    detail={"field": "path"},
                )
            saved = _pdf_adapter().save_transactional(session_id, path)
            return _pdf_response({"path": saved})
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/cancel")
    async def pdf_cancel(session_id: str) -> dict[str, Any]:
        try:
            _pdf_adapter().cancel(session_id)
            return _pdf_response({"cancelled": True})
        except Exception as exc:
            return _pdf_error(exc)

    @app.post("/v2/pdf/sessions/{session_id}/reset_cancel")
    async def pdf_reset_cancel(session_id: str) -> dict[str, Any]:
        try:
            _pdf_adapter().reset_cancel(session_id)
            return _pdf_response({"reset": True})
        except Exception as exc:
            return _pdf_error(exc)

    # ------------------------------------------------------------------
    # QR decode / generate (plan §4.1 — bounded QR capability)
    # ------------------------------------------------------------------

    @app.post("/v2/qrcode/decode")
    async def qrcode_decode(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id)
        if not isinstance(body, dict) or "image" not in body:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id, detail={"field": "image"})
        import base64
        import io

        from PIL import Image as PILImage

        try:
            raw = base64.b64decode(body["image"])
            img = PILImage.open(io.BytesIO(raw))
        except Exception:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id, detail={"reason": "invalid image"})
        try:
            from vibeocr.services.qrcode_decode_service import QrcodeDecodeService

            svc = QrcodeDecodeService()
            items = svc.decode(img)
            # DecodedItem exposes {data, type, is_url}. Surface the real fields
            # (type = symbology like "QR"/"CODE128"; is_url = strict http(s)
            # check computed by the service). Keep `format` as a backwards-
            # compatible alias for `type`.
            codes = [
                {
                    "data": it.data,
                    "type": getattr(it, "type", None) or "QR",
                    "format": getattr(it, "type", None) or "QR",
                    "is_url": bool(getattr(it, "is_url", False)),
                }
                for it in items
            ]
        except Exception as exc:
            return _error_response(ErrorCode.INTERNAL_ERROR, instance_id, detail={"error": str(exc)})
        return {
            "schema_version": SCHEMA_VERSION,
            "instance_id": instance_id,
            "codes": codes,
        }

    @app.post("/v2/qrcode/generate")
    async def qrcode_generate(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id)
        if not isinstance(body, dict) or "data" not in body:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id, detail={"field": "data"})
        text = body["data"]
        fmt = body.get("format", "qr")
        options = body.get("options", {})
        import base64
        import io

        try:
            from vibeocr.services.qrcode_service import QrcodeService

            svc = QrcodeService()
            pil_img = svc.generate(text, options)
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as exc:
            return _error_response(ErrorCode.INTERNAL_ERROR, instance_id, detail={"error": str(exc)})
        return {
            "schema_version": SCHEMA_VERSION,
            "instance_id": instance_id,
            "image": image_b64,
            "format": fmt,
            "media_type": "image/png",
        }

    return app


__all__ = ["create_app"]
