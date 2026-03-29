"""设置管理器

管理应用程序设置的加载、保存和验证。
所有实际读写已委托给 ConfigManager，本类保留信号桥接以兼容现有消费者。
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from vibeocr.models.extraction_template import ExtractionTemplate
    from vibeocr.models.llm_config import LLMConfigs

logger = logging.getLogger(__name__)


class SettingsManager(QObject):
    """设置管理器

    管理 LLM 配置、模板和预加载设置的加载、保存和验证。
    所有持久化操作委托给 ConfigManager，本类仅保留信号桥接。

    Signals:
        llm_config_loaded: LLM 配置加载完成
        llm_config_saved: LLM 配置保存完成
        templates_loaded: 模板列表加载完成
        template_added: 模板添加完成
        template_deleted: 模板删除完成
    """

    llm_config_loaded = Signal(object)  # LLMConfig
    llm_config_saved = Signal()
    templates_loaded = Signal(list)  # List[str]
    template_added = Signal(str)
    template_deleted = Signal(str)

    def __init__(self, project_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        from vibeocr.managers.config_manager import ConfigManager

        self._project_root = project_root
        self._cm = ConfigManager.instance(project_root)
        self._config_dir = self._cm.config_dir
        self._llm_configs: LLMConfigs | None = None
        self._templates: list[ExtractionTemplate] = []

        # 桥接 ConfigManager 信号
        self._cm.llm_config_changed.connect(self._on_llm_config_changed)
        self._cm.templates_changed.connect(self._on_templates_changed)

    def _on_llm_config_changed(self, configs: Any) -> None:
        self._llm_configs = configs
        self.llm_config_loaded.emit(configs)

    def _on_templates_changed(self, templates: list) -> None:
        self._templates = templates

    @property
    def llm_configs(self) -> Optional["LLMConfigs"]:
        """获取当前 LLM 配置容器"""
        return self._llm_configs

    @property
    def config_dir(self) -> Path:
        """获取配置目录"""
        return self._config_dir

    def load_llm_config(self) -> "LLMConfigs":
        """加载 LLM 配置"""
        self._llm_configs = self._cm.load_llm_configs()
        assert self._llm_configs is not None
        return self._llm_configs

    def save_llm_config(self, config: Optional["LLMConfigs"] = None) -> bool:
        """保存 LLM 配置"""
        if config is not None:
            self._llm_configs = config
        success = self._cm.save_llm_configs(self._llm_configs)
        if success:
            self.llm_config_saved.emit()
        return success

    def update_llm_config(
        self,
        service_url: str | None = None,
        model_name: str | None = None,
        api_key: str | None = None,
        is_mllm: bool = True,
    ) -> None:
        """更新 LLM 配置字段"""
        if self._llm_configs is None:
            self.load_llm_config()

        if self._llm_configs is None:
            logger.warning("无法更新 LLM 配置：加载失败")
            return

        config = self._llm_configs.mllm if is_mllm else self._llm_configs.llm
        if service_url is not None:
            config.service_url = service_url
        if model_name is not None:
            config.model_name = model_name
        if api_key is not None:
            config.api_key = api_key

    def load_templates(self) -> list[str]:
        """加载模板列表"""
        from vibeocr.models.extraction_template import DEFAULT_TEMPLATES

        template_names = []
        for template in DEFAULT_TEMPLATES:
            template_names.append(template.name)

        self._templates = self._cm.load_templates()
        for template in self._templates:
            template_names.append(f"[自定义] {template.name}")
        logger.info(f"已加载 {len(self._templates)} 个自定义模板")

        self.templates_loaded.emit(template_names)
        return template_names

    def add_template(self, name: str, keys: list[str]) -> bool:
        """添加自定义模板"""
        from vibeocr.models.extraction_template import ExtractionTemplate

        template = ExtractionTemplate(name=name, keys=keys)
        success = self._cm.add_template(template)
        if success:
            logger.info(f"模板已添加: {name}")
            self.template_added.emit(name)
        return success

    def delete_template(self, name: str) -> bool:
        """删除自定义模板"""
        success = self._cm.delete_template(name)
        if success:
            logger.info(f"模板已删除: {name}")
            self.template_deleted.emit(name)
        return success

    def get_template_keys(self, name: str) -> list[str] | None:
        """获取模板的键列表"""
        return self._cm.get_template_keys(name)

    def get_preload_config(self) -> dict[str, Any]:
        """获取预加载配置"""
        pipelines = self._cm.get_preload_pipelines()
        return {
            "enabled": len(pipelines) > 0,
            "pipelines": pipelines,
        }
