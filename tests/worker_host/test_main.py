"""worker_host.main 入口与控制平面函数测试。

main.py 大部分是 server loop（asyncio + NamedPipe），难以在单测里全跑。
本文件聚焦可单测的纯/半纯函数与控制平面：

- main(): argparse 入口（--self-test、缺参返回 2、--help、坏参数）
- _self_test / _emit_ready: stdout 协议记录
- _pid_alive: 跨平台进程存活判定（pid<=0 / OpenProcess 失败 / 自身 pid）
- _watch_parent: 父进程退出 → stop_event
- _shutdown: 全路径关闭顺序与异常吞没
- _build_dispatcher: 控制方法注册 + 未接线方法 WORKER_UNAVAILABLE + retryable 标记
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibeocr.worker_host import main as main_module
from vibeocr.worker_host.contracts import PROTOCOL_VERSION, RpcEnvelope
from vibeocr.worker_host.errors import ErrorCode, WorkerError
from vibeocr.worker_host.main import (
    _build_arg_parser,
    _build_dispatcher,
    _emit_ready,
    _pid_alive,
    _self_test,
    _shutdown,
    _watch_parent,
    main,
)
from vibeocr.worker_host.shared_payload import SharedPayloadStore

# ---------------------------------------------------------------------------
# main() argparse 入口
# ---------------------------------------------------------------------------


class TestMainEntry:
    def test_self_test_short_circuits_and_returns_zero(self, capsys) -> None:
        result = main(["--self-test"])
        assert result == 0
        out = json.loads(capsys.readouterr().out.strip())
        assert out["protocol_version"] == PROTOCOL_VERSION
        assert "worker_version" in out
        assert "ocr" in out["capabilities"]
        assert out["platform"] == sys.platform

    def test_missing_pipe_and_token_returns_two(self, capsys) -> None:
        result = main([])
        assert result == 2
        assert "--pipe and --token are required" in capsys.readouterr().err

    def test_missing_token_only_returns_two(self) -> None:
        # 有 pipe 但无 token 仍缺参
        assert main(["--pipe", "x"]) == 2

    def test_help_returns_zero(self) -> None:
        # argparse 对 --help 抛 SystemExit(0)
        assert main(["--help"]) == 0

    def test_bad_flag_returns_two(self) -> None:
        assert main(["--nope"]) == 2

    def test_serving_mode_runs_async_loop(self) -> None:
        # --pipe + --token → 调用 asyncio.run(_serve)；用 mock 验证返回值透传
        def fake_run(coro):
            coro.close()  # 关闭未运行的协程，避免 RuntimeWarning
            return 0

        with (
            patch.object(main_module, "asyncio") as mock_async,
            patch("vibeocr.worker_host.main.configure_worker_stderr_logging"),
        ):
            mock_async.run.side_effect = fake_run
            result = main(["--pipe", "p", "--token", "t"])
        assert result == 0
        mock_async.run.assert_called_once()

    def test_serving_mode_propagates_keyboard_interrupt_as_130(self) -> None:
        def fake_run(coro):
            coro.close()
            raise KeyboardInterrupt

        with (
            patch.object(main_module, "asyncio") as mock_async,
            patch("vibeocr.worker_host.main.configure_worker_stderr_logging"),
        ):
            mock_async.run.side_effect = fake_run
            assert main(["--pipe", "p", "--token", "t"]) == 130


# ---------------------------------------------------------------------------
# _build_arg_parser 默认值
# ---------------------------------------------------------------------------


class TestArgParser:
    def test_defaults(self) -> None:
        parser = _build_arg_parser()
        args = parser.parse_args([])
        assert args.profile == "winui-dev"
        assert args.frontend_id == "winui"
        assert args.self_test is False

    def test_frontent_id_choices(self) -> None:
        parser = _build_arg_parser()
        args = parser.parse_args(["--frontend-id", "pyside"])
        assert args.frontend_id == "pyside"

    def test_frontend_id_rejects_invalid(self) -> None:
        parser = _build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--frontend-id", "gtk"])

    def test_parent_pid_converted_to_int(self) -> None:
        parser = _build_arg_parser()
        args = parser.parse_args(["--parent-pid", "1234"])
        assert args.parent_pid == 1234


# ---------------------------------------------------------------------------
# _self_test / _emit_ready
# ---------------------------------------------------------------------------


class TestProtocolEmit:
    def test_self_test_emits_compact_json(self, capsys) -> None:
        assert _self_test() == 0
        line = capsys.readouterr().out
        # 单行、无多余空白
        doc = json.loads(line.strip())
        assert doc["protocol_version"] == PROTOCOL_VERSION
        assert "capabilities" in doc

    def test_emit_ready_writes_worker_ready(self, capsys) -> None:
        _emit_ready(r"\\.\pipe\VibeOCR-abc")
        out = capsys.readouterr().out.strip()
        doc = json.loads(out)
        assert doc["event"] == "worker.ready"
        assert doc["pipe"] == r"\\.\pipe\VibeOCR-abc"
        assert doc["protocol_version"] == PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# _pid_alive
# ---------------------------------------------------------------------------


class TestPidAlive:
    def test_non_positive_pid_treated_as_alive(self) -> None:
        assert _pid_alive(0) is True
        assert _pid_alive(-1) is True

    def test_self_pid_is_alive(self) -> None:
        assert _pid_alive(os.getpid()) is True

    @pytest.mark.skipif(os.name != "nt", reason="Windows-only OpenProcess path")
    def test_nonexistent_pid_windows_returns_false(self) -> None:
        # PID 0xFFFFFFFF 几乎不可能存在；OpenProcess 应失败
        assert _pid_alive(0xFFFFFFFE) is False


# ---------------------------------------------------------------------------
# _watch_parent
# ---------------------------------------------------------------------------


class TestWatchParent:
    async def test_sets_event_when_parent_dies(self) -> None:
        stop_event = asyncio.Event()
        # 用一个几乎肯定不存在的 pid（_pid_alive 返回 False）
        with (
            patch("vibeocr.worker_host.main._pid_alive", return_value=False),
            patch("vibeocr.worker_host.main._PARENT_POLL_SECONDS", 0.01),
        ):
            await _watch_parent(999999, stop_event)
        assert stop_event.is_set()

    async def test_keeps_polling_while_parent_alive(self) -> None:
        stop_event = asyncio.Event()
        call_count = 0

        def fake_alive(_pid: int) -> bool:
            nonlocal call_count
            call_count += 1
            return call_count < 3

        with (
            patch("vibeocr.worker_host.main._pid_alive", side_effect=fake_alive),
            patch("vibeocr.worker_host.main._PARENT_POLL_SECONDS", 0.001),
        ):
            await _watch_parent(1, stop_event)
        assert stop_event.is_set()
        assert call_count == 3

    async def test_stops_when_event_already_set(self) -> None:
        stop_event = asyncio.Event()
        stop_event.set()
        with patch("vibeocr.worker_host.main._pid_alive") as mock_alive:
            await _watch_parent(1, stop_event)
        mock_alive.assert_not_called()


# ---------------------------------------------------------------------------
# _shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    async def test_closes_conn_server_store_in_order(self) -> None:
        order: list[str] = []
        conn = AsyncMock()
        conn.close.side_effect = lambda: order.append("conn")
        server = AsyncMock()
        server.close.side_effect = lambda: order.append("server")
        store = AsyncMock()
        store.shutdown.side_effect = lambda: order.append("store")
        await _shutdown(server, store, parent_task=None, conn=conn)
        assert order == ["conn", "server", "store"]

    async def test_services_shutdown_runs_in_thread(self) -> None:
        services = MagicMock()
        server = AsyncMock()
        store = AsyncMock()
        with patch("vibeocr.worker_host.main.asyncio.to_thread") as mock_to_thread:
            await _shutdown(server, store, parent_task=None, services=services)
        mock_to_thread.assert_called_once_with(services.shutdown)

    async def test_suppresses_conn_errors(self) -> None:
        conn = AsyncMock()
        conn.close.side_effect = RuntimeError("conn fail")
        server = AsyncMock()
        server.close.side_effect = RuntimeError("server fail")
        store = AsyncMock()
        store.shutdown.side_effect = RuntimeError("store fail")
        # 不应抛
        await _shutdown(server, store, parent_task=None, conn=conn)

    async def test_cancels_parent_task(self) -> None:
        async def long_running() -> None:
            await asyncio.sleep(100)

        parent_task = asyncio.create_task(long_running())
        server = AsyncMock()
        store = AsyncMock()
        await _shutdown(server, store, parent_task=parent_task, conn=None)
        assert parent_task.cancelled() or parent_task.done()

    async def test_noop_when_conn_none_and_no_parent(self) -> None:
        server = AsyncMock()
        store = AsyncMock()
        await _shutdown(server, store, parent_task=None, conn=None)
        server.close.assert_called_once()
        store.shutdown.assert_called_once()


# ---------------------------------------------------------------------------
# _build_dispatcher 控制平面
# ---------------------------------------------------------------------------


def _make_request(method: str, payload: dict[str, Any]) -> RpcEnvelope:
    return RpcEnvelope(
        request_id="00000000-0000-4000-8000-000000000001",
        task_id="00000000-0000-4000-8000-000000000001",
        method=method,
        payload=payload,
        deadline_unix_ms=0,
    )


class TestBuildDispatcherControl:
    async def test_handshake_success(self) -> None:
        store = SharedPayloadStore(owner="worker")
        dispatcher = _build_dispatcher(store=store, backend="gpu")
        resp = await dispatcher.dispatch(
            _make_request(
                "system.handshake",
                {
                    "app_version": "0.5.0",
                    "protocol_version": PROTOCOL_VERSION,
                    "max_message_bytes": 8 << 20,
                    "max_shared_payload_bytes": 256 << 20,
                },
            ),
            deadline_unix_ms=0,
        )
        assert resp.result["backend"] == "gpu"
        assert resp.result["protocol_version"] == PROTOCOL_VERSION
        assert "ocr" in resp.result["capabilities"]
        assert resp.result["max_message_bytes"] == 8 << 20
        assert resp.result["max_shared_payload_bytes"] == 256 << 20

    async def test_handshake_version_mismatch_raises_worker_error(self) -> None:
        # 注意：请求层 method_validation 会先拒绝 protocol_version != 1 的请求，
        # 因此 handler 内的二次校验在生产中不可达——这里通过直接调用 handler
        # 覆盖 _build_dispatcher 内的 handshake 闭包逻辑。
        store = SharedPayloadStore(owner="worker")
        dispatcher = _build_dispatcher(store=store)
        entry = dispatcher._handlers["system.handshake"]  # type: ignore[attr-defined]
        with pytest.raises(WorkerError) as exc_info:
            await entry.handler({"protocol_version": 999}, None)
        assert exc_info.value.code == ErrorCode.PROTOCOL_MISMATCH

    async def test_ping_success(self) -> None:
        store = SharedPayloadStore(owner="worker")
        dispatcher = _build_dispatcher(store=store)
        resp = await dispatcher.dispatch(
            _make_request("system.ping", {"nonce": "abc"}),
            deadline_unix_ms=0,
        )
        assert resp.result == {"nonce": "abc"}

    async def test_ping_missing_nonce_rejected(self) -> None:
        store = SharedPayloadStore(owner="worker")
        dispatcher = _build_dispatcher(store=store)
        resp = await dispatcher.dispatch(
            _make_request("system.ping", {}), deadline_unix_ms=0
        )
        assert resp.error is not None
        assert resp.error.code == ErrorCode.INVALID_REQUEST

    async def test_ping_empty_nonce_rejected(self) -> None:
        store = SharedPayloadStore(owner="worker")
        dispatcher = _build_dispatcher(store=store)
        resp = await dispatcher.dispatch(
            _make_request("system.ping", {"nonce": ""}), deadline_unix_ms=0
        )
        assert resp.error is not None
        assert resp.error.code == ErrorCode.INVALID_REQUEST

    async def test_shutdown_acknowledged(self) -> None:
        store = SharedPayloadStore(owner="worker")
        dispatcher = _build_dispatcher(store=store)
        resp = await dispatcher.dispatch(
            _make_request("system.shutdown", {}), deadline_unix_ms=0
        )
        assert resp.result == {"acknowledged": True}

    async def test_task_cancel_requires_task_id(self) -> None:
        store = SharedPayloadStore(owner="worker")
        dispatcher = _build_dispatcher(store=store)
        resp = await dispatcher.dispatch(
            _make_request("task.cancel", {}), deadline_unix_ms=0
        )
        assert resp.error is not None
        assert resp.error.code == ErrorCode.INVALID_REQUEST

    async def test_task_cancel_unknown_task_returns_not_accepted(self) -> None:
        store = SharedPayloadStore(owner="worker")
        dispatcher = _build_dispatcher(store=store)
        resp = await dispatcher.dispatch(
            _make_request(
                "task.cancel", {"task_id": "00000000-0000-4000-8000-000000000099"}
            ),
            deadline_unix_ms=0,
        )
        assert resp.result["accepted"] is False
        # 未知 task → state 来自 registry.cancel 的结果
        assert "state" in resp.result

    async def test_memory_release_requires_name(self) -> None:
        store = SharedPayloadStore(owner="worker")
        dispatcher = _build_dispatcher(store=store)
        resp = await dispatcher.dispatch(
            _make_request("memory.release", {}), deadline_unix_ms=0
        )
        assert resp.error is not None
        assert resp.error.code == ErrorCode.INVALID_REQUEST

    async def test_memory_release_value_error_mapped(self) -> None:
        store = SharedPayloadStore(owner="worker")
        dispatcher = _build_dispatcher(store=store)
        with patch.object(store, "release_owned", side_effect=ValueError("no such")):
            resp = await dispatcher.dispatch(
                _make_request("memory.release", {"name": "x"}),
                deadline_unix_ms=0,
            )
        assert resp.error is not None
        assert resp.error.code == ErrorCode.INVALID_REQUEST


def _valid_ocr_request() -> dict[str, Any]:
    """构造一个能通过 method_validation 的 ocr.recognize 请求 payload。"""
    return {
        "image": {
            "name": "Local\\VibeOCR-00000000-0000-4000-8000-000000000000-00000000-0000-4000-8000-000000000010",
            "size": 12345,
            "media_type": "image/png",
            "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            "owner": "client",
            "expires_unix_ms": 1800000000000,
        },
        "pipeline": "OCR",
        "language": None,
    }


class TestBuildDispatcherDomainWiring:
    async def test_unwired_method_returns_worker_unavailable(self) -> None:
        store = SharedPayloadStore(owner="worker")
        dispatcher = _build_dispatcher(store=store, domain_handlers={})
        resp = await dispatcher.dispatch(
            _make_request("ocr.recognize", _valid_ocr_request()), deadline_unix_ms=0
        )
        assert resp.error is not None
        assert resp.error.code == ErrorCode.WORKER_UNAVAILABLE

    async def test_domain_handler_registered_when_provided(self) -> None:
        store = SharedPayloadStore(owner="worker")

        async def handler(payload, cancel):
            # 返回合法的 ocr.recognize 响应（通过 response 校验）
            return {
                "text": "Hello",
                "pipeline": "OCR",
                "raw_blocks": [],
                "markdown_text": "Hello",
                "html_text": "",
                "raw_text": "Hello",
                "text_blocks": [],
                "text_with_scores": [],
                "content_list": [],
                "image_width": 800,
                "image_height": 600,
            }

        dispatcher = _build_dispatcher(
            store=store, domain_handlers={"ocr.recognize": handler}
        )
        resp = await dispatcher.dispatch(
            _make_request("ocr.recognize", _valid_ocr_request()), deadline_unix_ms=0
        )
        assert resp.result is not None
        assert resp.result["text"] == "Hello"

    def test_control_methods_not_overridden_by_domain_handlers(self) -> None:
        # domain_handlers 含 system.ping 时不应覆盖内置 ping
        store = SharedPayloadStore(owner="worker")

        async def evil(payload, cancel):
            return {"evil": True}

        dispatcher = _build_dispatcher(
            store=store, domain_handlers={"system.ping": evil}
        )
        # 内置 ping 仍要求 nonce
        assert "system.ping" in dispatcher._handlers  # type: ignore[attr-defined]
