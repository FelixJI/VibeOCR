#!/usr/bin/env python3
"""将 Qt .ui 文件编译为 Python 代码

用法:
    python scripts/compile_ui.py
    uv run python scripts/compile_ui.py
"""

import subprocess
import sys
from pathlib import Path


def compile_ui_file(ui_path: Path, output_path: Path) -> bool:
    """编译单个 UI 文件"""
    print(f"编译: {ui_path.name} -> {output_path.name}")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "PySide6.QtUiTools.uic",
            "-g",
            "python",
            str(ui_path),
            "-o",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"错误: {result.stderr}")
        return False

    print(f"成功: {output_path}")
    return True


def main():
    """编译所有 UI 文件"""
    # 项目根目录
    project_root = Path(__file__).parent.parent.parent

    # UI 文件目录
    ui_dir = project_root / "src" / "vibeocr" / "ui"

    # 输出目录 (相同目录)
    output_dir = ui_dir

    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)

    # 查找所有 .ui 文件
    ui_files = list(ui_dir.glob("*.ui"))

    if not ui_files:
        print("警告: 未找到 .ui 文件")
        return

    print(f"找到 {len(ui_files)} 个 UI 文件")

    success = True
    for ui_file in ui_files:
        # 输出文件名: ui_XXX.py
        output_name = f"ui_{ui_file.stem}"
        output_path = output_dir / output_name

        if not compile_ui_file(ui_file, output_path):
            success = False

    if success:
        print("\n所有 UI 文件编译成功!")
    else:
        print("\n部分 UI 文件编译失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
