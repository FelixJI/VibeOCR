"""Worker restart regression tests for persistent and finite TTL leases."""

from __future__ import annotations

from typing import TYPE_CHECKING

import vibeocr.services.worker_runtime_state as runtime_state

if TYPE_CHECKING:
    from typing import Any


class _FakeMinerUCache:
    def __init__(self) -> None:
        self.ttl = 0
        self.release_calls = 0

    def set_ttl(self, ttl: int) -> None:
        self.ttl = ttl

    def status(self) -> dict[str, object]:
        return {
            "loaded": False,
            "ttl_seconds": self.ttl,
            "last_used_unix_ms": None,
        }

    def release(self) -> bool:
        self.release_calls += 1
        return False


class _FakeWorkerBase:
    def __init__(self, clock: list[float]) -> None:
        self.worker_id = 0
        self.clock = clock
        self.loaded: set[str] = set()
        self.last_used: dict[str, float] = {}
        self.ttls: dict[str, int] = {}
        self.preload_calls: list[list[str]] = []
        self.warmup_calls: list[list[str]] = []
        self.start_calls = 0

    @property
    def is_ready(self) -> bool:
        return True

    def start(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.start_calls += 1
        # A new child process starts with an empty in-memory cache.
        self.loaded.clear()
        self.last_used.clear()

    def set_ttls(self, pipeline_ttls: dict[str, int], *args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        self.ttls = dict(pipeline_ttls)
        return True

    def cache_status(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        del args, kwargs
        return {
            "pipeline_ttls": dict(self.ttls),
            "max_heavy": 2,
            "loaded_pipelines": sorted(self.loaded),
            "last_used_unix_ms": {
                name: int(self.last_used.get(name, self.clock[0]) * 1000)
                for name in self.loaded
            },
        }

    def recognize(
        self, image_data: bytes, options: dict[str, object], *args: Any, **kwargs: Any
    ) -> str:
        del image_data, args, kwargs
        name = str(options.get("pipeline", "OCR"))
        self.loaded.add(name)
        self.last_used[name] = self.clock[0]
        return "ok"

    def recognize_batch(
        self,
        images: list[bytes],
        options: dict[str, object],
        *args: Any,
        **kwargs: Any,
    ) -> list[str]:
        del images, args, kwargs
        name = str(options.get("pipeline", "OCR"))
        self.loaded.add(name)
        self.last_used[name] = self.clock[0]
        return ["ok"]

    def preload_pipelines(
        self, pipelines: list[str], *args: Any, **kwargs: Any
    ) -> dict[str, bool]:
        del args, kwargs
        names = [str(name) for name in pipelines]
        self.preload_calls.append(names)
        self.loaded.update(names)
        for name in names:
            self.last_used[name] = self.clock[0]
        return dict.fromkeys(names, True)

    def warmup_pipelines(
        self, pipelines: list[str], *args: Any, **kwargs: Any
    ) -> dict[str, bool]:
        del args, kwargs
        names = [str(name) for name in pipelines]
        self.warmup_calls.append(names)
        return dict.fromkeys(names, True)

    def release_pipelines(
        self, heavy_only: bool = True, *args: Any, **kwargs: Any
    ) -> list[str]:
        del heavy_only, args, kwargs
        released = sorted(self.loaded)
        self.loaded.clear()
        self.last_used.clear()
        return released


def _patched_worker(clock: list[float]) -> _FakeWorkerBase:
    class FakeWorker(_FakeWorkerBase):
        pass

    runtime_state.install_ocr_worker_runtime_state_patch(FakeWorker)
    return FakeWorker(clock)


def _install_test_doubles(monkeypatch, clock: list[float]):
    mineru = _FakeMinerUCache()
    monkeypatch.setattr(runtime_state.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        "vibeocr.services.mineru_runtime_cache.get_mineru_runtime_cache",
        lambda: mineru,
    )
    restored: list[tuple[dict[str, int], dict[str, float]]] = []

    def send_state(worker, pipeline_ttls, last_used):
        worker.ttls = dict(pipeline_ttls)
        worker.last_used = dict(last_used)
        restored.append((dict(pipeline_ttls), dict(last_used)))
        return True

    monkeypatch.setattr(runtime_state, "_send_state_payload", send_state)
    return mineru, restored


def test_restart_restores_persistent_and_unexpired_finite_leases(monkeypatch) -> None:
    clock = [1000.0]
    mineru, restored = _install_test_doubles(monkeypatch, clock)
    worker = _patched_worker(clock)

    worker.set_ttls({"OCR": 0, "PP-StructureV3": 60, "MinerU": 300})
    worker.recognize(b"ocr", {"pipeline": "OCR"})
    worker.recognize(b"structure", {"pipeline": "PP-StructureV3"})

    clock[0] = 1030.0
    worker.start()

    assert worker.preload_calls == [["OCR", "PP-StructureV3"]]
    assert worker.warmup_calls == [["OCR", "PP-StructureV3"]]
    assert restored == [
        (
            {"OCR": 0, "PP-StructureV3": 60, "MinerU": 300},
            {"OCR": 1000.0, "PP-StructureV3": 1000.0},
        )
    ]
    assert mineru.ttl == 300


def test_restart_does_not_revive_an_expired_finite_lease(monkeypatch) -> None:
    clock = [1000.0]
    _mineru, restored = _install_test_doubles(monkeypatch, clock)
    worker = _patched_worker(clock)

    worker.set_ttls({"OCR": 0, "PP-StructureV3": 60})
    worker.recognize(b"ocr", {"pipeline": "OCR"})
    worker.recognize(b"structure", {"pipeline": "PP-StructureV3"})

    clock[0] = 1061.0
    worker.start()

    assert worker.preload_calls == [["OCR"]]
    assert worker.warmup_calls == [["OCR"]]
    assert restored[0][1] == {"OCR": 1000.0}


def test_explicit_release_removes_model_from_restart_snapshot(monkeypatch) -> None:
    clock = [1000.0]
    mineru, restored = _install_test_doubles(monkeypatch, clock)
    worker = _patched_worker(clock)

    worker.set_ttls({"OCR": 0})
    worker.recognize(b"ocr", {"pipeline": "OCR"})
    assert worker.release_pipelines(heavy_only=False) == ["OCR"]

    clock[0] = 1100.0
    worker.start()

    assert worker.preload_calls == []
    assert restored == [({"OCR": 0}, {})]
    assert mineru.release_calls == 1
