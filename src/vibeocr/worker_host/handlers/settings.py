"""Settings RPC handler: bridges ``settings.snapshot`` to the settings facade."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from vibeocr.application.contracts import CancelToken, SettingsSnapshot


@runtime_checkable
class SettingsFacade(Protocol):
    def get_snapshot(self) -> SettingsSnapshot: ...


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


__all__ = ["SettingsFacade", "SettingsSnapshotHandler"]
