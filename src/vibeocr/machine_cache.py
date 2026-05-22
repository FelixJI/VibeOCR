"""机器码生成和依赖检测缓存管理模块"""

import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

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
    mac = uuid.getnode()
    if mac == uuid.getnode():
        return f"{mac:012X}"
    return ""


_cached_machine_id: str | None = None


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
    global _cached_machine_id
    if _cached_machine_id is not None:
        return _cached_machine_id

    # 收集硬件信息
    hardware_info = []
    hardware_info.append(_get_cpu_id())
    hardware_info.append(_get_baseboard_serial())
    hardware_info.append(_get_mac_address())

    # 生成哈希
    combined = "|".join(hardware_info)
    _cached_machine_id = hashlib.sha256(combined.encode()).hexdigest()
    return _cached_machine_id


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


def load_cache(project_root: Path) -> dict | None:
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

        with open(cache_file, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print("[缓存] 缓存文件损坏，将重新检测")
        return None
    except Exception as e:
        print(f"[缓存] 加载缓存失败: {e}")
        return None


def is_cache_valid(project_root: Path) -> tuple[bool, dict | None]:
    """
    检查缓存是否有效

    缓存有效的条件：
    1. 缓存文件存在
    2. 缓存版本匹配
    3. 机器码匹配

    Args:
        project_root: 项目根目录

    Returns:
        (是否有效, 缓存数据或None)
    """
    cache_data = load_cache(project_root)
    if cache_data is None:
        return False, None

    # 检查版本
    if cache_data.get("version") != CACHE_VERSION:
        print(f"[缓存] 缓存版本不匹配: {cache_data.get('version')} != {CACHE_VERSION}")
        return False, None

    # 检查机器码
    current_machine_id = generate_machine_id()
    cached_machine_id = cache_data.get("machine_id", "")
    if current_machine_id != cached_machine_id:
        return False, None

    return True, cache_data


def clear_cache(project_root: Path) -> bool:
    """
    清除缓存文件

    Args:
        project_root: 项目根目录

    Returns:
        是否清除成功
    """
    try:
        cache_file = get_cache_path(project_root)
        if cache_file.exists():
            cache_file.unlink()
            print("[缓存] 缓存已清除")
        return True
    except Exception as e:
        print(f"[缓存] 清除缓存失败: {e}")
        return False


def create_cache_entry(
    project_root: Path, dependencies: dict, hardware_info: dict
) -> dict | None:
    """
    创建新的缓存条目

    Args:
        project_root: 项目根目录
        dependencies: 依赖检测结果
        hardware_info: 硬件信息

    Returns:
        创建的缓存数据，失败返回 None
    """
    import sys

    cache_data = {
        "version": CACHE_VERSION,
        "machine_id": generate_machine_id(),
        "last_check_time": datetime.now().isoformat(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "dependencies": dependencies,
        "hardware_info": hardware_info,
    }

    if save_cache(project_root, cache_data):
        print("[缓存] 缓存已更新")
        return cache_data
    return None


def refresh_cache(project_root: Path) -> bool:
    """
    刷新缓存（重新生成缓存文件）

    Args:
        project_root: 项目根目录

    Returns:
        是否刷新成功
    """
    import sys

    try:
        cache_data = {
            "version": CACHE_VERSION,
            "machine_id": generate_machine_id(),
            "last_check_time": datetime.now().isoformat(),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "dependencies": {},
            "hardware_info": {},
        }
        if save_cache(project_root, cache_data):
            print("[缓存] 缓存已刷新")
            return True
        return False
    except Exception as e:
        print(f"[缓存] 刷新缓存失败: {e}")
        return False


def get_cache_info(project_root: Path) -> str:
    """
    获取缓存信息字符串

    Args:
        project_root: 项目根目录

    Returns:
        缓存信息字符串
    """
    cache_data = load_cache(project_root)
    if cache_data is None:
        return "无缓存"

    last_check = cache_data.get("last_check_time", "未知")
    version = cache_data.get("version", "未知")
    return f"版本 {version}, 最后检查: {last_check}"
