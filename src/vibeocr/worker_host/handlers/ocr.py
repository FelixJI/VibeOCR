"""OCR RPC handler: bridges ``ocr.recognize`` to the OCR application facade.

Translates the wire payload (a SharedPayloadRef for the image plus pipeline
options) into an ``OcrRequest``, invokes the facade offloaded to the executor,
and returns the result payload. Never imports PySide6.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from vibeocr.application.contracts import (
    CancelToken,
    OcrError,
    OcrExportRequest,
    OcrExportResult,
    OcrRequest,
    OcrResult,
)
from vibeocr.worker_host.errors import ErrorCode, WorkerError
from vibeocr.worker_host.shared_payload import SharedPayloadRef, SharedPayloadStore


@runtime_checkable
class OcrRecognizeFacade(Protocol):
    def recognize(self, request: OcrRequest, cancel: CancelToken) -> OcrResult: ...


@runtime_checkable
class OcrExportFacade(Protocol):
    def export(self, request: OcrExportRequest, cancel: CancelToken) -> OcrExportResult: ...


class OcrHandler:
    """Handle ``ocr.recognize``: read image from shared memory and run OCR."""

    def __init__(self, *, facade: OcrRecognizeFacade, store: SharedPayloadStore) -> None:
        self._facade = facade
        self._store = store

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        if "image" not in payload:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "ocr.recognize requires 'image'")
        ref = SharedPayloadRef.from_descriptor(payload["image"])
        image_data = await self._store.read(ref)
        pipeline = str(payload.get("pipeline", "OCR"))
        language = payload.get("language")
        request = OcrRequest(
            image_data=image_data,
            pipeline=pipeline,
            language=str(language) if language is not None else None,
        )
        try:
            result = await asyncio.to_thread(self._facade.recognize, request, cancel)
        except OcrError as exc:
            raise WorkerError(ErrorCode.INTERNAL_ERROR, str(exc)) from exc
        return {
            "text": result.text,
            "pipeline": result.pipeline,
            "raw_blocks": list(result.raw_blocks),
            "markdown_text": result.markdown_text,
            "html_text": result.html_text,
            "raw_text": result.raw_text or result.text,
        }


class OcrExportHandler:
    def __init__(self, *, facade: OcrExportFacade) -> None:
        self._facade = facade

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        request = OcrExportRequest(
            raw_text=str(payload.get("raw_text", "")),
            markdown_text=str(payload.get("markdown_text", "")),
            html_text=str(payload.get("html_text", "")),
            raw_blocks=list(payload.get("raw_blocks", [])),
            output_path=Path(str(payload.get("output_path", ""))),
            format=str(payload.get("format", "")),
            overwrite=bool(payload.get("overwrite", False)),
        )
        if not request.output_path.is_absolute():
            raise WorkerError(ErrorCode.INVALID_REQUEST, "output_path must be absolute")
        if request.format not in {"txt", "markdown", "html"}:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "unsupported export format")
        if request.output_path.exists() and not request.overwrite:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "output already exists")
        result = await asyncio.to_thread(self._facade.export, request, cancel)
        return {"output_path": str(result.output_path), "bytes_written": result.bytes_written}


__all__ = ["OcrExportFacade", "OcrExportHandler", "OcrHandler", "OcrRecognizeFacade"]
