"""Deterministic tests for ResidencyManager: TTL, pin, LRU, capacity."""

from __future__ import annotations

import pytest

from vibeocr.protocol.v2 import (
    ErrorCode,
    EvictionReason,
    PipelineSpec,
    ResidencyKind,
)
from vibeocr.supervisor.inference.residency import PinCapacityConflict, ResidencyManager


def test_lease_and_release_marks_active() -> None:
    t = [0.0]
    rm = ResidencyManager(default_ttl_seconds=300, clock=lambda: t[0])
    rm.lease("OCR")
    status = rm.status()
    entry = next(e for e in status.entries if e.pipeline == "OCR")
    assert entry.active_leases == 1
    rm.release("OCR")
    entry = next(e for e in rm.status().entries if e.pipeline == "OCR")
    assert entry.active_leases == 0


def test_ttl_evicts_only_idle_non_pinned() -> None:
    t = [0.0]
    rm = ResidencyManager(default_ttl_seconds=100, clock=lambda: t[0])
    rm.lease("OCR")
    rm.release("OCR")
    t[0] = 200  # past TTL
    evicted = rm.evict_expired()
    assert evicted == ["OCR"]
    entry = next(e for e in rm.status().entries if e.pipeline == "OCR")
    assert entry.kind is ResidencyKind.EVICTED
    assert entry.eviction_reason is EvictionReason.TTL_EXPIRED


def test_active_lease_not_evicted_by_ttl() -> None:
    t = [0.0]
    rm = ResidencyManager(default_ttl_seconds=100, clock=lambda: t[0])
    rm.lease("OCR")  # still active
    t[0] = 1000
    assert rm.evict_expired() == []
    entry = next(e for e in rm.status().entries if e.pipeline == "OCR")
    assert entry.active_leases == 1


def test_pinned_model_not_evicted_by_ttl_or_pressure() -> None:
    t = [0.0]
    rm = ResidencyManager(default_ttl_seconds=100, capacity_vram_mb=10000, clock=lambda: t[0])
    rm.lease("MinerU", estimated_vram_mb=2000)
    rm.release("MinerU")
    rm.pin("MinerU")
    t[0] = 1000
    assert rm.evict_expired() == []
    assert rm.apply_vram_pressure(needed_mb=9000) == []


def test_lru_eviction_under_vram_pressure() -> None:
    t = [0.0]
    rm = ResidencyManager(default_ttl_seconds=10000, capacity_vram_mb=10000, clock=lambda: t[0])
    rm.lease("OCR", estimated_vram_mb=2000)
    rm.release("OCR")
    t[0] = 5.0
    rm.lease("MinerU", estimated_vram_mb=2000)
    rm.release("MinerU")
    # OCR was used earlier (LRU), so it should be evicted first.
    evicted = rm.apply_vram_pressure(needed_mb=2000)
    assert evicted == ["OCR"]


def test_pin_capacity_conflict_raises_typed_error() -> None:
    t = [0.0]
    rm = ResidencyManager(default_ttl_seconds=100, capacity_vram_mb=3000, clock=lambda: t[0])
    rm.lease("OCR", estimated_vram_mb=2000)
    rm.release("OCR")
    rm.lease("MinerU", estimated_vram_mb=2000)
    rm.release("MinerU")
    with pytest.raises(PinCapacityConflict) as exc_info:
        rm.pin("MinerU")
    assert exc_info.value.code is ErrorCode.PIN_CAPACITY_CONFLICT


def test_per_pipeline_ttl_override() -> None:
    t = [0.0]
    rm = ResidencyManager(default_ttl_seconds=1000, clock=lambda: t[0])
    rm.configure(pipelines=[PipelineSpec(name="MinerU", ttl_seconds=50, pinned=False)])
    rm.lease("MinerU")
    rm.release("MinerU")
    t[0] = 60  # past the 50s override but below default
    assert rm.evict_expired() == ["MinerU"]


def test_release_idle_marks_explicit_release() -> None:
    t = [0.0]
    rm = ResidencyManager(default_ttl_seconds=1000, clock=lambda: t[0])
    rm.lease("OCR")
    rm.release("OCR")
    released = rm.release_idle()
    assert released == ["OCR"]
    entry = next(e for e in rm.status().entries if e.pipeline == "OCR")
    assert entry.eviction_reason is EvictionReason.EXPLICIT_RELEASE


def test_release_idle_skips_active_and_pinned() -> None:
    t = [0.0]
    rm = ResidencyManager(default_ttl_seconds=1000, capacity_vram_mb=10000, clock=lambda: t[0])
    rm.lease("OCR")  # active
    rm.lease("MinerU", estimated_vram_mb=1000)
    rm.release("MinerU")
    rm.pin("MinerU")
    released = rm.release_idle()
    assert released == []


def test_status_reports_remaining_ttl_and_vram() -> None:
    t = [0.0]
    rm = ResidencyManager(default_ttl_seconds=300, capacity_vram_mb=24000, clock=lambda: t[0])
    rm.lease("OCR", estimated_vram_mb=1200)
    status = rm.status()
    entry = next(e for e in status.entries if e.pipeline == "OCR")
    assert entry.remaining_ttl_seconds is not None
    assert 0 <= entry.remaining_ttl_seconds <= 300
    assert status.vram_used_mb == 1200
    assert status.vram_total_mb == 24000


def test_configure_unpins_when_active_zero() -> None:
    t = [0.0]
    rm = ResidencyManager(default_ttl_seconds=1000, capacity_vram_mb=10000, clock=lambda: t[0])
    rm.lease("OCR", estimated_vram_mb=1000)
    rm.release("OCR")
    rm.pin("OCR")
    rm.configure(pipelines=[PipelineSpec(name="OCR", ttl_seconds=None, pinned=False)])
    entry = next(e for e in rm.status().entries if e.pipeline == "OCR")
    assert entry.kind is not ResidencyKind.PINNED
