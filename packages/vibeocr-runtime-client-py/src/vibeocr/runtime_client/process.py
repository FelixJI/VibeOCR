"""SupervisorProcess: launch the supervisor child process and read its ready envelope.

Phase 2 exit criteria addressed:

* the parent generates the 256-bit session token and passes it to the child
  via an inherited env var (``VIBEOCR_SUP_TOKEN``) — never on argv or stdout;
* the child binds ``127.0.0.1:0`` itself and reports the chosen port back in
  the first stdout line (ready envelope), eliminating the port-selection race;
* the parent records the PID and uses a Job Object on Windows to terminate the
  whole process tree on shutdown (implemented in production wiring; here we
  expose the lifecycle seam).

The launcher is split from the HTTP client so tests can inject a fake
transport (e.g. ASGI transport backed by the supervisor app) without spawning
a real process.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from vibeocr.runtime_client.job_object import JobObjectGuard
from vibeocr.runtime_contracts.generated import (
    ALL_CAPABILITIES,
    PROTOCOL_VERSION,
    READY_ENVELOPE_VERSION,
    SCHEMA_VERSION,
    RuntimeReadyEnvelope,
)
from vibeocr.runtime_contracts.utils.http_log import (
    guess_response_size,
    log_http_response,
)

logger = logging.getLogger(__name__)
_UVICORN_ACCESS_LINE = re.compile(
    r'^INFO:\s+\S+\s+-\s+"(?P<method>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) '
    r'(?P<url>\S+) HTTP/\d(?:\.\d)?" (?P<status>\d{3})'
)
_HTTP_REQUEST_LINE = re.compile(
    r"HTTP Request:\s+(?P<method>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+"
    r'(?P<url>\S+)\s+"HTTP/\d(?:\.\d)? (?P<status>\d{3})(?:\s+(?P<reason>[^"]+))?"'
)

if TYPE_CHECKING:
    from pathlib import Path


class SupervisorLaunchError(RuntimeError):
    """Raised when the supervisor fails to report ready in time."""


@dataclass(frozen=True, slots=True)
class ReadyEnvelope:
    """Parsed ready envelope emitted by the supervisor on stdout."""

    ready: bool
    pid: int
    port: int
    instance_id: str
    protocol_version: int
    schema_version: int
    capabilities: tuple[str, ...]
    ready_version: int

    @classmethod
    def from_line(cls, line: str) -> ReadyEnvelope:
        data = RuntimeReadyEnvelope.from_payload(json.loads(line))
        envelope = cls(
            ready=data.ready,
            pid=data.pid,
            port=data.port,
            instance_id=data.instance_id,
            protocol_version=data.protocol_version,
            schema_version=data.schema_version,
            capabilities=data.capabilities,
            ready_version=data.ready_version,
        )
        if (
            not envelope.ready
            or envelope.pid <= 0
            or not 1 <= envelope.port <= 65535
            or not envelope.instance_id
        ):
            raise SupervisorLaunchError("invalid supervisor ready envelope")
        if (
            envelope.protocol_version != PROTOCOL_VERSION
            or envelope.schema_version != SCHEMA_VERSION
            or envelope.ready_version != READY_ENVELOPE_VERSION
        ):
            raise SupervisorLaunchError("incompatible supervisor protocol")
        if len(envelope.capabilities) != len(set(envelope.capabilities)):
            raise SupervisorLaunchError("duplicate supervisor capability")
        unknown = set(envelope.capabilities) - set(ALL_CAPABILITIES)
        if unknown:
            raise SupervisorLaunchError(
                f"unknown supervisor capabilities: {sorted(unknown)}"
            )
        return envelope

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def generate_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass
class SupervisorProcess:
    """Owns the lifecycle of one supervisor child process.

    Use as a context manager:

        async with SupervisorProcess.launch(python=sys.executable) as proc:
            async with SupervisorClient(base_url=proc.base_url, ...) as c:
                ...
    """

    python_exe: str
    module: str = "vibeocr.backend.supervisor.main"
    startup_timeout: float = 15.0
    env: dict[str, str] = field(default_factory=dict)
    working_directory: str | None = None
    _proc: subprocess.Popen | None = field(default=None, repr=False)
    _ready: ReadyEnvelope | None = field(default=None, repr=False)
    _token: str | None = field(default=None, repr=False)
    _stdout_thread: threading.Thread | None = field(default=None, repr=False)
    _stderr_thread: threading.Thread | None = field(default=None, repr=False)
    _log_lines: list[str] = field(default_factory=list, repr=False)
    _job_guard: JobObjectGuard | None = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Launch / ready
    # ------------------------------------------------------------------

    @classmethod
    def launch(
        cls,
        *,
        python_exe: str | None = None,
        module: str = "vibeocr.backend.supervisor.main",
        startup_timeout: float = 15.0,
        extra_env: dict[str, str] | None = None,
        stager_root: Path | None = None,
        working_directory: str | Path | None = None,
    ) -> SupervisorProcess:
        proc = cls(
            python_exe=python_exe or sys.executable,
            module=module,
            startup_timeout=startup_timeout,
            env=dict(extra_env or {}),
            working_directory=(
                str(working_directory) if working_directory is not None else None
            ),
        )
        proc._start(stager_root)
        return proc

    def _start(self, stager_root: Path | None) -> None:
        token = generate_token()
        self._token = token
        env = dict(os.environ)
        env.update(self.env)
        env["VIBEOCR_SUP_TOKEN"] = token
        # In a PyInstaller build the portable Python is a separate interpreter:
        # it cannot import modules from the executable's PYZ archive.  The build
        # ships a flat ``vibeocr`` source tree under ``sys._MEIPASS`` for child
        # processes, so expose that tree explicitly just like the OCR/PDF child
        # launchers do.
        if getattr(sys, "frozen", False):
            bundle_root = getattr(sys, "_MEIPASS", None)
            if bundle_root:
                bundle_path = str(bundle_root)
                existing = env.get("PYTHONPATH", "")
                existing_parts = existing.split(os.pathsep) if existing else []
                if bundle_path not in existing_parts:
                    env["PYTHONPATH"] = os.pathsep.join([bundle_path, *existing_parts])
        # The parent decodes both pipes as UTF-8 below, so make Python's side
        # of the stdio contract explicit as well.  This also avoids locale-
        # encoded warnings when the Windows system code page is GBK.
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        if stager_root is not None:
            env["VIBEOCR_SUP_ROOT"] = str(stager_root)
        self._job_guard = JobObjectGuard()
        try:
            self._proc = subprocess.Popen(
                [self.python_exe, "-m", self.module],
                cwd=self.working_directory,
                env=env,
                stdout=subprocess.PIPE,
                # stdout is a protocol channel: its first line must be the JSON
                # ready envelope. Third-party imports (notably Paddle) may write
                # diagnostics to stderr before ready, so stderr stays separate.
                stderr=subprocess.PIPE,
                text=True,
                # Supervisor protocol and logs are UTF-8.  Do not inherit the
                # Windows ANSI code page (commonly GBK), which can crash the
                # background drain thread while decoding valid UTF-8 output.
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            # KILL_ON_JOB_CLOSE is the actual parent-crash guarantee on Windows.
            # CREATE_NEW_PROCESS_GROUP alone only changes console signalling.
            self._job_guard.assign_from_popen(self._proc)
            assert self._proc.stderr is not None
            self._stderr_thread = threading.Thread(
                target=self._drain_stream,
                args=(self._proc.stderr,),
                daemon=True,
            )
            self._stderr_thread.start()
            self._read_ready()
        except BaseException:
            self.shutdown()
            raise

    def _read_ready(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        # First line must be the ready envelope.
        line = self._proc.stdout.readline()
        if not line:
            raise SupervisorLaunchError(
                "supervisor produced no ready envelope (no stdout output)"
            )
        try:
            self._ready = ReadyEnvelope.from_line(line)
        except Exception as exc:
            raise SupervisorLaunchError(
                f"invalid ready envelope: {line!r}: {exc}"
            ) from exc
        if not self._ready.ready:
            raise SupervisorLaunchError("supervisor reported not ready")
        # Subsequent stdout is log text; drain it on a background thread so the
        # pipe does not fill and block the child.
        self._stdout_thread = threading.Thread(
            target=self._drain_stream,
            args=(self._proc.stdout,),
            daemon=True,
        )
        self._stdout_thread.start()

    def _drain_stream(self, stream) -> None:  # type: ignore[no-untyped-def]
        for line in stream:
            message = line.rstrip()
            m = _UVICORN_ACCESS_LINE.search(message)
            if m:
                status = int(m.group("status"))
                if status >= 400:
                    log_http_response(
                        logger,
                        m.group("method"),
                        m.group("url"),
                        status,
                    )
                continue

            m = _HTTP_REQUEST_LINE.search(message)
            if m:
                status = int(m.group("status"))
                log_http_response(
                    logger,
                    m.group("method"),
                    m.group("url"),
                    status,
                    reason=(m.group("reason") or "").strip() or None,
                    response_bytes=guess_response_size(None, None),
                )
                continue
            self._log_lines.append(message)
            if not message:
                continue
            if "[Supervisor][" in message:
                logger.info("%s", message)
            else:
                logger.debug("[Supervisor child] %s", message)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def ready(self) -> ReadyEnvelope:
        if self._ready is None:
            raise SupervisorLaunchError("supervisor not launched")
        return self._ready

    @property
    def base_url(self) -> str:
        return self.ready.base_url

    @property
    def session_token(self) -> str:
        if self._token is None:
            raise SupervisorLaunchError("supervisor not launched")
        return self._token

    @property
    def pid(self) -> int:
        if self._proc is None:
            raise SupervisorLaunchError("supervisor not launched")
        return self._proc.pid

    @property
    def log_lines(self) -> list[str]:
        return list(self._log_lines)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self, *, timeout: float = 5.0) -> int:
        """Terminate the supervisor and wait. Returns the exit code."""
        proc = self._proc
        self._proc = None
        if proc is None:
            guard = self._job_guard
            self._job_guard = None
            if guard is not None:
                guard.close()
            return 0
        try:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                return proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                return proc.wait(timeout=timeout)
        finally:
            guard = self._job_guard
            self._job_guard = None
            if guard is not None:
                guard.close()

    def __enter__(self) -> SupervisorProcess:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.shutdown()


__all__ = [
    "ReadyEnvelope",
    "SupervisorLaunchError",
    "SupervisorProcess",
    "generate_token",
]
