"""ResidencyManager: TTL, pin, LRU eviction under VRAM pressure.

Plan §3/§4 Phase 3 invariants:

* Active leases are never evicted.
* Ordinary TTL is the *maximum* idle residency; VRAM pressure may evict idle
  models earlier via LRU.
* Hard pin is explicit; pinning requires a capacity check and a typed error
  on conflict.
* Status reports remaining TTL, estimated VRAM and eviction reason.
* MinerU idle release stops the API subprocess (disk models are not deleted).

Deterministic: uses an injected fake clock.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from vibeocr.protocol.v2 import (
    ErrorCode,
    EvictionReason,
    PipelineSpec,
    ResidencyEntry,
    ResidencyKind,
    ResidencyStatus,
)

Clock = Callable[[], float]


class PinCapacityConflict(Exception):
    """Raised when pinning would exceed capacity."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = ErrorCode.PIN_CAPACITY_CONFLICT


@dataclass(slots=True)
class _Model:
    pipeline: str
    kind: ResidencyKind
    last_used_at: float
    loaded_at: float
    ttl_seconds: int | None  # None = inherit default
    active_leases: int = 0
    estimated_vram_mb: int = 0
    eviction_reason: EvictionReason = EvictionReason.NONE


@dataclass
class ResidencyManager:
    """Tracks loaded models and applies TTL/LRU/pin policy."""

    default_ttl_seconds: int = 300
    capacity_vram_mb: int = 0
    clock: Clock = field(default_factory=lambda: _monotonic)
    _models: dict[str, _Model] = field(default_factory=dict, repr=False)
    _overrides: dict[str, PipelineSpec] = field(default_factory=dict, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ------------------------------------------------------------------
    # Lease lifecycle
    # ------------------------------------------------------------------

    def lease(self, pipeline: str, *, estimated_vram_mb: int = 0) -> None:
        """Acquire (or reload) a model and mark it active."""
        with self._lock:
            now = self.clock()
            model = self._models.get(pipeline)
            if model is None:
                model = _Model(
                    pipeline=pipeline,
                    kind=ResidencyKind.SOFT_TTL,
                    last_used_at=now,
                    loaded_at=now,
                    ttl_seconds=self._override_ttl(pipeline),
                    estimated_vram_mb=estimated_vram_mb,
                )
                self._models[pipeline] = model
            model.active_leases += 1
            model.last_used_at = now
            model.eviction_reason = EvictionReason.NONE
            if model.kind is ResidencyKind.IDLE:
                model.kind = (
                    ResidencyKind.PINNED if self._is_pinned(pipeline) else ResidencyKind.SOFT_TTL
                )

    def release(self, pipeline: str) -> None:
        with self._lock:
            model = self._models.get(pipeline)
            if model is None:
                return
            if model.active_leases > 0:
                model.active_leases -= 1
            model.last_used_at = self.clock()

    def touch(self, pipeline: str) -> None:
        with self._lock:
            model = self._models.get(pipeline)
            if model is not None:
                model.last_used_at = self.clock()

    # ------------------------------------------------------------------
    # Configure (pin / TTL)
    # ------------------------------------------------------------------

    def configure(self, *, default_ttl_seconds: int | None = None, pipelines: list[PipelineSpec] | None = None) -> None:
        with self._lock:
            if default_ttl_seconds is not None:
                self.default_ttl_seconds = default_ttl_seconds
            if pipelines is not None:
                self._overrides = {p.name: p for p in pipelines}
                # Apply pin transitions.
                for spec in pipelines:
                    model = self._models.get(spec.name)
                    if model is None:
                        continue
                    if spec.pinned and model.kind is not ResidencyKind.PINNED:
                        self._check_capacity(model)
                        model.kind = ResidencyKind.PINNED
                    elif not spec.pinned and model.kind is ResidencyKind.PINNED and model.active_leases == 0:
                        model.kind = ResidencyKind.SOFT_TTL

    def pin(self, pipeline: str) -> None:
        with self._lock:
            model = self._models.get(pipeline)
            if model is None:
                # Pinning a not-yet-loaded model: record override; capacity
                # is checked on load.
                self._overrides[pipeline] = PipelineSpec(
                    name=pipeline, ttl_seconds=self._override_ttl(pipeline), pinned=True
                )
                return
            self._check_capacity(model)
            model.kind = ResidencyKind.PINNED
            self._overrides[pipeline] = PipelineSpec(
                name=pipeline, ttl_seconds=model.ttl_seconds, pinned=True
            )

    # ------------------------------------------------------------------
    # Eviction
    # ------------------------------------------------------------------

    def evict_expired(self) -> list[str]:
        """Evict models whose TTL has elapsed (only idle, non-pinned)."""
        evicted: list[str] = []
        with self._lock:
            now = self.clock()
            for pipeline, model in list(self._models.items()):
                if model.active_leases > 0:
                    continue
                if model.kind is ResidencyKind.PINNED:
                    continue
                ttl = model.ttl_seconds if model.ttl_seconds is not None else self.default_ttl_seconds
                if ttl is None:
                    continue
                if now - model.last_used_at >= ttl:
                    model.kind = ResidencyKind.EVICTED
                    model.eviction_reason = EvictionReason.TTL_EXPIRED
                    evicted.append(pipeline)
            return evicted

    def apply_vram_pressure(self, needed_mb: int) -> list[str]:
        """Evict idle LRU models to free ``needed_mb`` of VRAM.

        Never evicts active-lease or pinned models.
        """
        freed = 0
        evicted: list[str] = []
        with self._lock:
            candidates = sorted(
                (
                    m
                    for m in self._models.values()
                    if m.active_leases == 0 and m.kind not in (ResidencyKind.PINNED, ResidencyKind.EVICTED)
                ),
                key=lambda m: m.last_used_at,
            )
            for model in candidates:
                if freed >= needed_mb:
                    break
                model.kind = ResidencyKind.EVICTED
                model.eviction_reason = EvictionReason.VRAM_PRESSURE
                freed += max(0, model.estimated_vram_mb)
                evicted.append(model.pipeline)
        return evicted

    def release_idle(self, pipeline: str | None = None) -> list[str]:
        """Explicitly release idle models (MinerU stops its subprocess)."""
        released: list[str] = []
        with self._lock:
            targets = (
                [pipeline] if pipeline is not None else list(self._models.keys())
            )
            for name in targets:
                model = self._models.get(name)
                if model is None or model.active_leases > 0:
                    continue
                if model.kind is ResidencyKind.PINNED:
                    continue
                model.kind = ResidencyKind.EVICTED
                model.eviction_reason = EvictionReason.EXPLICIT_RELEASE
                released.append(name)
        return released

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> ResidencyStatus:
        with self._lock:
            entries = tuple(self._entry_for(m) for m in self._models.values())
            pipelines = tuple(self._overrides.values())
            used = sum(m.estimated_vram_mb for m in self._models.values() if m.kind is not ResidencyKind.EVICTED)
            return ResidencyStatus(
                default_ttl_seconds=self.default_ttl_seconds,
                entries=entries,
                pipelines=pipelines,
                vram_total_mb=self.capacity_vram_mb or None,
                vram_used_mb=used,
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _entry_for(self, model: _Model) -> ResidencyEntry:
        ttl = model.ttl_seconds if model.ttl_seconds is not None else self.default_ttl_seconds
        if model.kind in (ResidencyKind.PINNED,):
            remaining = None
        elif model.kind is ResidencyKind.EVICTED:
            remaining = 0
        elif ttl is None:
            remaining = None
        else:
            remaining = max(0, int(ttl - (self.clock() - model.last_used_at)))
        return ResidencyEntry(
            pipeline=model.pipeline,
            kind=model.kind,
            active_leases=model.active_leases,
            remaining_ttl_seconds=remaining,
            estimated_vram_mb=model.estimated_vram_mb or None,
            eviction_reason=model.eviction_reason,
        )

    def _override_ttl(self, pipeline: str) -> int | None:
        spec = self._overrides.get(pipeline)
        if spec is not None and spec.ttl_seconds is not None:
            return spec.ttl_seconds
        return None

    def _is_pinned(self, pipeline: str) -> bool:
        spec = self._overrides.get(pipeline)
        return bool(spec and spec.pinned)

    def _check_capacity(self, model: _Model) -> None:
        if self.capacity_vram_mb <= 0:
            return
        used = sum(
            m.estimated_vram_mb
            for m in self._models.values()
            if m.kind is not ResidencyKind.EVICTED
        )
        if used + model.estimated_vram_mb > self.capacity_vram_mb:
            raise PinCapacityConflict(
                f"pinning {model.pipeline} would exceed VRAM capacity "
                f"({used + model.estimated_vram_mb} > {self.capacity_vram_mb})"
            )


def _monotonic() -> float:
    import time

    return time.monotonic()


__all__ = ["Clock", "PinCapacityConflict", "ResidencyManager"]
