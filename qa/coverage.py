#!/usr/bin/env python3
"""
测试覆盖率脚本
使用 pytest-cov 生成覆盖率报告

用法:
    python qa/coverage.py           # 运行测试并生成覆盖率报告
    python qa/coverage.py --html    # 生成 HTML 报告
    python qa/coverage.py --xml     # 生成 XML 报告
    python qa/coverage.py --min 80  # 设置最低覆盖率阈值
"""

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# Coverage is a Python unit-test gate. Native-model/subprocess integration and
# C#/WinUI-only checks run in dedicated jobs and are intentionally excluded so
# this command remains deterministic in one Python process.
PYTHON_COVERAGE_IGNORES = (
    "tests/integration",
    "tests/e2e/winui",
    "tests/parity",
    "tests/release_layout/test_winui_layout.py",
    "tests/test_soak_winui.py",
    "tests/test_upgrade_deps.py",
    "tests/architecture/test_protocol_method_consistency.py",
)


def _append_python_test_scope(cmd: list[str]) -> None:
    cmd.extend(f"--ignore={path}" for path in PYTHON_COVERAGE_IGNORES)
    cmd.append("tests/")


def run_command(cmd: list[str]) -> subprocess.CompletedProcess:
    """运行命令并返回结果"""
    print(f"运行: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # 始终打印输出，确保在被父脚本调用时输出能被捕获
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result


def run_coverage(
    *,
    html: bool = False,
    xml: bool = False,
    min_coverage: int | None = None,
    verbose: bool = True,
) -> int:
    """运行测试并生成覆盖率报告"""
    cmd = [sys.executable, "-m", "pytest"]

    if verbose:
        cmd.append("-v")

    cmd.extend(["--cov=vibeocr", "--cov-report=term-missing"])

    if html:
        cmd.append("--cov-report=html:htmlcov")
    if xml:
        cmd.append("--cov-report=xml:coverage.xml")
    if min_coverage is not None:
        cmd.append(f"--cov-fail-under={min_coverage}")

    _append_python_test_scope(cmd)

    return run_command(cmd).returncode


def run_quick_coverage() -> int:
    """快速覆盖率检查（无详细输出）"""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--cov=vibeocr",
        "--cov-report=term",
        "-q",
    ]
    _append_python_test_scope(cmd)
    return run_command(cmd).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="测试覆盖率工具")
    parser.add_argument(
        "--html",
        action="store_true",
        help="生成 HTML 覆盖率报告",
    )
    parser.add_argument(
        "--xml",
        action="store_true",
        help="生成 XML 覆盖率报告",
    )
    parser.add_argument(
        "--min",
        type=int,
        default=None,
        metavar="N",
        help="设置最低覆盖率阈值 (0-100)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="快速模式（减少输出）",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("测试覆盖率")
    print("=" * 60)

    if args.quick:
        result = run_quick_coverage()
    else:
        result = run_coverage(
            html=args.html,
            xml=args.xml,
            min_coverage=args.min,
        )

    print("\n" + "=" * 60)
    if result == 0:
        print("[OK] 覆盖率检查通过!")
        if args.html:
            print("[REPORT] HTML 报告已生成到 htmlcov/index.html")
        if args.xml:
            print("[REPORT] XML 报告已生成到 coverage.xml")
    else:
        print("[FAIL] 覆盖率检查未通过")
    print("=" * 60)

    return result


if __name__ == "__main__":
    sys.exit(main())
