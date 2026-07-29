"""Synchronous transport for the generic supervisor job interface.

PySide PDF workers are ``QThread`` instances and cannot await the async
``SupervisorClient``.  This wrapper drives exactly the same
submit/observe/command contract on the background loop already owned by the
PDF supervisor client.  It deliberately does not expose recognition-specific
or transport-private methods.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .background_loop import get_background_loop
from .client import SupervisorClient

_get_bg_loop = get_background_loop

if TYPE_CHECKING:
    from vibeocr.runtime_contracts import (
        CancelMode,
        JobCommand,
        JobRef,
        JobUpdate,
        SubmitRequest,
    )


class SyncSupervisorClient:
    """Blocking façade over the three generic job operations."""

    def __init__(
        self, *, base_url: str, session_token: str, instance_id: str | None = None
    ) -> None:
        self._async = SupervisorClient(
            base_url=base_url,
            session_token=session_token,
            instance_id=instance_id,
        )
        self._entered = False

    def _ensure_entered(self) -> SupervisorClient:
        if not self._entered:
            _get_bg_loop().run(self._async.__aenter__())
            self._entered = True
        return self._async

    def start(self) -> None:
        self._ensure_entered()

    def close(self) -> None:
        if not self._entered:
            return
        closing = self._async.__aexit__(None, None, None)
        try:
            result = _get_bg_loop().run(closing)
            # Defensive test/dry-run seam: a fake loop may echo the coroutine
            # instead of consuming it. Close it so resource warnings remain a
            # real signal in the Phase 1 gate.
            if result is closing:
                closing.close()
        except Exception:
            closing.close()
            raise
        finally:
            self._entered = False

    def submit(
        self,
        request: SubmitRequest,
        attachments: dict[str, tuple[str | None, bytes]],
    ) -> JobRef:
        return _get_bg_loop().run(
            self._ensure_entered().submit(request, attachments)
        )

    def observe(self, job_id: str, *, after_sequence: int = 0) -> JobUpdate:
        return _get_bg_loop().run(
            self._ensure_entered().observe(
                job_id, after_sequence=after_sequence
            )
        )

    def command(self, command: JobCommand) -> JobRef | CancelMode | None:
        return _get_bg_loop().run(self._ensure_entered().command(command))

    def export_ocr(
        self,
        *,
        raw_text: str,
        markdown_text: str,
        html_text: str,
        raw_blocks: list[dict] | None = None,
        output_path: str,
        fmt: str,
        overwrite: bool = False,
    ) -> dict:
        return _get_bg_loop().run(
            self._ensure_entered().export_ocr(
                raw_text=raw_text,
                markdown_text=markdown_text,
                html_text=html_text,
                raw_blocks=raw_blocks,
                output_path=output_path,
                fmt=fmt,
                overwrite=overwrite,
            )
        )

    def decode_qrcode(self, image_bytes: bytes) -> list[dict]:
        return _get_bg_loop().run(
            self._ensure_entered().decode_qrcode(image_bytes)
        )

    def generate_qrcode(
        self,
        data: str,
        *,
        fmt: str = "qrcode",
        options: dict[str, Any] | None = None,
    ) -> str:
        return _get_bg_loop().run(
            self._ensure_entered().generate_qrcode(
                data,
                fmt=fmt,
                options=options,
            )
        )


__all__ = ["SyncSupervisorClient"]
