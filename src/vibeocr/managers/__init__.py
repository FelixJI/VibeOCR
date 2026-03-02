"""管理器模块

包含：
- DependencyManager: 依赖管理
- SubprocessManager: 子进程管理
- SettingsManager: 设置管理
"""

from vibeocr.managers.dependency_manager import DependencyManager
from vibeocr.managers.subprocess_manager import SubprocessManager
from vibeocr.managers.settings_manager import SettingsManager

__all__ = ["DependencyManager", "SubprocessManager", "SettingsManager"]
