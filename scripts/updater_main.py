#!/usr/bin/env python3
"""VibeOCR 独立更新助手

由 VibeOCR 主程序在更新时启动，负责：
1. 验证下载的 zip 完整性
2. 替换应用文件（保留 python/、data/、config/）
3. 检测并同步 AI 依赖版本变化
4. 清理临时文件
5. 重新启动 VibeOCR

不依赖 VibeOCR 的任何模块，保持独立可执行。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# 更新时保留的目录
_PRESERVE_DIRS = {"python", "data", "config"}


def parse_args() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(description="VibeOCR 更新助手")
    parser.add_argument("--update", required=True, help="更新包 zip 路径")
    parser.add_argument("--app-dir", required=True, help="应用目录路径")
    args = parser.parse_args()
    return Path(args.update), Path(args.app_dir)


def verify_zip(zip_path: Path) -> bool:
    if not zip_path.exists():
        print(f"[updater] 错误: zip 文件不存在: {zip_path}")
        return False
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                print(f"[updater] 错误: zip 文件损坏，损坏条目: {bad}")
                return False
        return True
    except zipfile.BadZipFile:
        print("[updater] 错误: 无效的 zip 文件")
        return False


def verify_sha256(zip_path: Path) -> bool:
    sha256_path = Path(str(zip_path) + ".sha256")
    if not sha256_path.exists():
        print("[updater] 警告: 未找到 SHA256 校验文件，跳过校验")
        return True

    expected = sha256_path.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = hashlib.sha256(zip_path.read_bytes()).hexdigest().lower()

    if actual != expected:
        print("[updater] 错误: SHA256 校验失败")
        print(f"  expected: {expected}")
        print(f"  actual:   {actual}")
        return False
    return True


def extract_zip(zip_path: Path, app_dir: Path) -> Path:
    tmp_dir = app_dir / "data" / "cache" / "update" / "tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    print("[updater] 解压更新包...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp_dir)

    # zip 内可能有一层 VibeOCR/ 目录
    contents = list(tmp_dir.iterdir())
    if len(contents) == 1 and contents[0].is_dir():
        return contents[0]
    return tmp_dir


def replace_app_files(new_files_dir: Path, app_dir: Path) -> bool:
    """用新文件替换 app_dir 中的非保留内容。

    采用「先备份 → 删除旧 → 复制新 → 失败回滚」策略，确保 app_dir 永远不会
    处于半残状态（旧文件已删、新文件未拷全），否则用户机器上的应用将无法启动。
    """
    print("[updater] 替换应用文件...")

    # 记录旧 version.json 的 dep_versions（用于依赖同步）
    old_version_json = app_dir / "version.json"
    old_deps: dict = {}
    if old_version_json.exists():
        try:
            old_data = json.loads(old_version_json.read_text(encoding="utf-8"))
            old_deps = old_data.get("dep_versions", {})
        except Exception:
            pass

    # 待替换的旧条目（保留目录除外）
    old_items = [item for item in app_dir.iterdir() if item.name not in _PRESERVE_DIRS]

    # 1) 备份将要删除/覆盖的旧条目，以便复制失败时回滚
    backup_dir = app_dir / "data" / "cache" / "update" / "_backup"
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backed_up: list[tuple[Path, Path]] = []  # (原位置, 备份位置)
    try:
        for item in old_items:
            bak = backup_dir / item.name
            if item.is_dir():
                shutil.copytree(item, bak, dirs_exist_ok=True)
            else:
                shutil.copy2(item, bak)
            backed_up.append((item, bak))
    except Exception as e:
        print(f"[updater] 错误: 备份旧文件失败，中止更新: {e}")
        shutil.rmtree(backup_dir, ignore_errors=True)
        return False

    # 2) 删除旧条目
    for item in old_items:
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        except Exception as e:
            print(f"[updater] 警告: 删除 {item} 失败: {e}")

    # 3) 复制新文件；任一失败则回滚
    try:
        for item in new_files_dir.iterdir():
            if item.name in _PRESERVE_DIRS:
                continue
            dest = app_dir / item.name
            try:
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
            except Exception as e:
                # 此处 item 必然已绑定（来自上面的 for）
                print(f"[updater] 错误: 复制 {item} 失败: {e}")
                print("[updater] 正在回滚到更新前状态...")
                _restore_backup(app_dir, backed_up, backup_dir)
                return False
    except Exception as e:
        # iterdir() 自身失败（目录不存在/无权限等），item 此时未绑定
        print(f"[updater] 错误: 读取更新包内容失败: {e}")
        print("[updater] 正在回滚到更新前状态...")
        _restore_backup(app_dir, backed_up, backup_dir)
        return False

    # 4) 复制成功，清理备份
    shutil.rmtree(backup_dir, ignore_errors=True)

    # 5) 检查 AI 依赖版本变化
    new_version_json = app_dir / "version.json"
    if new_version_json.exists():
        try:
            new_data = json.loads(new_version_json.read_text(encoding="utf-8"))
            _sync_dependencies(old_deps, new_data, app_dir)
        except Exception as e:
            print(f"[updater] 警告: 检查依赖版本失败: {e}")

    return True


def _restore_backup(
    app_dir: Path, backed_up: list[tuple[Path, Path]], backup_dir: Path
) -> None:
    """从备份恢复 app_dir 中被删除/覆盖的条目。"""
    # 先清掉复制阶段可能已写入的残缺文件（非保留、非备份目录）
    for item in app_dir.iterdir():
        if item.name in _PRESERVE_DIRS:
            continue
        # 跳过备份目录自身（在 data/ 下，属于保留目录，这里保险起见再判一次）
        try:
            if backup_dir in item.parents or item == backup_dir:
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        except Exception:
            pass

    for original, bak in backed_up:
        try:
            if bak.is_dir():
                shutil.copytree(bak, original, dirs_exist_ok=True)
            else:
                shutil.copy2(bak, original)
        except Exception as e:
            print(f"[updater] 警告: 回滚 {original} 失败: {e}")

    shutil.rmtree(backup_dir, ignore_errors=True)


def _sync_dependencies(old_deps: dict, new_data: dict, app_dir: Path) -> None:
    """检查 AI 依赖版本变化并写入"待同步"标记。

    updater 不能 import vibeocr（python/ 里没装 vibeocr，updater 是独立 --onefile
    打包），因此不在 updater 里直接 pip 安装。改为：若 dep_versions 有变化，把变更项
    写入 data/settings/pending_sync.json，由覆盖后的新版 VibeOCR 启动时用
    env_manager.install_embedded_dependencies（含 GPU/CUDA tag/镜像/PyPI 回退的完整
    逻辑）执行升级。这样避免 updater 用裸 pip 走 PyPI 把 paddle/torch 装成 CPU 版。
    """
    new_deps = new_data.get("dep_versions", {})
    changed = {
        pkg: version
        for pkg, version in new_deps.items()
        if old_deps.get(pkg) != version
    }

    if not changed:
        print("[updater] AI 依赖版本无变化")
        return

    print(f"[updater] 检测到依赖变化: {changed}")
    print("[updater] 写入待同步标记，将由新版 VibeOCR 启动时升级...")

    settings_dir = app_dir / "data" / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    pending_path = settings_dir / "pending_sync.json"

    pending = {
        "version": new_data.get("version", ""),
        "dep_versions": changed,
        "written_at": datetime.now().isoformat(),
    }
    try:
        pending_path.write_text(
            json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[updater] 已写入待同步标记: {pending_path}")
    except Exception as e:
        print(f"[updater] 警告: 写入待同步标记失败（依赖将不会自动升级）: {e}")


def cleanup(zip_path: Path, tmp_dir: Path | None) -> None:
    if tmp_dir and tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)

    zip_path.unlink(missing_ok=True)
    sha256_path = Path(str(zip_path) + ".sha256")
    sha256_path.unlink(missing_ok=True)

    # 清理 update 缓存目录（如果为空）
    update_dir = zip_path.parent
    if update_dir.exists():
        try:
            update_dir.rmdir()
        except OSError:
            pass


def launch_app(app_dir: Path) -> None:
    exe_name = "VibeOCR.exe" if os.name == "nt" else "VibeOCR"
    exe_path = app_dir / exe_name
    if exe_path.exists():
        print(f"[updater] 启动 {exe_path}")
        subprocess.Popen(
            [str(exe_path)],
            creationflags=0x8 if os.name == "nt" else 0,
            cwd=str(app_dir),
        )
    else:
        print(f"[updater] 警告: 未找到主程序 {exe_path}")


def main() -> int:
    print("[updater] VibeOCR 更新助手启动")
    zip_path, app_dir = parse_args()

    if not verify_zip(zip_path):
        return 1
    if not verify_sha256(zip_path):
        return 1

    new_files_dir = extract_zip(zip_path, app_dir)

    if not replace_app_files(new_files_dir, app_dir):
        print("[updater] 更新失败，请手动下载最新版本")
        return 1

    cleanup(
        zip_path, new_files_dir.parent if new_files_dir.name != "tmp" else new_files_dir
    )
    launch_app(app_dir)

    print("[updater] 更新完成!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
