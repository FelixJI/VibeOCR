"""src/vibeocr/pipeline_status.py"""

import json
import logging
from pathlib import Path

from vibeocr.machine_cache import generate_machine_id

_logger = logging.getLogger(__name__)

PIPELINE_NAMES = {
    "OCR",
    "PP-StructureV3",
    "PaddleOCR-VL",
    "TABLE_RECOGNITION",
    "FORMULA_RECOGNITION",
    "MinerU",  # 文档解析（首次使用需下载模型，标记成功以跳过重复下载）
}


def _cache_path(project_root: Path) -> Path:
    return project_root / ".vibeocr" / "cache.json"


def _read_cache(project_root: Path) -> dict | None:
    path = _cache_path(project_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("machine_id") != generate_machine_id():
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(project_root: Path, data: dict) -> None:
    path = _cache_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_pipeline_ever_succeeded(pipeline_name: str, project_root: Path) -> bool:
    cache = _read_cache(project_root)
    if cache is None:
        return False
    return bool(cache.get("pipeline_success", {}).get(pipeline_name, False))


def mark_pipeline_success(pipeline_name: str, project_root: Path) -> None:
    cache = _read_cache(project_root)
    if cache is None:
        cache = {"version": 1, "machine_id": generate_machine_id()}
    ps = cache.setdefault("pipeline_success", {})
    ps[pipeline_name] = True
    _write_cache(project_root, cache)
    _logger.debug("管道 %s 标记为已成功", pipeline_name)
