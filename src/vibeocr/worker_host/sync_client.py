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
        self._active_lock = threading.Lock()
        self._active_calls: set[Any] = set()
        self._io_threads: list[threading.Thread] = []

    # -- lifecycle ------------------------------------------------------

    def start(
        self,
        *,
        profile: str = "production",
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
        child_env = os.environ.copy()
        # WorkerHost stdout/stderr is a machine-owned pipe, not a user console.
        # Pin both ends to UTF-8: relying on the Windows ANSI code page makes a
        # Chinese locale decode UTF-8 dependency logs as GBK and kills the drain
        # thread. ``errors=replace`` also keeps native extensions that write
        # malformed bytes directly to fd 1/2 from blocking the child on a full
        # pipe.
        child_env["PYTHONIOENCODING"] = "utf-8:backslashreplace"
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(working_dir) if working_dir else None,
            env=child_env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        ready = await asyncio.wait_for(
            self._await_ready(), timeout=_READY_TIMEOUT_SECONDS
        )
        _log.info("WorkerHost ready on %s", ready.get("pipe", pipe_name))
        self._start_output_drains()
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

    def _start_output_drains(self) -> None:
        """Continuously drain worker pipes so backend imports cannot deadlock."""
        assert self._process is not None

        def drain(stream: Any, level: int) -> None:
            if stream is None:
                return
            try:
                for raw in stream:
                    line = raw.rstrip()
                    if line:
                        _log.log(level, "WorkerHost: %s", line)
            except Exception:
                # Output forwarding must never take down the pipe drain. A
                # stopped drain can eventually fill the OS pipe buffer and
                # deadlock an otherwise healthy WorkerHost.
                _log.warning("WorkerHost %s drain stopped unexpectedly", stream, exc_info=True)

        self._io_threads = []
        for stream, level, name in (
            (self._process.stdout, logging.DEBUG, "stdout"),
            (self._process.stderr, logging.WARNING, "stderr"),
        ):
            thread = threading.Thread(
                target=drain,
                args=(stream, level),
                name=f"vibeocr-worker-{name}",
                daemon=True,
            )
            thread.start()
            self._io_threads.append(thread)

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
            self._io_threads = []

    # -- synchronous QR surface ----------------------------------------

    def _run_sync(self, coro, *, timeout: float = 60.0) -> Any:
        if not self._started or self._loop is None or self._client is None:
            raise SyncBackendError("SyncBackendClient is not started")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        with self._active_lock:
            self._active_calls.add(fut)
        try:
            return fut.result(timeout=timeout)
        finally:
            with self._active_lock:
                self._active_calls.discard(fut)

    def cancel_active(self) -> None:
        """Best-effort cancel every active call on this frontend session."""
        with self._active_lock:
            active = tuple(self._active_calls)
        for future in active:
            future.cancel()

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

    def recognize_batch_sync(
        self,
        images: list[bytes],
        *,
        pipeline: str = "OCR",
        language: str | None = None,
        timeout: float = 1800.0,
    ) -> list[Any]:
        assert self._client is not None
        raw_results = self._run_sync(
            self._client.recognize_batch(
                images, pipeline=pipeline, language=language
            ),
            timeout=timeout,
        )
        return [
            _reconstruct_ocr_result(raw) if raw is not None else None
            for raw in raw_results
        ]

    def export_ocr_sync(
        self,
        result: dict[str, Any],
        *,
        output_path: str,
        export_format: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        assert self._client is not None
        return self._run_sync(
            self._client.export_ocr(
                result,
                output_path=output_path,
                export_format=export_format,
                overwrite=overwrite,
            )
        )

    # -- synchronous PDF surface --------------------------------------

    def open_pdf_sync(self, file_path: str) -> dict[str, Any]:
        assert self._client is not None
        return self._run_sync(self._client.open_pdf(file_path))

    def close_pdf_sync(self, session_id: str) -> bool:
        assert self._client is not None
        return self._run_sync(self._client.close_pdf(session_id))

    def pdf_command_sync(
        self,
        session_id: str,
        operation: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 600.0,
    ) -> Any:
        assert self._client is not None
        return self._run_sync(
            self._client.pdf_command(
                session_id, operation, params, timeout=timeout
            ),
            timeout=timeout + 5.0,
        )

    def render_pdf_page_sync(
        self,
        session_id: str,
        page_index: int,
        *,
        size: int | None = None,
        dpi: int | None = None,
        timeout: float = 120.0,
    ) -> bytes:
        assert self._client is not None
        return self._run_sync(
            self._client.render_pdf_page(
                session_id, page_index, size=size, dpi=dpi
            ),
            timeout=timeout,
        )

    def rotate_pdf_pages_sync(
        self, session_id: str, page_indices: list[int], angle: int
    ) -> int:
        assert self._client is not None
        return self._run_sync(
            self._client.rotate_pdf_pages(session_id, page_indices, angle)
        )

    def delete_pdf_pages_sync(self, session_id: str, page_indices: list[int]) -> int:
        assert self._client is not None
        return self._run_sync(self._client.delete_pdf_pages(session_id, page_indices))

    def add_pdf_text_layer_sync(
        self,
        session_id: str,
        page_index: int,
        *,
        overwrite: bool,
        save: bool = True,
    ) -> dict[str, Any]:
        assert self._client is not None
        return self._run_sync(
            self._client.add_pdf_text_layer(
                session_id, page_index, overwrite=overwrite, save=save
            ),
            timeout=300.0,
        )

    def delete_pdf_text_layers_sync(
        self, session_id: str, page_indices: list[int]
    ) -> dict[str, Any]:
        assert self._client is not None
        return self._run_sync(
            self._client.delete_pdf_text_layers(session_id, page_indices),
            timeout=300.0,
        )

    def save_pdf_sync(self, session_id: str, output_path: str | None = None) -> str:
        assert self._client is not None
        return self._run_sync(
            self._client.save_pdf(session_id, output_path), timeout=300.0
        )

    def start_pdf_ocr_sync(
        self,
        session_id: str,
        file_path: str,
        page_indices: list[int],
        *,
        overwrite: bool,
        sidecar_root: str | None = None,
        timeout: float = 3600.0,
    ) -> dict[str, Any]:
        assert self._client is not None
        return self._run_sync(
            self._client.start_pdf_ocr(
                session_id,
                file_path,
                page_indices,
                overwrite=overwrite,
                sidecar_root=sidecar_root,
                timeout=timeout,
            ),
            timeout=timeout + 5.0,
        )

    # -- synchronous settings surface ---------------------------------

    def settings_snapshot_sync(self) -> dict[str, Any]:
        assert self._client is not None
        return self._run_sync(self._client.settings_snapshot())

    def switch_backend_sync(self, backend: str) -> dict[str, Any]:
        assert self._client is not None
        return self._run_sync(self._client.switch_backend(backend))

    def install_dependency_sync(
        self,
        name: str,
        *,
        source: str | None = None,
        timeout: float = 1800.0,
    ) -> dict[str, Any]:
        assert self._client is not None
        return self._run_sync(
            self._client.install_dependency(name, source=source, timeout=timeout),
            timeout=timeout + 5.0,
        )


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
            polygon = blk.get("polygon")
            if polygon is not None and not isinstance(polygon, (list, tuple)):
                polygon = None
            text_blocks.append(
                TextBlock(
                    text=str(blk.get("text", "")),
                    score=float(blk.get("score", blk.get("confidence", 0.0))),
                    bbox=tuple(bbox) if bbox is not None else None,  # type: ignore[arg-type]
                    polygon=tuple(polygon) if polygon is not None else None,  # type: ignore[arg-type]
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
