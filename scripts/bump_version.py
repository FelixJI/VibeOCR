#!/usr/bin/env python3
"""语义化版本管理脚本

用法:
    python scripts/bump_version.py              # 交互式菜单
    python scripts/bump_version.py patch        # 升级修订号 x.y.Z
    python scripts/bump_version.py minor        # 升级次版本 x.Y.0
    python scripts/bump_version.py major        # 升级主版本 X.0.0
    python scripts/bump_version.py 2.0.0        # 指定版本号
    python scripts/bump_version.py ... --no-edit  # 跳过编辑器
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量 — 支持通过环境变量覆盖（便于测试）
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_TOML = Path(os.environ.get("PYPROJECT_TOML", str(PROJECT_ROOT / "pyproject.toml")))
INIT_PY = Path(os.environ.get("INIT_PY", str(PROJECT_ROOT / "src" / "vibeocr" / "__init__.py")))
MAIN_PY = Path(os.environ.get("MAIN_PY", str(PROJECT_ROOT / "src" / "vibeocr" / "main.py")))
CHANGELOG = Path(os.environ.get("CHANGELOG", str(PROJECT_ROOT / "CHANGELOG.md")))

VERSION_RE = re.compile(r'version\s*=\s*"(\d+)\.(\d+)\.(\d+)"')
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------

def read_current_version(pyproject_path: Path) -> tuple[int, int, int]:
    """从 pyproject.toml 中读取当前版本号

    Args:
        pyproject_path: pyproject.toml 文件路径

    Returns:
        (major, minor, patch) 三元组

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 无法匹配版本号
    """
    text = pyproject_path.read_text(encoding="utf-8")
    m = VERSION_RE.search(text)
    if not m:
        raise ValueError(f"无法在 {pyproject_path} 中找到版本号")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def bump_version(
    current: tuple[int, int, int], bump_type: str
) -> tuple[int, int, int]:
    """根据升级类型计算新版本号

    Args:
        current: 当前版本号 (major, minor, patch)
        bump_type: 升级类型 "patch" / "minor" / "major"

    Returns:
        新版本号三元组
    """
    major, minor, patch = current
    if bump_type == "patch":
        return (major, minor, patch + 1)
    elif bump_type == "minor":
        return (major, minor + 1, 0)
    elif bump_type == "major":
        return (major + 1, 0, 0)
    else:
        raise ValueError(f"未知升级类型: {bump_type}")


def update_file_version(file_path: Path, old_version: str, new_version: str) -> None:
    """替换文件中第一次出现的版本号字符串

    Args:
        file_path: 目标文件路径
        old_version: 旧版本号字符串 (如 "0.1.0")
        new_version: 新版本号字符串 (如 "0.2.0")
    """
    text = file_path.read_text(encoding="utf-8")
    text = text.replace(old_version, new_version, 1)
    file_path.write_text(text, encoding="utf-8")


def get_commits_since_last_tag() -> list[tuple[str, str]]:
    """获取自上次 tag 以来的 git 提交

    Returns:
        [(hash, subject), ...] 列表
    """
    # 查找最近的 tag
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        last_tag = result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        last_tag = ""

    # 获取提交列表
    cmd = ["git", "log", "--pretty=format:%h %s"]
    if last_tag:
        cmd.insert(3, f"{last_tag}..HEAD")

    try:
        result = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    commits: list[tuple[str, str]] = []
    stdout = result.stdout or ""
    for line in stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2:
            commits.append((parts[0], parts[1]))
        else:
            commits.append((parts[0], ""))

    return commits


def categorize_commits(
    commits: list[tuple[str, str]],
) -> dict[str, list[str]]:
    """按 conventional commit 前缀分类提交

    分类规则:
        feat:     → Added
        fix:      → Fixed
        refactor, perf, chore, docs: → Changed
        其他      → Changed

    Args:
        commits: [(hash, subject), ...] 列表

    Returns:
        {"Added": [...], "Fixed": [...], "Changed": [...]} 字典
    """
    categories: dict[str, list[str]] = {
        "Added": [],
        "Fixed": [],
        "Changed": [],
    }

    for _, subject in commits:
        # 去掉 scope 部分，如 feat(scope):xxx
        prefix = subject.split(":")[0].split("(")[0].strip().lower()
        msg = subject.strip()

        if prefix == "feat":
            categories["Added"].append(msg)
        elif prefix == "fix":
            categories["Fixed"].append(msg)
        elif prefix in ("refactor", "perf", "chore", "docs"):
            categories["Changed"].append(msg)
        else:
            categories["Changed"].append(msg)

    return categories


def generate_changelog_entry(
    version: str, commits: list[tuple[str, str]]
) -> str:
    """生成 CHANGELOG 条目文本

    Args:
        version: 新版本号字符串
        commits: 提交列表

    Returns:
        格式化的 CHANGELOG 条目
    """
    today = date.today().isoformat()
    lines: list[str] = [f"## [{version}] - {today}", ""]

    categories = categorize_commits(commits)

    for cat_name, cat_commits in categories.items():
        if not cat_commits:
            continue
        lines.append(f"### {cat_name}")
        for commit_msg in cat_commits:
            lines.append(f"- {commit_msg}")
        lines.append("")

    return "\n".join(lines)


def update_changelog(version: str, commits: list[tuple[str, str]]) -> None:
    """更新 CHANGELOG.md，在第一个 ## 标题之前插入新条目

    如果文件不存在则创建。

    Args:
        version: 新版本号字符串
        commits: 提交列表
    """
    entry = generate_changelog_entry(version, commits)

    if CHANGELOG.exists():
        content = CHANGELOG.read_text(encoding="utf-8")
    else:
        content = "# Changelog\n"

    # 在第一个 ## 标题前插入
    idx = content.find("\n## ")
    if idx >= 0:
        # 找到该行的开头（上一个换行符之后）
        insert_pos = idx + 1  # 跳过换行符
        content = content[:insert_pos] + entry + "\n" + content[insert_pos:]
    else:
        # 没有 ## 标题，追加到末尾
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + entry

    CHANGELOG.write_text(content, encoding="utf-8")


def interactive_menu(current: tuple[int, int, int]) -> tuple[int, int, int] | None:
    """交互式版本选择菜单

    Args:
        current: 当前版本号三元组

    Returns:
        新版本号三元组，或 None 表示取消
    """
    major, minor, patch = current
    current_str = f"{major}.{minor}.{patch}"

    patch_new = bump_version(current, "patch")
    minor_new = bump_version(current, "minor")
    major_new = bump_version(current, "major")

    print(f"当前版本: {current_str}")
    print("请选择版本升级方式:")
    print(f"  1) Patch  (修订号)  {current_str} → {'.'.join(map(str, patch_new))}")
    print(f"  2) Minor  (次版本)  {current_str} → {'.'.join(map(str, minor_new))}")
    print(f"  3) Major  (主版本)  {current_str} → {'.'.join(map(str, major_new))}")
    print("  4) 自定义版本号")
    print("  0) 取消")
    print("请输入选项 [0-4]: ", end="", flush=True)

    choice = input().strip()

    if choice == "1":
        return patch_new
    elif choice == "2":
        return minor_new
    elif choice == "3":
        return major_new
    elif choice == "4":
        print("请输入版本号 (x.y.z): ", end="", flush=True)
        custom = input().strip()
        m = SEMVER_RE.match(custom)
        if not m:
            print(f"错误: 无效版本号 '{custom}'")
            return None
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        return None


def _open_editor(file_path: Path) -> None:
    """打开编辑器让用户审阅文件

    Args:
        file_path: 要编辑的文件路径
    """
    editor = os.environ.get("EDITOR")
    if not editor:
        editor = "notepad" if sys.platform == "win32" else "vi"

    try:
        subprocess.run([editor, str(file_path)])
    except FileNotFoundError:
        print(f"警告: 无法找到编辑器 '{editor}'，跳过编辑步骤")


def main() -> int:
    """主入口函数

    Returns:
        退出码 (0=成功, 1=失败)
    """
    # 解析命令行参数
    args = sys.argv[1:]
    no_edit = "--no-edit" in args
    args = [a for a in args if a != "--no-edit"]

    # 读取当前版本
    try:
        current = read_current_version(PYPROJECT_TOML)
    except (FileNotFoundError, ValueError) as e:
        print(f"错误: {e}")
        return 1

    current_str = ".".join(map(str, current))

    # 确定新版本
    if not args:
        # 交互式模式
        new_version = interactive_menu(current)
        if new_version is None:
            print("已取消")
            return 0
    elif args[0] in ("patch", "minor", "major"):
        new_version = bump_version(current, args[0])
    elif SEMVER_RE.match(args[0]):
        m = SEMVER_RE.match(args[0])
        assert m is not None
        new_version = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        print(f"错误: 无效参数 '{args[0]}'")
        print("用法: bump_version.py [patch|minor|major|x.y.z] [--no-edit]")
        return 1

    new_str = ".".join(map(str, new_version))
    print(f"版本升级: {current_str} → {new_str}")

    # 获取提交记录
    commits = get_commits_since_last_tag()

    # 更新文件
    update_file_version(PYPROJECT_TOML, current_str, new_str)
    print(f"  已更新 {PYPROJECT_TOML}")

    if INIT_PY.exists():
        update_file_version(INIT_PY, current_str, new_str)
        print(f"  已更新 {INIT_PY}")

    if MAIN_PY.exists():
        update_file_version(MAIN_PY, current_str, new_str)
        print(f"  已更新 {MAIN_PY}")

    # 更新 CHANGELOG
    update_changelog(new_str, commits)
    print(f"  已更新 {CHANGELOG}")

    # 打开编辑器（CHANGELOG）
    if not no_edit:
        _open_editor(CHANGELOG)

    # Git 操作
    try:
        subprocess.run(["git", "add", str(PYPROJECT_TOML)], check=True)
        if INIT_PY.exists():
            subprocess.run(["git", "add", str(INIT_PY)], check=True)
        if MAIN_PY.exists():
            subprocess.run(["git", "add", str(MAIN_PY)], check=True)
        subprocess.run(["git", "add", str(CHANGELOG)], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"release: v{new_str}"], check=True
        )
        subprocess.run(["git", "tag", f"v{new_str}"], check=True)
        print(f"  已创建 git commit 和 tag v{new_str}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"警告: git 操作失败: {e}")
        return 1

    print(f"完成! 版本已升级到 {new_str}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
