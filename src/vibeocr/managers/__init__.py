"""管理器模块

包含：
- ConfigManager: 统一配置管理
- DependencyManager: 依赖管理
- LayoutManager: 布局管理
- SubprocessManager: 子进程管理
"""

from vibeocr.managers.config_manager import ConfigManager
from vibeocr.managers.dependency_manager import DependencyManager
from vibeocr.managers.layout_manager import LayoutManager
from vibeocr.managers.subprocess_manager import SubprocessManager

__all__ = [
    "ConfigManager",
    "DependencyManager",
    "LayoutManager",
    "SubprocessManager",
]
