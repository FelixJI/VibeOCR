"""PDF RPC handlers: bridges PDF protocol methods to the backend + orchestrator.

``pdf.open`` delegates to the application facade; the session-oriented methods
(close/render/rotate/delete/add_text_layer/delete_text_layers/save/start_ocr)
delegate to a :class:`PdfSessionBackend` that wraps the real
``PdfBackendClient`` and the UI-free :class:`PdfOcrOrchestrator`. The Python
backend remains the algorithm source of truth; these handlers only map wire
payloads to backend calls and back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from vibeocr.application.contracts import (
    CancelToken,
    PdfError,
    PdfOpenRequest,
    PdfSessionDto,
)
from vibeocr.worker_host.errors import ErrorCode, WorkerError


@runtime_checkable
class PdfFacade(Protocol):
    def open(self, request: PdfOpenRequest, cancel: CancelToken) -> PdfSessionDto: ...


@runtime_checkable
class PdfSessionBackend(Protocol):
    """Backend boundary for session-oriented PDF operations.

    Implementations wrap ``PdfBackendClient`` (close/render/mutate/save) and
    the OCR orchestrator (start_ocr). Methods may raise; handlers map
    exceptions to ``WorkerError``.
    """

    def close(self, session_id: str) -> bool: ...
    def render_page(
        self, session_id: str, page_index: int, size: int | None, dpi: int | None
    ) -> bytes: ...
    def rotate(self, session_id: str, page_indices: list[int], angle: int) -> int: ...
    def delete_pages(self, session_id: str, page_indices: list[int]) -> int: ...
    def add_text_layer(
        self, session_id: str, page_index: int, overwrite: bool, save: bool
    ) -> dict[str, Any]: ...
    def delete_text_layers(
        self, session_id: str, page_indices: list[int], cancel: CancelToken
    ) -> dict[str, Any]: ...
    def save(self, session_id: str, output_path: str | None) -> str: ...
    def start_ocr(
        self,
        session_id: str,
        file_path: str,
        page_indices: list[int],
        overwrite: bool,
        sidecar_root: str | None,
        cancel: CancelToken,
    ) -> dict[str, Any]: ...


class PdfOpenHandler:
    """Handle ``pdf.open``: open a PDF file and create a session."""

    def __init__(self, *, facade: PdfFacade) -> None:
        self._facade = facade

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        file_path = payload.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "pdf.open requires 'file_path'")
        path = Path(file_path)
        if not path.is_absolute():
            raise WorkerError(
                ErrorCode.INVALID_REQUEST,
                "pdf.open requires an absolute 'file_path'",
            )
        request = PdfOpenRequest(file_path=path)
        try:
            session = await asyncio.to_thread(self._facade.open, request, cancel)
        except PdfError as exc:
            raise WorkerError(ErrorCode.INTERNAL_ERROR, str(exc)) from exc
        return {
            "session_id": session.session_id,
            "file_path": str(session.file_path),
            "page_count": session.page_count,
        }


def _require_session_id(payload: dict[str, Any], method: str) -> str:
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise WorkerError(ErrorCode.INVALID_REQUEST, f"{method} requires 'session_id'")
    return session_id


def _page_indices(payload: dict[str, Any], method: str) -> list[int]:
    raw = payload.get("page_indices")
    if not isinstance(raw, list) or not all(isinstance(i, int) for i in raw):
        raise WorkerError(ErrorCode.INVALID_REQUEST, f"{method} requires 'page_indices' array")
    return raw


class PdfCloseHandler:
    def __init__(self, *, backend: PdfSessionBackend) -> None:
        self._backend = backend

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        session_id = _require_session_id(payload, "pdf.close")
        closed = await asyncio.to_thread(self._backend.close, session_id)
        return {"closed": bool(closed)}


class PdfRenderPageHandler:
    """Handle ``pdf.render_page``: render a page to PNG via shared memory."""

    def __init__(self, *, backend: PdfSessionBackend, store: Any) -> None:
        self._backend = backend
        self._store = store

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        session_id = _require_session_id(payload, "pdf.render_page")
        page_index = payload.get("page_index")
        if not isinstance(page_index, int) or page_index < 0:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "pdf.render_page requires 'page_index'")
        size = payload.get("size") if isinstance(payload.get("size"), int) else None
        dpi = payload.get("dpi") if isinstance(payload.get("dpi"), int) else None
        png = await asyncio.to_thread(
            self._backend.render_page, session_id, page_index, size, dpi
        )
        ref = await self._store.put(png, media_type="image/png")
        return {"image": ref.to_descriptor()}


class PdfRotateHandler:
    def __init__(self, *, backend: PdfSessionBackend) -> None:
        self._backend = backend

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        session_id = _require_session_id(payload, "pdf.rotate")
        pages = _page_indices(payload, "pdf.rotate")
        angle = payload.get("angle")
        if angle not in (90, -90, 180, 270):
            raise WorkerError(ErrorCode.INVALID_REQUEST, "pdf.rotate requires valid 'angle'")
        page_count = await asyncio.to_thread(self._backend.rotate, session_id, pages, angle)
        return {"page_count": page_count}


class PdfDeletePagesHandler:
    def __init__(self, *, backend: PdfSessionBackend) -> None:
        self._backend = backend

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        session_id = _require_session_id(payload, "pdf.delete_pages")
        pages = _page_indices(payload, "pdf.delete_pages")
        page_count = await asyncio.to_thread(self._backend.delete_pages, session_id, pages)
        return {"page_count": page_count}


class PdfAddTextLayerHandler:
    def __init__(self, *, backend: PdfSessionBackend) -> None:
        self._backend = backend

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        session_id = _require_session_id(payload, "pdf.add_text_layer")
        page_index = payload.get("page_index")
        if not isinstance(page_index, int) or page_index < 0:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "pdf.add_text_layer requires 'page_index'")
        overwrite = bool(payload.get("overwrite"))
        save = bool(payload.get("save", True))
        return await asyncio.to_thread(
            self._backend.add_text_layer, session_id, page_index, overwrite, save
        )


class PdfDeleteTextLayersHandler:
    def __init__(self, *, backend: PdfSessionBackend) -> None:
        self._backend = backend

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        session_id = _require_session_id(payload, "pdf.delete_text_layers")
        pages = _page_indices(payload, "pdf.delete_text_layers")
        return await asyncio.to_thread(
            self._backend.delete_text_layers, session_id, pages, cancel
        )


class PdfSaveHandler:
    def __init__(self, *, backend: PdfSessionBackend) -> None:
        self._backend = backend

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        session_id = _require_session_id(payload, "pdf.save")
        output_path = payload.get("output_path")
        if output_path is not None and not isinstance(output_path, str):
            raise WorkerError(ErrorCode.INVALID_REQUEST, "pdf.save 'output_path' must be a string or null")
        saved_path = await asyncio.to_thread(self._backend.save, session_id, output_path)
        return {"saved_path": saved_path}


class PdfStartOcrHandler:
    """Handle ``pdf.start_ocr``: durable batch OCR via the orchestrator."""

    def __init__(self, *, backend: PdfSessionBackend) -> None:
        self._backend = backend

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        session_id = _require_session_id(payload, "pdf.start_ocr")
        file_path = payload.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "pdf.start_ocr requires 'file_path'")
        pages = _page_indices(payload, "pdf.start_ocr")
        overwrite = bool(payload.get("overwrite"))
        sidecar_root = payload.get("sidecar_root")
        if sidecar_root is not None and not isinstance(sidecar_root, str):
            raise WorkerError(ErrorCode.INVALID_REQUEST, "pdf.start_ocr 'sidecar_root' must be a string or null")
        return await asyncio.to_thread(
            self._backend.start_ocr,
            session_id,
            file_path,
            pages,
            overwrite,
            sidecar_root,
            cancel,
        )


__all__ = [
    "PdfAddTextLayerHandler",
    "PdfCloseHandler",
    "PdfDeletePagesHandler",
    "PdfDeleteTextLayersHandler",
    "PdfFacade",
    "PdfOpenHandler",
    "PdfRenderPageHandler",
    "PdfRotateHandler",
    "PdfSaveHandler",
    "PdfSessionBackend",
    "PdfStartOcrHandler",
]
