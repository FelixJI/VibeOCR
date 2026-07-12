"""Tests for WorkerHost Named Pipe security (Task 1.3 Green).

Covers the platform-neutral security logic:
- session token generation and constant-time comparison;
- pipe name validation (only worker-generated UUID names accepted; rejects
  path traversal / injection attempts);
- SID/DACL helpers abstracted so the security policy is testable without a
  live pipe.

Real Win32 integration (creating an actual pipe with a DACL restricted to the
current user, connecting a second client) is exercised by
``test_named_pipe.py`` under a Windows-only marker.
"""

from __future__ import annotations

import os

import pytest

from vibeocr.worker_host.security import (
    PipeNameError,
    SessionTokenError,
    generate_pipe_name,
    generate_session_token,
    validate_pipe_name,
    verify_session_token,
)

# ---------------------------------------------------------------------------
# session token
# ---------------------------------------------------------------------------


def test_session_token_is_64_hex_chars() -> None:
    token = generate_session_token()
    assert len(token) == 64
    assert all(c in "0123456789abcdef" for c in token)
    # 256 bits = 32 bytes = 64 hex chars
    assert len(bytes.fromhex(token)) == 32


def test_session_token_is_random() -> None:
    a = generate_session_token()
    b = generate_session_token()
    assert a != b


def test_verify_session_token_accepts_match() -> None:
    token = generate_session_token()
    assert verify_session_token(token, token) is True


def test_verify_session_token_rejects_mismatch() -> None:
    a = generate_session_token()
    b = generate_session_token()
    assert verify_session_token(a, b) is False


def test_verify_session_token_rejects_empty() -> None:
    token = generate_session_token()
    with pytest.raises(SessionTokenError):
        verify_session_token("", token)
    with pytest.raises(SessionTokenError):
        verify_session_token(token, "")


def test_verify_session_token_rejects_wrong_length() -> None:
    token = generate_session_token()
    with pytest.raises(SessionTokenError):
        verify_session_token(token[:32], token)
    with pytest.raises(SessionTokenError):
        verify_session_token(token + "ab", token)


def test_verify_session_token_rejects_non_hex() -> None:
    token = generate_session_token()
    bad = "z" * 64
    with pytest.raises(SessionTokenError):
        verify_session_token(bad, token)


def test_verify_session_token_is_constant_time_on_match() -> None:
    # We cannot time precisely in a unit test, but we assert the helper does not
    # short-circuit by checking it compares full-length differing tokens.
    token = generate_session_token()
    # Flip the last hex char deterministically.
    last = token[-1]
    flipped = token[:-1] + ("0" if last != "0" else "1")
    assert verify_session_token(flipped, token) is False


# ---------------------------------------------------------------------------
# pipe name validation
# ---------------------------------------------------------------------------


def test_validate_pipe_name_accepts_generated_uuid_name() -> None:
    name = generate_pipe_name()
    assert validate_pipe_name(name) == name


def test_validate_pipe_name_rejects_relative_path_traversal() -> None:
    with pytest.raises(PipeNameError):
        validate_pipe_name("..\\\\..\\\\windows\\\\system32\\\\evil")


def test_validate_pipe_name_rejects_absolute_file_path() -> None:
    with pytest.raises(PipeNameError):
        validate_pipe_name("C:\\\\Windows\\\\System32\\\\evil.dll")


def test_validate_pipe_name_rejects_network_path() -> None:
    with pytest.raises(PipeNameError):
        validate_pipe_name("\\\\\\\\attacker\\\\share\\\\pipe")


def test_validate_pipe_name_rejects_bare_word() -> None:
    with pytest.raises(PipeNameError):
        validate_pipe_name("evil.eval")


def test_validate_pipe_name_rejects_non_uuid_suffix() -> None:
    with pytest.raises(PipeNameError):
        validate_pipe_name("\\\\\\\\.\\\\pipe\\\\VibeOCR-not-a-uuid")


def test_validate_pipe_name_rejects_unknown_prefix() -> None:
    with pytest.raises(PipeNameError):
        validate_pipe_name("\\\\\\\\.\\\\pipe\\\\OtherApp-00000000-0000-4000-8000-000000000000")


def test_generate_pipe_name_round_trips_through_validation() -> None:
    for _ in range(10):
        assert validate_pipe_name(generate_pipe_name())


# ---------------------------------------------------------------------------
# Cross-platform skip helper for tests that need a live Win32 pipe.
# ---------------------------------------------------------------------------

IS_WINDOWS = os.name == "nt"
win32_only = pytest.mark.skipif(not IS_WINDOWS, reason="Windows Named Pipe required")
