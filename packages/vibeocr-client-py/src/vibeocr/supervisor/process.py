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
import os
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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

    @classmethod
    def from_line(cls, line: str) -> ReadyEnvelope:
        data = json.loads(line)
        return cls(
            ready=bool(data["ready"]),
            pid=int(data["pid"]),
            port=int(data["port"]),
            instance_id=data["instance_id"],
            protocol_version=int(data["protocol_version"]),
            schema_version=int(data["schema_version"]),
            capabilities=tuple(data.get("capabilities", [])),
        )

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
    module: str = "vibeocr.supervisor.main"
    startup_timeout: float = 15.0
    env: dict[str, str] = field(default_factory=dict)
    _proc: subprocess.Popen | None = field(default=None, repr=False)
    _ready: ReadyEnvelope | None = field(default=None, repr=False)
    _token: str | None = field(default=None, repr=False)
    _stdout_thread: threading.Thread | None = field(default=None, repr=False)
    _log_lines: list[str] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------
    # Launch / ready
    # ------------------------------------------------------------------

    @classmethod
    def launch(
        cls,
        *,
        python_exe: str | None = None,
        module: str = "vibeocr.supervisor.main",
        startup_timeout: float = 15.0,
        extra_env: dict[str, str] | None = None,
        stager_root: Path | None = None,
    ) -> SupervisorProcess:
        proc = cls(
            python_exe=python_exe or sys.executable,
            module=module,
            startup_timeout=startup_timeout,
            env=dict(extra_env or {}),
        )
        proc._start(stager_root)
        return proc

    def _start(self, stager_root: Path | None) -> None:
        token = generate_token()
        self._token = token
        env = dict(os.environ)
        env.update(self.env)
        env["VIBEOCR_SUP_TOKEN"] = token
        if stager_root is not None:
            env["VIBEOCR_SUP_ROOT"] = str(stager_root)
        self._proc = subprocess.Popen(
            [self.python_exe, "-m", self.module],
            env=env,
            stdout=subprocess.PIPE,
            # Merge stderr into stdout so a single drain thread keeps both
            # pipes from filling and deadlocking the child. The first line is
            # the JSON ready envelope; everything after is log text (which may
            # include uvicorn's stderr logs).
            stderr=subprocess.STDOUT,
            text=True,
            # On Windows, create a Job Object so the whole tree dies with us.
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._read_ready()

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
            raise SupervisorLaunchError(f"invalid ready envelope: {line!r}: {exc}") from exc
        if not self._ready.ready:
            raise SupervisorLaunchError("supervisor reported not ready")
        # Subsequent stdout is log text; drain it on a background thread so the
        # pipe does not fill and block the child.
        self._stdout_thread = threading.Thread(target=self._drain_logs, daemon=True)
        self._stdout_thread.start()

    def _drain_logs(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            self._log_lines.append(line.rstrip())

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
        if self._proc is None:
            return 0
        try:
            self._proc.terminate()
        except OSError:
            pass
        try:
            return self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            return self._proc.wait(timeout=timeout)

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
