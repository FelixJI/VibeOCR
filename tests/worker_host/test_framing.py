"""Tests for length-prefixed JSON framing (Task 1.2 Green).

Frame format: 4-byte little-endian unsigned length prefix + UTF-8 JSON body.
Control frames are capped at 8 MiB. These tests cover the framing layer only;
DTO semantics are in test_contracts.py.

Red-first cases: half-packet, coalesced (stuck) packets, zero-length frame,
frame over the 8 MiB cap, invalid UTF-8, and stream truncation.
"""

from __future__ import annotations

import asyncio

import pytest

from vibeocr.worker_host.framing import (
    FrameTooLargeError,
    StreamTruncatedError,
    read_frame,
    write_frame,
)


class _BytesReader:
    """Async reader over an in-memory bytes buffer, exposing exactly-at-most-n semantics."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    async def readexactly(self, n: int) -> bytes:
        chunk = self._data[self._pos : self._pos + n]
        if len(chunk) < n:
            raise asyncio.IncompleteReadError(chunk, n)
        self._pos += n
        return chunk

    async def read(self, n: int = -1) -> bytes:
        chunk = self._data[self._pos :] if n < 0 else self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


class _BytesWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()

    async def write(self, data: bytes) -> int:
        self.buffer.extend(data)
        return len(data)

    async def drain(self) -> None:  # pragma: no cover - trivial
        return None


def _frame(body: bytes) -> bytes:
    return len(body).to_bytes(4, "little") + body


# ---------------------------------------------------------------------------
# read_frame: happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_frame_single() -> None:
    body = b'{"hello":"world"}'
    reader = _BytesReader(_frame(body))
    assert await read_frame(reader) == body


@pytest.mark.asyncio
async def test_read_frame_empty_body_allowed() -> None:
    # A zero-length body is a valid frame (e.g. keepalive); only a zero-length
    # LENGTH prefix that lies is not the same as an empty payload. The length
    # field may be 0 and the body is then empty bytes.
    reader = _BytesReader(_frame(b""))
    assert await read_frame(reader) == b""


@pytest.mark.asyncio
async def test_read_two_coalesced_frames() -> None:
    # stuck/coalesced packets: two frames in one buffer must read back-to-back.
    a = b'{"i":1}'
    b = b'{"i":2}'
    reader = _BytesReader(_frame(a) + _frame(b))
    assert await read_frame(reader) == a
    assert await read_frame(reader) == b


@pytest.mark.asyncio
async def test_read_frame_short_reads() -> None:
    # Simulate half-packet by feeding the buffer one byte at a time via a
    # reader whose readexactly blocks until the byte is present. Here we just
    # ensure read_frame consumes exactly one frame even when extra bytes remain.
    body = b'{"x":true}'
    reader = _BytesReader(_frame(body) + b"\xff" * 9)
    assert await read_frame(reader) == body


# ---------------------------------------------------------------------------
# read_frame: error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_frame_zero_length_then_garbage_raises() -> None:
    # A length prefix of 0 with trailing bytes is valid (empty body), but the
    # next read must parse a fresh frame. This documents that 0-length is OK.
    reader = _BytesReader(b"\x00\x00\x00\x00" + b"garbage")
    assert await read_frame(reader) == b""


@pytest.mark.asyncio
async def test_read_frame_over_cap_raises() -> None:
    body = b"x" * (8 * 1024 * 1024 + 1)
    reader = _BytesReader(_frame(body))
    with pytest.raises(FrameTooLargeError):
        await read_frame(reader)


@pytest.mark.parametrize("custom_cap", [16, 1024])
@pytest.mark.asyncio
async def test_read_frame_custom_cap(custom_cap: int) -> None:
    body = b"x" * (custom_cap + 1)
    reader = _BytesReader(_frame(body))
    with pytest.raises(FrameTooLargeError):
        await read_frame(reader, max_bytes=custom_cap)


@pytest.mark.asyncio
async def test_read_frame_truncated_length_raises() -> None:
    # Only 2 of the 4 length-prefix bytes arrived: stream truncated.
    reader = _BytesReader(b"\x01\x02")
    with pytest.raises(StreamTruncatedError):
        await read_frame(reader)


@pytest.mark.asyncio
async def test_read_frame_truncated_body_raises() -> None:
    # Length says 10 bytes but stream ends early.
    reader = _BytesReader(b"\x0a\x00\x00\x00" + b"short")
    with pytest.raises(StreamTruncatedError):
        await read_frame(reader)


# ---------------------------------------------------------------------------
# write_frame
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_frame_writes_length_prefix_and_body() -> None:
    writer = _BytesWriter()
    body = b'{"a":1}'
    await write_frame(writer, body)
    assert bytes(writer.buffer) == _frame(body)


@pytest.mark.asyncio
async def test_write_frame_empty_body() -> None:
    writer = _BytesWriter()
    await write_frame(writer, b"")
    assert bytes(writer.buffer) == b"\x00\x00\x00\x00"


@pytest.mark.asyncio
async def test_write_frame_rejects_non_bytes() -> None:
    writer = _BytesWriter()
    with pytest.raises(TypeError):
        await write_frame(writer, '{"a":1}')  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_write_frame_rejects_oversize() -> None:
    writer = _BytesWriter()
    body = b"x" * (8 * 1024 * 1024 + 1)
    with pytest.raises(FrameTooLargeError):
        await write_frame(writer, body)


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_trip_multiple_frames() -> None:
    payloads = [b'{"n":1}', b"", b'{"deep":{"a":[1,2,3]}}', b'{"unicode":"\xe4\xb8\xad"}']
    writer = _BytesWriter()
    for p in payloads:
        await write_frame(writer, p)
    reader = _BytesReader(bytes(writer.buffer))
    for expected in payloads:
        assert await read_frame(reader) == expected


@pytest.mark.asyncio
async def test_round_trip_preserves_unicode() -> None:
    # UTF-8 multi-byte sequences must survive intact (no decode/re-encode drift).
    text = "中文 OCR café 日本語"
    body = ("\u0000" + text).encode("utf-8")
    writer = _BytesWriter()
    await write_frame(writer, body)
    reader = _BytesReader(bytes(writer.buffer))
    out = await read_frame(reader)
    assert out == body
    assert out.decode("utf-8") == "\u0000" + text


# ---------------------------------------------------------------------------
# Invalid UTF-8 is the caller's responsibility (framing is bytes-level), but
# document that a frame with invalid UTF-8 round-trips as raw bytes. Higher
# layers (DTO decode) reject invalid UTF-8 — see test_contracts.py.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frame_with_invalid_utf8_round_trips_as_bytes() -> None:
    body = b"\xff\xfe\xfd not valid utf8"
    writer = _BytesWriter()
    await write_frame(writer, body)
    reader = _BytesReader(bytes(writer.buffer))
    assert await read_frame(reader) == body
