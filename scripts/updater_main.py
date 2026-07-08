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
from update_replacer import _detect_self_exe_names, logger, run_replacement, setup_logging


def _notify_failure(message: str) -> None:
    """用 Windows 原生 MessageBox 弹出更新失败提示。

    updater.exe 以 ``console=False``（windowed）运行，stdout/stderr 不可见，仅写日志文件。
    历史问题：更新失败时用户只看到「应用关了什么都没发生」，因为没有任何 UI 反馈。
    本回调由 run_replacement 在失败路径调用，确保用户能看到失败结论 + 手动下载指引。
    用 ctypes 调 user32.MessageBoxW 避免引入 PySide6（替换器须保持纯 stdlib）。
    """
    if sys.platform != "win32":
        # 非 Windows（开发/CI）退化到 stderr，总比静默好。
        print(message, file=sys.stderr)
        return
    try:
        import ctypes

        # MB_ICONERROR=0x10；返回值忽略。
        ctypes.windll.user32.MessageBoxW(0, message, "VibeOCR 更新失败", 0x10)
    except Exception as e:
        logger.error(f"弹出失败提示框异常: {e}")


def parse_args() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(description="VibeOCR 更新助手")
    parser.add_argument("--update", required=True, help="更新包 zip 路径")
    parser.add_argument("--app-dir", required=True, help="应用目录路径")
    args = parser.parse_args()
    return Path(args.update), Path(args.app_dir)


def main() -> int:
    zip_path, app_dir = parse_args()
    # updater 专用日志文件（与旧版 self_update.log 历史区分，现仅 updater 一条路径）。
    setup_logging(app_dir, "updater.log")
    logger.info("VibeOCR 更新助手启动（updater.exe）")

    # 自动判断新旧路径：updater 自身是否在 app_dir。
    # 新路径（暂存目录运行）无需避让 updater.exe；旧路径（过渡期，自身在 app_dir）需避让。
    # VibeOCR.exe 避让始终保留（容错：旧主程序 _force_quit 后锁可能未及时释放，
    # 新路径下 rename 瞬时成功不产生持久 .old）。
    detected = _detect_self_exe_names(app_dir)
    self_exe_names = (*detected, "VibeOCR.exe")
    logger.info(f"路径判定: detected={detected}, self_exe_names={self_exe_names}")

    # 就绪信号用默认的 updater.ready，与主程序端 _launch_updater 的轮询文件名对应。
    # on_failure: windowed 运行下 stdout 不可见，失败必须弹窗告知用户。
    return run_replacement(
        zip_path,
        app_dir,
        self_exe_names=self_exe_names,
        ready_filename="updater.ready",
        on_failure=_notify_failure,
    )


if __name__ == "__main__":
    sys.exit(main())
