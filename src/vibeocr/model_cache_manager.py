"""模型缓存管理器

用于管理 PaddleX 模型的本地缓存，避免每次启动时重复检查模型是否存在。
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional
from datetime import datetime

_logger = logging.getLogger(__name__)

# 缓存版本号（用于缓存格式升级时失效旧缓存）
CACHE_VERSION = 1

# PaddleX 默认模型存储目录
DEFAULT_PADDLEX_HOME = Path.home() / ".paddlex"

# OCR 相关管道及其对应的模型名称
PIPELINE_MODELS = {
    "OCR": {
        "det_model": "PP-OCRv5_server_det",
        "rec_model": "PP-OCRv5_server_rec",
        "optional_models": ["PP-LCNet_x1_0_doc_ori", "UVDoc"],
    },
    "table_recognition": {
        "structure_model": "SLANet",
        "det_model": "PP-OCRv5_server_det",
        "rec_model": "PP-OCRv5_server_rec",
    },
    "formula_recognition": {
        "formula_model": "PP-FormulaNet-L",
        "det_model": "PP-OCRv5_server_det",
        "rec_model": "PP-OCRv5_server_rec",
    },
    "PP-StructureV3": {
        "layout_model": "PP-DocLayout-L",
        "det_model": "PP-OCRv5_server_det",
        "rec_model": "PP-OCRv5_server_rec",
        "formula_model": "PP-FormulaNet-L",
        "table_model": "SLANet",
    },
}


def get_paddlex_home() -> Path:
    """获取 PaddleX 模型存储主目录"""
    paddlex_home = os.environ.get("PADDLEX_HOME")
    if paddlex_home:
        return Path(paddlex_home)
    return DEFAULT_PADDLEX_HOME


def get_model_cache_dir(project_root: Optional[Path] = None) -> Path:
    """获取模型缓存目录路径

    Args:
        project_root: 项目根目录，如果为 None 则使用当前工作目录

    Returns:
        模型缓存目录路径
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent.parent
    return project_root / ".vibeocr"


def get_model_cache_path(project_root: Optional[Path] = None) -> Path:
    """获取模型缓存文件路径

    Args:
        project_root: 项目根目录，如果为 None 则使用当前工作目录

    Returns:
        模型缓存文件路径
    """
    return get_model_cache_dir(project_root) / "model_cache.json"


def get_official_models_dir() -> Path:
    """获取官方模型存储目录"""
    return get_paddlex_home() / "official_models"


def get_inference_models_dir() -> Path:
    """获取推理模型存储目录"""
    return get_paddlex_home() / "inference_model"


def _scan_existing_models() -> dict[str, list[str]]:
    """扫描已存在的模型

    Returns:
        模型类型到模型名称列表的映射
    """
    models = {
        "official": [],
        "inference": [],
    }

    # 扫描官方模型目录
    official_dir = get_official_models_dir()
    if official_dir.exists():
        for item in official_dir.iterdir():
            if item.is_dir():
                models["official"].append(item.name)

    # 扫描推理模型目录
    inference_dir = get_inference_models_dir()
    if inference_dir.exists():
        for item in inference_dir.iterdir():
            if item.is_dir():
                models["inference"].append(item.name)

    return models


def _check_pipeline_models_ready(pipeline_name: str) -> tuple[bool, list[str]]:
    """检查指定管道的模型是否都已就绪

    Args:
        pipeline_name: 管道名称

    Returns:
        (是否就绪, 缺失的模型列表)
    """
    if pipeline_name not in PIPELINE_MODELS:
        return True, []  # 未知管道，不检查

    config = PIPELINE_MODELS[pipeline_name]
    missing = []

    # 检查所有必需的模型
    all_models = []
    for key, value in config.items():
        if key.endswith("_model"):
            if isinstance(value, list):
                all_models.extend(value)
            else:
                all_models.append(value)
        elif key == "optional_models":
            # 可选模型不检查
            pass

    inference_dir = get_inference_models_dir()
    official_dir = get_official_models_dir()

    for model_name in all_models:
        # 检查推理模型是否存在
        model_found = False

        # 检查推理模型目录
        if inference_dir.exists():
            for item in inference_dir.iterdir():
                if item.is_dir() and model_name in item.name:
                    # 检查是否有模型文件
                    if list(item.glob("*.pdmodel")) or list(item.glob("*.json")):
                        model_found = True
                        break

        # 检查官方模型目录
        if not model_found and official_dir.exists():
            for item in official_dir.iterdir():
                if item.is_dir() and model_name in item.name:
                    model_found = True
                    break

        if not model_found:
            missing.append(model_name)

    return len(missing) == 0, missing


def check_models_cached(pipeline_names: Optional[list[str]] = None) -> dict[str, bool]:
    """检查模型是否已缓存

    Args:
        pipeline_names: 要检查的管道名称列表，如果为 None 则检查所有已知管道

    Returns:
        管道名称到是否缓存的映射
    """
    if pipeline_names is None:
        pipeline_names = list(PIPELINE_MODELS.keys())

    result = {}
    for name in pipeline_names:
        is_ready, _ = _check_pipeline_models_ready(name)
        result[name] = is_ready

    return result


def load_model_cache(project_root: Optional[Path] = None) -> Optional[dict]:
    """加载模型缓存

    Args:
        project_root: 项目根目录

    Returns:
        缓存数据，如果不存在或损坏则返回 None
    """
    try:
        cache_file = get_model_cache_path(project_root)
        if not cache_file.exists():
            return None

        with open(cache_file, "r", encoding="utf-8") as f:
            cache = json.load(f)

        # 验证缓存版本
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
    project_root: Optional[Path] = None,
    pipeline_status: Optional[dict[str, bool]] = None,
) -> bool:
    """保存模型缓存

    Args:
        project_root: 项目根目录
        pipeline_status: 管道状态映射

    Returns:
        是否保存成功
    """
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


def is_pipeline_cached(pipeline_name: str, project_root: Optional[Path] = None) -> bool:
    """检查指定管道是否已缓存

    首先检查缓存文件，如果缓存不存在或过期则进行实际检查。

    Args:
        pipeline_name: 管道名称
        project_root: 项目根目录

    Returns:
        模型是否已缓存且就绪
    """
    # 先尝试加载缓存
    cache = load_model_cache(project_root)

    if cache and "pipelines" in cache:
        cached_status = cache["pipelines"].get(pipeline_name)
        if cached_status is True:
            # 缓存显示模型已就绪，再验证一次目录确实存在
            is_ready, _ = _check_pipeline_models_ready(pipeline_name)
            if is_ready:
                _logger.debug(f"管道 {pipeline_name} 模型已从缓存确认")
                return True
            else:
                # 缓存过期，模型可能已被删除
                _logger.info(f"管道 {pipeline_name} 缓存过期，模型不存在")
                return False

    # 没有缓存或缓存显示未就绪，进行实际检查
    is_ready, missing = _check_pipeline_models_ready(pipeline_name)

    if is_ready:
        # 更新缓存
        current_status = check_models_cached()
        save_model_cache(project_root, current_status)
        _logger.info(f"管道 {pipeline_name} 模型已就绪并缓存")
    else:
        _logger.info(f"管道 {pipeline_name} 模型未就绪，缺失: {missing}")

    return is_ready


def get_pipeline_model_info(pipeline_name: str) -> dict:
    """获取管道的模型信息

    Args:
        pipeline_name: 管道名称

    Returns:
        模型信息字典
    """
    is_ready, missing = _check_pipeline_models_ready(pipeline_name)

    return {
        "pipeline": pipeline_name,
        "ready": is_ready,
        "missing_models": missing,
        "paddlex_home": str(get_paddlex_home()),
    }


def invalidate_cache(project_root: Optional[Path] = None) -> bool:
    """使缓存失效并删除缓存文件

    Args:
        project_root: 项目根目录

    Returns:
        是否成功删除
    """
    try:
        cache_file = get_model_cache_path(project_root)
        if cache_file.exists():
            cache_file.unlink()
            _logger.info("模型缓存已清除")
        return True
    except Exception as e:
        _logger.error(f"清除模型缓存失败: {e}")
        return False


def update_cache(project_root: Optional[Path] = None) -> dict[str, bool]:
    """强制更新模型缓存

    Args:
        project_root: 项目根目录

    Returns:
        更新后的管道状态
    """
    pipeline_status = check_models_cached()
    save_model_cache(project_root, pipeline_status)
    return pipeline_status


# 便捷函数
def quick_check_all_models(project_root: Optional[Path] = None) -> dict[str, bool]:
    """快速检查所有模型状态

    如果有缓存则使用缓存，否则进行实际检查。

    Args:
        project_root: 项目根目录

    Returns:
        所有管道的模型就绪状态
    """
    cache = load_model_cache(project_root)

    if cache and "pipelines" in cache:
        # 验证缓存是否仍然有效
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

    # 重新扫描并更新缓存
    return update_cache(project_root)
