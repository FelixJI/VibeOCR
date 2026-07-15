"""Process-wide PySide BackendSession ownership tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

import vibeocr.client.session as session


class _FakeClient:
    def __init__(self) -> None:
        self.start_calls: list[dict] = []
        self.shutdown_calls = 0

    def start(self, **kwargs) -> None:
        self.start_calls.append(kwargs)

    def shutdown(self) -> None:
        self.shutdown_calls += 1


@pytest.fixture(autouse=True)
def _reset_session(monkeypatch):
    session.shutdown_backend_client()
    created: list[_FakeClient] = []

    def factory():
        client = _FakeClient()
        created.append(client)
        return client

    monkeypatch.setattr(session, "_client_factory", factory)
    yield created
    session.shutdown_backend_client()


def test_all_callers_share_one_production_worker(_reset_session) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(lambda _: session.get_backend_client(), range(32)))

    assert len({id(client) for client in clients}) == 1
    assert len(_reset_session) == 1
    assert _reset_session[0].start_calls == [
        {"profile": "production", "frontend_id": "pyside"}
    ]


def test_restart_shuts_down_old_worker_before_replacement(_reset_session) -> None:
    old = session.get_backend_client()
    new = session.restart_backend_client()
    assert new is not old
    assert old.shutdown_calls == 1
    assert len(_reset_session) == 2
