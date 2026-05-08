"""OCR 选项持久化管理器

模块级单例，作为所有界面的 OCR 选项统一数据源。
自动从 config/ocr_preferences.json 加载/保存选项。

注意：不使用 SingletonMeta，因为 QObject 子类与 SingletonMeta 元类不兼容。
改用模块级实例实现单例。
"""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from vibeocr.core.pipelines import OCRPipeline
from vibeocr.models.ocr_options import OCROptions

if TYPE_CHECKING:
    from vibeocr.managers.config_manager import ConfigManager

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "ocr_preferences.json"
_CONFIG_VERSION = 1

_instance: "OCRPreferences | None" = None


class OCRPreferences(QObject):
    """OCR 选项持久化管理器

    所有 OCR 选项的统一数据源，提供跨界面同步和持久化。

    Usage:
        prefs = OCRPreferences.instance(config_manager)
        options = prefs.get_options()
        prefs.set_options(new_options)  # 自动持久化并发出信号
    """

    options_changed = Signal(object)  # OCROptions
    batch_options_changed = Signal(object)  # OCROptions

    def __init__(self, config_manager: "ConfigManager | Path") -> None:
        super().__init__()
        if isinstance(config_manager, Path):
            self._cm = None
            self._config_dir = config_manager
            self._config_path = config_manager / _CONFIG_FILENAME
        else:
            self._cm = config_manager
            self._config_dir = config_manager.config_dir
            self._config_path = self._config_dir / _CONFIG_FILENAME
        self._options = OCROptions()
        self._batch_options = OCROptions(pipeline=OCRPipeline.DOCUMENT_PARSING)
        self._load()

    @staticmethod
    def instance(config_manager: "ConfigManager | Path | None" = None) -> "OCRPreferences":
        """获取单例实例

        Args:
            config_manager: 首次调用时必须传入 ConfigManager 或 config_dir 路径

        Returns:
            OCRPreferences 实例
        """
        global _instance
        if _instance is None:
            if config_manager is None:
                raise RuntimeError("OCRPreferences 首次创建必须传入 config_manager")
            _instance = OCRPreferences(config_manager)
        return _instance

    @staticmethod
    def reset_instance() -> None:
        """重置单例（仅供测试使用）。"""
        global _instance
        _instance = None

    def _load(self) -> None:
        """从 JSON 文件加载选项"""
        if self._cm is not None:
            data = self._cm._load_json(_CONFIG_FILENAME)
        else:
            if not self._config_path.exists():
                return
            try:
                with open(self._config_path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning(f"加载 OCR 选项失败: {e}")
                return

        if data:
            self._options = OCROptions.from_dict(data)
            # 加载批量选项（独立于单次选项）
            batch_data = data.get("batch_options")
            if batch_data:
                self._batch_options = OCROptions.from_dict(batch_data)
            logger.debug("OCR 选项已加载")

    def get_options(self) -> OCROptions:
        """获取当前选项"""
        return self._options

    def set_options(self, options: OCROptions) -> None:
        """设置选项并持久化

        Args:
            options: 新的 OCR 选项
        """
        # 通过 to_dict → from_dict 规范化，确保枚举类型一致
        self._options = OCROptions.from_dict(options.to_dict())
        self.save()
        self.options_changed.emit(self._options)

    def get_batch_options(self) -> OCROptions:
        """获取批量识别选项"""
        return self._batch_options

    def set_batch_options(self, options: OCROptions) -> None:
        """设置批量识别选项并持久化

        Args:
            options: 新的批量识别选项
        """
        self._batch_options = OCROptions.from_dict(options.to_dict())
        self.save()
        self.batch_options_changed.emit(self._batch_options)

    def save(self) -> bool:
        """保存选项到 JSON 文件

        Returns:
            是否保存成功
        """
        save_data = {
            **self._options.to_dict(),
            "batch_options": self._batch_options.to_dict(),
            "version": _CONFIG_VERSION,
        }
        if self._cm is not None:
            return self._cm._save_json(_CONFIG_FILENAME, save_data)

        # 旧路径兼容
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存 OCR 选项失败: {e}")
            return False
