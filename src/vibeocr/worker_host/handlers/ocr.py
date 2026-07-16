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
class OcrBatchRecognizeFacade(Protocol):
    def recognize_batch(
        self, requests: list[OcrRequest], cancel: CancelToken
    ) -> list[OcrResult | None]: ...


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
        # preprocessed_image, if present, is staged in shared memory.
        return _result_payload(result)


class OcrBatchHandler:
    """Handle one control RPC backed by one engine-level OCR batch."""

    def __init__(
        self, *, facade: OcrBatchRecognizeFacade, store: SharedPayloadStore
    ) -> None:
        self._facade = facade
        self._store = store

    async def handle(
        self, payload: dict[str, Any], cancel: CancelToken
    ) -> dict[str, Any]:
        raw_refs = payload.get("images")
        if not isinstance(raw_refs, list) or not raw_refs:
            raise WorkerError(
                ErrorCode.INVALID_REQUEST,
                "ocr.recognize_batch requires a non-empty 'images' array",
            )
        refs = [SharedPayloadRef.from_descriptor(item) for item in raw_refs]
        images = await asyncio.gather(*(self._store.read(ref) for ref in refs))
        pipeline = str(payload.get("pipeline", "OCR"))
        language = payload.get("language")
        requests = [
            OcrRequest(
                image_data=image,
                pipeline=pipeline,
                language=str(language) if language is not None else None,
            )
            for image in images
        ]
        try:
            results = await asyncio.to_thread(
                self._facade.recognize_batch, requests, cancel
            )
        except OcrError as exc:
            raise WorkerError(ErrorCode.INTERNAL_ERROR, str(exc)) from exc
        if len(results) != len(requests):
            raise WorkerError(
                ErrorCode.INTERNAL_ERROR,
                "OCR batch result count does not match request count",
            )
        return {
            "results": [
                _result_payload(result) if result is not None else None
                for result in results
            ]
        }


def _result_payload(result: OcrResult) -> dict[str, Any]:
    return {
        "text": result.text,
        "pipeline": result.pipeline,
        "raw_blocks": list(result.raw_blocks),
        "markdown_text": result.markdown_text,
        "html_text": result.html_text,
        "raw_text": result.raw_text or result.text,
        "text_blocks": list(result.text_blocks),
        "text_with_scores": list(result.text_with_scores),
        "content_list": list(result.content_list),
        "image_width": result.image_width,
        "image_height": result.image_height,
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
        if request.format not in {"txt", "markdown", "html", "docx", "xlsx"}:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "unsupported export format")
        if request.output_path.exists() and not request.overwrite:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "output already exists")
        result = await asyncio.to_thread(self._facade.export, request, cancel)
        return {"output_path": str(result.output_path), "bytes_written": result.bytes_written}


__all__ = [
    "OcrBatchHandler",
    "OcrBatchRecognizeFacade",
    "OcrExportFacade",
    "OcrExportHandler",
    "OcrHandler",
    "OcrRecognizeFacade",
]
