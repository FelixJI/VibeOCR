"""Semantic regression tests for per-pipeline runtime TTL handling."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from vibeocr.services.pipeline_cache_manager import PipelineCacheManager

_RESTORE_LAST_USED_KEY = "__vibeocr_restore_last_used_unix_ms__"


def _manager(
    pipelines: dict[str, object],
    ttls: dict[str, int],
    *,
    max_heavy: int = 2,
) -> tuple[PipelineCacheManager, SimpleNamespace]:
    service = SimpleNamespace(_pipelines=dict(pipelines))
    manager = PipelineCacheManager.__new__(PipelineCacheManager)
    manager._service = service
    manager._ttls = dict(ttls)
    manager._last_used = {}
    manager._active_counts = {}
    manager._state_lock = threading.RLock()
    manager._max_heavy = max_heavy
    manager._tick_interval = 30.0
    manager._stop_event = threading.Event()
    manager._wakeup_event = threading.Event()
    manager._thread = None
    return manager, service


def test_finite_ttl_starts_when_active_lease_finishes(monkeypatch) -> None:
    manager, service = _manager({"OCR": object()}, {"OCR": 60})
    clock = [1000.0]
    monkeypatch.setattr(
        "vibeocr.services.pipeline_cache_manager.time.time", lambda: clock[0]
    )

    with manager.lease("OCR"):
        clock[0] = 1100.0
        # The request may run longer than the TTL; an active pipeline is never
        # deleted underneath inference.
        assert manager.evict_idle(now=1100.0) == []
        assert "OCR" in service._pipelines

    assert manager.get_last_used("OCR") == 1100.0
    assert manager.evict_idle(now=1159.999) == []
    assert manager.evict_idle(now=1160.0) == ["OCR"]


def test_missing_last_used_begins_a_finite_lease_instead_of_immediate_eviction() -> None:
    manager, service = _manager({"OCR": object()}, {"OCR": 60})

    assert manager.evict_idle(now=1000.0) == []
    assert manager.get_last_used("OCR") == 1000.0
    assert manager.evict_idle(now=1059.999) == []
    assert manager.evict_idle(now=1060.0) == ["OCR"]
    assert "OCR" not in service._pipelines


def test_switching_persistent_model_to_finite_ttl_uses_existing_idle_age(
    monkeypatch,
) -> None:
    manager, service = _manager({"OCR": object()}, {"OCR": 0})
    manager.touch("OCR", now=1000.0)
    monkeypatch.setattr(
        "vibeocr.services.pipeline_cache_manager.time.time", lambda: 2000.0
    )

    manager.ttls = {"OCR": 60}

    # Changing policy does not pretend the model was just used.  It has already
    # been idle longer than the new finite TTL and is therefore immediately due.
    assert manager.evict_idle(now=2000.0) == ["OCR"]
    assert "OCR" not in service._pipelines


def test_restart_restore_keeps_original_finite_ttl_deadline(monkeypatch) -> None:
    manager, service = _manager({"OCR": object()}, {"OCR": 0})
    monkeypatch.setattr(
        "vibeocr.services.pipeline_cache_manager.time.time", lambda: 1000.0
    )

    manager.ttls = {
        "OCR": 60,
        _RESTORE_LAST_USED_KEY: {"OCR": 950_000},
    }

    assert manager.get_last_used("OCR") == 950.0
    assert manager.evict_idle(now=1009.999) == []
    assert manager.evict_idle(now=1010.0) == ["OCR"]
    assert "OCR" not in service._pipelines


def test_capacity_pressure_is_an_explicit_exception_to_ttl_residency() -> None:
    manager, service = _manager(
        {"PP-StructureV3": object(), "PaddleOCR-VL": object()},
        {"PP-StructureV3": 0, "PaddleOCR-VL": 0},
        max_heavy=1,
    )
    manager.touch("PP-StructureV3", now=1000.0)
    manager.touch("PaddleOCR-VL", now=2000.0)

    evicted = manager.enforce_capacity("PaddleOCR-VL")

    # TTL=0 prevents idle-time eviction, not an impossible overcommit.  Under
    # the one-heavy-model memory budget, FIFO keeps the newly requested model.
    assert evicted == ["PP-StructureV3"]
    assert set(service._pipelines) == {"PaddleOCR-VL"}


def test_explicit_release_overrides_any_ttl() -> None:
    manager, service = _manager(
        {"OCR": object(), "PP-StructureV3": object()},
        {"OCR": 0, "PP-StructureV3": 1800},
    )

    assert manager.release(heavy_only=False) == ["OCR", "PP-StructureV3"]
    assert service._pipelines == {}
