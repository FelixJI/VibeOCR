"""设置管理器

管理应用程序设置的加载、保存和验证。
所有实际读写已委托给 ConfigManager，本类保留信号桥接以兼容现有消费者。
"""

import logging
from pathlib import Path

from PySide6.QtCore import QObject

logger = logging.getLogger(__name__)


class SettingsManager(QObject):
    """设置管理器

    管理预加载设置的加载和保存。
    所有持久化操作委托给 ConfigManager。
    """

    def __init__(self, project_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        from vibeocr.managers.config_manager import ConfigManager

        self._project_root = project_root
        self._cm = ConfigManager.instance(project_root)
        self._config_dir = self._cm.config_dir

    @property
    def config_dir(self) -> Path:
        """获取配置目录"""
        return self._config_dir

    def get_preload_config(self) -> dict:
        """获取预加载配置"""
        pipelines = self._cm.get_preload_pipelines()
        return {
            "enabled": len(pipelines) > 0,
            "pipelines": pipelines,
        }
