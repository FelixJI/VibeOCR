"""Qt shell compatibility surface during the physical package split.

Views import this module for Qt-owned lifecycle and preference helpers.  It is
excluded from the backend wheel and must not be imported by WorkerHost code.
"""

from __future__ import annotations

import importlib
from typing import Any

from vibeocr.services import env_config as _env_config
from vibeocr.services.log_service import setup_logging


class _ClassProxy:
    """Resolve a moved Qt-shell class lazily and preserve patchability."""

    def __init__(self, module: str, name: str) -> None:
        self._module = module
        self._name = name

    def _resolve(self) -> Any:
        return getattr(importlib.import_module(self._module), self._name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


ConfigManager = _ClassProxy(
    "vibeocr.managers.config_manager", "ConfigManager"
)
DependencyManager = _ClassProxy(
    "vibeocr.managers.dependency_manager", "DependencyManager"
)
LayoutManager = _ClassProxy("vibeocr.managers.layout_manager", "LayoutManager")
SubprocessManager = _ClassProxy(
    "vibeocr.managers.subprocess_manager", "SubprocessManager"
)
ShutdownCoordinator = _ClassProxy(
    "vibeocr.managers.shutdown_coordinator", "ShutdownCoordinator"
)

GITHUB_REPO_BASE = _env_config.GITHUB_REPO_BASE
GITEE_REPO_BASE = _env_config.GITEE_REPO_BASE
OCR_CHECK_MODULES = _env_config.OCR_CHECK_MODULES
SYNC_MAX_ATTEMPTS = _env_config.SYNC_MAX_ATTEMPTS


def get_pending_sync_path():
    """Forward dynamically so tests/updater patches keep one source of truth."""
    return _env_config.get_pending_sync_path()


def get_update_progress_path():
    return _env_config.get_update_progress_path()


def validate_dep_check_consistency(*args, **kwargs):
    return _env_config.validate_dep_check_consistency(*args, **kwargs)

__all__ = [
    "GITEE_REPO_BASE",
    "GITHUB_REPO_BASE",
    "OCR_CHECK_MODULES",
    "SYNC_MAX_ATTEMPTS",
    "ConfigManager",
    "DependencyManager",
    "LayoutManager",
    "ShutdownCoordinator",
    "SubprocessManager",
    "get_pending_sync_path",
    "get_update_progress_path",
    "setup_logging",
    "validate_dep_check_consistency",
]
