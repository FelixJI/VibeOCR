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

    # 每管道默认 TTL（秒）。paddle 重管道（PP-StructureV3 / PaddleOCR-VL）5 分钟；
    # 其他（OCR / TABLE / FORMULA / MinerU）持久缓存（0）。
    _DEFAULT_PIPELINE_TTLS: dict[str, int] = {
        "OCR": 0,
        "TABLE_RECOGNITION": 0,
        "FORMULA_RECOGNITION": 0,
        "PP-StructureV3": 300,
        "MinerU": 0,
        "PaddleOCR-VL": 300,
    }

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
        # 规范化管道名（大小写容错，兼容历史小写配置如 'table_recognition'）
        from vibeocr.core.pipelines import OCRPipeline

        valid_map = {p.value.lower(): p.value for p in OCRPipeline}
        pipelines = [valid_map.get(p.lower(), p) for p in pipelines]
        data["preload_pipelines"] = pipelines
        self._preload_pipelines = pipelines
        success = self._save_json("app_settings.json", data)
        if success:
            self.preload_pipelines_changed.emit(pipelines)
        return success

    @property
    def preload_pipelines(self) -> list[str]:
        return getattr(self, "_preload_pipelines", [])

    def get_pipeline_ttls(self) -> dict[str, int]:
        """返回完整 6 管道 TTL 字典；缺失补默认；自动一次性迁移旧字段。

        迁移语义（spec §2.4）：
          - 旧 ``pipeline_ttl_seconds`` 存在且无 ``pipeline_ttls``：
            paddle 重管道（PP-StructureV3 / PaddleOCR-VL）= 旧值；其余 = 0。
            迁移后删除旧字段。
          - 新旧字段并存：以 dict 为准，不迁移、不删旧字段（避免误删用户手填数据）。
          - 缺失管道：补默认（重管道 300，其余 0）。
          - 损坏值（非 int / bool）：回退到默认。
        """
        data = self._load_json("app_settings.json", {})
        # 一次性迁移：仅当 dict 不存在时执行
        if "pipeline_ttl_seconds" in data and "pipeline_ttls" not in data:
            legacy_raw = data.pop("pipeline_ttl_seconds")
            try:
                legacy = max(0, int(legacy_raw))
            except (TypeError, ValueError):
                legacy = self._DEFAULT_PIPELINE_TTLS["PP-StructureV3"]
            data["pipeline_ttls"] = {
                "OCR": 0,
                "TABLE_RECOGNITION": 0,
                "FORMULA_RECOGNITION": 0,
                "PP-StructureV3": legacy,
                "MinerU": 0,
                "PaddleOCR-VL": legacy,
            }
            self._save_json("app_settings.json", data)
        return self._normalize_ttls(data.get("pipeline_ttls", {}))

    def get_pipeline_ttl_seconds(self) -> int:
        """[legacy bridge] 重管道 TTL 秒数（取 PP-StructureV3 的 TTL）。

        保留给 ``settings_page_controller`` 旧 UI 路径使用；Task 7 重写 UI 后
        会移除。新代码应使用 ``get_pipeline_ttls``。
        """
        return self.get_pipeline_ttls()["PP-StructureV3"]

    def set_pipeline_ttl_seconds(self, ttl: int) -> bool:
        """[legacy bridge] 同时把所有 paddle 重管道置为同一 TTL。

        保留给 ``settings_page_controller`` 旧 UI 路径使用；Task 7 重写 UI 后
        会移除。新代码应使用 ``set_pipeline_ttls``。
        """
        seconds = max(0, int(ttl))
        ttls = self.get_pipeline_ttls()
        ttls["PP-StructureV3"] = seconds
        ttls["PaddleOCR-VL"] = seconds
        return self.set_pipeline_ttls(ttls)

    def set_pipeline_ttl(self, pipeline_name: str, ttl: int) -> bool:
        """设置单个管道的 TTL（0=持久）。未知管道名返回 False。"""
        if pipeline_name not in self._DEFAULT_PIPELINE_TTLS:
            return False
        ttls = self.get_pipeline_ttls()
        ttls[pipeline_name] = max(0, int(ttl))
        return self.set_pipeline_ttls(ttls)

    def set_pipeline_ttls(self, ttls: dict[str, int]) -> bool:
        """批量设置每管道 TTL；非法值回退默认；仅写入已知管道。"""
        data = self._load_json("app_settings.json", {})
        data["pipeline_ttls"] = self._normalize_ttls(ttls)
        return self._save_json("app_settings.json", data)

    def _normalize_ttls(self, raw: object) -> dict[str, int]:
        """规范化 TTL 字典：补齐缺失管道，丢弃非法值（非 int 或 bool）。"""
        if not isinstance(raw, dict):
            raw = {}
        result: dict[str, int] = {}
        for name, default in self._DEFAULT_PIPELINE_TTLS.items():
            val = raw.get(name, default) if isinstance(raw, dict) else default
            # bool 是 int 子类，必须显式拒绝（避免 True/False 被当成 1/0）
            if isinstance(val, bool) or not isinstance(val, int):
                val = default
            result[name] = max(0, val)
        return result

    def get_max_heavy_pipelines(self) -> int | None:
        """手动覆盖的重管道并存上限，None=按显存自动分档。"""
        data = self._load_json("app_settings.json", {})
        val = data.get("max_heavy_pipelines")
        return int(val) if val is not None else None

    def set_max_heavy_pipelines(self, value: int | None) -> bool:
        data = self._load_json("app_settings.json", {})
        data["max_heavy_pipelines"] = value
        return self._save_json("app_settings.json", data)

    def get_log_level(self) -> str:
        """返回持久化日志级别；无效旧值自动回退到 INFO。"""
        data = self._load_json("app_settings.json", {})
        level = str(data.get("log_level", "INFO")).upper()
        return level if level in {"DEBUG", "INFO", "WARNING"} else "INFO"

    def set_log_level(self, level: str) -> bool:
        normalized = str(level).upper()
        if normalized not in {"DEBUG", "INFO", "WARNING"}:
            normalized = "INFO"
        data = self._load_json("app_settings.json", {})
        data["log_level"] = normalized
        return self._save_json("app_settings.json", data)

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
