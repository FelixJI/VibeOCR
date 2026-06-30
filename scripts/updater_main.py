#!/usr/bin/env python3
"""VibeOCR 独立更新助手（入口脚本）

由 VibeOCR 主程序在更新时启动，负责：
1. 验证下载的 zip 完整性
2. 替换应用文件（保留 python/、data/、config/）
3. 检测并同步 AI 依赖版本变化
4. 清理临时文件
5. 重新启动 VibeOCR

不依赖 VibeOCR 的任何模块，保持独立可执行。

替换逻辑实现在同目录的 ``update_replacer.py``（共享模块，主程序的 ``--self-update``
兜底模式也复用同一份逻辑）。本文件只负责：参数解析 + 日志配置 + 调用 run_replacement。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 与 update_replacer.py 同目录（scripts/），PyInstaller --onefile 自动收集。
# 打包态下两者都在 PYZ 内，普通 import 即可。
from update_replacer import logger, run_replacement, setup_logging


def parse_args() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(description="VibeOCR 更新助手")
    parser.add_argument("--update", required=True, help="更新包 zip 路径")
    parser.add_argument("--app-dir", required=True, help="应用目录路径")
    args = parser.parse_args()
    return Path(args.update), Path(args.app_dir)


def main() -> int:
    zip_path, app_dir = parse_args()
    # updater 专用日志文件；主程序 --self-update 模式用 self_update.log 区分。
    setup_logging(app_dir, "updater.log")
    logger.info("VibeOCR 更新助手启动（updater.exe）")

    # updater.exe 是独立进程，替换时只需避让自己（主程序 VibeOCR.exe 已在
    # 主程序端 sys.exit 后释放文件锁）。就绪信号用默认的 updater.ready，
    # 与主程序端 _launch_updater 的轮询文件名对应。
    return run_replacement(
        zip_path,
        app_dir,
        self_exe_names=("updater.exe",),
        ready_filename="updater.ready",
    )


if __name__ == "__main__":
    sys.exit(main())
