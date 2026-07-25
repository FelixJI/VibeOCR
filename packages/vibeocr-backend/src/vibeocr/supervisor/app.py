"""FastAPI application for the v2 supervisor.

Routes map 1:1 to plan §4.1. The app is constructed from a
:class:`~vibeocr.supervisor.module.SupervisorModule` and a session token;
both are injected so tests can drive the full surface with a fake executor.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

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
    # The supervisor owns the PDF child process; these endpoints proxy
    # open/render/rotate/delete/save operations so the UI never talks to
    # the PDF child directly.
    # ------------------------------------------------------------------

    @app.post("/v2/pdf/sessions/open")
    async def pdf_open(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id)
        path = body.get("path", "")
        _password = body.get("password")
        if not path:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id, detail={"field": "path"})
        try:
            # Use the supervisor's PdfProcessAdapter to own the child.
            # For now we proxy via the existing PdfBackendClient singleton.
            from vibeocr.services.pdf_backend_client import PdfBackendClient

            client = PdfBackendClient.instance()
            result = client.open_session(path)
            return {
                "schema_version": SCHEMA_VERSION,
                "instance_id": instance_id,
                "session_id": result.session_id,
                "page_count": result.page_count,
                "file_path": result.file_path,
            }
        except Exception as exc:
            return _error_response(ErrorCode.INTERNAL_ERROR, instance_id, detail={"error": str(exc)})

    @app.get("/v2/pdf/sessions/{session_id}/render")
    async def pdf_render(session_id: str, page: int = 0, size: int = 1024) -> Response:
        try:
            from vibeocr.services.pdf_backend_client import PdfBackendClient

            client = PdfBackendClient.instance()
            data = client.render_preview(session_id, page, dpi=min(size, 300))
            return Response(content=data, media_type="image/png")
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"schema_version": SCHEMA_VERSION, "instance_id": instance_id,
                         "code": "INTERNAL_ERROR", "message": str(exc), "category": "internal",
                         "retryable": False, "detail": {}, "job_id": None},
            )

    @app.post("/v2/pdf/sessions/{session_id}/rotate")
    async def pdf_rotate(session_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id)
        pages = body.get("pages", [])
        angle = body.get("angle", 90)
        try:
            from vibeocr.services.pdf_backend_client import PdfBackendClient

            client = PdfBackendClient.instance()
            result = client.rotate(session_id, pages, angle)
            return {
                "schema_version": SCHEMA_VERSION,
                "instance_id": instance_id,
                "page_count": result.page_count,
            }
        except Exception as exc:
            return _error_response(ErrorCode.INTERNAL_ERROR, instance_id, detail={"error": str(exc)})

    @app.post("/v2/pdf/sessions/{session_id}/delete_pages")
    async def pdf_delete_pages(session_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id)
        pages = body.get("pages", [])
        try:
            from vibeocr.services.pdf_backend_client import PdfBackendClient

            client = PdfBackendClient.instance()
            result = client.delete_pages(session_id, pages)
            return {
                "schema_version": SCHEMA_VERSION,
                "instance_id": instance_id,
                "page_count": result.page_count,
            }
        except Exception as exc:
            return _error_response(ErrorCode.INTERNAL_ERROR, instance_id, detail={"error": str(exc)})

    @app.post("/v2/pdf/sessions/{session_id}/save")
    async def pdf_save(session_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id)
        target = body.get("output_path", "")
        if not target:
            return _error_response(ErrorCode.VALIDATION_ERROR, instance_id, detail={"field": "output_path"})
        try:
            from vibeocr.services.pdf_backend_client import PdfBackendClient

            client = PdfBackendClient.instance()
            saved_path = client.save(session_id, target)
            return {
                "schema_version": SCHEMA_VERSION,
                "instance_id": instance_id,
                "saved_path": str(saved_path),
            }
        except Exception as exc:
            return _error_response(ErrorCode.INTERNAL_ERROR, instance_id, detail={"error": str(exc)})

    @app.post("/v2/pdf/sessions/{session_id}/close")
    async def pdf_close(session_id: str) -> dict[str, Any]:
        try:
            from vibeocr.services.pdf_backend_client import PdfBackendClient

            client = PdfBackendClient.instance()
            client.close_session(session_id)
            return {"schema_version": SCHEMA_VERSION, "instance_id": instance_id, "closed": True}
        except Exception as exc:
            return _error_response(ErrorCode.INTERNAL_ERROR, instance_id, detail={"error": str(exc)})

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
            codes = [
                {"data": it.data, "format": getattr(it, "format", None) or "QR", "is_url": False}
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
