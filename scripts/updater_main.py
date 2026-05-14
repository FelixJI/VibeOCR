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
        print(f"[updater] 错误: SHA256 校验失败")
        print(f"  expected: {expected}")
        print(f"  actual:   {actual}")
        return False
    return True


def extract_zip(zip_path: Path, app_dir: Path) -> Path:
    tmp_dir = app_dir / "data" / "cache" / "update" / "tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    print(f"[updater] 解压更新包...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp_dir)

    # zip 内可能有一层 VibeOCR/ 目录
    contents = list(tmp_dir.iterdir())
    if len(contents) == 1 and contents[0].is_dir():
        return contents[0]
    return tmp_dir


def replace_app_files(new_files_dir: Path, app_dir: Path) -> bool:
    print("[updater] 替换应用文件...")

    # 记录旧 version.json 的 dep_versions
    old_version_json = app_dir / "version.json"
    old_deps: dict = {}
    if old_version_json.exists():
        try:
            old_data = json.loads(old_version_json.read_text(encoding="utf-8"))
            old_deps = old_data.get("dep_versions", {})
        except Exception:
            pass

    # 删除旧文件（保留目录）
    for item in app_dir.iterdir():
        if item.name in _PRESERVE_DIRS:
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        except Exception as e:
            print(f"[updater] 警告: 删除 {item} 失败: {e}")

    # 复制新文件
    for item in new_files_dir.iterdir():
        if item.name in _PRESERVE_DIRS:
            continue
        try:
            dest = app_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        except Exception as e:
            print(f"[updater] 错误: 复制 {item} 失败: {e}")
            return False

    # 检查 AI 依赖版本变化
    new_version_json = app_dir / "version.json"
    if new_version_json.exists():
        try:
            new_data = json.loads(new_version_json.read_text(encoding="utf-8"))
            new_deps = new_data.get("dep_versions", {})
            _sync_dependencies(old_deps, new_deps, app_dir)
        except Exception as e:
            print(f"[updater] 警告: 检查依赖版本失败: {e}")

    return True


def _sync_dependencies(old_deps: dict, new_deps: dict, app_dir: Path) -> None:
    changed = {}
    for pkg, version in new_deps.items():
        if old_deps.get(pkg) != version:
            changed[pkg] = version

    if not changed:
        print("[updater] AI 依赖版本无变化")
        return

    print(f"[updater] 检测到依赖变化: {changed}")
    print("[updater] 开始更新 AI 依赖...")

    python_exe = app_dir / "python" / "python.exe"
    if not python_exe.exists():
        print("[updater] 警告: 未找到嵌入式 Python，跳过依赖更新")
        return

    for pkg, version in changed.items():
        print(f"[updater] 更新 {pkg} → {version}")
        try:
            subprocess.run(
                [str(python_exe), "-m", "pip", "install", "--upgrade", f"{pkg}=={version}"],
                check=True,
                timeout=600,
                creationflags=0x8 if os.name == "nt" else 0,
            )
        except subprocess.CalledProcessError as e:
            print(f"[updater] 警告: 更新 {pkg} 失败: {e}")
        except subprocess.TimeoutExpired:
            print(f"[updater] 警告: 更新 {pkg} 超时")


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

    cleanup(zip_path, new_files_dir.parent if new_files_dir.name != "tmp" else new_files_dir)
    launch_app(app_dir)

    print("[updater] 更新完成!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
