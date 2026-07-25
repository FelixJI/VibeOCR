"""``vibeocr-supervisor`` entry point.

Binds a pre-created ``127.0.0.1:0`` socket, emits the ready envelope on
stdout, then serves the FastAPI app via uvicorn. The session token is
delivered out of band via the ``VIBEOCR_SUP_TOKEN`` env var (inherited from
the parent process) so it never appears on stdout/argv/logs.

Usage from a parent process (PySide/WinUI):

    proc = subprocess.Popen(
        [python, "-m", "vibeocr.supervisor.main"],
        env={**os.environ, "VIBEOCR_SUP_TOKEN": token, "VIBEOCR_SUP_ROOT": root},
        stdout=PIPE,
    )
    ready = json.loads(proc.stdout.readline())
    port = ready["port"]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .bootstrap import (
    BootstrapHandle,
    ReadyEnvelope,
    bind_loopback_socket,
    emit_ready,
    new_instance_id,
    token_from_environment,
)
from .composition import build_supervisor


def run_supervisor(argv: list[str] | None = None) -> int:
    """Run the supervisor until interrupted. Returns process exit code."""
    instance_id = new_instance_id()
    token = token_from_environment()
    if not token:
        # Without a token we cannot safely serve; fail fast.
        sys.stderr.write("vibeocr-supervisor: missing VIBEOCR_SUP_TOKEN\n")
        return 2
    handle = BootstrapHandle(token)
    sock = bind_loopback_socket()
    port = sock.getsockname()[1]
    root_env = os.environ.get("VIBEOCR_SUP_ROOT")
    stager_root = Path(root_env) if root_env else None

    module, _ = build_supervisor(
        instance_id=instance_id, stager_root=stager_root, bootstrap_handle=handle
    )
    envelope = ReadyEnvelope(
        ready=True,
        pid=os.getpid(),
        port=port,
        instance_id=instance_id,
        protocol_version=2,
        schema_version=2,
        capabilities=["recognition", "pdf_ocr", "mineru_parse", "qrcode", "settings"],
    )
    emit_ready(envelope)

    # Import lazily so the module can be imported in environments without
    # uvicorn (e.g. pure contract tests).
    try:
        import uvicorn

        from .app import create_app
    except Exception as exc:  # pragma: no cover - environment dependent
        sys.stderr.write(f"vibeocr-supervisor: cannot start server: {exc}\n")
        return 3

    app = create_app(module, handle.token)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        # Pass the pre-bound socket so uvicorn does not rebind (port-0 race).
        workers=1,
    )
    server = uvicorn.Server(config)
    config.load()
    # Hand the bound socket to the server.
    server.servers = []  # type: ignore[attr-defined]
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_serve_with_socket(server, sock, app, handle.token))
    except KeyboardInterrupt:  # pragma: no cover - signal
        pass
    finally:
        module.shutdown_now()
        loop.close()
    return 0


async def _serve_with_socket(server, sock, app, token) -> None:  # pragma: no cover - integration
    """Serve using the pre-bound socket. Kept thin for testability."""
    config = server.config
    import uvicorn.protocols.utils  # noqa: F401

    # uvicorn supports passing a configured socket via Server.startup via
    # the ``sockets`` kwarg once the server is started. For the test path we
    # exercise the app via httpx/ASGI directly; this function only runs in
    # the real subprocess.
    config.app = app
    server.config = config
    await server.serve(sockets=[sock])


def main() -> int:  # pragma: no cover - entry point
    return run_supervisor()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
