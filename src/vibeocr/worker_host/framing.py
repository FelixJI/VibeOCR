"""Length-prefixed JSON framing for the WorkerHost control channel.

Frame format::

    +--------------------+--------------------------+
    | uint32 LE length   | UTF-8 JSON body (bytes)  |
    +--------------------+--------------------------+

Control frames are capped at 8 MiB by default to bound memory. The framing
layer is bytes-only; it does not parse JSON (that is the caller's job via
``contracts.envelope_from_json_bytes``). This keeps framing reusable and makes
invalid-UTF-8 and invalid-JSON two distinct, locatable failure modes.

The reader/writer protocols mirror asyncio.StreamReader / StreamWriter so the
real Named Pipe transport (Task 1.3) and test fakes share one interface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

DEFAULT_MAX_BYTES = 8 << 20  # 8 MiB control-frame cap
_LEN_PREFIX = 4  # uint32 little-endian


class FramingError(Exception):
    """Base class for framing errors."""


class FrameTooLargeError(FramingError):
    """A frame's declared length exceeds the configured cap."""


class StreamTruncatedError(FramingError):
    """The stream ended before a full frame (length prefix or body) arrived."""


@runtime_checkable
class AsyncByteReader(Protocol):
    """Minimal async reader protocol (subset of asyncio.StreamReader)."""

    async def readexactly(self, n: int) -> bytes: ...


@runtime_checkable
class AsyncByteWriter(Protocol):
    """Minimal async writer protocol (subset of asyncio.StreamWriter)."""

    async def write(self, data: bytes) -> int: ...


async def read_frame(
    reader: AsyncByteReader, *, max_bytes: int = DEFAULT_MAX_BYTES
) -> bytes:
    """Read exactly one length-prefixed frame.

    Args:
        reader: anything with an awaitable ``readexactly(n)`` (e.g. asyncio
            StreamReader or a test fake).
        max_bytes: maximum allowed body length; default 8 MiB.

    Returns:
        The frame body bytes (without the length prefix).

    Raises:
        StreamTruncatedError: the stream closed mid-frame.
        FrameTooLargeError: the declared length exceeds ``max_bytes``.
    """
    if max_bytes < 0:
        raise ValueError(f"max_bytes must be non-negative, got {max_bytes}")
    try:
        prefix = await reader.readexactly(_LEN_PREFIX)
    except StreamTruncatedError:
        raise
    except Exception as exc:  # asyncio.IncompleteReadError and friends
        if _is_incomplete_read(exc):
            raise StreamTruncatedError("stream closed while reading length prefix") from exc
        raise
    (length,) = _decode_prefix(prefix)
    if length > max_bytes:
        raise FrameTooLargeError(
            f"frame length {length} exceeds cap {max_bytes}"
        )
    if length == 0:
        return b""
    try:
        body = await reader.readexactly(length)
    except Exception as exc:
        if _is_incomplete_read(exc):
            raise StreamTruncatedError(
                f"stream closed while reading {length}-byte body"
            ) from exc
        raise
    return body


async def write_frame(
    writer: AsyncByteWriter,
    payload: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> None:
    """Write one length-prefixed frame.

    Args:
        writer: anything with an awaitable ``write(data)``.
        payload: raw frame body bytes (caller serializes JSON).
        max_bytes: cap used to refuse oversized frames.

    Raises:
        TypeError: ``payload`` is not bytes.
        FrameTooLargeError: payload exceeds ``max_bytes``.
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError(f"payload must be bytes, got {type(payload).__name__}")
    payload = bytes(payload)
    if len(payload) > max_bytes:
        raise FrameTooLargeError(
            f"frame length {len(payload)} exceeds cap {max_bytes}"
        )
    prefix = _encode_prefix(len(payload))
    await writer.write(prefix + payload)


def _encode_prefix(length: int) -> bytes:
    if length < 0 or length >= (1 << 32):
        raise ValueError(f"length out of uint32 range: {length}")
    return length.to_bytes(_LEN_PREFIX, "little")


def _decode_prefix(prefix: bytes) -> tuple[int]:
    return (int.from_bytes(prefix, "little"),)


def _is_incomplete_read(exc: Exception) -> bool:
    """Best-effort detection of asyncio.IncompleteReadError without importing it."""
    import asyncio

    if isinstance(exc, asyncio.IncompleteReadError):
        return True
    # Some transports raise ConnectionError / EOFError on closed streams.
    return isinstance(exc, (ConnectionError,))


__all__ = [
    "DEFAULT_MAX_BYTES",
    "AsyncByteReader",
    "AsyncByteWriter",
    "FrameTooLargeError",
    "FramingError",
    "StreamTruncatedError",
    "read_frame",
    "write_frame",
]
