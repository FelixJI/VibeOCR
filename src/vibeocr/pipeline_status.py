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

# 本地推理管道集合（走 PaddleX/registry，非远程 MinerU）。
# ``mark_pipeline_success`` 的调用方据此判断是否标记——必须覆盖此集合的
# 全部成员，否则被遗漏管道的 ``is_pipeline_ever_succeeded`` 永远为 False，
# 导致 SingleRecognitionTab.run_ocr 每次识别都同步构造 QWebEngineView
# （Chromium 冷启动数百毫秒），表现为截图遮罩在点击管道按钮后卡住。
# MinerU 由各自调用方单独硬编码标记（远程 API，独立路径）。
LOCAL_MARKABLE_PIPELINES = frozenset(
    {
        "OCR",
        "PP-StructureV3",
        "PaddleOCR-VL",
        "TABLE_RECOGNITION",
        "FORMULA_RECOGNITION",
    }
)


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
