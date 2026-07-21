"""WorkerHost RPC handlers for the inference pipeline cache lifecycle."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from vibeocr.worker_host.errors import ErrorCode, WorkerError

if TYPE_CHECKING:
    from vibeocr.application.contracts import CancelToken


@runtime_checkable
class PipelineCacheBoundary(Protocol):
    def pipeline_cache_status(self) -> dict[str, Any]: ...

    def set_pipeline_ttls(self, pipeline_ttls: dict[str, int]) -> bool: ...

    def release_pipelines(self, heavy_only: bool = True) -> list[str]: ...

    def preload_pipelines(self, pipelines: list[str]) -> dict[str, bool]: ...

    def warmup_pipelines(self, pipelines: list[str]) -> dict[str, bool]: ...


class PipelineCacheStatusHandler:
    def __init__(self, *, boundary: PipelineCacheBoundary) -> None:
        self._boundary = boundary

    async def handle(
        self, payload: dict[str, Any], cancel: CancelToken
    ) -> dict[str, Any]:
        del payload
        return await asyncio.to_thread(self._boundary.pipeline_cache_status)


class SetPipelineCacheTtlHandler:
    def __init__(self, *, boundary: PipelineCacheBoundary) -> None:
        self._boundary = boundary

    async def handle(
        self, payload: dict[str, Any], cancel: CancelToken
    ) -> dict[str, Any]:
        ttls = payload.get("pipeline_ttls")
        if not isinstance(ttls, dict):
            raise WorkerError(
                ErrorCode.INVALID_REQUEST, "pipeline_ttls must be an object"
            )
        updated = await asyncio.to_thread(
            self._boundary.set_pipeline_ttls, dict(ttls)
        )
        if not updated:
            raise WorkerError(
                ErrorCode.WORKER_UNAVAILABLE, "pipeline cache TTL was not updated"
            )
        return {"updated": True, "pipeline_ttls": dict(ttls)}


class ReleasePipelineCacheHandler:
    def __init__(self, *, boundary: PipelineCacheBoundary) -> None:
        self._boundary = boundary

    async def handle(
        self, payload: dict[str, Any], cancel: CancelToken
    ) -> dict[str, Any]:
        heavy_only = payload.get("heavy_only", True)
        if not isinstance(heavy_only, bool):
            raise WorkerError(ErrorCode.INVALID_REQUEST, "heavy_only must be boolean")
        released = await asyncio.to_thread(
            self._boundary.release_pipelines, heavy_only
        )
        return {"released": list(released)}


class _PipelineListHandler:
    def __init__(
        self, *, boundary: PipelineCacheBoundary, warmup: bool
    ) -> None:
        self._boundary = boundary
        self._warmup = warmup

    async def handle(
        self, payload: dict[str, Any], cancel: CancelToken
    ) -> dict[str, Any]:
        pipelines = payload.get("pipelines")
        if not isinstance(pipelines, list) or not all(
            isinstance(item, str) and item for item in pipelines
        ):
            raise WorkerError(
                ErrorCode.INVALID_REQUEST,
                "pipelines must be an array of non-empty strings",
            )
        if cancel.is_cancelled:
            raise WorkerError(ErrorCode.TASK_CANCELLED, "pipeline task cancelled")
        operation = (
            self._boundary.warmup_pipelines
            if self._warmup
            else self._boundary.preload_pipelines
        )
        results = await asyncio.to_thread(operation, pipelines)
        return {"results": {str(name): bool(ok) for name, ok in results.items()}}


class PreloadPipelineCacheHandler(_PipelineListHandler):
    def __init__(self, *, boundary: PipelineCacheBoundary) -> None:
        super().__init__(boundary=boundary, warmup=False)


class WarmupPipelineCacheHandler(_PipelineListHandler):
    def __init__(self, *, boundary: PipelineCacheBoundary) -> None:
        super().__init__(boundary=boundary, warmup=True)


__all__ = [
    "PipelineCacheBoundary",
    "PipelineCacheStatusHandler",
    "PreloadPipelineCacheHandler",
    "ReleasePipelineCacheHandler",
    "SetPipelineCacheTtlHandler",
    "WarmupPipelineCacheHandler",
]
