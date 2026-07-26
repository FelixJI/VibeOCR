"""OCR 批次预算的纯函数边界测试。"""

from __future__ import annotations

from vibeocr.core.batch_budget import BatchBudget, BatchEntry, partition_batches
from vibeocr.core.constants import Constants


def _values(entries, budget):
    return [chunk.values for chunk in partition_batches(entries, budget)]


def test_ocr_default_pixel_budget_is_64m() -> None:
    assert BatchBudget.ocr_default().max_pixels == 64_000_000
    assert Constants.OCR_BATCH_MAX_PIXELS == 64_000_000


def test_ocr_default_a4_300dpi_pages_fit_seven_per_chunk() -> None:
    entries = [
        BatchEntry(value=index, encoded_bytes=1, pixels=8_700_000)
        for index in range(8)
    ]

    assert [len(chunk.entries) for chunk in partition_batches(entries, BatchBudget.ocr_default())] == [7, 1]


def test_item_limit_and_order_are_stable():
    entries = [BatchEntry(value=index, encoded_bytes=1, pixels=1) for index in range(5)]
    budget = BatchBudget(max_items=2, max_encoded_bytes=100, max_pixels=100)

    assert _values(entries, budget) == [[0, 1], [2, 3], [4]]


def test_encoded_byte_and_pixel_limits_are_independent():
    budget = BatchBudget(max_items=10, max_encoded_bytes=5, max_pixels=100)
    byte_entries = [
        BatchEntry(value="a", encoded_bytes=3, pixels=10),
        BatchEntry(value="b", encoded_bytes=2, pixels=10),
        BatchEntry(value="c", encoded_bytes=1, pixels=10),
    ]
    assert _values(byte_entries, budget) == [["a", "b"], ["c"]]

    pixel_entries = [
        BatchEntry(value="a", encoded_bytes=1, pixels=60),
        BatchEntry(value="b", encoded_bytes=1, pixels=50),
        BatchEntry(value="c", encoded_bytes=1, pixels=10),
    ]
    assert _values(pixel_entries, budget) == [["a"], ["b", "c"]]


def test_unknown_pixels_fall_back_to_item_and_byte_limits():
    entries = [
        BatchEntry(value="a", encoded_bytes=3, pixels=None),
        BatchEntry(value="b", encoded_bytes=3, pixels=None),
    ]
    budget = BatchBudget(max_items=10, max_encoded_bytes=5, max_pixels=1)

    assert _values(entries, budget) == [["a"], ["b"]]


def test_oversized_single_always_enters_one_batch():
    entries = [
        BatchEntry(value="huge", encoded_bytes=101, pixels=101),
        BatchEntry(value="small", encoded_bytes=1, pixels=1),
    ]
    budget = BatchBudget(max_items=2, max_encoded_bytes=10, max_pixels=10)

    chunks = partition_batches(entries, budget)

    assert [chunk.values for chunk in chunks] == [["huge"], ["small"]]
    assert chunks[0].oversized_single is True
    assert chunks[1].oversized_single is False
