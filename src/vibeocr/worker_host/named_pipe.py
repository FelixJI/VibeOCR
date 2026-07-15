"""Named Pipe server/client for the WorkerHost control channel (Task 1.3).

Implements a current-user-isolated Windows Named Pipe using only the Python
standard library (``ctypes``) — no extra runtime dependency for a single
platform API. The pipe DACL allows only the current user's SID; the handshake
verifies a 256-bit session token before any RPC is serviced.

Async surface: blocking Win32 calls run in the default executor so the event
loop stays responsive. Frame I/O uses ``vibeocr.worker_host.framing``.

On non-Windows the module imports cleanly but ``create`` / ``connect`` raise
``NotImplementedError``; the security primitives in ``security.py`` work
everywhere and are unit-tested independently.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from vibeocr.worker_host.framing import (
    DEFAULT_MAX_BYTES,
    AsyncByteReader,
    AsyncByteWriter,
    read_frame,
    write_frame,
)
from vibeocr.worker_host.security import (
    SessionTokenError,
    generate_pipe_name,
    generate_session_token,
    validate_pipe_name,
    verify_session_token,
)

IS_WINDOWS = os.name == "nt"

# ---------------------------------------------------------------------------
# Win32 constants and bindings (loaded lazily on Windows)
# ---------------------------------------------------------------------------

# Pipe access mode
PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000

# Security descriptor revision
SECURITY_DESCRIPTOR_REVISION = 1

# Generic access
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
SYNCHRONIZE = 0x00100000

# Open mode for CreateFile
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# Security: DACL present, no SACL, default owner/group
SE_DACL_PRESENT = 0x00000004


def _load_win32() -> dict[str, Any]:
    """Bind the Win32 functions we need. Returns a dict of callables.

    Raises ``OSError`` if a required entry point is missing (very old Windows).
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    a32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]

    bindings: dict[str, Any] = {"kernel32": k32, "advapi32": a32}

    k32.CreateNamedPipeW.restype = wt.HANDLE
    k32.CreateNamedPipeW.argtypes = [
        wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPVOID
    ]
    k32.ConnectNamedPipe.restype = wt.BOOL
    k32.ConnectNamedPipe.argtypes = [wt.HANDLE, ctypes.c_void_p]
    k32.CreateFileW.restype = wt.HANDLE
    k32.CreateFileW.argtypes = [
        wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE
    ]
    k32.ReadFile.restype = wt.BOOL
    k32.ReadFile.argtypes = [
        wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p
    ]
    k32.WriteFile.restype = wt.BOOL
    k32.WriteFile.argtypes = [
        wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p
    ]
    k32.CloseHandle.restype = wt.BOOL
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.WaitNamedPipeW.restype = wt.BOOL
    k32.WaitNamedPipeW.argtypes = [wt.LPCWSTR, wt.DWORD]
    k32.SetEvent.restype = wt.BOOL
    k32.SetEvent.argtypes = [wt.HANDLE]
    k32.CreateEventW.restype = wt.HANDLE
    k32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
    k32.GetOverlappedResult.restype = wt.BOOL
    k32.GetOverlappedResult.argtypes = [
        wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(wt.DWORD), wt.BOOL
    ]
    k32.CancelIoEx.restype = wt.BOOL
    k32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.c_void_p]
    k32.WaitForSingleObject.restype = wt.DWORD
    k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    k32.ResetEvent.restype = wt.BOOL
    k32.ResetEvent.argtypes = [wt.HANDLE]

    a32.ConvertStringSidToSidW.restype = wt.BOOL
    a32.ConvertStringSidToSidW.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    a32.InitializeSecurityDescriptor.restype = wt.BOOL
    a32.InitializeSecurityDescriptor.argtypes = [ctypes.c_void_p, wt.DWORD]
    a32.SetSecurityDescriptorDacl.restype = wt.BOOL
    a32.SetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p, wt.BOOL, ctypes.c_void_p, wt.BOOL
    ]
    a32.BuildExplicitAccessWithNameW.restype = None
    a32.BuildExplicitAccessWithNameW.argtypes = [
        ctypes.c_void_p, wt.LPWSTR, wt.DWORD, wt.DWORD, wt.DWORD
    ]
    a32.SetEntriesInAclW.restype = wt.DWORD
    a32.SetEntriesInAclW.argtypes = [
        wt.ULONG, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
    ]
    # LocalFree lives in kernel32, not advapi32.
    k32.LocalFree.restype = ctypes.c_void_p
    k32.LocalFree.argtypes = [ctypes.c_void_p]

    bindings["CreateNamedPipeW"] = k32.CreateNamedPipeW
    bindings["ConnectNamedPipe"] = k32.ConnectNamedPipe
    bindings["CreateFileW"] = k32.CreateFileW
    bindings["ReadFile"] = k32.ReadFile
    bindings["WriteFile"] = k32.WriteFile
    bindings["CloseHandle"] = k32.CloseHandle
    bindings["WaitNamedPipeW"] = k32.WaitNamedPipeW
    bindings["CreateEventW"] = k32.CreateEventW
    bindings["GetOverlappedResult"] = k32.GetOverlappedResult
    bindings["CancelIoEx"] = k32.CancelIoEx
    bindings["WaitForSingleObject"] = k32.WaitForSingleObject
    bindings["ResetEvent"] = k32.ResetEvent
    bindings["SetEvent"] = k32.SetEvent
    bindings["ConvertStringSidToSidW"] = a32.ConvertStringSidToSidW
    bindings["InitializeSecurityDescriptor"] = a32.InitializeSecurityDescriptor
    bindings["SetSecurityDescriptorDacl"] = a32.SetSecurityDescriptorDacl
    bindings["BuildExplicitAccessWithNameW"] = a32.BuildExplicitAccessWithNameW
    bindings["SetEntriesInAclW"] = a32.SetEntriesInAclW
    bindings["LocalFree"] = k32.LocalFree
    return bindings


# WaitForSingleObject return codes
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF

ERROR_IO_PENDING = 997
ERROR_PIPE_CONNECTED = 535
ERROR_PIPE_BUSY = 231


class OVERLAPPED(ctypes.Structure):
    """Win32 OVERLAPPED for async pipe operations."""

    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wt.DWORD),
        ("OffsetHigh", wt.DWORD),
        ("hEvent", wt.HANDLE),
    ]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PipeEndpoint:
    """The address + credential a client needs to reach the server."""

    name: str
    session_token: str

    def __post_init__(self) -> None:
        # Validate eagerly so a malformed endpoint never reaches the OS.
        validate_pipe_name(self.name)
        # Comparing a token with itself reuses the canonical shape validator
        # while retaining constant-time comparison in the actual handshake.
        verify_session_token(self.session_token, self.session_token)


class PipeConnection:
    """A duplex byte-stream connection over a Named Pipe handle.

    Adapts a raw Win32 HANDLE to the AsyncByteReader/AsyncByteWriter protocols
    used by ``vibeocr.worker_host.framing``. Reads/writes are offloaded to the
    default executor so the event loop is never blocked by blocking I/O.
    """

    def __init__(self, handle: int, *, server_side: bool) -> None:
        self._handle = handle
        self._server_side = server_side
        self._closed = False
        if IS_WINDOWS:
            self._win = _load_win32()
        else:  # pragma: no cover - exercised only on non-Windows import path
            self._win = {}

    # -- framing API -----------------------------------------------------

    async def read_frame(self, *, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
        return await read_frame(self, max_bytes=max_bytes)  # type: ignore[arg-type]

    async def write_frame(
        self, payload: bytes, *, max_bytes: int = DEFAULT_MAX_BYTES
    ) -> None:
        await write_frame(self, payload, max_bytes=max_bytes)  # type: ignore[arg-type]

    async def validate_handshake(self) -> None:
        """Read the client's session token frame and verify it in constant time.

        Raises ``SessionTokenError`` on mismatch and closes the connection.
        """
        token_frame = await self.read_frame(max_bytes=256)
        client_token = token_frame.decode("utf-8", errors="strict")
        # The server's expected token is stored on server-side connections.
        expected = getattr(self, "_expected_token", None)
        if expected is None or not verify_session_token(client_token, expected):
            await self.close()
            raise SessionTokenError("session token mismatch during handshake")

    # -- AsyncByteReader / AsyncByteWriter -------------------------------

    async def readexactly(self, n: int) -> bytes:
        if not IS_WINDOWS:  # pragma: no cover
            raise NotImplementedError("Named Pipe requires Windows")
        return await asyncio.to_thread(self._readexactly_sync, n)

    async def write(self, data: bytes) -> int:
        if not IS_WINDOWS:  # pragma: no cover
            raise NotImplementedError("Named Pipe requires Windows")
        return await asyncio.to_thread(self._write_sync, data)

    # -- synchronous Win32 I/O (run in executor) -------------------------

    def _readexactly_sync(self, n: int) -> bytes:
        if self._closed:
            raise ConnectionError("pipe closed")
        buf = (ctypes.c_ubyte * n)()
        got = 0
        while got < n:
            transferred = self._overlapped_transfer(
                "ReadFile", ctypes.byref(buf, got), n - got
            )
            if transferred == 0:
                # End of stream before n bytes: emulate asyncio truncation.
                import asyncio as _aio

                raise _aio.IncompleteReadError(bytes(buf)[:got], n)
            got += transferred
        return bytes(buf)[:got]

    def _write_sync(self, data: bytes) -> int:
        # Loop on partial writes: PIPE_TYPE_BYTE WriteFile may transfer fewer
        # bytes than requested when the pipe buffer is full. Without this loop
        # any frame larger than the 64 KiB pipe buffer would be truncated and
        # desync the framing layer.
        if self._closed:
            raise ConnectionError("pipe closed")
        total = len(data)
        buf = (ctypes.c_ubyte * total).from_buffer_copy(data)
        written_so_far = 0
        while written_so_far < total:
            transferred = self._overlapped_transfer(
                "WriteFile",
                ctypes.byref(buf, written_so_far),
                total - written_so_far,
            )
            if transferred == 0:
                raise ConnectionError("WriteFile wrote 0 bytes (pipe full?)")
            written_so_far += transferred
        return written_so_far

    def _overlapped_transfer(self, operation: str, buffer: Any, size: int) -> int:
        """Run one cancellable overlapped ReadFile/WriteFile operation."""
        event = self._win["CreateEventW"](None, True, False, None)
        if not event:
            raise ConnectionError(f"CreateEventW failed: GLE={ctypes.get_last_error()}")
        overlapped = OVERLAPPED()
        overlapped.hEvent = event
        immediate = wt.DWORD(0)
        try:
            ok = self._win[operation](
                self._handle,
                buffer,
                size,
                ctypes.byref(immediate),
                ctypes.byref(overlapped),
            )
            if ok:
                return int(immediate.value)
            gle = ctypes.get_last_error()
            if gle != ERROR_IO_PENDING:
                raise ConnectionError(f"{operation} failed: GLE={gle}")
            waited = self._win["WaitForSingleObject"](event, INFINITE)
            if waited != WAIT_OBJECT_0:
                raise ConnectionError(f"{operation} wait failed: result={waited}")
            transferred = wt.DWORD(0)
            if not self._win["GetOverlappedResult"](
                self._handle,
                ctypes.byref(overlapped),
                ctypes.byref(transferred),
                False,
            ):
                raise ConnectionError(
                    f"{operation} result failed: GLE={ctypes.get_last_error()}"
                )
            return int(transferred.value)
        finally:
            self._win["CloseHandle"](event)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if IS_WINDOWS and self._handle:
            await asyncio.to_thread(self._win["CloseHandle"], self._handle)
        self._handle = 0


# ---------------------------------------------------------------------------
# Security descriptor builder (current-user-only DACL)
# ---------------------------------------------------------------------------


def _current_user_sid() -> str:
    """Return the current user's SID as an SDDL string (e.g. S-1-5-21-...)."""
    import ctypes
    from ctypes import wintypes

    ADVAPI32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]

    TOKEN_QUERY = 0x0008
    TokenUser = 1

    class TOKEN_USER(ctypes.Structure):
        class _SID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

        _anonymous_ = ("_sid_and_attributes",)
        _fields_ = [("_sid_and_attributes", _SID_AND_ATTRIBUTES)]

    ADVAPI32.OpenProcessToken.restype = wintypes.BOOL
    ADVAPI32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    ]
    ADVAPI32.GetTokenInformation.restype = wintypes.BOOL
    ADVAPI32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    ADVAPI32.ConvertSidToStringSidW.restype = wintypes.BOOL
    ADVAPI32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)
    ]

    KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    KERNEL32.GetCurrentProcess.restype = wintypes.HANDLE
    KERNEL32.GetCurrentProcess.argtypes = []

    token = wintypes.HANDLE()
    if not ADVAPI32.OpenProcessToken(
        KERNEL32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise OSError(f"OpenProcessToken failed: GLE={ctypes.get_last_error()}")

    try:
        needed = wintypes.DWORD(0)
        ADVAPI32.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(needed))
        buf = (ctypes.c_ubyte * needed.value)()
        if not ADVAPI32.GetTokenInformation(
            token, TokenUser, buf, needed.value, ctypes.byref(needed)
        ):
            raise OSError(f"GetTokenInformation failed: GLE={ctypes.get_last_error()}")
        tu = ctypes.cast(buf, ctypes.POINTER(TOKEN_USER)).contents
        sid_str = wintypes.LPWSTR()
        if not ADVAPI32.ConvertSidToStringSidW(tu.Sid, ctypes.byref(sid_str)):
            raise OSError(
                f"ConvertSidToStringSid failed: GLE={ctypes.get_last_error()}"
            )
        try:
            return sid_str.value  # type: ignore[no-any-return]
        finally:
            KERNEL32.LocalFree(sid_str)  # type: ignore[attr-defined]
    finally:
        KERNEL32.CloseHandle(token)


def _build_security_attributes(sid_sddl: str) -> Any:
    """Build a SECURITY_ATTRIBUTES whose DACL grants full access to one SID.

    Returns the SECURITY_ATTRIBUTES structure (kept alive by the caller for the
    duration of CreateNamedPipeW).
    """
    win = _load_win32()

    # Use the SDDL-convert path: allocate a self-relative SD via
    # ConvertStringSecurityDescriptorToSecurityDescriptorW. This is simpler and
    # less error-prone than manually building an EXPLICIT_ACCESS + ACL.
    advapi32 = win["advapi32"]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wt.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wt.LPCWSTR, wt.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.ULONG)
    ]

    # SDDL: DACL grants FILE_ALL_ACCESS (FA) to the current user SID only.
    # Per design §11 the ACL is restricted to the current user; SYSTEM is
    # intentionally not granted (stricter than the OS default). FA = FILE_ALL_ACCESS.
    sddl = f"D:(A;;FA;;;{sid_sddl})"
    sd = ctypes.c_void_p()
    sd_size = wt.ULONG(0)
    SDDL_REVISION_1 = 1
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, SDDL_REVISION_1, ctypes.byref(sd), ctypes.byref(sd_size)
    ):
        raise OSError(f"ConvertSDDLToSD failed: GLE={ctypes.get_last_error()}")

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wt.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wt.BOOL),
        ]

    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    sa.lpSecurityDescriptor = sd
    sa.bInheritHandle = False
    # Keep references alive: attach to the struct for the lifetime of the call.
    sa._sd_ref = sd  # type: ignore[attr-defined]
    return sa


# ---------------------------------------------------------------------------
# Server / client
# ---------------------------------------------------------------------------


class NamedPipeServer:
    """A single-connection Named Pipe server restricted to the current user.

    Usage::

        server = await NamedPipeServer.create()
        conn = await server.accept(timeout_ms=2000)
        await conn.validate_handshake()

    The server services exactly one client connection per instance (the
    WinUI client is the only expected peer). ``accept`` times out if no client
    connects within ``timeout_ms``.
    """

    def __init__(self) -> None:
        self._handle: int = 0
        self.endpoint: PipeEndpoint | None = None
        if IS_WINDOWS:
            self._win = _load_win32()
        else:  # pragma: no cover - import-time path on non-Windows
            self._win = {}

    @classmethod
    async def create(
        cls,
        *,
        pipe_name: str | None = None,
        session_token: str | None = None,
    ) -> NamedPipeServer:
        """Create the pipe (DACL = current user only) and return a bound server."""
        if not IS_WINDOWS:  # pragma: no cover
            raise NotImplementedError("Named Pipe requires Windows")
        name = pipe_name if pipe_name is not None else generate_pipe_name()
        validate_pipe_name(name)
        token = session_token if session_token is not None else generate_session_token()
        self = cls()
        self.endpoint = PipeEndpoint(name=name, session_token=token)
        await asyncio.to_thread(self._create_pipe_sync, name)
        return self

    def _create_pipe_sync(self, name: str) -> None:
        sid = _current_user_sid()
        sa = _build_security_attributes(sid)
        handle = self._win["CreateNamedPipeW"](
            name,
            PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE | FILE_FLAG_OVERLAPPED,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            1,  # nMaxInstances
            65536,  # nOutBufferSize
            65536,  # nInBufferSize
            5000,  # nDefaultTimeOut ms
            ctypes.byref(sa),
        )
        if handle == INVALID_HANDLE_VALUE or not handle:
            raise OSError(f"CreateNamedPipeW failed: GLE={ctypes.get_last_error()}")
        self._handle = handle

    async def accept(self, timeout_ms: int = 5000) -> PipeConnection:
        """Wait for a client to connect, bounded by ``timeout_ms``.

        Uses overlapped I/O so a timeout cancels the in-flight ConnectNamedPipe
        via CancelIoEx (a blocking call in an executor thread cannot be killed,
        so we must make it cancellable at the OS level).
        """
        if not IS_WINDOWS:
            raise NotImplementedError("Named Pipe requires Windows")
        if self._handle == 0:
            raise RuntimeError("server not created; call create() first")

        connected = await asyncio.to_thread(self._connect_overlapped, timeout_ms)
        if not connected:
            raise TimeoutError(f"no client connected within {timeout_ms} ms")

        conn = PipeConnection(self._handle, server_side=True)
        assert self.endpoint is not None
        # Attach expected token for validate_handshake.
        conn._expected_token = self.endpoint.session_token  # type: ignore[attr-defined]
        # The server transfers handle ownership to the connection.
        self._handle = 0
        return conn

    def _connect_overlapped(self, timeout_ms: int) -> bool:
        """Blocking helper (run in executor): return True on connect, False on timeout."""
        win = self._win
        event = win["CreateEventW"](None, True, False, None)
        if not event:
            raise OSError(f"CreateEventW failed: GLE={ctypes.get_last_error()}")
        try:
            overlapped = OVERLAPPED()
            overlapped.hEvent = event
            ok = win["ConnectNamedPipe"](self._handle, ctypes.byref(overlapped))
            gle = ctypes.get_last_error()
            if ok:
                return True  # connected immediately
            if gle != ERROR_IO_PENDING:
                # ERROR_PIPE_CONNECTED (535): client already connected — success.
                if gle == ERROR_PIPE_CONNECTED:
                    return True
                raise OSError(f"ConnectNamedPipe failed: GLE={gle}")
            # Pending: wait for the event, bounded by timeout.
            waited = win["WaitForSingleObject"](event, max(0, int(timeout_ms)))
            if waited == WAIT_OBJECT_0:
                # Event signalled; confirm with GetOverlappedResult.
                transferred = wt.DWORD(0)
                if not win["GetOverlappedResult"](
                    self._handle, ctypes.byref(overlapped), ctypes.byref(transferred), False
                ):
                    gle2 = ctypes.get_last_error()
                    if gle2 == ERROR_PIPE_CONNECTED:
                        return True
                    raise OSError(f"GetOverlappedResult failed: GLE={gle2}")
                return True
            # Timed out: cancel the pending IO.
            win["CancelIoEx"](self._handle, ctypes.byref(overlapped))
            # Drain the cancelled operation so the event is reusable / no leak.
            transferred = wt.DWORD(0)
            win["GetOverlappedResult"](
                self._handle, ctypes.byref(overlapped), ctypes.byref(transferred), True
            )
            return False
        finally:
            win["CloseHandle"](event)

    async def close(self) -> None:
        if self._handle:
            await asyncio.to_thread(self._win["CloseHandle"], self._handle)
            self._handle = 0


class NamedPipeClient:
    """The single expected peer: connects and sends the session token."""

    def __init__(self) -> None:
        self._handle: int = 0
        if IS_WINDOWS:
            self._win = _load_win32()
        else:  # pragma: no cover
            self._win = {}

    async def connect(
        self, endpoint: PipeEndpoint, *, timeout_ms: int = 5000
    ) -> PipeConnection:
        """Open the pipe and send the session token frame.

        Retries with WaitNamedPipe if the pipe is not yet available (server
        still starting), bounded by ``timeout_ms``.

        Can be called as an instance method (``client.connect(ep)``) or, for
        convenience, by constructing an instance first.
        """
        if not IS_WINDOWS:  # pragma: no cover
            raise NotImplementedError("Named Pipe requires Windows")
        validate_pipe_name(endpoint.name)

        def _open() -> int:
            while True:
                handle = self._win["CreateFileW"](
                    endpoint.name,
                    GENERIC_READ | GENERIC_WRITE,
                    0,  # no sharing
                    None,
                    OPEN_EXISTING,
                    FILE_FLAG_OVERLAPPED,
                    None,
                )
                gle = ctypes.get_last_error()
                if handle != INVALID_HANDLE_VALUE and handle:
                    return int(handle)  # type: ignore[return-value]
                if gle == 231:  # ERROR_PIPE_BUSY
                    if not self._win["WaitNamedPipeW"](endpoint.name, 100):
                        raise OSError("WaitNamedPipe timed out")
                    continue
                raise OSError(f"CreateFileW failed: GLE={gle}")

        self._handle = await asyncio.wait_for(
            asyncio.to_thread(_open), timeout=timeout_ms / 1000
        )
        conn = PipeConnection(self._handle, server_side=False)
        # Send the session token as the first frame.
        await conn.write_frame(endpoint.session_token.encode("utf-8"), max_bytes=256)
        # Transfer ownership to the connection.
        self._handle = 0
        return conn

    async def close(self) -> None:
        if self._handle:
            await asyncio.to_thread(self._win["CloseHandle"], self._handle)
            self._handle = 0


# Register protocol membership so framing accepts PipeConnection directly.
if TYPE_CHECKING:
    _: tuple[type[AsyncByteReader], type[AsyncByteWriter]] = (PipeConnection, PipeConnection)


__all__ = [
    "IS_WINDOWS",
    "NamedPipeClient",
    "NamedPipeServer",
    "PipeConnection",
    "PipeEndpoint",
]
