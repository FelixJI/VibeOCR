"""模型缓存管理器

用于管理 PaddleX 模型的本地缓存，避免每次启动时重复检查模型是否存在。

设计原则：
1. 所有管道配置使用 PaddleX 官方 YAML 格式（config/pipelines/*.yaml）
2. 配置文件可直接被 create_pipeline() 使用
3. 代码从配置文件递归提取所有模型信息
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

_logger = logging.getLogger(__name__)

# 缓存版本号
CACHE_VERSION = 4

# PaddleX 默认模型存储目录
DEFAULT_PADDLEX_HOME = Path.home() / ".paddlex"

# 配置文件目录
CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "pipelines"


def get_paddlex_home() -> Path:
    """获取 PaddleX 模型存储主目录"""
    paddlex_home = os.environ.get("PADDLEX_HOME")
    if paddlex_home:
        return Path(paddlex_home)
    return DEFAULT_PADDLEX_HOME


def get_config_dir() -> Path:
    """获取管道配置文件目录"""
    return CONFIG_DIR


def get_model_cache_dir(project_root: Path | None = None) -> Path:
    """获取模型缓存目录路径"""
    if project_root is None:
        project_root = Path(__file__).parent.parent.parent
    return project_root / ".vibeocr"


def get_model_cache_path(project_root: Path | None = None) -> Path:
    """获取模型缓存文件路径"""
    return get_model_cache_dir(project_root) / "model_cache.json"


def get_official_models_dir() -> Path:
    """获取官方模型存储目录"""
    return get_paddlex_home() / "official_models"


def get_inference_models_dir() -> Path:
    """获取推理模型存储目录"""
    return get_paddlex_home() / "inference_model"


def _extract_models_from_config(
    config: dict[str, Any], models: dict | None = None
) -> dict[str, list[str]]:
    """从配置中递归提取所有模型名称

    Args:
        config: 管道配置字典
        models: 累积的模型列表（用于递归）

    Returns:
        {"required": [...], "optional": [...]} 模型名称列表
    """
    if models is None:
        models = {"required": [], "optional": []}

    # 提取 SubModules 中的模型
    submodules = config.get("SubModules", {})
    if submodules:
        for module_config in submodules.values():
            if isinstance(module_config, dict):
                model_name = module_config.get("model_name")
                if model_name:
                    models["required"].append(model_name)

    # 递归提取 SubPipelines 中的模型
    subpipelines = config.get("SubPipelines", {})
    if subpipelines:
        for pipeline_config in subpipelines.values():
            if isinstance(pipeline_config, dict):
                _extract_models_from_config(pipeline_config, models)

    return models


def _load_pipeline_config(pipeline_name: str) -> dict | None:
    """加载管道配置文件

    Args:
        pipeline_name: 管道名称（字符串或 OCRPipeline 枚举）

    Returns:
        配置字典，如果文件不存在则返回 None
    """
    # 处理枚举类型
    from enum import Enum

    if isinstance(pipeline_name, Enum):
        pipeline_name = pipeline_name.value

    config_dir = get_config_dir()

    # 尝试不同的文件名格式
    possible_names = [
        f"{pipeline_name}.yaml",
        f"{pipeline_name}.yml",
        f"{pipeline_name.lower()}.yaml",
        f"{pipeline_name.lower()}.yml",
    ]

    for name in possible_names:
        config_path = config_dir / name
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    return yaml.safe_load(f)
            except Exception as e:
                _logger.warning(f"加载配置文件失败 {config_path}: {e}")
                return None

    return None


def _load_all_pipeline_configs() -> dict[str, dict]:
    """加载所有管道配置文件"""
    configs = {}
    config_dir = get_config_dir()

    if not config_dir.exists():
        _logger.warning(f"配置目录不存在: {config_dir}")
        return configs

    for config_file in config_dir.glob("*.yaml"):
        try:
            with open(config_file, encoding="utf-8") as f:
                config = yaml.safe_load(f)
                if config and "pipeline_name" in config:
                    configs[config["pipeline_name"]] = config
        except Exception as e:
            _logger.warning(f"加载配置文件失败 {config_file}: {e}")

    return configs


def get_pipeline_models(pipeline_name: str) -> dict[str, list[str]]:
    """获取管道所需的所有模型

    Args:
        pipeline_name: 管道名称

    Returns:
        {"required": [...], "optional": [...]} 模型名称列表
    """
    config = _load_pipeline_config(pipeline_name)
    if not config:
        return {"required": [], "optional": []}

    return _extract_models_from_config(config)


def _scan_existing_models() -> dict[str, list[str]]:
    """扫描已存在的模型"""
    models = {
        "official": [],
        "inference": [],
    }

    official_dir = get_official_models_dir()
    if official_dir.exists():
        for item in official_dir.iterdir():
            if item.is_dir():
                models["official"].append(item.name)

    inference_dir = get_inference_models_dir()
    if inference_dir.exists():
        for item in inference_dir.iterdir():
            if item.is_dir():
                models["inference"].append(item.name)

    return models


def _check_model_exists(model_name: str, existing_models: dict[str, list[str]]) -> bool:
    """检查模型是否已下载"""
    all_models = existing_models["official"] + existing_models["inference"]

    # 精确匹配
    if model_name in all_models:
        return True

    # 模糊匹配（处理模型名称变体）
    for name in all_models:
        # 处理 _infer 后缀
        base_name = model_name.replace("_infer", "")
        if name.startswith(base_name) or base_name in name:
            return True

    return False


def _check_pipeline_models_ready(pipeline_name: str) -> tuple[bool, list[str]]:
    """检查指定管道的模型是否都已就绪"""
    models = get_pipeline_models(pipeline_name)
    required = models.get("required", [])

    if not required:
        return True, []

    existing_models = _scan_existing_models()
    missing = []

    for model_name in required:
        # 跳过非本地模型（如 API 调用的 LLM）
        if model_name in ["ernie-3.5-8k", "embedding-v1", "PP-DocBee"]:
            continue
        # 跳过 API 配置
        if model_name in ["api_key", "openai", "qianfan"]:
            continue

        if not _check_model_exists(model_name, existing_models):
            missing.append(model_name)

    return len(missing) == 0, missing


def check_models_cached(pipeline_names: list[str] | None = None) -> dict[str, bool]:
    """检查模型是否已缓存"""
    if pipeline_names is None:
        configs = _load_all_pipeline_configs()
        pipeline_names = list(configs.keys())

    result = {}
    for name in pipeline_names:
        is_ready, _ = _check_pipeline_models_ready(name)
        result[name] = is_ready

    return result


def load_model_cache(project_root: Path | None = None) -> dict | None:
    """加载模型缓存"""
    try:
        cache_file = get_model_cache_path(project_root)
        if not cache_file.exists():
            return None

        with open(cache_file, encoding="utf-8") as f:
            cache = json.load(f)

        if cache.get("version") != CACHE_VERSION:
            _logger.info("模型缓存版本不匹配，将重新扫描")
            return None

        return cache
    except json.JSONDecodeError:
        _logger.warning("模型缓存文件损坏，将重新扫描")
        return None
    except Exception as e:
        _logger.error(f"加载模型缓存失败: {e}")
        return None


def save_model_cache(
    project_root: Path | None = None,
    pipeline_status: dict[str, bool] | None = None,
) -> bool:
    """保存模型缓存"""
    try:
        cache_dir = get_model_cache_dir(project_root)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = get_model_cache_path(project_root)

        if pipeline_status is None:
            pipeline_status = check_models_cached()

        cache = {
            "version": CACHE_VERSION,
            "last_update": datetime.now().isoformat(),
            "paddlex_home": str(get_paddlex_home()),
            "pipelines": pipeline_status,
            "models": _scan_existing_models(),
        }

        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

        _logger.info(f"模型缓存已保存到: {cache_file}")
        return True
    except Exception as e:
        _logger.error(f"保存模型缓存失败: {e}")
        return False


def is_pipeline_cached(pipeline_name: str, project_root: Path | None = None) -> bool:
    """检查指定管道是否已缓存"""
    cache = load_model_cache(project_root)

    if cache and "pipelines" in cache:
        cached_status = cache["pipelines"].get(pipeline_name)
        if cached_status is True:
            is_ready, _ = _check_pipeline_models_ready(pipeline_name)
            if is_ready:
                _logger.debug(f"管道 {pipeline_name} 模型已从缓存确认")
                return True
            else:
                _logger.info(f"管道 {pipeline_name} 缓存过期，模型不存在")
                return False

    is_ready, missing = _check_pipeline_models_ready(pipeline_name)

    if is_ready:
        current_status = check_models_cached()
        save_model_cache(project_root, current_status)
        _logger.info(f"管道 {pipeline_name} 模型已就绪并缓存")
    else:
        _logger.info(f"管道 {pipeline_name} 模型未就绪，缺失: {missing}")

    return is_ready


def get_pipeline_model_info(pipeline_name: str) -> dict:
    """获取管道的模型信息"""
    config = _load_pipeline_config(pipeline_name)
    is_ready, missing = _check_pipeline_models_ready(pipeline_name)
    models = get_pipeline_models(pipeline_name)
    existing_models = _scan_existing_models()

    return {
        "pipeline": pipeline_name,
        "description": config.get("description", "") if config else "",
        "ready": is_ready,
        "missing_models": missing,
        "required_models": models.get("required", []),
        "optional_models": models.get("optional", []),
        "paddlex_home": str(get_paddlex_home()),
        "downloaded_models": existing_models,
    }


def invalidate_cache(project_root: Path | None = None) -> bool:
    """使缓存失效并删除缓存文件"""
    try:
        cache_file = get_model_cache_path(project_root)
        if cache_file.exists():
            cache_file.unlink()
            _logger.info("模型缓存已清除")
        return True
    except Exception as e:
        _logger.error(f"清除模型缓存失败: {e}")
        return False


def update_cache(project_root: Path | None = None) -> dict[str, bool]:
    """强制更新模型缓存"""
    pipeline_status = check_models_cached()
    save_model_cache(project_root, pipeline_status)
    return pipeline_status


def quick_check_all_models(project_root: Path | None = None) -> dict[str, bool]:
    """快速检查所有模型状态"""
    cache = load_model_cache(project_root)

    if cache and "pipelines" in cache:
        all_ready = True
        for pipeline_name, status in cache["pipelines"].items():
            if status:
                is_ready, _ = _check_pipeline_models_ready(pipeline_name)
                if not is_ready:
                    all_ready = False
                    break

        if all_ready:
            _logger.debug("使用缓存的模型状态")
            return cache["pipelines"]

    return update_cache(project_root)


def get_downloaded_models() -> dict[str, list[str]]:
    """获取所有已下载的模型列表"""
    return _scan_existing_models()


def get_all_pipeline_configs() -> dict[str, dict]:
    """获取所有管道配置"""
    return _load_all_pipeline_configs()


def get_pipeline_config_path(pipeline_name: str) -> Path | None:
    """获取管道配置文件路径

    Args:
        pipeline_name: 管道名称

    Returns:
        配置文件路径，如果不存在则返回 None
    """
    config_dir = get_config_dir()

    possible_names = [
        f"{pipeline_name}.yaml",
        f"{pipeline_name}.yml",
    ]

    for name in possible_names:
        config_path = config_dir / name
        if config_path.exists():
            return config_path

    return None
