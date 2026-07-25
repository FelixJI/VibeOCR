"""Frontend-only client SDK for the PySide application.

Legacy worker_host client code has been removed. These stubs exist for
backward compatibility during the transition period.
"""

__all__ = [
    "get_backend_client",
    "get_output_filename",
    "get_unique_output_path",
    "shutdown_backend_client",
]


def get_output_filename(source_name: str, export_format: str) -> str:
    """Get the output filename for an export."""
    from pathlib import Path
    stem = Path(source_name).stem
    extension = {
        "markdown": ".md",
        "html": ".html",
        "txt": ".txt",
        "docx": ".docx",
        "xlsx": ".xlsx",
    }.get(export_format, ".txt")
    return stem + extension


def get_unique_output_path(output_path):
    """Get a unique output path by appending a counter if the file exists."""
    if not output_path.exists():
        return output_path
    counter = 1
    while True:
        candidate = output_path.with_name(f"{output_path.stem}_{counter}{output_path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def shutdown_backend_client():
    """No-op stub for backward compatibility (worker_host removed)."""


def get_backend_client():
    """Stub: worker_host removed. Raises to prevent accidental legacy use."""
    raise RuntimeError("get_backend_client is no longer available; use SupervisorClientAdapter.")
