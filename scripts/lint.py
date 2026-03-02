#!/usr/bin/env python3
"""
代码质量检查脚本
运行所有配置的 linter 和格式化工具

用法:
    python scripts/lint.py          # 运行所有检查
    python scripts/lint.py --fix    # 自动修复问题
    python scripts/lint.py --format # 只运行格式化
    python scripts/lint.py --check  # 只运行检查（不修复）
"""

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def run_command(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """运行命令并打印输出"""
    print(f"\n{'=' * 60}")
    print(f"运行: {' '.join(cmd)}")
    print("=" * 60)
    result = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=False,
        check=False,
    )
    if check and result.returncode != 0:
        print(f"❌ 命令失败: {' '.join(cmd)}")
    else:
        print(f"✅ 命令成功: {' '.join(cmd)}")
    return result


def run_black(*, fix: bool = True) -> int:
    """运行 Black 格式化"""
    if fix:
        return run_command(["black", "src", "tests"]).returncode
    return run_command(["black", "--check", "--diff", "src", "tests"]).returncode


def run_isort(*, fix: bool = True) -> int:
    """运行 isort 导入排序"""
    if fix:
        return run_command(["isort", "src", "tests"]).returncode
    return run_command(["isort", "--check-only", "--diff", "src", "tests"]).returncode


def run_ruff(*, fix: bool = True) -> int:
    """运行 Ruff linter"""
    cmd = ["ruff", "check", "src", "tests"]
    if fix:
        cmd.append("--fix")
    return run_command(cmd).returncode


def run_pyright() -> int:
    """运行 Pyright 类型检查"""
    return run_command(["pyright", "src"]).returncode


def run_mypy() -> int:
    """运行 Mypy 类型检查"""
    return run_command(["mypy", "src"]).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="代码质量检查工具")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="自动修复问题（格式化 + lint 修复）",
    )
    parser.add_argument(
        "--format",
        action="store_true",
        help="只运行格式化工具（black + isort）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只运行检查（不修复）",
    )
    parser.add_argument(
        "--type-check",
        action="store_true",
        help="只运行类型检查（pyright + mypy）",
    )
    args = parser.parse_args()

    total_errors = 0

    # 默认模式：运行所有检查
    if not (args.format or args.type_check):
        if args.fix:
            print("🔧 自动修复模式")
            total_errors += run_black(fix=True)
            total_errors += run_isort(fix=True)
            total_errors += run_ruff(fix=True)
        elif args.check:
            print("🔍 检查模式（不修复）")
            total_errors += run_black(fix=False)
            total_errors += run_isort(fix=False)
            total_errors += run_ruff(fix=False)
            total_errors += run_pyright()
            total_errors += run_mypy()
        else:
            # 默认：格式化 + 检查
            print("📋 格式化并检查")
            total_errors += run_black(fix=True)
            total_errors += run_isort(fix=True)
            total_errors += run_ruff(fix=True)
            total_errors += run_pyright()
            total_errors += run_mypy()

    elif args.format:
        print("💅 格式化模式")
        fix = not args.check
        total_errors += run_black(fix=fix)
        total_errors += run_isort(fix=fix)
        total_errors += run_ruff(fix=fix)

    elif args.type_check:
        print("🔬 类型检查模式")
        total_errors += run_pyright()
        total_errors += run_mypy()

    print("\n" + "=" * 60)
    if total_errors == 0:
        print("✅ 所有检查通过!")
    else:
        print(f"❌ 发现 {total_errors} 个错误")
    print("=" * 60)

    return min(total_errors, 1)


if __name__ == "__main__":
    sys.exit(main())
