"""设置管理器

管理应用程序设置的加载、保存和验证。
"""

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from vibeocr.models.llm_config import LLMConfig
    from vibeocr.models.extraction_template import ExtractionTemplate

logger = logging.getLogger(__name__)


class SettingsManager(QObject):
    """设置管理器

    管理 LLM 配置、模板和预加载设置的加载、保存和验证。

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

    def __init__(self, project_root: Path, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._config_dir = project_root / "config"
        self._llm_config: Optional["LLMConfig"] = None
        self._templates: List["ExtractionTemplate"] = []

        # 确保配置目录存在
        self._config_dir.mkdir(parents=True, exist_ok=True)

    @property
    def llm_config(self) -> Optional["LLMConfig"]:
        """获取当前 LLM 配置"""
        return self._llm_config

    @property
    def config_dir(self) -> Path:
        """获取配置目录"""
        return self._config_dir

    def load_llm_config(self) -> "LLMConfig":
        """加载 LLM 配置

        Returns:
            LLMConfig 实例
        """
        from vibeocr.models.llm_config import LLMConfig

        config_path = self._config_dir / "llm_config.json"

        try:
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._llm_config = LLMConfig.from_dict(data)
                logger.info(f"LLM 配置已加载: {config_path}")
            else:
                self._llm_config = LLMConfig()
                logger.info("使用默认 LLM 配置")
        except Exception as e:
            logger.warning(f"加载 LLM 配置失败: {e}")
            self._llm_config = LLMConfig()

        self.llm_config_loaded.emit(self._llm_config)
        return self._llm_config

    def save_llm_config(self, config: Optional["LLMConfig"] = None) -> bool:
        """保存 LLM 配置

        Args:
            config: 要保存的配置，如果为 None 则保存当前配置

        Returns:
            是否保存成功
        """
        if config is not None:
            self._llm_config = config

        if self._llm_config is None:
            logger.warning("没有 LLM 配置可保存")
            return False

        config_path = self._config_dir / "llm_config.json"

        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self._llm_config.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"LLM 配置已保存: {config_path}")
            self.llm_config_saved.emit()
            return True
        except Exception as e:
            logger.error(f"保存 LLM 配置失败: {e}")
            return False

    def update_llm_config(
        self,
        service_url: Optional[str] = None,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        """更新 LLM 配置字段

        Args:
            service_url: 服务 URL
            model_name: 模型名称
            api_key: API 密钥
        """
        if self._llm_config is None:
            self.load_llm_config()

        if self._llm_config is None:
            logger.warning("无法更新 LLM 配置：加载失败")
            return

        if service_url is not None:
            self._llm_config.service_url = service_url
        if model_name is not None:
            self._llm_config.model_name = model_name
        if api_key is not None:
            self._llm_config.api_key = api_key

    def load_templates(self) -> List[str]:
        """加载模板列表

        Returns:
            模板名称列表
        """
        from vibeocr.models.extraction_template import ExtractionTemplate, DEFAULT_TEMPLATES

        template_names = []

        # 加载默认模板
        for template in DEFAULT_TEMPLATES:
            template_names.append(template.name)

        # 加载自定义模板
        config_path = self._config_dir / "templates.json"
        self._templates = []

        try:
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    templates_data = json.load(f)
                for template_data in templates_data:
                    template = ExtractionTemplate.from_dict(template_data)
                    self._templates.append(template)
                    template_names.append(f"[自定义] {template.name}")
                logger.info(f"已加载 {len(self._templates)} 个自定义模板")
        except Exception as e:
            logger.warning(f"加载自定义模板失败: {e}")

        self.templates_loaded.emit(template_names)
        return template_names

    def add_template(self, name: str, keys: List[str]) -> bool:
        """添加自定义模板

        Args:
            name: 模板名称
            keys: 提取键列表

        Returns:
            是否添加成功
        """
        config_path = self._config_dir / "templates.json"
        templates = []

        try:
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    templates = json.load(f)

            # 检查名称是否已存在
            for t in templates:
                if t.get("name") == name:
                    logger.warning(f"模板名称已存在: {name}")
                    return False

            # 添加新模板
            templates.append({"name": name, "keys": keys})

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(templates, f, ensure_ascii=False, indent=2)

            logger.info(f"模板已添加: {name}")
            self.template_added.emit(name)
            return True
        except Exception as e:
            logger.error(f"添加模板失败: {e}")
            return False

    def delete_template(self, name: str) -> bool:
        """删除自定义模板

        Args:
            name: 模板名称

        Returns:
            是否删除成功
        """
        config_path = self._config_dir / "templates.json"

        try:
            if not config_path.exists():
                logger.warning("模板配置文件不存在")
                return False

            with open(config_path, "r", encoding="utf-8") as f:
                templates = json.load(f)

            # 查找并删除模板
            original_count = len(templates)
            templates = [t for t in templates if t.get("name") != name]

            if len(templates) == original_count:
                logger.warning(f"未找到模板: {name}")
                return False

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(templates, f, ensure_ascii=False, indent=2)

            logger.info(f"模板已删除: {name}")
            self.template_deleted.emit(name)
            return True
        except Exception as e:
            logger.error(f"删除模板失败: {e}")
            return False

    def get_template_keys(self, name: str) -> Optional[List[str]]:
        """获取模板的键列表

        Args:
            name: 模板名称

        Returns:
            键列表，如果未找到返回 None
        """
        from vibeocr.models.extraction_template import DEFAULT_TEMPLATES

        # 先检查默认模板
        for template in DEFAULT_TEMPLATES:
            if template.name == name:
                return template.keys

        # 再检查自定义模板
        for template in self._templates:
            if template.name == name:
                return template.keys

        return None

    def get_preload_config(self) -> Dict[str, Any]:
        """获取预加载配置

        Returns:
            预加载配置字典
        """
        from vibeocr.machine_cache import get_preload_pipelines

        pipelines = get_preload_pipelines(self._project_root)
        return {
            "enabled": len(pipelines) > 0,
            "pipelines": pipelines,
        }
