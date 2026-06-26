#!/usr/bin/env python3
"""语义化版本管理与打包脚本

用法:
    python scripts/bump_version.py              # 交互式菜单（含"仅打包当前版本"）
    python scripts/bump_version.py patch        # 升级修订号 x.y.Z
    python scripts/bump_version.py minor        # 升级次版本 x.Y.0
    python scripts/bump_version.py major        # 升级主版本 X.0.0
    python scripts/bump_version.py 2.0.0        # 指定版本号
    python scripts/bump_version.py ... --no-edit  # 跳过编辑器
    python scripts/bump_version.py --build      # 打包当前版本
    python scripts/bump_version.py --rebuild 1.2.3  # 重新打包指定版本
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量 — 支持通过环境变量覆盖（便于测试）
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_TOML = Path(
    os.environ.get("PYPROJECT_TOML", str(PROJECT_ROOT / "pyproject.toml"))
)
INIT_PY = Path(
    os.environ.get("INIT_PY", str(PROJECT_ROOT / "src" / "vibeocr" / "__init__.py"))
)
MAIN_PY = Path(
    os.environ.get("MAIN_PY", str(PROJECT_ROOT / "src" / "vibeocr" / "main.py"))
)
CHANGELOG = Path(os.environ.get("CHANGELOG", str(PROJECT_ROOT / "CHANGELOG.md")))
UV_LOCK = Path(os.environ.get("UV_LOCK", str(PROJECT_ROOT / "uv.lock")))

VERSION_RE = re.compile(r'version\s*=\s*"(\d+)\.(\d+)\.(\d+)"')
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# ---------------------------------------------------------------------------
# 打包常量
# ---------------------------------------------------------------------------
APP_ICON = PROJECT_ROOT / "resources" / "app_icon.ico"
DIST_BASE_DIR = PROJECT_ROOT / "dist"

# PyInstaller 排除的大依赖（由嵌入式 Python 独立安装）
EXCLUDED_PACKAGES = [
    "paddle",
    "paddlepaddle",
    "paddlepaddle_gpu",
    "paddlex",
    "mineru",
    "torch",
    "torchvision",
    "torchaudio",
    "nvidia",
    "triton",
]

# PySide6 中本项目完全未使用的子模块。
#
# 代码实际只用：QtCore / QtGui / QtWidgets / QtSvg / QtPdf /
# QtWebChannel / QtWebEngineCore / QtWebEngineWidgets，以及 WebEngine 的
# 依赖 QtNetwork / QtOpenGL / QtPrintSupport / QtPositioning。
# 下方所有模块确认无引用，排除后不触发 import 即不打入 .pyd/.dll。
# 注意：--exclude-module 只阻止 Python 侧 import 收集，对应 Qt6*.dll 仍会被
# PyInstaller 二进制依赖扫描带入，需配合 CLEANUP_QT_BINARIES 删除。
EXCLUDED_QT_MODULES = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtWebSockets",
    "PySide6.QtXml",
]

# 打包后需删除的无用 Qt 二进制（PyInstaller 的二进制依赖扫描无法识别这些
# 是未使用模块的附属 DLL，会全量收入）。
#
# ⚠ 依赖保留清单：QtWebChannel / QtWebEngine 在 C++ 层依赖 Qml + Quick 核心，
#    故以下必须保留，不可删除：
#      Qt6Qml / Qt6QmlCore / Qt6QmlMeta / Qt6QmlModels / Qt6QmlNetwork
#      Qt6Quick / Qt6QuickWidgets
#    （QtWebChannel.dll→Qt6Qml.dll；Qt6WebEngineCore.dll→Qt6Qml/Qt6Quick.dll；
#     Qt6WebEngineWidgets.dll→Qt6QuickWidgets.dll。误删会导致
#     "DLL load failed while importing QtWebChannel"。）
# 可删除的是 Quick 的扩展控件（Controls2/Shapes/Layouts/Dialogs 等）与
# 3D/图表/传感器等独立模块——它们不被 WebChannel/WebEngine 依赖。
#
# - opengl32sw.dll：软件渲染兜底，目标机器有 GPU 时用不到
# - Qt6VirtualKeyboard/Qt6Test/Qt6Scxml/Qt6TextToSpeech/Qt6SerialPort 等：
#   对应排除的子模块
# - PySide6/translations：Qt 全语种翻译（~53MB），仅保留 qtbase 中文
CLEANUP_QT_BINARIES = [
    "Qt63DAnimation.dll",
    "Qt63DCore.dll",
    "Qt63DExtras.dll",
    "Qt63DInput.dll",
    "Qt63DLogic.dll",
    "Qt63DRender.dll",
    "Qt6Charts.dll",
    "Qt6ChartsQml.dll",
    "Qt6DataVisualization.dll",
    "Qt6DataVisualizationQml.dll",
    "Qt6Graphs.dll",
    "Qt6Location.dll",
    "Qt6Multimedia.dll",
    "Qt6MultimediaQuick.dll",
    "Qt6PdfQuick.dll",
    "Qt6PositioningQuick.dll",
    # 注意：Qt6Qml*/Qt6Quick/Qt6QuickWidgets 不可删（WebChannel/WebEngine 依赖）。
    # Qml 核心模块之间有交叉依赖（如 Qt6QmlMeta→Qt6QmlWorkerScript），
    # 整组 Qt6Qml* 必须全保留；Qt6Qml 系列总共仅十几 MB，不值得冒险逐个删。
    # Qt6Quick.dll / Qt6QuickWidgets.dll 同样是 WebEngine 的 C++ 依赖。
    "Qt6Quick3D.dll",
    "Qt6QuickControls2.dll",
    "Qt6QuickControls2Basic.dll",
    "Qt6QuickControls2Fusion.dll",
    "Qt6QuickControls2Imagine.dll",
    "Qt6QuickControls2Material.dll",
    "Qt6QuickControls2Universal.dll",
    "Qt6QuickDialogs2.dll",
    "Qt6QuickEffects.dll",
    "Qt6QuickLayouts.dll",
    "Qt6QuickParticles.dll",
    "Qt6QuickShapes.dll",
    "Qt6QuickTemplates2.dll",
    "Qt6QuickTest.dll",
    "Qt6QuickTimeline.dll",
    # Qt6QuickWidgets.dll 保留（WebEngineWidgets 依赖）
    "Qt6RemoteObjects.dll",
    "Qt6Scxml.dll",
    "Qt6Sensors.dll",
    "Qt6SerialPort.dll",
    "Qt6ShaderTools.dll",
    "Qt6SpatialAudio.dll",
    "Qt6Test.dll",
    "Qt6TextToSpeech.dll",
    "Qt6VirtualKeyboard.dll",
    "Qt6WebSockets.dll",
    "Qt6WebView.dll",
    "opengl32sw.dll",
]

# 需要打包进 exe 的数据文件 (源目录, 目标目录)
PACKAGE_DATA = [
    ("config", "config"),
    ("resources", "resources"),
]

# vibeocr 子模块通过 --collect-submodules 自动收集，此处只列出第三方包
HIDDEN_IMPORTS = [
    "shiboken6",
    "qasync",
    "httpx",
    "PIL",
    "numpy",
    "markdown",
    "qrcode",
    "qrcode.image.pil",
    "barcode",
    "barcode.writer",
    "fitz",
]


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
    return (int(m[1]), int(m[2]), int(m[3]))


def bump_version(current: tuple[int, int, int], bump_type: str) -> tuple[int, int, int]:
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
    if bump_type == "minor":
        return (major, minor + 1, 0)
    if bump_type == "major":
        return (major + 1, 0, 0)
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


def _collect_commits(
    rev_range: str, cwd: Path | None = None
) -> list[tuple[str, str]]:
    """收集给定 git 范围内的提交

    Args:
        rev_range: git 修订范围，如 "v0.1.0..HEAD" 或 "main..develop"
        cwd: git 仓库目录；None 表示继承调用者 CWD（与原 get_commits_since_last_tag
            行为一致，子进程跑在脚本启动目录）。测试可显式传 tmp_path 隔离。

    Returns:
        [(hash, subject), ...] 列表
    """
    cmd = ["git", "log", "--pretty=format:%h %s", rev_range]
    # cwd=None 时不传 cwd，让子进程继承调用者工作目录（保持原行为）
    kwargs: dict[str, object] = {"capture_output": True, "encoding": "utf-8", "check": True}
    if cwd is not None:
        kwargs["cwd"] = str(cwd)

    try:
        result = subprocess.run(cmd, **kwargs)  # type: ignore[arg-type]
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


def _filter_release_commits(
    commits: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """过滤掉 ``release: vX.Y.Z`` 提交（版本边界，非功能性改动）

    Args:
        commits: [(hash, subject), ...]

    Returns:
        不含 release: 前缀提交的列表
    """
    return [(h, s) for h, s in commits if not s.startswith("release: ")]


def get_commits_since_last_tag() -> list[tuple[str, str]]:
    """获取自上次 tag 以来的 git 提交（develop bump 路径用，保留向后兼容）"""
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

    if last_tag:
        return _collect_commits(f"{last_tag}..HEAD")
    return _collect_commits("HEAD")


def check_unversioned_commits(
    version: str, cwd: Path | None = None
) -> tuple[bool, int]:
    """检测当前版本号之后是否有未版本化提交（发版安全闸）

    找 ``release: v{version}`` 提交，比较它与 HEAD：
    - 相等 → HEAD 即 release 点，干净，返回 (False, 0)。
    - 不等 → 用 rev-list 统计其后提交数，返回 (True, N)。
    - 找不到 release 提交 → 保守视为全部未版本化，
      N 取该仓库全部提交数。

    Args:
        version: 当前版本号字符串（如 "0.1.6"）
        cwd: git 仓库目录；None 表示继承调用者 CWD

    Returns:
        (是否有未版本化提交, 未版本化提交数)
    """
    kwargs: dict[str, object] = {"capture_output": True, "encoding": "utf-8", "check": True}
    if cwd is not None:
        kwargs["cwd"] = str(cwd)

    # 找 release 提交的完整 hash
    try:
        result = subprocess.run(
            ["git", "log", "--grep", f"^release: v{version}$", "--pretty=%H", "-1"],
            **kwargs,  # type: ignore[arg-type]
        )
        release_hash = (result.stdout or "").strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        release_hash = ""

    if not release_hash:
        # 找不到 release 点：保守统计全部提交数
        try:
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                **kwargs,  # type: ignore[arg-type]
            )
            total = int((result.stdout or "0").strip() or "0")
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            total = 0
        return (True, total)

    # release 点之后有多少提交
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{release_hash}..HEAD"],
            **kwargs,  # type: ignore[arg-type]
        )
        count = int((result.stdout or "0").strip() or "0")
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        count = 0

    return (count > 0, count)


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


def generate_changelog_entry(version: str, commits: list[tuple[str, str]]) -> str:
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


def generate_consolidated_entry(
    version: str, commits: list[tuple[str, str]]
) -> str:
    """生成合并至 main 用的单条整合 CHANGELOG 条目

    与 generate_changelog_entry 区别：自动过滤 release: 提交、对 subject 去重。
    合并至 main 时，多个 develop 小版本的提交揉成一条正式版条目。

    Args:
        version: 整合后的目标版本号字符串（如 "0.1.6"）
        commits: 范围内的原始提交列表（含 release: 提交，会被过滤）

    Returns:
        格式化的单条 CHANGELOG 条目（## [version] - 日期 + 分类）
    """
    today = date.today().isoformat()
    lines: list[str] = [f"## [{version}] - {today}", ""]

    filtered = _filter_release_commits(commits)
    # 按 subject 去重，保留首次出现顺序
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for h, s in filtered:
        if s not in seen:
            seen.add(s)
            deduped.append((h, s))

    categories = categorize_commits(deduped)
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


def update_main_changelog(entry: str) -> None:
    """把整合条目插入 main 的 CHANGELOG.md 顶部

    与 update_changelog 区别：接受已生成好的 entry 文本（由
    generate_consolidated_entry 产出），不复算分类。

    Args:
        entry: 单条 CHANGELOG 条目文本（含 ``## [version]`` 标题）
    """
    if CHANGELOG.exists():
        content = CHANGELOG.read_text(encoding="utf-8")
    else:
        content = "# Changelog\n"

    # 在第一个 ## 标题前插入（与 update_changelog 同逻辑）
    idx = content.find("\n## ")
    if idx >= 0:
        insert_pos = idx + 1
        content = content[:insert_pos] + entry + "\n" + content[insert_pos:]
    else:
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + entry

    CHANGELOG.write_text(content, encoding="utf-8")


def interactive_menu(current: tuple[int, int, int]) -> tuple[int, int, int] | str | None:
    """交互式操作选择菜单

    Args:
        current: 当前版本号三元组

    Returns:
        新版本号三元组 / 字符串 "build"（仅打包当前版本，不升级版本号）/
        字符串 "merge"（合并至 main）/ None（取消）
    """
    major, minor, patch = current
    current_str = f"{major}.{minor}.{patch}"

    patch_new = bump_version(current, "patch")
    minor_new = bump_version(current, "minor")
    major_new = bump_version(current, "major")

    print(f"当前版本: {current_str}")
    print("请选择操作:")
    print(f"  1) Patch  (修订号)  {current_str} → {'.'.join(map(str, patch_new))}")
    print(f"  2) Minor  (次版本)  {current_str} → {'.'.join(map(str, minor_new))}")
    print(f"  3) Major  (主版本)  {current_str} → {'.'.join(map(str, major_new))}")
    print("  4) 自定义版本号")
    print(f"  5) 仅打包当前版本（{current_str}，不升级版本号）")
    print("  6) 合并至 main（squash + 整合 CHANGELOG + 打 tag）")
    print("  0) 取消")
    print("请输入选项 [0-6]: ", end="", flush=True)

    choice = input().strip()

    if choice == "1":
        return patch_new
    if choice == "2":
        return minor_new
    if choice == "3":
        return major_new
    if choice == "4":
        print("请输入版本号 (x.y.z): ", end="", flush=True)
        custom = input().strip()
        m = SEMVER_RE.match(custom)
        if not m:
            print(f"错误: 无效版本号 '{custom}'")
            return None
        return (int(m[1]), int(m[2]), int(m[3]))
    if choice == "5":
        # 仅打包当前版本，不升级版本号、不动 git
        return "build"
    if choice == "6":
        # 合并至 main：由 main() 识别 'merge' 哨兵后调用 cmd_to_main
        return "merge"
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


def _sync_uv_lock(version: str) -> bool:
    """同步 uv.lock 中的 vibeocr 自身版本号

    pyproject.toml 的 project.version 升级后，uv.lock 里 editable 包
    （name = "vibeocr"）记录的 version 不会自动更新，需手动跑 ``uv lock``
    刷新。历史发版（v0.1.1/v0.1.2）均漏了这一步，导致 lock 滞后于实际版本。

    本函数在版本号文件更新后、git 提交前调用，确保 uv.lock 与版本号同源、
    同提交。uv 不可用时降级为警告（不阻断发版，但会提示手动处理）。

    Args:
        version: 新版本号字符串（仅用于日志）

    Returns:
        uv.lock 是否已更新（False 表示 uv 不可用或无变化）
    """
    lock_path = UV_LOCK
    if not lock_path.exists():
        return False

    try:
        result = subprocess.run(
            ["uv", "lock"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError:
        print("  警告: 未找到 uv 命令，uv.lock 未同步（请手动运行 `uv lock`）")
        return False
    except subprocess.TimeoutExpired:
        print("  警告: uv lock 超时，uv.lock 未同步")
        return False

    if result.returncode != 0:
        print(f"  警告: uv lock 失败（退出码 {result.returncode}）")
        if result.stderr:
            print(f"    {result.stderr.strip()[:200]}")
        return False

    # 检查 git 是否检测到变化（uv lock 也可能因依赖变动产生其它改动）
    diff = subprocess.run(
        ["git", "diff", "--quiet", str(lock_path)],
        capture_output=True,
        text=True,
    )
    if diff.returncode != 0:
        print(f"  已同步 {lock_path.name}（vibeocr → {version}）")
        return True
    return False


# ---------------------------------------------------------------------------
# 打包功能
# ---------------------------------------------------------------------------


def _generate_version_json(version: str, dist_dir: Path) -> None:
    """生成 version.json 到输出目录"""
    import tomllib

    tomldata = tomllib.loads(PYPROJECT_TOML.read_text(encoding="utf-8"))
    deps = tomldata.get("project", {}).get("dependencies", [])

    # 需要追踪版本的包前缀（对应 EXCLUDED_PACKAGES 中排除的大依赖）
    _TRACKED_PREFIXES = ("paddle", "paddleocr", "mineru", "torch", "nvidia")
    _KEY_ALIASES = {"paddlepaddle-gpu": "paddlepaddle"}

    dep_versions: dict[str, str] = {}
    for dep in deps:
        dep = dep.strip()
        if dep.startswith("#"):
            continue
        for op in [">=", "==", "<=", "~="]:
            if op in dep:
                pkg, ver = dep.split(op, 1)
                pkg = pkg.strip().lower()
                # 剥掉 extras 后缀：paddleocr[doc-parser] → paddleocr
                # 使 key 与 env_config.OCR_CHECK_MODULES 包名一致
                pkg = pkg.split("[", 1)[0]
                if any(pkg.startswith(p) for p in _TRACKED_PREFIXES):
                    # dict.get 在 _KEY_ALIASES 命中时返回别名，否则回退 pkg（恒非 None）；
                    # 静态签名是 str|None，故用 pkg 默认值并显式断言收窄。
                    key: str = _KEY_ALIASES.get(pkg) or pkg
                    dep_versions[key] = ver.strip()
                break

    # python_version 读自 .python-version（单一源，避免与 pyproject requires-python 漂移）
    dot_python_version_path = PROJECT_ROOT / ".python-version"
    if dot_python_version_path.exists():
        python_version = dot_python_version_path.read_text(encoding="utf-8").strip()
    else:
        python_version = "3.13"  # fallback：与 .python-version 默认值一致

    data = {
        "version": version,
        "channel": "stable",
        "python_version": python_version,
        "dep_versions": dep_versions,
    }
    version_path = dist_dir / "version.json"
    version_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  已生成 {version_path}")


def _build_updater(dist_dir: Path) -> bool:
    """打包 updater.exe"""
    updater_script = PROJECT_ROOT / "scripts" / "updater_main.py"
    if not updater_script.exists():
        print(f"错误: updater 脚本不存在: {updater_script}")
        return False

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(updater_script),
        "--onefile",
        "--name",
        "updater",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(DIST_BASE_DIR / "build-updater"),
        "--specpath",
        str(DIST_BASE_DIR),
    ]

    if os.name == "nt":
        cmd.append("--windowed")

    print("打包 updater.exe...")
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"updater 打包失败: {e.returncode}")
        return False


def _cleanup_dist(dist_dir: Path) -> None:
    """打包后清理无用 Qt 二进制，削减体积

    PyInstaller 的 --exclude-module 只阻止 Python 侧 import 收集，但 Qt 的
    Qt6*.dll 是被二进制依赖扫描带进来的（PyInstaller 无法判断某个 DLL
    属于哪个 Qt 模块），所以排除模块后这些 DLL 仍残留在 _internal。
    这里在打包完成后、打 zip 前显式删除它们。

    删除范围见 CLEANUP_QT_BINARIES（仅删确认无用的 QML/3D/图表/传感器等），
    WebEngine 必需的 Qt6WebEngineCore/Qt6Core/Qt6Gui/Qt6Widgets 等全部保留。
    同时精简 PySide6/translations（仅保留 qtbase 中文翻译）。

    Args:
        dist_dir: VibeOCR 应用目录（含 _internal）
    """
    pyside6_dir = dist_dir / "_internal" / "PySide6"
    deleted = 0
    freed_bytes = 0

    for name in CLEANUP_QT_BINARIES:
        target = pyside6_dir / name
        if target.exists():
            freed_bytes += target.stat().st_size
            target.unlink()
            deleted += 1

    # 精简 translations：删除除 qtbase_zh_CN.qm 外的所有 .qm（~53MB → ~几十 KB）
    trans_dir = pyside6_dir / "translations"
    if trans_dir.is_dir():
        for qm in trans_dir.glob("*.qm"):
            if qm.name != "qtbase_zh_CN.qm":
                freed_bytes += qm.stat().st_size
                qm.unlink()
                deleted += 1

    freed_mb = freed_bytes / (1024 * 1024)
    print(f"  清理无用 Qt 文件: 删除 {deleted} 个，释放 {freed_mb:.1f} MB")


def _package_zip(dist_dir: Path, version: str) -> Path | None:
    """将 dist_dir 打包为 zip 并计算 SHA256"""
    zip_name = f"VibeOCR-v{version}-win64"
    zip_path = DIST_BASE_DIR / f"{zip_name}.zip"

    # 解压后顶层文件夹命名为 VibeOCR（而非版本号目录），方便用户手动拖出。
    # zip 文件名本身保留版本号，便于分发与归档。
    top_folder = "VibeOCR"

    print(f"打包 {zip_path}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in dist_dir.rglob("*"):
            if file_path.is_file():
                arcname = f"{top_folder}/{file_path.relative_to(dist_dir)}"
                zf.write(file_path, arcname)

    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sha256_path = DIST_BASE_DIR / f"{zip_name}.zip.sha256"
    sha256_path.write_text(f"{sha256}  {zip_name}.zip\n", encoding="utf-8")

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"  zip 大小: {size_mb:.1f} MB")
    print(f"  SHA256: {sha256}")
    return zip_path


def _check_pyinstaller() -> bool:
    """检查 PyInstaller 是否已安装"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return result.returncode == 0
    except Exception:
        return False


def _get_pyinstaller_cmd(version: str) -> list[str]:
    """构建 PyInstaller 命令行参数

    Args:
        version: 版本号字符串

    Returns:
        PyInstaller 命令列表
    """
    separator = ";" if os.name == "nt" else ":"
    dist_name = f"VibeOCR-v{version}-win64-Windows10_11"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(MAIN_PY),
        "--windowed",
        "--onedir",
        "--name",
        "VibeOCR",
        "--clean",
        "--noconfirm",
        "--paths",
        str(PROJECT_ROOT / "src"),
    ]

    if APP_ICON.exists():
        cmd.extend(["--icon", str(APP_ICON)])

    cmd.extend(["--distpath", str(DIST_BASE_DIR / dist_name)])
    cmd.extend(["--workpath", str(DIST_BASE_DIR / f"build-{version}")])
    cmd.extend(["--specpath", str(DIST_BASE_DIR)])

    for src, dst in PACKAGE_DATA:
        src_path = PROJECT_ROOT / src
        if src_path.exists():
            cmd.extend(["--add-data", f"{src_path}{separator}{dst}"])

    for pkg in EXCLUDED_PACKAGES:
        cmd.extend(["--exclude-module", pkg])

    for qt_mod in EXCLUDED_QT_MODULES:
        cmd.extend(["--exclude-module", qt_mod])

    cmd.extend(["--collect-submodules", "vibeocr"])

    for mod in HIDDEN_IMPORTS:
        cmd.extend(["--hidden-import", mod])

    return cmd


def _run_build(version: str) -> bool:
    """执行完整构建流程"""
    if not _check_pyinstaller():
        print("\n错误: PyInstaller 未安装")
        print(f"请运行: {sys.executable} -m pip install pyinstaller")
        return False

    dist_name = f"VibeOCR-v{version}-win64-Windows10_11"
    dist_path = DIST_BASE_DIR / dist_name / "VibeOCR"

    if dist_path.exists():
        print(f"\n目标目录已存在: {dist_path}")
        print("是否删除后重新打包? [Y/n]: ", end="", flush=True)
        choice = input().strip().lower()
        if choice not in ("", "y", "yes", "是"):
            print("已取消打包")
            return False
        shutil.rmtree(DIST_BASE_DIR / dist_name, ignore_errors=True)

    # 1. 打包主程序
    cmd = _get_pyinstaller_cmd(version)
    print(f"\n[1/5] 打包主程序 VibeOCR v{version}...")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n主程序打包失败，退出码: {e.returncode}")
        return False

    # 2. 打包 updater.exe
    print("\n[2/5] 打包 updater.exe...")
    if not _build_updater(dist_path):
        return False

    # 3. 清理无用 Qt 二进制（削减 ~230MB 体积）
    print("\n[3/5] 清理无用 Qt 模块...")
    _cleanup_dist(dist_path)

    # 4. 生成 version.json
    print("\n[4/5] 生成 version.json...")
    _generate_version_json(version, dist_path)

    # 5. 打 zip + SHA256
    print("\n[5/5] 打包 zip...")
    zip_path = _package_zip(dist_path, version)
    if zip_path is None:
        return False

    print(f"\n{'=' * 50}")
    print("构建完成!")
    print(f"  应用目录: {dist_path}")
    print(f"  分发包:   {zip_path}")
    print(f"{'=' * 50}")
    return True


def _create_release(version: str) -> bool:
    """创建 Gitee/GitHub Release 并上传产物"""

    zip_name = f"VibeOCR-v{version}-win64"
    zip_path = DIST_BASE_DIR / f"{zip_name}.zip"
    sha256_path = DIST_BASE_DIR / f"{zip_name}.zip.sha256"

    if not zip_path.exists():
        print(f"错误: 分发包不存在: {zip_path}")
        print("请先运行 --build 构建分发包")
        return False

    # 读取 CHANGELOG
    changelog_body = ""
    if CHANGELOG.exists():
        content = CHANGELOG.read_text(encoding="utf-8")
        pattern = rf"##\s+\[{re.escape(version)}\].*?(?=\n##\s|$)"
        m = re.search(pattern, content, re.DOTALL)
        if m:
            changelog_body = m.group(0).strip()

    print(f"\n发布 v{version} 到:")
    print(f"  zip: {zip_path} ({zip_path.stat().st_size / (1024 * 1024):.1f} MB)")

    gitee_token = os.environ.get("GITEE_TOKEN", "")
    if gitee_token:
        print("\n上传到 Gitee...")
        try:
            _upload_to_gitee(
                version, zip_path, sha256_path, changelog_body, gitee_token
            )
            print("  Gitee 上传成功")
        except Exception as e:
            print(f"  Gitee 上传失败: {e}")
    else:
        print("\n跳过 Gitee（未设置 GITEE_TOKEN 环境变量）")

    github_token = os.environ.get("GITHUB_TOKEN", "")
    if github_token:
        print("\n上传到 GitHub...")
        try:
            _upload_to_github(
                version, zip_path, sha256_path, changelog_body, github_token
            )
            print("  GitHub 上传成功")
        except Exception as e:
            print(f"  GitHub 上传失败: {e}")
    else:
        print("\n跳过 GitHub（未设置 GITHUB_TOKEN 环境变量）")

    return True


def _upload_to_gitee(
    version: str, zip_path: Path, sha256_path: Path, body: str, token: str
) -> None:
    import httpx

    owner = "felixji"
    repo = "vibeocr"
    api: str = f"https://gitee.com/api/v5/repos/{owner}/{repo}/releases"

    resp = httpx.post(
        api,
        json={
            "access_token": token,
            "tag_name": f"v{version}",
            "name": f"v{version}",
            "body": body or f"VibeOCR v{version}",
            "target_commitish": "main",
        },
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Gitee Release 创建失败: {resp.status_code} {resp.text}")

    release_id = resp.json()["id"]
    upload_url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/releases/{release_id}/attach_files"

    for file_path in [zip_path, sha256_path]:
        if not file_path.exists():
            continue
        with open(file_path, "rb") as f:
            resp = httpx.post(
                upload_url,
                params={"access_token": token},
                files={"file": (file_path.name, f)},
            )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Gitee asset 上传失败: {resp.status_code} {resp.text}")


def _upload_to_github(
    version: str, zip_path: Path, sha256_path: Path, body: str, token: str
) -> None:
    import httpx

    owner = "felixji"
    repo = "vibeocr"
    api: str = f"https://api.github.com/repos/{owner}/{repo}/releases"

    resp = httpx.post(
        api,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "tag_name": f"v{version}",
            "name": f"v{version}",
            "body": body or f"VibeOCR v{version}",
            "target_commitish": "main",
        },
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"GitHub Release 创建失败: {resp.status_code} {resp.text}")

    upload_url_template = resp.json()["upload_url"].split("{")[0]

    for file_path in [zip_path, sha256_path]:
        if not file_path.exists():
            continue
        with open(file_path, "rb") as f:
            resp = httpx.post(
                upload_url_template,
                params={"name": file_path.name},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/zip",
                },
                content=f.read(),
            )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"GitHub asset 上传失败: {resp.status_code} {resp.text}")


def _ask_build(version: str) -> bool:
    """交互式询问是否打包

    Args:
        version: 版本号字符串

    Returns:
        用户是否选择打包
    """
    print(f"\n{'=' * 50}")
    print(f"版本 v{version} 已升级并提交。")
    print("是否立即执行 PyInstaller 打包? [Y/n]: ", end="", flush=True)
    choice = input().strip().lower()
    return choice in ("", "y", "yes", "是")


class _Args(argparse.Namespace):
    """带类型注解的 Namespace，让静态检查器能识别 args 的各字段类型。

    argparse.Namespace 的属性是动态的，静态检查器只能看到
    __getattr__(name: str)，访问 args.release 等会被推断为 Literal['release']
    与 str 不兼容（PyCharm/Pyright 报 reportArgumentType）。这里显式声明
    每个字段的类型，并通过 parse_args(namespace=_Args()) 绑定。
    """

    release: bool
    build: bool
    no_edit: bool
    no_build: bool
    version: str | None
    rebuild: str | None


def main() -> int:
    """主入口函数

    Returns:
        退出码 (0=成功, 1=失败)
    """
    parser = argparse.ArgumentParser(
        description="VibeOCR 版本管理与打包工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                  交互式选择版本升级方式
  %(prog)s patch            升级修订号
  %(prog)s 2.0.0            指定版本号
  %(prog)s minor --no-edit  升级次版本，跳过编辑器
  %(prog)s --build          仅打包当前版本
  %(prog)s --rebuild 1.2.3  重新打包指定版本
        """,
    )
    parser.add_argument(
        "version",
        nargs="?",
        help="版本升级类型 (patch/minor/major) 或版本号 (x.y.z)",
    )
    parser.add_argument(
        "--no-edit",
        action="store_true",
        dest="no_edit",
        help="跳过编辑器审阅 CHANGELOG",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="仅打包当前版本，不执行版本升级",
    )
    parser.add_argument(
        "--rebuild",
        metavar="VERSION",
        help="重新打包指定版本 (如 1.2.3)",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        dest="no_build",
        help="跳过打包提示",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="构建并发布到 Gitee/GitHub（需要 GITEE_TOKEN / GITHUB_TOKEN）",
    )

    args = parser.parse_args(namespace=_Args())

    # 模式0: 构建并发布
    if args.release:
        try:
            current = read_current_version(PYPROJECT_TOML)
        except (FileNotFoundError, ValueError) as e:
            print(f"错误: {e}")
            return 1
        current_str = ".".join(map(str, current))
        if not _run_build(current_str):
            return 1
        return 0 if _create_release(current_str) else 1

    # 模式1: 仅打包当前版本
    if args.build:
        try:
            current = read_current_version(PYPROJECT_TOML)
        except (FileNotFoundError, ValueError) as e:
            print(f"错误: {e}")
            return 1
        current_str = ".".join(map(str, current))
        return 0 if _run_build(current_str) else 1

    # 模式2: 重新打包指定版本
    if args.rebuild:
        rebuild_version = args.rebuild
        if not SEMVER_RE.match(rebuild_version):
            print(f"错误: 无效版本号 '{rebuild_version}'")
            return 1
        return 0 if _run_build(rebuild_version) else 1

    # 模式3: 版本升级流程
    try:
        current = read_current_version(PYPROJECT_TOML)
    except (FileNotFoundError, ValueError) as e:
        print(f"错误: {e}")
        return 1

    current_str = ".".join(map(str, current))

    if not args.version:
        # 交互式模式
        new_version = interactive_menu(current)
        if new_version is None:
            print("已取消")
            return 0
        if new_version == "build":
            # 仅打包当前版本（不升级版本号、不动 git）
            return 0 if _run_build(current_str) else 1
    elif args.version in ("patch", "minor", "major"):
        new_version = bump_version(current, args.version)
    elif SEMVER_RE.match(args.version):
        m = SEMVER_RE.match(args.version)
        assert m is not None
        new_version = (int(m[1]), int(m[2]), int(m[3]))
    else:
        parser.print_help()
        return 1

    new_str = ".".join(map(str, new_version))
    print(f"版本升级: {current_str} → {new_str}")

    # 更新版本号文件（develop bump 只前进版本号 + release 提交；
    # CHANGELOG 与 tag 改由 --to-main 在 main 上维护）
    update_file_version(PYPROJECT_TOML, current_str, new_str)
    print(f"  已更新 {PYPROJECT_TOML}")

    if INIT_PY.exists():
        update_file_version(INIT_PY, current_str, new_str)
        print(f"  已更新 {INIT_PY}")

    # 注意：main.py 通过 __version__ 引用版本号（无字面量），无需在此更新。

    # 同步 uv.lock（pyproject 版本号已变，锁文件需刷新避免滞后漂移）
    _sync_uv_lock(new_str)

    # Git 操作（develop 不打 tag —— tag 只在 main 上由 --to-main 创建）
    try:
        subprocess.run(["git", "add", str(PYPROJECT_TOML)], check=True)
        if INIT_PY.exists():
            subprocess.run(["git", "add", str(INIT_PY)], check=True)
        # uv.lock 与版本号同源，纳入同一 release 提交
        if UV_LOCK.exists():
            subprocess.run(["git", "add", str(UV_LOCK)], check=True)
        subprocess.run(["git", "commit", "-m", f"release: v{new_str}"], check=True)
        print(f"  已创建 git commit release: v{new_str}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"警告: git 操作失败: {e}")
        return 1

    # 询问是否打包
    if not args.no_build and _ask_build(new_str):
        _run_build(new_str)

    print(f"\n完成! 版本已升级到 {new_str}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
