"""Frontend export naming plus RPC dispatch to the backend exporter."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibeocr.worker_host.sync_client import SyncBackendClient

_log = logging.getLogger(__name__)


def get_output_filename(source_name: str, export_format: str) -> str:
    stem = Path(source_name).stem
    extension = {
        "markdown": ".md",
        "html": ".html",
        "txt": ".txt",
        "docx": ".docx",
        "xlsx": ".xlsx",
    }.get(export_format, ".txt")
    return stem + extension


def get_unique_output_path(output_path: Path) -> Path:
    if not output_path.exists():
        return output_path
    counter = 1
    while True:
        candidate = output_path.with_name(
            f"{output_path.stem}_{counter}{output_path.suffix}"
        )
        if not candidate.exists():
            return candidate
        counter += 1


def _wire_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    return {
        "raw_text": str(getattr(result, "raw_text", "") or ""),
        "markdown_text": str(getattr(result, "markdown_text", "") or ""),
        "html_text": str(getattr(result, "html_text", "") or ""),
        "content_list": list(getattr(result, "content_list", []) or []),
    }


def export_result(
    client: SyncBackendClient,
    result: Any,
    output_path: Path,
    export_format: str,
) -> bool:
    try:
        client.export_ocr_sync(
            _wire_result(result),
            output_path=str(output_path),
            export_format=export_format,
            overwrite=False,
        )
        return True
    except Exception:
        _log.exception("backend export failed: %s", output_path)
        return False


__all__ = ["export_result", "get_output_filename", "get_unique_output_path"]
