"""机器码生成和依赖检测缓存管理模块"""

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

# 缓存版本号（用于缓存格式升级时失效旧缓存）
CACHE_VERSION = 1


def _get_cpu_id() -> str:
    """获取 CPU ID"""
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["wmic", "cpu", "get", "processorid"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:
                    return lines[1].strip()
    except Exception:
        pass
    return ""


def _get_baseboard_serial() -> str:
    """获取主板序列号"""
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["wmic", "baseboard", "get", "serialnumber"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) > 1:
                    return lines[1].strip()
    except Exception:
        pass
    return ""


def _get_mac_address() -> str:
    """获取第一个有效网卡 MAC 地址"""
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["wmic", "nic", "get", "macaddress"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                for line in lines[1:]:
                    mac = line.strip()
                    if mac and mac != "MACAddress":
                        return mac
    except Exception:
        pass
    return ""


def generate_machine_id() -> str:
    """
    生成机器唯一标识码

    组合以下硬件信息生成 SHA256 哈希：
    - CPU ID
    - 主板序列号
    - 第一个有效网卡 MAC 地址

    Returns:
        64字符的十六进制机器码
    """
    # 收集硬件信息
    hardware_info = []
    hardware_info.append(_get_cpu_id())
    hardware_info.append(_get_baseboard_serial())
    hardware_info.append(_get_mac_address())

    # 生成哈希
    combined = "|".join(hardware_info)
    return hashlib.sha256(combined.encode()).hexdigest()


def get_cache_dir(project_root: Path) -> Path:
    """
    获取缓存目录路径

    Args:
        project_root: 项目根目录

    Returns:
        .vibeocr 目录路径
    """
    return project_root / ".vibeocr"


def get_cache_path(project_root: Path) -> Path:
    """
    获取缓存文件路径

    Args:
        project_root: 项目根目录

    Returns:
        cache.json 文件路径
    """
    return get_cache_dir(project_root) / "cache.json"


def save_cache(project_root: Path, data: dict) -> bool:
    """
    保存缓存到文件

    Args:
        project_root: 项目根目录
        data: 缓存数据

    Returns:
        是否保存成功
    """
    try:
        cache_dir = get_cache_dir(project_root)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = get_cache_path(project_root)

        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return True
    except Exception as e:
        print(f"[缓存] 保存缓存失败: {e}")
        return False


def load_cache(project_root: Path) -> Optional[dict]:
    """
    加载缓存

    Args:
        project_root: 项目根目录

    Returns:
        缓存数据，如果不存在或损坏则返回 None
    """
    try:
        cache_file = get_cache_path(project_root)
        if not cache_file.exists():
            return None

        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print("[缓存] 缓存文件损坏，将重新检测")
        return None
    except Exception as e:
        print(f"[缓存] 加载缓存失败: {e}")
        return None
