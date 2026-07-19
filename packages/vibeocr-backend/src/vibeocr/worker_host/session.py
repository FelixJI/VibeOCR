"""Session management for the WorkerHost (Task 1.5).

A ``WorkerSession`` binds one accepted pipe connection to a dispatcher and
tracks per-connection state: the authenticated session token, in-flight task
ids, and disconnect cleanup. On disconnect the session cancels every in-flight
task belonging to the peer so handlers stop promptly and resources release.

Pure-Python orchestration over the connection abstraction from Task 1.3; the
read/write loop itself is wired in Task 1.6 (``main.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from vibeocr.worker_host.task_registry import State, TaskRegistry

if TYPE_CHECKING:
    from vibeocr.worker_host.dispatcher import Dispatcher

_log = logging.getLogger(__name__)


@dataclass
class WorkerSession:
    """One client connection's session state."""

    session_token: str
    dispatcher: Dispatcher
    registry: TaskRegistry = field(default_factory=TaskRegistry)
    # task ids belonging to this peer (for cancel-on-disconnect).
    _task_ids: set[str] = field(default_factory=set)
    _closed: bool = False

    def bind_task(self, task_id: str) -> None:
        """Record that ``task_id`` belongs to this session."""
        if self._closed:
            return
        self._task_ids.add(task_id)

    def task_ids(self) -> frozenset[str]:
        return frozenset(self._task_ids)

    def disconnect(self) -> None:
        """Cancel every in-flight task for this peer and mark the session closed.

        Called when the pipe drops. Terminal tasks are left as-is; only queued
        or running tasks are cancelled.
        """
        if self._closed:
            return
        self._closed = True
        for tid in list(self._task_ids):
            handle = self.registry.get(tid)
            if handle is None:
                continue
            if handle.state in (State.QUEUED, State.RUNNING):
                self.registry.cancel(tid)
        _log.debug("session disconnected, %d tasks released", len(self._task_ids))

    @property
    def closed(self) -> bool:
        return self._closed


__all__ = ["WorkerSession"]
