"""PaddlePipelineAdapter: the unified ``recognize_many`` seam for Paddle.

Plan §4 Phase 4 goals:

* All Paddle pipelines enter the scheduler through one adapter.
* Single image is a one-element batch (no separate single implementation).
* The capability reports the *honest* real-batch support: only the OCR
  pipeline registers a true ``recognize_batch``; PP-StructureV3 / VL /
  formula / table currently fall back to per-item loops and must not be
  reported as real batch.
* Stable OCRResult / TextBlock / parsing logic is reused unchanged.

This adapter is deliberately thin: it injects the existing
:class:`OCRService`/registry and never re-implements result parsing.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from vibeocr.protocol.v2 import ResidencyStatus

from .budgets import AdapterCapability, ComputeBatch, InputItem

if TYPE_CHECKING:
    from .residency import ResidencyManager


class _OCRServiceLike(Protocol):
    """Minimal slice of :class:`OCRService` we depend on."""

    def recognize_batch(self, images: list[Any], options: Any | None = ...) -> list[Any]: ...


@dataclass
class PaddlePipelineAdapter:
    """Wraps an OCRService-like object behind ``recognize_many``."""

    service: _OCRServiceLike
    pipeline_name: str = "OCR"
    residency: ResidencyManager | None = None
    # Cached capability — populated lazily from the pipeline registry.
    _capability: AdapterCapability | None = None

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------

    def capabilities(self) -> AdapterCapability:
        if self._capability is not None:
            return self._capability
        real_batch = self._pipeline_supports_real_batch()
        max_compute = 8 if real_batch else 1
        self._capability = AdapterCapability(
            name=self.pipeline_name,
            real_batch=real_batch,
            max_compute_batch=max_compute,
        )
        return self._capability

    def _pipeline_supports_real_batch(self) -> bool:
        """Return True only if the registry registers a real batch adapter."""
        try:
            from vibeocr.core.pipelines import get_registry  # type: ignore

            registry = get_registry()
            spec = registry.get(self.pipeline_name) if registry.has(self.pipeline_name) else None
            return bool(spec is not None and getattr(spec, "recognize_batch", None) is not None)
        except Exception:
            # In test environments without the pipeline registry we report
            # conservatively: not a real batch.
            return False

    # ------------------------------------------------------------------
    # recognize_many — the unified entry point
    # ------------------------------------------------------------------

    def recognize_many(
        self,
        items: list[InputItem],
        *,
        options: Any | None = None,
        compute_batch: ComputeBatch | None = None,
    ) -> list[dict[str, Any]]:
        """Recognise a list of staged inputs.

        ``items`` must carry raw image bytes in ``encoded_bytes``-style
        payloads (we read bytes via the item's ``data`` attribute if present,
        else assume the caller pre-decoded). Returns one result dict per
        input, in identical order. Single-image callers pass a one-element
        list; there is no separate single path.
        """
        if not items:
            return []
        images = [self._to_ndarray(self._raw_bytes(it)) for it in items]
        if self.residency is not None:
            self.residency.lease(self.pipeline_name)
        try:
            results = self.service.recognize_batch(images, options)
        finally:
            if self.residency is not None:
                self.residency.release(self.pipeline_name)
                self.residency.touch(self.pipeline_name)
        return [self._result_to_payload(r) for r in results]

    # ------------------------------------------------------------------
    # Residency passthrough
    # ------------------------------------------------------------------

    def residency_status(self) -> ResidencyStatus:
        if self.residency is None:
            return ResidencyStatus()
        return self.residency.status()

    def release_idle(self, pipeline: str | None = None) -> ResidencyStatus:
        if self.residency is None:
            return ResidencyStatus()
        self.residency.release_idle(pipeline)
        return self.residency.status()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _raw_bytes(item: InputItem) -> bytes:
        data = getattr(item, "data", b"")
        if isinstance(data, (bytes, bytearray)) and len(data) > 0:
            return bytes(data)
        raise ValueError(
            f"InputItem {item.item_id} has no raw bytes; decode before calling recognize_many"
        )

    @staticmethod
    def _to_ndarray(raw: bytes) -> np.ndarray:
        from PIL import Image as PILImage

        img = PILImage.open(io.BytesIO(raw))
        if img.mode != "RGB":
            img = img.convert("RGB")
        return np.array(img)

    @staticmethod
    def _result_to_payload(result: Any) -> dict[str, Any]:
        """Convert an OCRResult to a JSON-native payload.

        Delegates to :func:`vibeocr.models.ocr_result_serializer.ocr_result_to_payload`,
        which is the single source of truth for the wire shape and produces the
        key set consumed by downstream callers (``text_blocks``/``preproc_angle``
        for PDF text-layer writeback, ``raw_text``/``markdown_text``/
        ``html_text``/``content_list`` for export). ``dict`` inputs pass through
        unchanged so test fakes keep working.
        """
        # Imported lazily: the serializer lives in vibeocr-client-py (models),
        # which the backend depends on via wheel. Lazy import keeps this module
        # importable in minimal test environments that monkeypatch the adapter.
        from vibeocr.models import ocr_result_to_payload

        return ocr_result_to_payload(result)


__all__ = ["PaddlePipelineAdapter"]
