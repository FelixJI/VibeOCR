"""JobHandle: a thin async helper around a submitted job.

PySide wraps this in a Qt-safe adapter (Phase 7A); the handle itself is
UI-free and only depends on :class:`SupervisorClient`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from vibeocr.runtime_contracts import (
    TERMINAL_JOB_STATES,
    CancelMode,
    ItemState,
    JobCommand,
    JobCommandKind,
    JobRef,
    JobSnapshot,
    JobUpdate,
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
        return (await self.observe()).snapshot

    async def events(self, *, after_sequence: int = 0) -> list[StageEvent]:
        return list((await self.observe(after_sequence=after_sequence)).events)

    async def observe(self, *, after_sequence: int = 0) -> JobUpdate:
        return await self.client.observe(
            self.job_id, after_sequence=after_sequence
        )

    async def result(self) -> list[ResultEntry]:
        update = await self.observe()
        outcomes = {outcome.item_id: outcome for outcome in update.outcomes}
        return [
            ResultEntry(
                item_id=item.item_id,
                display_name=item.display_name,
                payload=(
                    outcomes[item.item_id].payload or {}
                    if item.item_id in outcomes
                    and outcomes[item.item_id].state is ItemState.SUCCEEDED
                    else {}
                ),
                error_code=(
                    outcomes[item.item_id].error_code
                    if item.item_id in outcomes
                    else None
                ),
            )
            for item in self.ref.items
        ]

    async def cancel(self) -> CancelMode:
        result = await self.client.command(
            JobCommand(
                command_id=str(uuid4()),
                kind=JobCommandKind.CANCEL,
                job_id=self.job_id,
            )
        )
        if not isinstance(result, CancelMode):
            raise RuntimeError("cancel command returned no cancel mode")
        return result

    async def wait_for_terminal(self, *, timeout: float | None = None) -> JobSnapshot:
        """Poll status until terminal. Raises asyncio.TimeoutError on timeout."""
        async def _wait() -> JobSnapshot:
            last_seq = 0
            while True:
                update = await self.observe(after_sequence=last_seq)
                snap = update.snapshot
                if snap.state in TERMINAL_JOB_STATES:
                    return snap
                last_seq = update.through_sequence
                await asyncio.sleep(0.02)
        if timeout is None:
            return await _wait()
        return await asyncio.wait_for(_wait(), timeout=timeout)

    async def stream_events(self) -> AsyncIterator[StageEvent]:
        """Yield events as they arrive until the job is terminal."""
        last_seq = 0
        while True:
            update = await self.observe(after_sequence=last_seq)
            for e in update.events:
                yield e
            last_seq = update.through_sequence
            if update.snapshot.state in TERMINAL_JOB_STATES:
                return
            await asyncio.sleep(0.02)


__all__ = ["JobHandle"]
