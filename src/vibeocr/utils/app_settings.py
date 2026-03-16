"""应用级设置管理

管理工具栏自动隐藏、系统托盘最小化、开机自启动等设置的持久化。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 默认设置
_DEFAULTS = {
    "auto_hide_toolbar": False,
    "minimize_to_tray": False,
    "auto_start": False,
    "hide_delay_ms": 500,
}

_CONFIG_FILENAME = "app_settings.json"


class AppSettings:
    """应用设置管理器

    负责加载、保存和访问应用级设置。

    Usage:
        settings = AppSettings(project_root / "config")
        settings.auto_hide_toolbar = True
        settings.save()
    """

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._config_path = config_dir / _CONFIG_FILENAME
        self._data: dict = dict(_DEFAULTS)
        self._load()

    def _load(self) -> None:
        """加载配置文件"""
        if not self._config_path.exists():
            return
        try:
            with open(self._config_path, encoding="utf-8") as f:
                stored = json.load(f)
            for key in _DEFAULTS:
                if key in stored:
                    self._data[key] = stored[key]
            logger.info(f"应用设置已加载: {self._config_path}")
        except Exception as e:
            logger.warning(f"加载应用设置失败: {e}")

    def save(self) -> bool:
        """保存配置到文件"""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            logger.info(f"应用设置已保存: {self._config_path}")
            return True
        except Exception as e:
            logger.error(f"保存应用设置失败: {e}")
            return False

    # ---- 属性 ----

    @property
    def auto_hide_toolbar(self) -> bool:
        return bool(self._data.get("auto_hide_toolbar", False))

    @auto_hide_toolbar.setter
    def auto_hide_toolbar(self, value: bool) -> None:
        self._data["auto_hide_toolbar"] = value

    @property
    def minimize_to_tray(self) -> bool:
        return bool(self._data.get("minimize_to_tray", False))

    @minimize_to_tray.setter
    def minimize_to_tray(self, value: bool) -> None:
        self._data["minimize_to_tray"] = value

    @property
    def auto_start(self) -> bool:
        return bool(self._data.get("auto_start", False))

    @auto_start.setter
    def auto_start(self, value: bool) -> None:
        self._data["auto_start"] = value

    @property
    def hide_delay_ms(self) -> int:
        return int(self._data.get("hide_delay_ms", 500))

    @hide_delay_ms.setter
    def hide_delay_ms(self, value: int) -> None:
        self._data["hide_delay_ms"] = max(100, min(5000, value))
