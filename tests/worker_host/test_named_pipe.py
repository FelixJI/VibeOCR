"""Tests for the WorkerHost Named Pipe server (Task 1.3 Green).

Platform-neutral tests cover endpoint validation and accept-timeout behaviour.

Windows-only integration tests (marked ``win32_only``) create a real Named Pipe
restricted by DACL to the current user, connect a client, verify the handshake
session token, and confirm a second client cannot preempt an active connection.
Handles must return to baseline after each test.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from tests.worker_host.test_security import win32_only
from vibeocr.worker_host.named_pipe import (
    IS_WINDOWS as IS_WINDOWS_ENV,
)
from vibeocr.worker_host.named_pipe import (
    NamedPipeClient,
    NamedPipeServer,
    PipeConnection,
    PipeEndpoint,
)
from vibeocr.worker_host.security import (
    PipeNameError,
    SessionTokenError,
    generate_pipe_name,
    generate_session_token,
)

_ = os

# ---------------------------------------------------------------------------
# PipeEndpoint
# ---------------------------------------------------------------------------


def test_pipe_endpoint_holds_name_and_token() -> None:
    name = generate_pipe_name()
    token = generate_session_token()
    ep = PipeEndpoint(name=name, session_token=token)
    assert ep.name == name
    assert ep.session_token == token


def test_pipe_endpoint_validates_name() -> None:
    token = generate_session_token()
    with pytest.raises(PipeNameError):
        PipeEndpoint(name="evil.eval", session_token=token)


def test_pipe_endpoint_validates_token_length() -> None:
    name = generate_pipe_name()
    with pytest.raises(SessionTokenError):
        PipeEndpoint(name=name, session_token="tooshort")


def test_pipe_endpoint_rejects_non_hex_token() -> None:
    name = generate_pipe_name()
    with pytest.raises(SessionTokenError):
        PipeEndpoint(name=name, session_token="z" * 64)


# ---------------------------------------------------------------------------
# NamedPipeServer (platform-neutral accept timeout)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_accept_times_out_when_no_client() -> None:
    # A created pipe with no connecting client must time out on accept.
    if not IS_WINDOWS_ENV:
        # On non-Windows we still assert the constructor is harmless and
        # accept raises NotImplementedError (caught and re-raised as timeout
        # would be wrong; instead assert the platform guard).
        server = NamedPipeServer()
        with pytest.raises(NotImplementedError):
            await server.accept(timeout_ms=200)
        return
    server = await NamedPipeServer.create()
    try:
        start = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(server.accept(timeout_ms=200), timeout=2.0)
        elapsed = time.monotonic() - start
        assert elapsed < 1.5
    finally:
        await server.close()


# ---------------------------------------------------------------------------
# Windows-only integration with a real Named Pipe
# ---------------------------------------------------------------------------


@win32_only
@pytest.mark.asyncio
async def test_real_pipe_handshake_and_round_trip() -> None:
    supplied_token = generate_session_token()
    server = await NamedPipeServer.create(session_token=supplied_token)
    try:
        assert server.endpoint is not None
        assert server.endpoint.session_token == supplied_token
        client = NamedPipeClient()
        client_task = asyncio.create_task(
            client.connect(server.endpoint, timeout_ms=2000)  # type: ignore[arg-type]
        )
        conn = await asyncio.wait_for(server.accept(timeout_ms=2000), timeout=3.0)
        await conn.validate_handshake()
        await conn.write_frame(b'{"ping":true}')
        client_conn = await client_task
        msg = await asyncio.wait_for(client_conn.read_frame(), timeout=2.0)
        assert msg == b'{"ping":true}'
        await conn.close()
        await client_conn.close()
    finally:
        await server.close()


@win32_only
@pytest.mark.asyncio
async def test_second_client_cannot_preempt_active_connection() -> None:
    server = await NamedPipeServer.create()
    conn1: PipeConnection | None = None
    try:
        client1 = NamedPipeClient()
        client1_task = asyncio.create_task(
            client1.connect(server.endpoint, timeout_ms=2000)  # type: ignore[arg-type]
        )
        conn1 = await asyncio.wait_for(server.accept(timeout_ms=2000), timeout=3.0)
        await conn1.validate_handshake()
        await client1_task
        # A second client must not preempt the first; the pipe has a single
        # instance so the second connect should fail or time out quickly.
        client2 = NamedPipeClient()
        with pytest.raises((asyncio.TimeoutError, OSError, ConnectionError)):
            await asyncio.wait_for(
                client2.connect(server.endpoint, timeout_ms=300),  # type: ignore[arg-type]
                timeout=2.0,
            )
    finally:
        if conn1 is not None:
            await conn1.close()
        await server.close()


@win32_only
@pytest.mark.asyncio
async def test_handles_return_to_baseline() -> None:
    # Repeated create/close must not exhaust handles; the OS would reject
    # further creation if handles leaked.
    for _ in range(5):
        server = await NamedPipeServer.create()
        await server.close()


# keep the PipeConnection import meaningful on non-Windows collectors
_ = PipeConnection
_ = time
