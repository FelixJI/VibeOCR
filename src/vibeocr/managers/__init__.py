"""管理器模块

包含：
- DependencyManager: 依赖管理
- LayoutManager: 布局管理
- SubprocessManager: 子进程管理
- SettingsManager: 设置管理
"""

from vibeocr.managers.dependency_manager import DependencyManager
from vibeocr.managers.layout_manager import LayoutManager
from vibeocr.managers.settings_manager import SettingsManager
from vibeocr.managers.subprocess_manager import SubprocessManager

__all__ = ["DependencyManager", "LayoutManager", "SettingsManager", "SubprocessManager"]
