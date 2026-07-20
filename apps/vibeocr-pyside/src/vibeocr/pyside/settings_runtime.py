"""Settings-page runtime bridge for the PySide shell.

UI modules depend on this platform boundary instead of importing manager and
service implementations directly.
"""

from __future__ import annotations

from vibeocr.managers.config_manager import ConfigManager
from vibeocr.services.log_service import apply_log_level


def get_log_level() -> str:
    """Return the persisted application log level."""
    try:
        return ConfigManager.instance().get_log_level()
    except RuntimeError:
        # Isolated settings-page construction (tests/embedding) can happen
        # before MainWindow initializes the application configuration root.
        return "INFO"


def set_log_level(level: str) -> bool:
    """Persist and immediately apply an application log level."""
    try:
        config = ConfigManager.instance()
    except RuntimeError:
        return False
    if not config.set_log_level(level):
        return False
    apply_log_level(level)
    return True


__all__ = ["get_log_level", "set_log_level"]
