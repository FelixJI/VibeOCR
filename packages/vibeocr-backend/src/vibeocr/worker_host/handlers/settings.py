"""Settings RPC handler: bridges ``settings.snapshot`` to the settings facade."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from vibeocr.application.contracts import CancelToken, SettingsSnapshot

from vibeocr.worker_host.errors import ErrorCode, WorkerError


@runtime_checkable
class SettingsFacade(Protocol):
    def get_snapshot(self) -> SettingsSnapshot: ...


@runtime_checkable
class BackendSwitchBoundary(Protocol):
    """Persist a backend switch (cpu/gpu). Never auto-retries."""

    def switch_backend(self, target: str) -> str: ...


@runtime_checkable
class DependencyInstallBoundary(Protocol):
    """Install a named dependency (runtime/model). Never auto-retries."""

    def install_dependency(
        self, name: str, source: str | None, cancel: CancelToken
    ) -> dict[str, Any]: ...


class SettingsSnapshotHandler:
    """Handle ``settings.snapshot``: return the current read-only settings."""

    def __init__(self, *, facade: SettingsFacade) -> None:
        self._facade = facade

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        snap = self._facade.get_snapshot()
        return {
            "backend": snap.backend,
            "preload_pipelines": list(snap.preload_pipelines),
            "ttl_seconds": snap.ttl_seconds,
        }


class SwitchBackendHandler:
    """Handle ``settings.switch_backend``: persist a cpu/gpu switch.

    Mutations never auto-retry; network/mirror/write errors surface as
    WorkerError and leave the current backend unchanged.
    """

    def __init__(self, *, boundary: BackendSwitchBoundary) -> None:
        self._boundary = boundary

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        target = payload.get("backend")
        if target not in ("cpu", "gpu"):
            raise WorkerError(
                ErrorCode.INVALID_REQUEST, "settings.switch_backend requires 'backend' cpu|gpu"
            )
        try:
            new_backend = await asyncio.to_thread(self._boundary.switch_backend, target)
        except Exception as exc:
            raise WorkerError(ErrorCode.INTERNAL_ERROR, str(exc)) from exc
        return {"backend": new_backend, "restart_required": True}


class InstallDependencyHandler:
    """Handle ``settings.install_dependency``: install a runtime/model dep.

    Mutations never auto-retry. The boundary reports the outcome.
    """

    def __init__(self, *, boundary: DependencyInstallBoundary) -> None:
        self._boundary = boundary

    async def handle(self, payload: dict[str, Any], cancel: CancelToken) -> dict[str, Any]:
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            raise WorkerError(
                ErrorCode.INVALID_REQUEST, "settings.install_dependency requires 'name'"
            )
        source = payload.get("source")
        if source is not None and not isinstance(source, str):
            raise WorkerError(ErrorCode.INVALID_REQUEST, "source must be a string or null")
        try:
            result = await asyncio.to_thread(
                self._boundary.install_dependency, name, source, cancel
            )
        except Exception as exc:
            raise WorkerError(ErrorCode.INTERNAL_ERROR, str(exc)) from exc
        return result


__all__ = [
    "BackendSwitchBoundary",
    "DependencyInstallBoundary",
    "InstallDependencyHandler",
    "SettingsFacade",
    "SettingsSnapshotHandler",
    "SwitchBackendHandler",
]
