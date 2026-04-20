"""统一配置管理器

所有用户配置的唯一读写入口，提供统一的路径管理和 JSON 读写。
"""

import json
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class ConfigManager(QObject):
    """统一配置管理器单例

    负责所有用户配置的读写、路径管理和版本迁移。
    """

    _instance: "ConfigManager | None" = None

    # ── 信号 ──────────────────────────────────────────
    llm_config_changed = Signal(object)  # LLMConfigs
    templates_changed = Signal(list)  # List[ExtractionTemplate]
    preload_pipelines_changed = Signal(list)  # List[str]

    def __init__(self, project_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._config_dir = project_root / "config"
        self._cache_dir = project_root / ".vibeocr"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # LLM 配置
        self._llm_configs: Any = None
        # 自定义模板
        self._custom_templates: list = []

    # ── 单例 ──────────────────────────────────────────

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

    # ── 路径属性 ──────────────────────────────────────

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    @property
    def project_root(self) -> Path:
        return self._project_root

    # ── 通用 JSON 读写 ───────────────────────────────

    def _load_json(self, filename: str, default: dict | None = None) -> dict:
        """加载 config/ 目录下的 JSON 文件。"""
        filepath = self._config_dir / filename
        if not filepath.exists():
            return default if default is not None else {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else (default if default is not None else {})
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("加载配置文件 %s 失败: %s", filename, e)
            return default if default is not None else {}

    def _save_json(self, filename: str, data: dict) -> bool:
        """保存 JSON 到 config/ 目录。"""
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
        """加载 .vibeocr/ 目录下的 JSON 文件。"""
        filepath = self._cache_dir / filename
        if not filepath.exists():
            return default if default is not None else {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else (default if default is not None else {})
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("加载缓存文件 %s 失败: %s", filename, e)
            return default if default is not None else {}

    def _save_cache_json(self, filename: str, data: dict) -> bool:
        """保存 JSON 到 .vibeocr/ 目录。"""
        filepath = self._cache_dir / filename
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except OSError as e:
            logger.error("保存缓存文件 %s 失败: %s", filename, e)
            return False

    # ── LLM 配置 ─────────────────────────────────────

    def load_llm_configs(self) -> Any:
        """加载 LLM 配置（向后兼容旧格式）。"""
        from vibeocr.models.llm_config import LLMConfigs

        data = self._load_json("llm_config.json", {})
        if not data:
            self._llm_configs = LLMConfigs()
        else:
            self._llm_configs = LLMConfigs.from_dict(data)
        self.llm_config_changed.emit(self._llm_configs)
        return self._llm_configs

    def save_llm_configs(self, config: Any = None) -> bool:
        """保存 LLM 配置。"""
        if config is not None:
            self._llm_configs = config
        if self._llm_configs is None:
            return False
        data = {**self._llm_configs.to_dict(), "version": 1}
        success = self._save_json("llm_config.json", data)
        if success:
            self.llm_config_changed.emit(self._llm_configs)
        return success

    @property
    def llm_configs(self) -> Any:
        return self._llm_configs

    # ── 模板管理 ─────────────────────────────────────

    def load_templates(self) -> list:
        """加载自定义模板。"""
        from vibeocr.models.extraction_template import ExtractionTemplate

        data = self._load_json("templates.json", {})
        templates = [ExtractionTemplate.from_dict(t) for t in data.get("templates", [])]
        self._custom_templates = templates
        self.templates_changed.emit(templates)
        return templates

    def add_template(self, template: Any) -> bool:
        """添加自定义模板。"""
        # 检查名称是否已存在
        for t in self._custom_templates:
            if t.name == template.name:
                logger.warning("模板名称已存在: %s", template.name)
                return False
        self._custom_templates.append(template)
        return self._save_templates()

    def update_template(self, name: str, template: Any) -> bool:
        """更新自定义模板。"""
        for i, t in enumerate(self._custom_templates):
            if t.name == name:
                self._custom_templates[i] = template
                return self._save_templates()
        return False

    def delete_template(self, name: str) -> bool:
        """删除自定义模板。"""
        original_count = len(self._custom_templates)
        self._custom_templates = [t for t in self._custom_templates if t.name != name]
        if len(self._custom_templates) == original_count:
            logger.warning("未找到模板: %s", name)
            return False
        return self._save_templates()

    def get_template_keys(self, name: str) -> list[str] | None:
        """获取模板键列表（合并默认+自定义模板查询）。"""
        from vibeocr.models.extraction_template import DEFAULT_TEMPLATES

        for t in DEFAULT_TEMPLATES + self._custom_templates:
            if t.name == name:
                return t.keys
        return None

    @property
    def custom_templates(self) -> list:
        return self._custom_templates

    # ── 预加载管道 ───────────────────────────────────

    def get_preload_pipelines(self) -> list[str]:
        """获取预加载管道配置（从 config/app_settings.json 读取）。"""
        data = self._load_json("app_settings.json", {})
        pipelines = data.get("preload_pipelines", [])
        if not pipelines:
            # 迁移：从 cache.json 读取旧配置
            cache = self._load_cache_json("cache.json", {})
            pipelines = cache.get("preload_pipelines", [])
            if pipelines:
                self.set_preload_pipelines(pipelines)
        self._preload_pipelines = pipelines
        return pipelines

    def set_preload_pipelines(self, pipelines: list[str]) -> bool:
        """保存预加载管道配置到 config/app_settings.json。"""
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

    def _save_templates(self) -> bool:
        """保存自定义模板到文件。"""
        data = {
            "version": 1,
            "templates": [t.to_dict() for t in self._custom_templates],
        }
        success = self._save_json("templates.json", data)
        if success:
            self.templates_changed.emit(list(self._custom_templates))
        return success

    # ── 导出设置 ──────────────────────────────────────

    def get_export_settings(self) -> dict:
        """获取导出设置。"""
        return self._load_json("export_settings.json", {
            "version": 1,
            "format": "markdown",
            "location_mode": "same_as_source",
            "custom_directory": "",
            "last_custom_directory": "",
        })

    def save_export_settings(self, settings: dict) -> bool:
        """保存导出设置。"""
        data = {"version": 1, **settings}
        return self._save_json("export_settings.json", data)
