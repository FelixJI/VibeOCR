"""Regression tests for MinerU's separate-process TTL implementation."""

from __future__ import annotations

import sys
import time
from types import ModuleType

from vibeocr.services.mineru_runtime_cache import MinerURuntimeCache


class _FakeProcess:
    def __init__(self) -> None:
        self.running = True

    def poll(self):
        return None if self.running else 0



def _install_fake_mineru(monkeypatch, *, loaded: bool = True):
    module = ModuleType("vibeocr.services.mineru_service")

    class FakeMinerUService:
        _api_process = _FakeProcess() if loaded else None
        init_calls = 0
        shutdown_calls = 0

        def __init__(self) -> None:
            type(self).init_calls += 1

        def parse(self, *args, **kwargs):
            del args, kwargs
            return "ok"

        def shutdown(self) -> None:
            type(self).shutdown_calls += 1
            process = type(self)._api_process
            if process is not None:
                process.running = False
            type(self)._api_process = None

    module.MinerUService = FakeMinerUService
    monkeypatch.setitem(sys.modules, "vibeocr.services.mineru_service", module)
    return FakeMinerUService


def test_status_does_not_start_mineru(monkeypatch) -> None:
    service_cls = _install_fake_mineru(monkeypatch, loaded=False)
    runtime = MinerURuntimeCache()
    try:
        status = runtime.status()
        assert status["loaded"] is False
        assert service_cls.init_calls == 0
    finally:
        runtime.close()


def test_explicit_release_waits_for_active_request(monkeypatch) -> None:
    service_cls = _install_fake_mineru(monkeypatch)
    runtime = MinerURuntimeCache()
    try:
        with runtime.lease():
            assert runtime.release() is True
            assert service_cls._api_process is not None
            assert service_cls.shutdown_calls == 0

        assert service_cls._api_process is None
        assert service_cls.shutdown_calls == 1
    finally:
        runtime.close()


def test_finite_ttl_stops_mineru_after_completed_use(monkeypatch) -> None:
    service_cls = _install_fake_mineru(monkeypatch)
    runtime = MinerURuntimeCache()
    try:
        runtime.set_ttl(1)
        assert service_cls().parse(b"data", "application/pdf") == "ok"
        assert runtime.status()["loaded"] is True

        deadline = time.monotonic() + 2.5
        while service_cls._api_process is not None and time.monotonic() < deadline:
            time.sleep(0.02)

        assert service_cls._api_process is None
        assert service_cls.shutdown_calls == 1
    finally:
        runtime.close()


def test_persistent_ttl_never_triggers_idle_shutdown(monkeypatch) -> None:
    service_cls = _install_fake_mineru(monkeypatch)
    runtime = MinerURuntimeCache()
    try:
        runtime.set_ttl(0)
        assert service_cls().parse(b"data", "application/pdf") == "ok"
        time.sleep(0.1)
        assert service_cls._api_process is not None
        assert service_cls.shutdown_calls == 0
    finally:
        runtime.release()
        runtime.close()
