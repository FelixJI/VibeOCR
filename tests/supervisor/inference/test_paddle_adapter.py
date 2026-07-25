"""Tests for PaddlePipelineAdapter: recognize_many, capability honesty, order."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from vibeocr.models.ocr_result import OCRResult, TextBlock
from vibeocr.supervisor.inference.budgets import InputItem
from vibeocr.supervisor.inference.paddle_adapter import PaddlePipelineAdapter

if TYPE_CHECKING:
    import numpy as np


def _png_bytes(label: str, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    img = Image.new("RGB", (8, 8), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
    # Attach a label so the fake service can echo it back.


class _FakeOCRService:
    """Records calls and returns one OCRResult per input in order.

    Mirrors the real ``OCRService.recognize_batch`` contract (returns
    ``list[OCRResult]``) so the adapter's serializer is exercised end-to-end.
    """

    def __init__(self) -> None:
        self.calls: list[int] = []  # batch sizes
        self.predict_calls = 0

    def recognize_batch(self, images: list[np.ndarray], options=None) -> list[OCRResult]:
        self.predict_calls += 1
        self.calls.append(len(images))
        return [
            OCRResult(
                raw_text=f"text-{i}",
                pipeline_type="OCR",
                text_blocks=[TextBlock(text=f"text-{i}", score=0.9, bbox=(0.0, 0.0, 1.0, 1.0))],
            )
            for i in range(len(images))
        ]


def _make_items(*labels: str) -> list[InputItem]:
    return [
        InputItem(
            item_id=f"it-{i}",
            encoded_bytes=len(_png_bytes(lbl)),
            decoded_pixels=64,
            estimated_pages=1,
        )
        for i, lbl in enumerate(labels)
    ]


# Attach raw bytes via a small wrapper so the adapter can decode.
@dataclass(frozen=True)
class _BytesItem(InputItem.__class__ if False else object):  # type: ignore[misc]
    pass


class _RawItem:
    """Lightweight item carrying raw bytes plus the budget fields."""

    def __init__(self, item_id: str, raw: bytes) -> None:
        self.item_id = item_id
        self.data = raw
        self.encoded_bytes = len(raw)
        self.decoded_pixels = 64
        self.estimated_pages = 1


def _raw_items(*labels: str) -> list[_RawItem]:
    return [_RawItem(f"it-{i}", _png_bytes(lbl)) for i, lbl in enumerate(labels)]


# ---------------------------------------------------------------------------
# Capability honesty
# ---------------------------------------------------------------------------


def test_capability_reports_not_real_batch_without_registry() -> None:
    adapter = PaddlePipelineAdapter(service=_FakeOCRService(), pipeline_name="PP-StructureV3")
    cap = adapter.capabilities()
    # Without the real pipeline registry (not importable in unit tests) we
    # conservatively report not-real-batch.
    assert cap.real_batch is False
    assert cap.max_compute_batch == 1


def test_capability_cached_after_first_call() -> None:
    adapter = PaddlePipelineAdapter(service=_FakeOCRService(), pipeline_name="OCR")
    cap1 = adapter.capabilities()
    cap2 = adapter.capabilities()
    assert cap1 is cap2


# ---------------------------------------------------------------------------
# recognize_many
# ---------------------------------------------------------------------------


def test_recognize_many_preserves_order() -> None:
    service = _FakeOCRService()
    adapter = PaddlePipelineAdapter(service=service, pipeline_name="OCR")
    items = _raw_items("a", "b", "c")
    results = adapter.recognize_many(items)
    # The fake returns OCRResult(raw_text="text-N"); the serializer surfaces
    # it as the structured `raw_text` key (not the old broken `text`+repr).
    assert [r["raw_text"] for r in results] == ["text-0", "text-1", "text-2"]
    # And text_blocks survived serialization as a list of dicts.
    assert all(isinstance(r["text_blocks"], list) and r["text_blocks"] for r in results)


def test_single_image_is_one_element_batch() -> None:
    service = _FakeOCRService()
    adapter = PaddlePipelineAdapter(service=service, pipeline_name="OCR")
    results = adapter.recognize_many(_raw_items("only"))
    assert len(results) == 1
    assert service.calls == [1]


def test_recognize_many_empty_returns_empty() -> None:
    adapter = PaddlePipelineAdapter(service=_FakeOCRService(), pipeline_name="OCR")
    assert adapter.recognize_many([]) == []


def test_recognize_many_uses_one_predict_call_for_batch() -> None:
    service = _FakeOCRService()
    adapter = PaddlePipelineAdapter(service=service, pipeline_name="OCR")
    adapter.recognize_many(_raw_items("a", "b", "c", "d"))
    assert service.predict_calls == 1
    assert service.calls == [4]


def test_recognize_many_raises_on_missing_raw_bytes() -> None:
    adapter = PaddlePipelineAdapter(service=_FakeOCRService(), pipeline_name="OCR")
    # Plain InputItem has no .data attribute.
    plain = InputItem(item_id="x", encoded_bytes=10, decoded_pixels=10, estimated_pages=1)
    with pytest.raises(ValueError, match="no raw bytes"):
        adapter.recognize_many([plain])


def test_recognize_many_releases_residency_lease() -> None:
    from vibeocr.supervisor.inference.residency import ResidencyManager

    t = [0.0]
    rm = ResidencyManager(default_ttl_seconds=100, clock=lambda: t[0])
    service = _FakeOCRService()
    adapter = PaddlePipelineAdapter(service=service, pipeline_name="OCR", residency=rm)
    adapter.recognize_many(_raw_items("a"))
    status = adapter.residency_status()
    entry = next(e for e in status.entries if e.pipeline == "OCR")
    # Lease was released after the call.
    assert entry.active_leases == 0


def test_result_payload_passes_through_dict_results() -> None:
    class _DictService:
        def recognize_batch(self, images, options=None):
            return [{"text": "raw"} for _ in images]

    adapter = PaddlePipelineAdapter(service=_DictService(), pipeline_name="OCR")
    results = adapter.recognize_many(_raw_items("a"))
    assert results == [{"text": "raw"}]
