"""Synchronous bridge from the Qt/PySide UI to the async BackendClient.

The PySide UI runs on the Qt event loop (synchronous slots + qasync). The
WorkerHost ``BackendClient`` is asyncio-native. This wrapper owns a dedicated
background asyncio loop + thread, launches the WorkerHost subprocess on it,
and exposes **synchronous** QR methods that block the caller until the RPC
completes. This keeps the UI code synchronous (matching the old direct-service
calls) while routing through the RPC protocol.

Per ADR §5.1: the UI depends on this client surface, not on backend services.

Lifecycle::

    client = SyncBackendClient()
    client.start()          # spawn worker, connect, handshake
    png = client.generate_qrcode_sync("hello", {"format": "qrcode"})
    codes = client.decode_qrcode_sync(image_bytes)
    client.shutdown()       # drain + kill worker

All ``*_sync`` methods may be called from any thread; they submit a coroutine
to the background loop and block on the result.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
from typing import TYPE_CHECKING, Any

from vibeocr.worker_host.backend_client import BackendClient, DecodedCode

if TYPE_CHECKING:
    from pathlib import Path

_log = logging.getLogger(__name__)

_READY_TIMEOUT_SECONDS = 30.0


class SyncBackendError(RuntimeError):
    """A synchronous call to the worker failed (launch error or RPC error)."""


class SyncBackendClient:
    """Owns a background loop + WorkerHost subprocess; exposes sync QR calls."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: BackendClient | None = None
        self._process: subprocess.Popen[str] | None = None
        self._started = False
        self._lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------

    def start(
        self,
        *,
        profile: str = "winui-dev",
        frontend_id: str = "pyside",
        working_dir: Path | None = None,
    ) -> None:
        """Spawn the worker on a background loop and connect the client.

        Idempotent: a second call when already started is a no-op.
        """
        with self._lock:
            if self._started:
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, name="vibeocr-worker-loop", daemon=True
            )
            self._thread.start()
            # Launch + connect + handshake on the background loop.
            fut = asyncio.run_coroutine_threadsafe(
                self._start_async(profile, frontend_id, working_dir), self._loop
            )
            try:
                fut.result(timeout=_READY_TIMEOUT_SECONDS + 10)
            except Exception as exc:
                self.shutdown()
                raise SyncBackendError(f"failed to start WorkerHost: {exc}") from exc
            self._started = True

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _start_async(
        self,
        profile: str,
        frontend_id: str,
        working_dir: Path | None,
    ) -> None:
        import os
        import secrets
        import sys
        import uuid

        from vibeocr.worker_host.named_pipe import PipeEndpoint

        pipe_name = f"\\\\.\\pipe\\VibeOCR-{uuid.uuid4()}"
        token = secrets.token_hex(32)
        parent_pid = os.getpid()
        cmd = [
            sys.executable,
            "-m",
            "vibeocr.worker_host.main",
            "--pipe",
            pipe_name,
            "--token",
            token,
            "--profile",
            profile,
            "--frontend-id",
            frontend_id,
            "--parent-pid",
            str(parent_pid),
        ]
        _log.info("launching WorkerHost: %s", " ".join(cmd))
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(working_dir) if working_dir else None,
            text=True,
        )
        ready = await asyncio.wait_for(
            self._await_ready(), timeout=_READY_TIMEOUT_SECONDS
        )
        _log.info("WorkerHost ready on %s", ready.get("pipe", pipe_name))
        self._client = BackendClient()
        await self._client.connect(
            PipeEndpoint(name=pipe_name, session_token=token)
        )
        # The server requires system.handshake as the first request.
        await self._client.call(
            "system.handshake",
            {
                "app_version": self._app_version(),
                "protocol_version": 1,
            },
        )

    async def _await_ready(self) -> dict[str, Any]:
        import json

        assert self._process is not None
        assert self._process.stdout is not None
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(
                None, self._process.stdout.readline
            )
            if not line:
                err = self._process.stderr.read() if self._process.stderr else ""
                raise SyncBackendError(
                    f"WorkerHost exited (code={self._process.poll()}) before ready. "
                    f"stderr: {err}"
                )
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            if doc.get("event") == "worker.ready":
                return doc

    @staticmethod
    def _app_version() -> str:
        try:
            from vibeocr import __version__

            return str(__version__)
        except Exception:
            return "0.0.0"

    def shutdown(self) -> None:
        """Close the client, kill the worker, stop the background loop."""
        if self._loop is None:
            return
        loop = self._loop
        client = self._client
        process = self._process

        def _shutdown_on_loop() -> None:
            if client is not None:
                loop.create_task(client.close())

        try:
            if client is not None and loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(client.close(), loop)
                with _suppress(Exception):
                    fut.result(timeout=5.0)
        finally:
            if process is not None:
                with _suppress(Exception):
                    process.kill()
            loop.call_soon_threadsafe(loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5.0)
            self._loop = None
            self._thread = None
            self._client = None
            self._process = None
            self._started = False

    # -- synchronous QR surface ----------------------------------------

    def _run_sync(self, coro, *, timeout: float = 60.0) -> Any:
        if not self._started or self._loop is None or self._client is None:
            raise SyncBackendError("SyncBackendClient is not started")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    def generate_qrcode_sync(
        self, data: str, *, options: dict[str, Any] | None = None
    ) -> bytes:
        """Generate a styled QR/barcode PNG. Blocks until the RPC completes."""
        assert self._client is not None
        return self._run_sync(
            self._client.generate_qrcode(data, options=options)
        )

    def generate_qrcode_svg_sync(
        self, data: str, *, options: dict[str, Any] | None = None
    ) -> str:
        assert self._client is not None
        return self._run_sync(
            self._client.generate_qrcode_svg(data, options=options)
        )

    def decode_qrcode_sync(self, image_bytes: bytes) -> list[DecodedCode]:
        """Decode QR/barcodes from image bytes. Blocks until the RPC completes."""
        assert self._client is not None
        return self._run_sync(self._client.decode_qrcode(image_bytes))

    def recognize_sync(
        self,
        image_bytes: bytes,
        *,
        pipeline: str = "OCR",
        language: str | None = None,
        timeout: float = 300.0,
    ) -> Any:
        """Run OCR via ``ocr.recognize`` and reconstruct an ``OCRResult``.

        Blocks until the RPC completes (up to ``timeout`` seconds). Returns a
        ``vibeocr.models.ocr_result.OCRResult`` reconstructed from the wire
        response (text_blocks deserialized into ``TextBlock`` objects).
        """
        assert self._client is not None
        raw = self._run_sync(
            self._client.recognize(
                image_bytes, pipeline=pipeline, language=language
            ),
            timeout=timeout,
        )
        return _reconstruct_ocr_result(raw)


class _suppress:
    """Inline contextlib.suppress."""

    def __init__(self, *exceptions: type[BaseException]) -> None:
        self._exceptions = exceptions

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        return exc_type is not None and issubclass(exc_type, self._exceptions)


def _reconstruct_ocr_result(raw: dict[str, Any]) -> Any:
    """Reconstruct a ``vibeocr.models.ocr_result.OCRResult`` from a wire dict.

    Deserializes ``text_blocks`` dicts back into ``TextBlock`` dataclass
    instances and ``text_with_scores`` [[text, score], ...] into tuples.
    """
    from vibeocr.models.ocr_result import OCRResult, TextBlock

    raw_text = str(raw.get("raw_text") or raw.get("text") or "")
    markdown_text = str(raw.get("markdown_text") or "")
    html_text = str(raw.get("html_text") or "")
    pipeline_type = str(raw.get("pipeline") or "OCR")

    # Deserialize text_blocks: dicts → TextBlock objects.
    text_blocks: list[Any] = []
    for blk in raw.get("text_blocks", []) or []:
        if isinstance(blk, dict):
            bbox = blk.get("bbox")
            if bbox is not None and not isinstance(bbox, (list, tuple)):
                bbox = None
            text_blocks.append(
                TextBlock(
                    text=str(blk.get("text", "")),
                    score=float(blk.get("score", blk.get("confidence", 0.0))),
                    bbox=tuple(bbox) if bbox is not None else None,  # type: ignore[arg-type]
                    content_index=blk.get("content_index"),
                    order=int(blk.get("order", -1)),
                )
            )

    # text_with_scores: [[text, score], ...] → [(text, score), ...]
    tws_raw = raw.get("text_with_scores", []) or []
    text_with_scores = [
        (str(pair[0]), float(pair[1]))
        for pair in tws_raw
        if isinstance(pair, (list, tuple)) and len(pair) == 2
    ]

    content_list = list(raw.get("content_list", []) or [])

    return OCRResult(
        raw_text=raw_text,
        markdown_text=markdown_text,
        html_text=html_text,
        text_with_scores=text_with_scores,
        pipeline_type=pipeline_type,
        content_list=content_list,
        text_blocks=text_blocks,
        image_width=int(raw.get("image_width", 0) or 0),
        image_height=int(raw.get("image_height", 0) or 0),
    )


__all__ = ["SyncBackendClient", "SyncBackendError"]
