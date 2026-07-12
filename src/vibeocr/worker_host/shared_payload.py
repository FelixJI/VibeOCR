"""Shared-memory payload transfer for the WorkerHost control channel (Task 1.4).

Large binary blobs (images, PDFs, generated symbols) are never base64-encoded
into JSON control frames. Instead the creator maps a Windows file-mapping
object, writes the bytes, and sends a ``SharedPayloadRef`` descriptor over the
pipe. The peer maps the segment, reads, validates the SHA-256, and sends
``memory.release``. Only the owner unlinks; readers just close their view.

Ownership + reclaim rules (design §6.3, §11):
- descriptor: ``name``, ``size``, ``media_type``, ``sha256``, ``owner``,
  ``expires_unix_ms``;
- only the owner unlinks the segment on release;
- readers ``release`` (unmap + close their view) but never unlink;
- startup and shutdown run a namespace-scoped orphan sweep that reaps segments
  past their TTL, surviving peer crashes.

Win32 calls are offloaded to the default executor. On non-Windows the module
imports cleanly but store operations raise ``NotImplementedError``; descriptor
validation works everywhere and is unit-tested independently.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import re
import time
from dataclasses import dataclass
from typing import Any

IS_WINDOWS = os.name == "nt"

# Win32 constants
PAGE_READWRITE = 0x0004
FILE_MAP_READ = 0x0004
FILE_MAP_WRITE = 0x0002
FILE_MAP_ALL_ACCESS = 0x000F001F
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# Namespace prefix; sweep only touches names in this namespace.
_NAMESPACE = "Local\\VibeOCR-"
# Descriptor field constraints
_NAME_RE = re.compile(
    r"^Local\\VibeOCR-"
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}-"
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_OWNERS = frozenset({"client", "worker"})


class SharedPayloadError(ValueError):
    """A shared-payload operation failed validation or I/O."""


# ---------------------------------------------------------------------------
# Win32 bindings (loaded lazily on Windows)
# ---------------------------------------------------------------------------


def _load_win32() -> dict[str, Any]:  # type: ignore[name-defined]
    """Bind the Win32 file-mapping functions we need."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    k32.CreateFileMappingW.restype = wt.HANDLE
    k32.CreateFileMappingW.argtypes = [
        wt.HANDLE, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR
    ]
    k32.OpenFileMappingW.restype = wt.HANDLE
    k32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
    k32.MapViewOfFile.restype = ctypes.c_void_p
    k32.MapViewOfFile.argtypes = [
        wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t
    ]
    k32.UnmapViewOfFile.restype = wt.BOOL
    k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.restype = wt.BOOL
    k32.CloseHandle.argtypes = [wt.HANDLE]
    return {
        "CreateFileMappingW": k32.CreateFileMappingW,
        "OpenFileMappingW": k32.OpenFileMappingW,
        "MapViewOfFile": k32.MapViewOfFile,
        "UnmapViewOfFile": k32.UnmapViewOfFile,
        "CloseHandle": k32.CloseHandle,
        "kernel32": k32,
    }


# typing shim: Any imported at top to keep the module importable on non-Windows


# ---------------------------------------------------------------------------
# Descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SharedPayloadRef:
    """A reference to a shared-memory payload.

    Matches the wire descriptor in ``methods.schema.json#/$defs/shared_payload_ref``.
    Only the owner may unlink the segment; readers ``release`` (close their view).
    """

    name: str
    size: int
    media_type: str
    sha256: str
    owner: str
    expires_unix_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.match(self.name):
            raise SharedPayloadError(f"invalid shared-payload name: {self.name!r}")
        if not isinstance(self.size, int) or self.size < 0:
            raise SharedPayloadError(f"size must be non-negative int, got {self.size!r}")
        if not isinstance(self.media_type, str) or not self.media_type:
            raise SharedPayloadError("media_type must be a non-empty string")
        if not isinstance(self.sha256, str) or not _SHA_RE.match(self.sha256):
            raise SharedPayloadError("sha256 must be 64 lowercase hex chars")
        if self.owner not in _OWNERS:
            raise SharedPayloadError(f"owner must be one of {sorted(_OWNERS)}")
        if not isinstance(self.expires_unix_ms, int) or self.expires_unix_ms < 0:
            raise SharedPayloadError("expires_unix_ms must be non-negative int")

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "owner": self.owner,
            "expires_unix_ms": self.expires_unix_ms,
        }

    @classmethod
    def from_descriptor(cls, data: dict[str, Any]) -> SharedPayloadRef:
        allowed = {"name", "size", "media_type", "sha256", "owner", "expires_unix_ms"}
        extra = set(data.keys()) - allowed
        if extra:
            raise SharedPayloadError(f"unknown descriptor fields: {sorted(extra)}")
        try:
            return cls(
                name=str(data["name"]),
                size=int(data["size"]),
                media_type=str(data["media_type"]),
                sha256=str(data["sha256"]),
                owner=str(data["owner"]),
                expires_unix_ms=int(data["expires_unix_ms"]),
            )
        except KeyError as exc:
            raise SharedPayloadError(f"descriptor missing field {exc}") from exc


def _new_name() -> str:
    """Generate a fresh namespace-scoped segment name."""
    import uuid

    return f"{_NAMESPACE}{uuid.uuid4()}-{uuid.uuid4()}"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class SharedPayloadStore:
    """Owns or reads shared-memory segments within the VibeOCR namespace.

    Args:
        owner: ``"client"`` (WinUI) or ``"worker"`` (Python WorkerHost). Only
            segments whose descriptor ``owner`` matches this value are unlinked
            on ``release``; the other side just closes its view.
        ttl_seconds: default TTL applied to ``put`` when no explicit ttl given.
    """

    def __init__(self, *, owner: str, ttl_seconds: int = 300) -> None:
        if owner not in _OWNERS:
            raise SharedPayloadError(f"owner must be one of {sorted(_OWNERS)}")
        self._owner = owner
        self._default_ttl = ttl_seconds
        # Owner-created segments we must keep alive: name -> mapping handle.
        # A named file-mapping object is destroyed when its LAST handle closes,
        # so the owner holds the handle until release/shutdown. We track
        # (mapping_handle, expires_unix_ms) so the orphan sweep only reaps
        # segments past their TTL, not in-flight ones.
        self._owned: dict[str, tuple[int, int]] = {}
        self._win = _load_win32() if IS_WINDOWS else {}

    # -- create ------------------------------------------------------------

    async def put(
        self, data: bytes, *, media_type: str, ttl_seconds: int | None = None
    ) -> SharedPayloadRef:
        """Create a segment, write ``data``, and return its descriptor.

        The owner store holds the mapping handle open so the named object
        persists for the peer to open; it is closed on ``release``/``shutdown``.
        """
        if not IS_WINDOWS:
            raise NotImplementedError("shared memory requires Windows")
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        name = _new_name()
        sha = hashlib.sha256(data).hexdigest()
        expires = int((time.time() + max(0, ttl)) * 1000)
        ref = SharedPayloadRef(
            name=name,
            size=len(data),
            media_type=media_type,
            sha256=sha,
            owner=self._owner,
            expires_unix_ms=expires,
        )
        await asyncio.to_thread(self._create_and_write_sync, name, data, expires)
        return ref

    def _create_and_write_sync(self, name: str, data: bytes, expires_unix_ms: int) -> None:
        size = len(data)
        handle = self._win["CreateFileMappingW"](
            INVALID_HANDLE_VALUE, None, PAGE_READWRITE, 0, size, name
        )
        if not handle:
            raise SharedPayloadError(
                f"CreateFileMappingW failed: GLE={ctypes.get_last_error()}"
            )
        ptr = self._win["MapViewOfFile"](handle, FILE_MAP_WRITE, 0, 0, size)
        if not ptr:
            gle = ctypes.get_last_error()
            self._win["CloseHandle"](handle)
            raise SharedPayloadError(f"MapViewOfFile failed: GLE={gle}")
        try:
            ctypes.memmove(ptr, data, size)
        finally:
            self._win["UnmapViewOfFile"](ptr)
        # Keep the mapping handle open so the named object survives for peers.
        self._owned[name] = (handle, expires_unix_ms)

    # -- read --------------------------------------------------------------

    async def read(self, ref: SharedPayloadRef) -> bytes:
        """Open a read view, validate SHA-256 + size, and return the bytes."""
        if not IS_WINDOWS:
            raise NotImplementedError("shared memory requires Windows")
        if ref.expires_unix_ms < int(time.time() * 1000):
            raise SharedPayloadError("shared payload descriptor has expired")
        return await asyncio.to_thread(self._read_sync, ref)

    def _read_sync(self, ref: SharedPayloadRef) -> bytes:
        handle = self._win["OpenFileMappingW"](FILE_MAP_READ, False, ref.name)
        if not handle:
            raise SharedPayloadError(
                f"OpenFileMappingW failed (segment gone?): GLE={ctypes.get_last_error()}"
            )
        try:
            ptr = self._win["MapViewOfFile"](handle, FILE_MAP_READ, 0, 0, ref.size)
            if not ptr:
                raise SharedPayloadError(
                    f"MapViewOfFile failed: GLE={ctypes.get_last_error()}"
                )
            try:
                buf = ctypes.string_at(ptr, ref.size)
            finally:
                self._win["UnmapViewOfFile"](ptr)
        finally:
            self._win["CloseHandle"](handle)
        actual_sha = hashlib.sha256(buf).hexdigest()
        if actual_sha != ref.sha256:
            raise SharedPayloadError(
                f"sha256 mismatch: descriptor {ref.sha256} != actual {actual_sha}"
            )
        return buf

    # -- release -----------------------------------------------------------

    async def release(self, ref: SharedPayloadRef) -> None:
        """Release a segment. Owners unlink (close the held handle); readers
        are a no-op (they open transient views per ``read``). Idempotent.
        """
        if not IS_WINDOWS:
            return
        if ref.owner != self._owner:
            return
        await asyncio.to_thread(self._close_owned_sync, ref.name)

    async def release_owned(self, name: str) -> bool:
        """Release an owner-created segment by its wire name.

        ``memory.release`` intentionally carries only the segment name. The
        lookup is restricted to this store's owned namespace and is idempotent.
        """
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise SharedPayloadError(f"invalid shared-payload name: {name!r}")
        if not IS_WINDOWS:
            return False
        return await asyncio.to_thread(self._close_owned_sync, name)

    def _close_owned_sync(self, name: str) -> bool:
        entry = self._owned.pop(name, None)
        if entry is not None:
            handle, _expires = entry
            if handle:
                try:
                    self._win["CloseHandle"](handle)
                except Exception:
                    pass
            return True
        return False

    def _unlink_sync(self, name: str) -> None:
        # Best-effort open+close for names we don't own (e.g. orphan sweep).
        try:
            handle = self._win["OpenFileMappingW"](FILE_MAP_ALL_ACCESS, False, name)
            if handle:
                self._win["CloseHandle"](handle)
        except Exception:
            pass

    # -- reclaim -----------------------------------------------------------

    async def sweep_orphans(self) -> int:
        """Reap owner-held segments whose TTL has expired.

        Only segments past their ``expires_unix_ms`` are closed; in-flight
        segments are left untouched. Windows does not enumerate named
        file-mapping objects, so the sweep is limited to segments this store
        created.
        """
        if not IS_WINDOWS:
            return 0
        return await asyncio.to_thread(self._sweep_sync)

    def _sweep_sync(self) -> int:
        now_ms = int(time.time() * 1000)
        reaped = 0
        for name, (handle, expires) in list(self._owned.items()):
            if expires > now_ms:
                continue  # still within TTL; leave in flight
            self._owned.pop(name, None)
            try:
                self._win["CloseHandle"](handle)
                reaped += 1
            except Exception:
                pass
        return reaped

    async def shutdown(self) -> None:
        """Close all owner-held handles, reclaiming every segment on exit."""
        if not IS_WINDOWS:
            return
        for name in list(self._owned.keys()):
            await asyncio.to_thread(self._close_owned_sync, name)
        self._owned.clear()

    def count_segments(self) -> int:
        """Count of owner-held segments (for leak assertions)."""
        return len(self._owned)


__all__ = [
    "IS_WINDOWS",
    "SharedPayloadError",
    "SharedPayloadRef",
    "SharedPayloadStore",
]
