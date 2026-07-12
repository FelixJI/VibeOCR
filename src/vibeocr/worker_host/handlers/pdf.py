"""PDF RPC handler: bridges ``pdf.open`` to the PDF application facade."""

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


class PdfOpenHandler:
    """Handle ``pdf.open``: open a PDF file and create a session."""

    def __init__(self, *, facade: PdfFacade) -> None:
        self._facade = facade

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        file_path = payload.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "pdf.open requires 'file_path'")
        request = PdfOpenRequest(file_path=Path(file_path))
        try:
            session = await asyncio.to_thread(self._facade.open, request, cancel)
        except PdfError as exc:
            raise WorkerError(ErrorCode.INTERNAL_ERROR, str(exc)) from exc
        return {
            "session_id": session.session_id,
            "file_path": str(session.file_path),
            "page_count": session.page_count,
        }


__all__ = ["PdfFacade", "PdfOpenHandler"]
