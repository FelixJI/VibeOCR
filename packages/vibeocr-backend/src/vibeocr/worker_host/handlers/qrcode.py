"""QR code RPC handlers: bridges ``qrcode.decode`` and ``qrcode.generate``.

These handlers delegate to Python services (the algorithm source of truth).
Decode reads an image from shared memory; generate writes an image to shared
memory owned by the worker.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from vibeocr.worker_host.errors import ErrorCode, WorkerError
from vibeocr.worker_host.shared_payload import SharedPayloadRef, SharedPayloadStore

if TYPE_CHECKING:
    from vibeocr.application.contracts import CancelToken


@runtime_checkable
class QrDecodeFacade(Protocol):
    def decode(self, data: bytes, cancel: CancelToken) -> list[dict[str, Any]]: ...


@runtime_checkable
class QrGenerateFacade(Protocol):
    """Generate a styled QR/barcode image and return PNG bytes."""

    def generate(self, data: str, options: dict[str, Any], cancel: CancelToken) -> bytes: ...


@runtime_checkable
class QrGenerateSvgFacade(Protocol):
    """Generate a QR code as an SVG string."""

    def generate_svg(self, data: str, options: dict[str, Any], cancel: CancelToken) -> str: ...


class QrDecodeHandler:
    """Handle ``qrcode.decode``: read image from shared memory and decode codes."""

    def __init__(self, *, facade: QrDecodeFacade, store: SharedPayloadStore) -> None:
        self._facade = facade
        self._store = store

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        if "image" not in payload:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "qrcode.decode requires 'image'")
        ref = SharedPayloadRef.from_descriptor(payload["image"])
        image_data = await self._store.read(ref)
        codes = await asyncio.to_thread(self._facade.decode, image_data, cancel)
        return {
            "codes": [
                {"data": c["data"], "format": c["format"], "is_url": bool(c.get("is_url"))}
                for c in codes
            ]
        }


class QrGenerateHandler:
    """Handle ``qrcode.generate``: generate a styled image and expose it via shared memory."""

    def __init__(self, *, facade: QrGenerateFacade, store: SharedPayloadStore) -> None:
        self._facade = facade
        self._store = store

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, str) or not data:
            raise WorkerError(ErrorCode.INVALID_REQUEST, "qrcode.generate requires 'data'")
        # Build the options bag consumed by QrcodeService.generate + post-processing.
        fmt = str(payload.get("format", "qrcode"))
        options: dict[str, Any] = {"format": "qr" if fmt == "qrcode" else fmt}
        if fmt == "barcode":
            barcode_fmt = str(payload.get("barcode_format", "code128")).lower()
            options["format"] = barcode_fmt
        for key in (
            "size",
            "error_correction",
            "fg_color",
            "bg_color",
            "invert",
            "logo_path",
            "logo_ratio",
            "label_text",
            "label_position",
            "label_font_size",
        ):
            if key in payload:
                options[key] = payload[key]
        image_bytes = await asyncio.to_thread(self._facade.generate, data, options, cancel)
        ref = await self._store.put(image_bytes, media_type="image/png")
        return {"image": ref.to_descriptor()}


class QrGenerateSvgHandler:
    """Handle ``qrcode.generate_svg``: return a QR code as raw SVG text."""

    def __init__(self, *, facade: QrGenerateSvgFacade) -> None:
        self._facade = facade

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, str) or not data:
            raise WorkerError(
                ErrorCode.INVALID_REQUEST, "qrcode.generate_svg requires 'data'"
            )
        options: dict[str, Any] = {}
        for key in ("error_correction", "fg_color", "bg_color"):
            if key in payload:
                options[key] = payload[key]
        svg = await asyncio.to_thread(self._facade.generate_svg, data, options, cancel)
        return {"svg": svg}


__all__ = [
    "QrDecodeFacade",
    "QrDecodeHandler",
    "QrGenerateFacade",
    "QrGenerateHandler",
    "QrGenerateSvgFacade",
    "QrGenerateSvgHandler",
]
