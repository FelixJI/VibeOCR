"""JobHandle: a thin async helper around a submitted job.

PySide wraps this in a Qt-safe adapter (Phase 7A); the handle itself is
UI-free and only depends on :class:`SupervisorClient`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vibeocr.protocol.v2 import (
    TERMINAL_JOB_STATES,
    CancelMode,
    JobRef,
    JobSnapshot,
    ResultEntry,
    StageEvent,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .client import SupervisorClient


@dataclass(slots=True)
class JobHandle:
    """A submitted job's lifecycle helper."""

    client: SupervisorClient
    ref: JobRef

    @property
    def job_id(self) -> str:
        return self.ref.job_id

    async def status(self) -> JobSnapshot:
        return await self.client.status(self.job_id)

    async def events(self, *, after_sequence: int = 0) -> list[StageEvent]:
        return await self.client.events(self.job_id, after_sequence=after_sequence)

    async def result(self) -> list[ResultEntry]:
        return await self.client.result(self.job_id)

    async def cancel(self) -> CancelMode:
        return await self.client.cancel(self.job_id)

    async def wait_for_terminal(self, *, timeout: float | None = None) -> JobSnapshot:
        """Poll status until terminal. Raises asyncio.TimeoutError on timeout."""
        async def _wait() -> JobSnapshot:
            last_seq = 0
            while True:
                snap = await self.client.status(self.job_id)
                if snap.state in TERMINAL_JOB_STATES:
                    return snap
                # Long-poll events to avoid tight polling; fall back to a short sleep.
                try:
                    events = await self.client.events(self.job_id, after_sequence=last_seq)
                    if events:
                        last_seq = events[-1].sequence
                except Exception:
                    pass
                await asyncio.sleep(0.02)
        if timeout is None:
            return await _wait()
        return await asyncio.wait_for(_wait(), timeout=timeout)

    async def stream_events(self) -> AsyncIterator[StageEvent]:
        """Yield events as they arrive until the job is terminal."""
        last_seq = 0
        while True:
            snap = await self.client.status(self.job_id)
            events = await self.client.events(self.job_id, after_sequence=last_seq)
            for e in events:
                yield e
                last_seq = max(last_seq, e.sequence)
            if snap.state in TERMINAL_JOB_STATES:
                return
            await asyncio.sleep(0.02)


__all__ = ["JobHandle"]
