"""OCR 选项持久化管理器

模块级单例，作为所有界面的 OCR 选项统一数据源。
自动从 config/ocr_preferences.json 加载/保存选项。

注意：不使用 SingletonMeta，因为 QObject 子类与 SingletonMeta 元类不兼容。
改用模块级实例实现单例。
"""

import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from vibeocr.models.ocr_options import OCROptions

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "ocr_preferences.json"

_instance: "OCRPreferences | None" = None


class OCRPreferences(QObject):
    """OCR 选项持久化管理器

    所有 OCR 选项的统一数据源，提供跨界面同步和持久化。

    Usage:
        prefs = OCRPreferences.instance(config_dir)
        options = prefs.get_options()
        prefs.set_options(new_options)  # 自动持久化并发出信号
    """

    options_changed = Signal(object)  # OCROptions

    def __init__(self, config_dir: Path) -> None:
        super().__init__()
        self._config_dir = config_dir
        self._config_path = config_dir / _CONFIG_FILENAME
        self._options = OCROptions()
        self._load()

    @staticmethod
    def instance(config_dir: Path | None = None) -> "OCRPreferences":
        """获取单例实例

        Args:
            config_dir: 首次调用时必须传入配置目录

        Returns:
            OCRPreferences 实例
        """
        global _instance
        if _instance is None:
            if config_dir is None:
                raise RuntimeError("OCRPreferences 首次创建必须传入 config_dir")
            _instance = OCRPreferences(config_dir)
        return _instance

    def _load(self) -> None:
        """从 JSON 文件加载选项"""
        if not self._config_path.exists():
            return
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = json.load(f)
            self._options = OCROptions.from_dict(data)
            logger.info(f"OCR 选项已加载: {self._config_path}")
        except Exception as e:
            logger.warning(f"加载 OCR 选项失败: {e}")

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

    def save(self) -> bool:
        """保存选项到 JSON 文件

        Returns:
            是否保存成功
        """
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._options.to_dict(), f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存 OCR 选项失败: {e}")
            return False
