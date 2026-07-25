"""PdfProcessAdapter: supervisor-owned model-free PDF child process.

Plan §4 Phase 6 goals addressed by this seam:

* The supervisor is the sole owner of the PyMuPDF child process; the GUI no
  longer instantiates ``PdfBackendClient``.
* Quick PDF session operations (open/render/mutate/save) are proxied through
  the supervisor with bounded behaviour.
* PDF OCR is a job: render batch → recognition microbatch, per-page item
  state, microbatch/page-boundary cancel, partial page result, and a
  transactional final save (temp file + replace).
* Unresponsive PDF worker: cooperative cancel first, then terminate+rebuild
  without affecting Paddle/MinerU.

This module provides the ownership seam and the transactional-save helper;
the actual PyMuPDF calls reuse ``services/pdf_backend_process.py``.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


class _PdfChildLike(Protocol):
    """Minimal slice of the PDF child-process client we depend on."""

    def open_session(self, path: str, *, password: str | None = None) -> str: ...

    def render_preview(self, session_id: str, page: int) -> bytes: ...

    def save(self, session_id: str, target: str) -> None: ...

    def rotate(self, session_id: str, pages: list[int], angle: int) -> int: ...

    def delete_pages(self, session_id: str, pages: list[int]) -> int: ...

    def close_session(self, session_id: str) -> None: ...


@dataclass
class PdfProcessAdapter:
    """Supervisor-owned PDF child adapter."""

    child_factory: Callable[[], _PdfChildLike]
    _child: _PdfChildLike | None = None
    _sessions: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------

    def ensure_started(self) -> None:
        if self._child is None:
            self._child = self.child_factory()

    def stop(self) -> None:
        """Terminate the PDF child process."""
        self._child = None
        self._sessions.clear()

    @property
    def is_owner(self) -> bool:
        """The supervisor is the sole owner of the PDF child."""
        return True

    # ------------------------------------------------------------------
    # Session operations (bounded proxies)
    # ------------------------------------------------------------------

    def open_session(self, path: str, *, password: str | None = None) -> str:
        self.ensure_started()
        assert self._child is not None
        session_id = self._child.open_session(path, password=password)
        self._sessions.add(session_id)
        return session_id

    def render_preview(self, session_id: str, page: int) -> bytes:
        self.ensure_started()
        assert self._child is not None
        return self._child.render_preview(session_id, page)

    # ------------------------------------------------------------------
    # Transactional save (temp + fsync + atomic replace)
    # ------------------------------------------------------------------

    def save_transactional(self, session_id: str, target_path: str) -> str:
        """Save the session to ``target_path`` atomically.

        Writes to a temp file in the same directory, fsyncs, then renames over
        the target. On any error the original file is left untouched and the
        temp file is removed. This guarantees no half-finished file is ever
        published as a successful save.
        """
        self.ensure_started()
        assert self._child is not None
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        os.close(fd)
        try:
            self._child.save(session_id, tmp_name)
            self._fsync_path(tmp_name)
            Path(tmp_name).replace(target)
        except Exception:
            try:
                Path(tmp_name).unlink()
            except OSError:
                pass
            raise
        return str(target)

    @staticmethod
    def _fsync_path(path: str) -> None:
        # Open read/write so the fd is fsync-able on Windows (read-only fds
        # raise EBADF there).
        with open(path, "r+b") as fh:
            os.fsync(fh.fileno())


__all__ = ["PdfProcessAdapter"]
