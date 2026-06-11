#!/usr/bin/env python3
"""
依赖升级脚本
使用 uv 升级依赖并同步 pyproject.toml

用法:
    python qa/upgrade_deps.py           # 升级依赖
    python qa/upgrade_deps.py --dry-run # 预览变更
    python qa/upgrade_deps.py --sync    # 升级后同步环境
    python qa/upgrade_deps.py --stable  # 仅升级到正式版本（排除预发布版本）
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
UV_LOCK_PATH = PROJECT_ROOT / "uv.lock"

# 必须来自 CUDA 索引的包，升级后需要验证
CUDA_PACKAGES = {"torch", "torchvision"}


def run_command(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
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
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if check and result.returncode != 0:
        print(f"[ERROR] 命令失败: {' '.join(cmd)}")
        sys.exit(result.returncode)
    return result


def _uv_env() -> dict[str, str]:
    """构建 uv 运行环境变量，提高并发数加速下载"""
    env = dict(os.environ)
    env.setdefault("UV_CONCURRENT_DOWNLOADS", "10")
    env.setdefault("UV_CONCURRENT_BUILDS", "4")
    return env


def run_command_streaming(cmd: list[str]) -> int:
    """运行命令并实时输出 stdout/stderr，返回退出码"""
    print(f"运行: {' '.join(cmd)}")
    print("-" * 40)
    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_uv_env(),
    )
    if proc.stdout:
        for line in proc.stdout:
            print(line, end="")
    print("-" * 40)
    return proc.wait()


def get_locked_versions() -> dict[str, str]:
    """从 uv.lock 解析已锁定的包版本"""
    if not UV_LOCK_PATH.exists():
        print("[ERROR] uv.lock 文件不存在，请先运行 uv lock")
        return {}

    content = UV_LOCK_PATH.read_text(encoding="utf-8")
    versions = {}

    # 解析 uv.lock 格式 (TOML-like)
    # [[package]]
    # name = "xxx"
    # version = "x.x.x"
    current_name = None
    for line in content.splitlines():
        line = line.strip()
        if line == "[[package]]":
            current_name = None
        elif line.startswith("name = "):
            current_name = line.split('"')[1]
        elif line.startswith("version = ") and current_name:
            version = line.split('"')[1]
            versions[current_name] = version

    return versions


def parse_pyproject_dependencies() -> list[tuple[str, str, int, int]]:
    """解析 pyproject.toml 中的依赖

    返回: [(包名, 原始行, 行号, 缩进长度), ...]
    """
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()
    dependencies = []

    in_deps_section = False
    for i, line in enumerate(lines):
        # 检测 dependencies 数组开始
        if line.strip() == "dependencies = [":
            in_deps_section = True
            continue

        # 检测数组结束
        if in_deps_section and line.strip() == "]":
            in_deps_section = False
            continue

        # 解析依赖行
        if in_deps_section and line.strip().startswith('"'):
            indent = len(line) - len(line.lstrip())
            dep_line = line.strip().strip(",").strip('"')
            dependencies.append((dep_line, line, i, indent))

    return dependencies


def update_pyproject_versions(locked_versions: dict[str, str], *, dry_run: bool = False) -> list[str]:
    """更新 pyproject.toml 中的依赖版本到锁定版本

    返回: 变更列表
    """
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()
    changes = []

    in_deps_section = False
    for i, line in enumerate(lines):
        if line.strip() == "dependencies = [":
            in_deps_section = True
            continue

        if in_deps_section and line.strip() == "]":
            in_deps_section = False
            continue

        if in_deps_section and line.strip().startswith('"'):
            indent = len(line) - len(line.lstrip())
            dep_line = line.strip().strip(",").strip('"')

            # 解析包名和版本约束
            # 支持格式: package, package>=x.x.x, package[x,y]>=x.x.x
            match = re.match(r'^([a-zA-Z0-9_-]+)(\[[^\]]+\])?([<>=!]+.+)?$', dep_line)
            if match:
                pkg_name = match.group(1)
                extras = match.group(2) or ""
                version_spec = match.group(3) or ""

                # 查找锁定版本
                locked_version = locked_versions.get(pkg_name)
                if locked_version and pkg_name not in CUDA_PACKAGES:
                    # 保留上界约束（如 <2.4），只更新下界
                    upper_bounds = [
                        p.strip() for p in version_spec.split(",")
                        if p.strip().startswith(("<",))
                    ] if version_spec else []
                    new_version = ">=" + locked_version
                    if upper_bounds:
                        new_version += "," + ",".join(upper_bounds)
                    new_dep_line = f"{pkg_name}{extras}{new_version}"

                    if new_dep_line != dep_line:
                        # 保留原有的逗号和引号格式
                        has_comma = line.strip().endswith(",")
                        new_line = ' ' * indent + f'"{new_dep_line}"'
                        if has_comma:
                            new_line += ","
                        lines[i] = new_line
                        changes.append(f"  {pkg_name}: {dep_line} -> {new_dep_line}")

    if changes and not dry_run:
        PYPROJECT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return changes


def run_uv_lock_upgrade(
    *, dry_run: bool = False, stable: bool = False
) -> int:
    """运行 uv lock --upgrade，实时输出进度"""
    if dry_run:
        extra = " --no-prerelease" if stable else ""
        print(f"[DRY-RUN] 将运行: uv lock --upgrade{extra}")
        return 0

    cmd = ["uv", "lock", "--upgrade"]
    if stable:
        cmd.append("--no-prerelease")
    return run_command_streaming(cmd)


def verify_cuda_packages_in_lock() -> list[str]:
    """验证 uv.lock 中 CUDA 包的来源是否正确

    检查 torch/torchvision 的 source 字段不是来自通用镜像（CPU 版本）。
    返回有问题的包名列表（空列表表示全部正常）。
    """
    if not UV_LOCK_PATH.exists():
        return list(CUDA_PACKAGES)

    content = UV_LOCK_PATH.read_text(encoding="utf-8")
    bad_packages: list[str] = []

    current_name: str | None = None
    current_source: str | None = None

    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "[[package]]":
            # 检查上一个包
            if current_name in CUDA_PACKAGES and current_source:
                if "pytorch" not in current_source.lower():
                    bad_packages.append(current_name)
            current_name = None
            current_source = None
        elif stripped.startswith("name = ") and current_name is None:
            current_name = stripped.split('"')[1]
        elif stripped.startswith("source = ") and current_name in CUDA_PACKAGES:
            current_source = stripped

    # 检查最后一个包
    if current_name in CUDA_PACKAGES and current_source:
        if "pytorch" not in current_source.lower():
            bad_packages.append(current_name)

    return bad_packages


def verify_cuda_runtime() -> bool:
    """验证已安装的 torch 是否支持 CUDA

    在 uv sync 之后调用，确认 torch 是 CUDA 版本而非 CPU 版本。
    """
    import os

    venv_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        print("[ERROR] 找不到 venv Python，无法验证 CUDA")
        return False

    script = (
        "import torch; "
        "v=torch.__version__; "
        "cuda=torch.cuda.is_available(); "
        "print(f'torch={v} cuda={cuda}'); "
        "raise SystemExit(0 if cuda else 1)"
    )
    result = subprocess.run(
        [str(venv_python), "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.stdout:
        print(f"  {result.stdout.strip()}")
    return result.returncode == 0


def run_uv_sync(*, dry_run: bool = False) -> int:
    """运行 uv sync，实时输出进度"""
    if dry_run:
        print("[DRY-RUN] 将运行: uv sync")
        return 0

    return run_command_streaming(["uv", "sync"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="依赖升级工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python qa/upgrade_deps.py           # 升级依赖并更新 pyproject.toml
  python qa/upgrade_deps.py --dry-run # 预览变更
  python qa/upgrade_deps.py --sync    # 升级后同步环境
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览变更（不实际修改文件）",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="升级后同步环境",
    )
    parser.add_argument(
        "--skip-lock",
        action="store_true",
        help="跳过 uv lock --upgrade（仅更新 pyproject.toml）",
    )
    parser.add_argument(
        "--stable",
        action="store_true",
        help="仅升级到正式版本（排除预发布版本，等价于 uv lock --no-prerelease）",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  依赖升级工具")
    print("=" * 60)

    # Step 1: 运行 uv lock --upgrade
    if not args.skip_lock:
        print("\n[Step 1] 运行 uv lock --upgrade...")
        if args.stable:
            print("[INFO] 已启用 --stable，排除预发布版本")
        result = run_uv_lock_upgrade(
            dry_run=args.dry_run, stable=args.stable
        )
        if result != 0:
            print(f"[FAIL] uv lock --upgrade 失败 (code: {result})")
            return result

        # Step 1.5: 验证 CUDA 包来源
        if not args.dry_run:
            print("\n[Step 1.5] 验证 CUDA 包来源...")
            bad = verify_cuda_packages_in_lock()
            if bad:
                print(f"[FAIL] 以下包未来自 PyTorch CUDA 索引（可能是 CPU 版本）: {', '.join(bad)}")
                print("[HINT] 检查 pyproject.toml 中 [tool.uv.sources] 和 [[tool.uv.index]] 配置")
                print("[HINT] 可能是 PyTorch CUDA 索引尚无对应版本，需要等待或降级版本")
                return 1
            print("[OK] CUDA 包来源验证通过")

    # Step 2: 读取锁定版本
    print("\n[Step 2] 读取锁定版本...")
    locked_versions = get_locked_versions()
    if not locked_versions:
        print("[WARN] 未找到锁定版本")
        return 0

    print(f"[INFO] 找到 {len(locked_versions)} 个锁定包")

    # Step 3: 更新 pyproject.toml
    print("\n[Step 3] 更新 pyproject.toml...")
    changes = update_pyproject_versions(locked_versions, dry_run=args.dry_run)

    if changes:
        print(f"\n变更列表 ({len(changes)} 项):")
        for change in changes:
            print(change)

        if args.dry_run:
            print("\n[DRY-RUN] 未实际修改文件")
        else:
            print("\n[OK] pyproject.toml 已更新")
    else:
        print("\n[OK] 无需更新，所有依赖已是最新")

    # Step 4: 同步环境（可选）
    if args.sync:
        print("\n[Step 4] 同步环境...")
        result = run_uv_sync(dry_run=args.dry_run)
        if result != 0:
            print(f"[FAIL] uv sync 失败 (code: {result})")
            return result
        print("[OK] 环境已同步")

        # Step 4.5: 验证 CUDA 运行时可用性
        if not args.dry_run:
            print("\n[Step 4.5] 验证 CUDA 运行时...")
            if verify_cuda_runtime():
                print("[OK] torch CUDA 可用")
            else:
                print("[FAIL] torch CUDA 不可用，当前为 CPU 版本！")
                print("[HINT] 运行 'uv lock --upgrade' 重新解析，或检查 pyproject.toml 索引配置")
                return 1

    print("\n" + "=" * 60)
    print("[OK] 依赖升级完成!")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
