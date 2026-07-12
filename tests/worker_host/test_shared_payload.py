"""Tests for shared-memory payload transfer (Task 1.4 Green).

The SharedPayloadStore moves large binary blobs (images, PDFs, generated
symbols) through Windows shared memory instead of base64-encoding them into
JSON control frames. Ownership rules:

- The creator (``owner``) is responsible for unlinking the segment.
- Readers ``release`` (close their view) but never unlink.
- A TTL-based orphan sweep reaps segments past ``expires_unix_ms`` on startup
  and shutdown, surviving peer crashes.

Windows-only integration creates real file-mapping objects; the descriptor
shape and validation are unit-tested everywhere.
"""

from __future__ import annotations

import hashlib
import os
import time

import pytest

from tests.worker_host.test_security import win32_only
from vibeocr.worker_host.shared_payload import (
    SharedPayloadError,
    SharedPayloadRef,
    SharedPayloadStore,
)

IS_WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------------
# SharedPayloadRef: descriptor shape and validation
# ---------------------------------------------------------------------------


def _good_descriptor(**overrides: object) -> dict:
    base = {
        "name": "Local\\VibeOCR-00000000-0000-4000-8000-000000000000-00000000-0000-4000-8000-000000000001",
        "size": 5,
        "media_type": "text/plain",
        "sha256": hashlib.sha256(b"hello").hexdigest(),
        "owner": "client",
        "expires_unix_ms": int(time.time() * 1000) + 60_000,
    }
    base.update(overrides)
    return base


def test_ref_from_descriptor_round_trip() -> None:
    ref = SharedPayloadRef.from_descriptor(_good_descriptor())
    assert ref.owner == "client"
    d = ref.to_descriptor()
    assert d["sha256"] == hashlib.sha256(b"hello").hexdigest()


def test_ref_rejects_unknown_owner() -> None:
    with pytest.raises(SharedPayloadError, match="owner"):
        SharedPayloadRef.from_descriptor(_good_descriptor(owner="attacker"))


def test_ref_rejects_bad_sha() -> None:
    with pytest.raises(SharedPayloadError, match="sha256"):
        SharedPayloadRef.from_descriptor(_good_descriptor(sha256="tooshort"))


def test_ref_rejects_bad_name() -> None:
    with pytest.raises(SharedPayloadError, match="name"):
        SharedPayloadRef.from_descriptor(_good_descriptor(name="C:/evil.dll"))


def test_ref_rejects_negative_size() -> None:
    with pytest.raises(SharedPayloadError, match="size"):
        SharedPayloadRef.from_descriptor(_good_descriptor(size=-1))


def test_ref_rejects_extra_field() -> None:
    desc = _good_descriptor()
    desc["sneaky"] = True
    with pytest.raises(SharedPayloadError, match="unknown"):
        SharedPayloadRef.from_descriptor(desc)


# ---------------------------------------------------------------------------
# Store: round trip and ownership (Windows integration)
# ---------------------------------------------------------------------------


@win32_only
@pytest.mark.asyncio
async def test_put_read_release_round_trip() -> None:
    store = SharedPayloadStore(owner="client", ttl_seconds=60)
    data = b"hello shared memory"
    ref = await store.put(data, media_type="text/plain")
    assert ref.owner == "client"
    assert ref.size == len(data)
    got = await store.read(ref)
    assert got == data
    await store.release(ref)


@win32_only
@pytest.mark.asyncio
async def test_read_detects_sha_mismatch() -> None:
    store = SharedPayloadStore(owner="client", ttl_seconds=60)
    # Forge a ref whose sha256 does not match the segment contents.
    ref = await store.put(b"real data", media_type="text/plain")
    bad = SharedPayloadRef(
        name=ref.name,
        size=ref.size,
        media_type=ref.media_type,
        sha256=hashlib.sha256(b"different").hexdigest(),
        owner=ref.owner,
        expires_unix_ms=ref.expires_unix_ms,
    )
    with pytest.raises(SharedPayloadError, match="sha256"):
        await store.read(bad)
    await store.release(ref)


@win32_only
@pytest.mark.asyncio
async def test_read_rejects_out_of_bounds_size() -> None:
    store = SharedPayloadStore(owner="client", ttl_seconds=60)
    ref = await store.put(b"small", media_type="text/plain")
    oversized = SharedPayloadRef(
        name=ref.name,
        size=10_000_000,
        media_type=ref.media_type,
        sha256=hashlib.sha256(b"0" * 10_000_000).hexdigest(),
        owner=ref.owner,
        expires_unix_ms=ref.expires_unix_ms,
    )
    # The descriptor claims a size far larger than the segment; MapViewOfFile
    # refuses to map beyond the backing object, so read fails safely rather
    # than returning garbage. This is the bounds-protection guarantee.
    with pytest.raises(SharedPayloadError):
        await store.read(oversized)
    await store.release(ref)


@win32_only
@pytest.mark.asyncio
async def test_only_owner_unlinks_reader_just_releases() -> None:
    # A reader-side store (owner != the segment owner) must NOT unlink on release.
    owner_store = SharedPayloadStore(owner="worker", ttl_seconds=60)
    reader_store = SharedPayloadStore(owner="client", ttl_seconds=60)
    ref = await owner_store.put(b"from worker", media_type="image/png")
    # Reader reads then releases — segment must still be readable by owner.
    data = await reader_store.read(ref)
    assert data == b"from worker"
    await reader_store.release(ref)
    again = await owner_store.read(ref)
    assert again == b"from worker"
    # Now owner unlinks.
    await owner_store.release(ref)


@win32_only
@pytest.mark.asyncio
async def test_double_release_is_idempotent() -> None:
    store = SharedPayloadStore(owner="client", ttl_seconds=60)
    ref = await store.put(b"once", media_type="text/plain")
    await store.release(ref)
    # Second release must not raise.
    await store.release(ref)


@win32_only
@pytest.mark.asyncio
async def test_twenty_cycles_leave_no_leaked_segments() -> None:
    # Verify requirement: 20 create/read/release, zero leaked segments.
    store = SharedPayloadStore(owner="client", ttl_seconds=60)
    live_before = store.count_segments()
    for i in range(20):
        data = f"payload-{i}".encode()
        ref = await store.put(data, media_type="text/plain")
        assert await store.read(ref) == data
        await store.release(ref)
    live_after = store.count_segments()
    assert live_after == live_before, f"leaked {live_after - live_before} segments"


@win32_only
@pytest.mark.asyncio
async def test_ttl_orphan_sweep_reaps_expired_segments() -> None:
    # A segment past its TTL must be reaped by sweep_orphans.
    store = SharedPayloadStore(owner="client", ttl_seconds=60)
    ref = await store.put(b"expires soon", media_type="text/plain", ttl_seconds=0)
    reaped = await store.sweep_orphans()
    assert reaped >= 1
    # After sweep, reading the expired ref should fail (segment gone).
    with pytest.raises(SharedPayloadError):
        await store.read(ref)


@win32_only
@pytest.mark.asyncio
async def test_sweep_does_not_reap_in_flight_segments() -> None:
    # Regression: a segment within its TTL must survive sweep_orphans.
    store = SharedPayloadStore(owner="client", ttl_seconds=60)
    ref = await store.put(b"still valid", media_type="text/plain", ttl_seconds=60)
    reaped = await store.sweep_orphans()
    assert reaped == 0
    data = await store.read(ref)
    assert data == b"still valid"
    await store.release(ref)


@win32_only
@pytest.mark.asyncio
async def test_shutdown_sweeps_namespace() -> None:
    store = SharedPayloadStore(owner="client", ttl_seconds=60)
    ref = await store.put(b"orphan on crash", media_type="text/plain")
    # Simulate peer crash: do NOT release, just shutdown.
    await store.shutdown()
    # The segment must be gone.
    with pytest.raises(SharedPayloadError):
        await store.read(ref)
