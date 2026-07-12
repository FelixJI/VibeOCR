# src/vibeocr/utils/ocr_sidecar.py
"""OCR 断点续传 sidecar：记录已增量落盘的页，崩溃后可跳过。

存储位置：<install_root>/.vibeocr/ocr_sessions/<fingerprint>.json
（复用 machine_cache 的 .vibeocr 目录与原子写模式）。

sidecar 是"尽力而为"：写入失败只记日志，不阻断 OCR 主流程。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from vibeocr.env_manager import get_project_root
from vibeocr.machine_cache import get_cache_dir

logger = logging.getLogger(__name__)

SIDECAR_VERSION = 1
_SIDECAR_SUBDIR = "ocr_sessions"


def compute_fingerprint(file_path: str) -> str:
    """文件指纹 = f"{size}:{mtime_ns}"。O(1)，不读全文件。"""
    st = os.stat(file_path)
    return f"{st.st_size}:{int(st.st_mtime_ns)}"


def _sessions_dir() -> Path:
    return get_cache_dir(get_project_root()) / _SIDECAR_SUBDIR


def sidecar_path(file_path: str) -> Path:
    # 指纹含 ":"，在 Windows 上 ":" 会被解析为盘符分隔符（pathlib 把
    # "size:mtime" 当成 drive-relative，丢弃已累积的父目录）。文件名里用
    # "_" 替换 ":"；compute_fingerprint 仍返回 "size:mtime"（供 split 校验）。
    return _sessions_dir() / f"{compute_fingerprint(file_path).replace(':', '_')}.json"


def load_sidecar(file_path: str) -> dict | None:
    """读 sidecar；指纹不匹配或损坏返回 None。"""
    try:
        p = sidecar_path(file_path)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("version") != SIDECAR_VERSION:
            return None
        if data.get("fingerprint") != compute_fingerprint(file_path):
            return None
        return data
    except Exception as e:
        logger.debug("sidecar 读取失败（忽略）: %s", e)
        return None


def save_sidecar(file_path: str, data: dict) -> bool:
    """原子写（tmp + os.replace，复用 machine_cache 模式）。"""
    p = sidecar_path(file_path)
    tmp = p.with_suffix(".json.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception as e:
        logger.warning("sidecar 写入失败（忽略，不阻断 OCR）: %s", e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _new_sidecar(file_path: str) -> dict:
    return {
        "version": SIDECAR_VERSION,
        "file_path": os.path.abspath(file_path),
        "fingerprint": compute_fingerprint(file_path),
        "completed": False,
        "pages": {},
    }


def mark_pages_saved(
    file_path: str, page_indices: list[int], angles: dict[int, int]
) -> bool:
    """增量合并：把 page_indices 标记为已落盘。angles = {page: preproc_angle}。"""
    data = load_sidecar(file_path) or _new_sidecar(file_path)
    for idx in page_indices:
        data["pages"][str(idx)] = {
            "has_text_layer": True,
            "ocr_preproc_angle": int(angles.get(idx, 0)),
        }
    data["completed"] = False
    data["fingerprint"] = compute_fingerprint(file_path)
    return save_sidecar(file_path, data)


def mark_completed(file_path: str) -> bool:
    data = load_sidecar(file_path) or _new_sidecar(file_path)
    data["completed"] = True
    return save_sidecar(file_path, data)


def restore_pending_pages(file_path: str) -> dict[int, int] | None:
    """返回 {page_index: ocr_preproc_angle} 用于续传跳过。

    None 表示：无 sidecar / 指纹不匹配 / 已 completed。
    """
    data = load_sidecar(file_path)
    if data is None or data.get("completed"):
        return None
    return {
        int(k): v.get("ocr_preproc_angle", 0)
        for k, v in data.get("pages", {}).items()
    }
