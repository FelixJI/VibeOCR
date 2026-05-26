"""统一配置管理器

所有用户配置的唯一读写入口，提供统一的路径管理和 JSON 读写。
"""

import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class ConfigManager(QObject):
    """统一配置管理器单例

    负责所有用户配置的读写、路径管理和版本迁移。
    """

    _instance: "ConfigManager | None" = None

    preload_pipelines_changed = Signal(list)

    def __init__(self, project_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._config_dir = project_root / "config"
        self._cache_dir = project_root / ".vibeocr"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def instance(cls, project_root: Path | None = None) -> "ConfigManager":
        if cls._instance is None:
            if project_root is None:
                raise RuntimeError("ConfigManager 首次创建必须传入 project_root")
            cls._instance = cls(project_root)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅供测试使用）。"""
        cls._instance = None

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    @property
    def project_root(self) -> Path:
        return self._project_root

    def _load_json(self, filename: str, default: dict | None = None) -> dict:
        filepath = self._config_dir / filename
        if not filepath.exists():
            return default if default is not None else {}
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            return (
                data
                if isinstance(data, dict)
                else (default if default is not None else {})
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("加载配置文件 %s 失败: %s", filename, e)
            return default if default is not None else {}

    def _save_json(self, filename: str, data: dict) -> bool:
        filepath = self._config_dir / filename
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except OSError as e:
            logger.error("保存配置文件 %s 失败: %s", filename, e)
            return False

    def _load_cache_json(self, filename: str, default: dict | None = None) -> dict:
        filepath = self._cache_dir / filename
        if not filepath.exists():
            return default if default is not None else {}
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            return (
                data
                if isinstance(data, dict)
                else (default if default is not None else {})
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("加载缓存文件 %s 失败: %s", filename, e)
            return default if default is not None else {}

    def _save_cache_json(self, filename: str, data: dict) -> bool:
        filepath = self._cache_dir / filename
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except OSError as e:
            logger.error("保存缓存文件 %s 失败: %s", filename, e)
            return False

    def get_preload_pipelines(self) -> list[str]:
        data = self._load_json("app_settings.json", {})
        pipelines = data.get("preload_pipelines", [])
        if not pipelines:
            cache = self._load_cache_json("cache.json", {})
            pipelines = cache.get("preload_pipelines", [])
            if pipelines:
                self.set_preload_pipelines(pipelines)
        self._preload_pipelines = pipelines
        return pipelines

    def get_preload_enabled(self) -> bool:
        data = self._load_json("app_settings.json", {})
        if "preload_enabled" not in data:
            return len(data.get("preload_pipelines", [])) > 0
        return bool(data["preload_enabled"])

    def set_preload_enabled(self, enabled: bool) -> bool:
        data = self._load_json("app_settings.json", {})
        data["preload_enabled"] = enabled
        return self._save_json("app_settings.json", data)

    def set_preload_pipelines(self, pipelines: list[str]) -> bool:
        data = self._load_json("app_settings.json", {})
        data["preload_pipelines"] = pipelines
        self._preload_pipelines = pipelines
        success = self._save_json("app_settings.json", data)
        if success:
            self.preload_pipelines_changed.emit(pipelines)
        return success

    @property
    def preload_pipelines(self) -> list[str]:
        return getattr(self, "_preload_pipelines", [])

    def get_export_settings(self) -> dict:
        return self._load_json(
            "export_settings.json",
            {
                "version": 1,
                "format": "markdown",
                "location_mode": "same_as_source",
                "custom_directory": "",
                "last_custom_directory": "",
            },
        )

    def save_export_settings(self, settings: dict) -> bool:
        data = {"version": 1, **settings}
        return self._save_json("export_settings.json", data)
