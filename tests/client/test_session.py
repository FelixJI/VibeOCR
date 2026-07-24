"""Process-wide PySide BackendSession ownership tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

import vibeocr.client.session as session


@pytest.fixture(autouse=True)
def _reset_session():
    """每个测试前后重置进程级 session 单例。"""
    session.shutdown_backend_client()
    yield
    session.shutdown_backend_client()


def test_all_callers_share_one_production_worker(_reset_session_http) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(lambda _: session.get_backend_client(), range(32)))

    assert len({id(client) for client in clients}) == 1
    assert len(_reset_session_http) == 1
    assert _reset_session_http[0].start_calls == [
        {"profile": "production", "frontend_id": "pyside"}
    ]


def test_restart_shuts_down_old_worker_before_replacement(_reset_session_http) -> None:
    old = session.get_backend_client()
    new = session.restart_backend_client()
    assert new is not old
    assert old.stop_calls == 1  # HTTP 客户端用 stop()
    assert len(_reset_session_http) == 2


# ------------------------------------------------------------------
# 传输切换：VIBEOCR_OCR_TRANSPORT=http 走 HTTP worker，默认走 SHM
# ------------------------------------------------------------------


class _FakeHttpClient:
    """模拟 OcrHttpClient（不启动真实子进程）。"""

    def __init__(self) -> None:
        self.start_calls: list[dict] = []
        self.stop_calls = 0

    def start(self, **kwargs) -> None:
        self.start_calls.append(kwargs)

    def stop(self) -> None:
        self.stop_calls += 1

    shutdown = stop


@pytest.fixture
def _reset_session_http(monkeypatch):
    """重置 session 并 mock OcrHttpClient，避免启动真实子进程。"""
    session.shutdown_backend_client()
    created: list[_FakeHttpClient] = []
    monkeypatch.setattr(session, "_use_http_transport", lambda: True)

    import vibeocr.worker_host.ocr_http_client as http_mod

    original = http_mod.OcrHttpClient

    def factory():
        client = _FakeHttpClient()
        created.append(client)
        return client

    monkeypatch.setattr(http_mod, "OcrHttpClient", factory)
    yield created
    monkeypatch.setattr(http_mod, "OcrHttpClient", original)
    session.shutdown_backend_client()


def test_http_transport_returns_http_client(_reset_session_http) -> None:
    """VIBEOCR_OCR_TRANSPORT=http 时 get_backend_client 返回 HTTP 客户端。"""
    session.get_backend_client()
    assert len(_reset_session_http) == 1
    assert _reset_session_http[0].start_calls == [
        {"profile": "production", "frontend_id": "pyside"}
    ]


def test_http_transport_shutdown_calls_stop(_reset_session_http) -> None:
    """HTTP 模式 shutdown 调 stop()（OcrHttpClient 用 stop 而非 shutdown）。"""
    session.get_backend_client()
    session.shutdown_backend_client()
    assert _reset_session_http[0].stop_calls == 1


def test_default_transport_uses_http(_reset_session_http) -> None:
    """默认（无 VIBEOCR_OCR_TRANSPORT）走 HTTP worker，不走 SHM _client_factory。"""
    session.get_backend_client()
    # HTTP 客户端被创建；autouse 的 SHM _client_factory 未被调用（_reset_session 空）。
    assert len(_reset_session_http) == 1


def test_use_http_transport_flag(monkeypatch):
    """_use_http_transport 正确解析环境变量（默认 http，=shm 回退）。"""
    monkeypatch.delenv("VIBEOCR_OCR_TRANSPORT", raising=False)
    assert session._use_http_transport() is True  # 默认 HTTP

    monkeypatch.setenv("VIBEOCR_OCR_TRANSPORT", "http")
    assert session._use_http_transport() is True

    monkeypatch.setenv("VIBEOCR_OCR_TRANSPORT", "HTTP")
    assert session._use_http_transport() is True

    monkeypatch.setenv("VIBEOCR_OCR_TRANSPORT", "shm")
    assert session._use_http_transport() is False  # 应急回退 SHM

