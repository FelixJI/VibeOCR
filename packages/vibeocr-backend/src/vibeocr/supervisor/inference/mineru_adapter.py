"""MinerUProcessAdapter: supervisor-owned MinerU API subprocess.

Plan §4 Phase 5 goals addressed by this seam:

* Supervisor owns the MinerU API subprocess lifecycle (start/health/stop).
* ``recognize_many`` issues ONE budgeted multi-file ``/file_parse`` request
  with unique internal stems; results map back to stable input order.
* Default backend does NOT promise cross-document compute batching — the
  capability reports ``real_batch=False`` so metrics can distinguish HTTP
  batching from compute batching.
* Idle release stops the API subprocess (disk models are not deleted).
* Cancel: cooperative stop of subsequent chunks; exclusive MinerU jobs may
  escalate to hard termination after a grace period.

The actual HTTP call to mineru-api and result parsing reuse
``services/mineru_service.py``; this adapter is the ownership seam and the
unique-stem mapper.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .budgets import AdapterCapability, InputItem

if TYPE_CHECKING:
    from collections.abc import Callable

    from vibeocr.protocol.v2 import ResidencyStatus

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def unique_stem(original: str, index: int) -> str:
    """Build a unique, filesystem-safe stem for one input in a batch.

    Duplicate stems are disambiguated by an index + short random token so a
    single multi-file ``/file_parse`` request cannot collide on result keys.
    """
    p = Path(original or "")
    name = p.stem
    ext = p.suffix
    cleaned = _SAFE_RE.sub("_", name).strip("._-") or "input"
    cleaned = cleaned[:48]
    return f"{index:04d}-{cleaned}-{uuid.uuid4().hex[:6]}{ext}"


class _MinerUClientLike(Protocol):
    """Minimal slice of the MinerU API client we depend on."""

    def file_parse(self, files: list[tuple[str, bytes]], **kwargs: Any) -> Any: ...


class _MinerULifecycle(Protocol):
    """Owns the mineru-api subprocess start/stop.

    ``MinerUService`` is a singleton whose ``__init__`` blocks until the API is
    up, so in production the lifecycle just ensures the singleton exists (start)
    and tears it down (stop). Tests inject a no-op lifecycle or leave it None.
    """

    def start(self) -> None: ...

    def stop(self) -> None: ...


@dataclass
class MinerUProcessAdapter:
    """Supervisor-owned MinerU adapter."""

    client_factory: Callable[[], _MinerUClientLike]
    backend: str = "hybrid-engine"
    # Default does NOT promise cross-document compute batching.
    capability: AdapterCapability = field(
        default_factory=lambda: AdapterCapability(
            name="MinerU", real_batch=False, max_compute_batch=1
        )
    )
    # Optional real subprocess lifecycle. When None the adapter falls back to a
    # flag-flip (kept for unit tests that inject a fake client_factory without a
    # backing process). Production wiring injects a lifecycle over MinerUService.
    lifecycle: _MinerULifecycle | None = None
    _process_started: bool = False

    def capabilities(self) -> AdapterCapability:
        return self.capability

    # ------------------------------------------------------------------
    # Process lifecycle (ownership seam)
    # ------------------------------------------------------------------

    def ensure_started(self) -> None:
        if self._process_started:
            return
        # When a real lifecycle is injected, drive the mineru-api subprocess
        # through it; otherwise this is a no-op flag for test fakes.
        if self.lifecycle is not None:
            self.lifecycle.start()
        self._process_started = True

    def stop(self) -> None:
        """Stop the MinerU API subprocess; disk models are NOT deleted."""
        if self.lifecycle is not None and self._process_started:
            try:
                self.lifecycle.stop()
            except Exception:  # pragma: no cover - defensive
                pass
        self._process_started = False

    # ------------------------------------------------------------------
    # recognize_many — budgeted multi-file request
    # ------------------------------------------------------------------

    def recognize_many(
        self,
        items: list[InputItem],
        *,
        options: Any | None = None,
    ) -> list[dict[str, Any]]:
        if not items:
            return []
        self.ensure_started()
        client = self.client_factory()
        # Build unique-stem file list preserving input order.
        files: list[tuple[str, bytes]] = []
        stem_to_index: dict[str, int] = {}
        for idx, item in enumerate(items):
            raw = getattr(item, "data", b"")
            if not isinstance(raw, (bytes, bytearray)) or len(raw) == 0:
                raise ValueError(
                    f"InputItem {item.item_id} has no raw bytes for MinerU upload"
                )
            display = getattr(item, "display_name", None) or f"input-{idx}"
            stem = unique_stem(display, idx)
            stem_to_index[stem] = idx
            files.append((stem, bytes(raw)))
        raw_results = client.file_parse(files, backend=self.backend)
        return self._map_results_back(raw_results, stem_to_index, len(items))

    @staticmethod
    def _map_results_back(
        raw_results: Any, stem_to_index: dict[str, int], expected: int
    ) -> list[dict[str, Any]]:
        """Restore stable input order from a MinerU multi-file result dict.

        MinerU returns results keyed by filename; we map each stem back to its
        original index. Missing stems yield an empty dict (the caller decides
        whether to mark the item failed).
        """
        out: list[dict[str, Any]] = [{} for _ in range(expected)]
        if isinstance(raw_results, dict):
            for key, value in raw_results.items():
                idx = stem_to_index.get(key)
                if idx is not None:
                    payload = value if isinstance(value, dict) else {"raw": value}
                    out[idx] = payload
        elif isinstance(raw_results, list):
            # If the API returned a list in the same order, use positional mapping.
            for i, value in enumerate(raw_results):
                if i < expected:
                    out[i] = value if isinstance(value, dict) else {"raw": value}
        return out

    # ------------------------------------------------------------------
    # Residency passthrough (idle release stops the subprocess)
    # ------------------------------------------------------------------

    def residency_status(self) -> ResidencyStatus:
        from vibeocr.protocol.v2 import ResidencyEntry, ResidencyKind
        from vibeocr.protocol.v2 import ResidencyStatus as _RS

        kind = ResidencyKind.SOFT_TTL if self._process_started else ResidencyKind.EVICTED
        return _RS(
            default_ttl_seconds=300,
            entries=(ResidencyEntry(pipeline="MinerU", kind=kind),),
        )

    def release_idle(self, pipeline: str | None = None) -> ResidencyStatus:
        if pipeline is None or pipeline == "MinerU":
            self.stop()
        return self.residency_status()


__all__ = ["MinerUProcessAdapter", "unique_stem"]
