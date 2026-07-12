"""OCR RPC handler: bridges ``ocr.recognize`` to the OCR application facade.

Translates the wire payload (a SharedPayloadRef for the image plus pipeline
options) into an ``OcrRequest``, invokes the facade offloaded to the executor,
and returns the result payload. Never imports PySide6.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from vibeocr.application.contracts import CancelToken, OcrError, OcrRequest, OcrResult
from vibeocr.worker_host.errors import ErrorCode, WorkerError
from vibeocr.worker_host.shared_payload import SharedPayloadRef, SharedPayloadStore


@runtime_checkable
class OcrFacade(Protocol):
    def recognize(self, request: OcrRequest, cancel: CancelToken) -> OcrResult: ...


class OcrHandler:
    """Handle ``ocr.recognize``: read image from shared memory and run OCR."""

    def __init__(self, *, facade: OcrFacade, store: SharedPayloadStore) -> None:
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
        }


__all__ = ["OcrFacade", "OcrHandler"]
