#!/usr/bin/env python3
"""语义化版本管理与打包脚本

在 main 分支上 bump 版本号 → 生成 CHANGELOG → commit → 打 tag。
tag 一推（git push + push tag），GitHub Actions 自动打包并发布到
GitHub（代码另镜像到 CNB；见 .github/workflows/release.yml）。

用法:
    python scripts/bump_version.py              # 交互式菜单（含"仅打包当前版本"）
    python scripts/bump_version.py patch        # 升级修订号 x.y.Z
    python scripts/bump_version.py minor        # 升级次版本 x.Y.0
    python scripts/bump_version.py major        # 升级主版本 X.0.0
    python scripts/bump_version.py 2.0.0        # 指定版本号
    python scripts/bump_version.py ... --no-edit  # 跳过编辑器
    python scripts/bump_version.py ... --yes      # 跳过推送/打包确认（直接 commit+tag+push）
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
# stdout/stderr 强制 UTF-8
# ---------------------------------------------------------------------------
# Windows 默认控制台编码常为 cp1252/gbk，无法编码脚本里的中文（如
# "[1/5] 打包主程序..."），在 GitHub Actions windows-latest 上会抛
# UnicodeEncodeError 直接退出码 1。这里在导入后第一时间把标准输出/错误
# 流切到 UTF-8，保证任何 Windows 环境（CI、本地、任意代码页）都能正常打印。
# reconfigure 失败（极旧 Python 或非 TextI/O）时降级为 errors="replace"，
# 绝不因编码设置本身抛异常。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

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
#
# markdown：纯 Python，主进程 UI 不直接用（markdown_to_html 仅在 OCR/MinerU
# 子进程调用，主进程只用 markdown_converter.HTML_STYLE 字符串常量）。
# 主进程 import 已下沉到函数内，故可安全排除，由便携 Python 安装供 worker 用。
#
# scipy / pandas：paddleocr → paddlex[ocr] 的传递依赖，仅 OCR 子进程使用。
# 主进程 UI 零 import（src 树无任何引用），且便携 Python 安装 paddleocr 时
# pip 会自动拉取它们（paddlex 硬依赖），故便携侧无需显式安装或检测。
# *.libs 是 OpenBLAS 等 DLL 子目录，一并排除防止 PyInstaller 重新收入。
#
# lxml / pydantic(_core) / chardet / aiohttp 等：均为 paddleocr/mineru/paddlex
# 的核心传递依赖，仅 OCR 子进程使用，主进程 UI 零 import。便携 Python 安装
# paddleocr/mineru 时 pip 自动带入，无需显式安装或检测。
# 注意：已核实 httpx 0.28 不依赖 chardet（依赖 anyio/certifi/httpcore/idna），
# 故排除 chardet 不影响主进程 update_service/mineru_service 的 httpx 调用。
# aiohttp 卫星包（multidict/yarl/frozenlist/propcache 等）一并排除。
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
    "markdown",
    "scipy",
    "scipy.libs",
    "pandas",
    "pandas.libs",
    "lxml",
    "pydantic",
    "pydantic_core",
    "chardet",
    "aiohttp",
    "multidict",
    "yarl",
    "frozenlist",
    "propcache",
    "aiosignal",
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
    # CHANGELOG.md：关于页"更新日志"卡片读取，缺失时客户端只显示"暂无更新日志"。
    # 打入 . 让其落在 _MEIPASS/CHANGELOG.md（= _internal/CHANGELOG.md），
    # 由 env_manager.get_bundled_changelog_path() 解析。
    ("CHANGELOG.md", "."),
    # vibeocr 源码需以原始 .py 形式随主 exe 分发：打包态下 OCR Worker 子进程
    # 用便携式 Python（python/python.exe）跑 `python -m vibeocr.workers.ocr_worker`，
    # 而便携式 Python 是独立解释器，无法读取主 exe 内部的 PYZ 归档（collect_submodules
    # 收集的字节码只进 PYZ）。通过 PYTHONPATH 指向 _MEIPASS（见 ocr_worker_process
    # 的 _get_worker_env）让它能 import 这份平铺源码。
    #
    # 目标目录与 PYZ 内同名为 vibeocr：主 exe 与便携 Python 是两个独立解释器，
    # 主 exe 走 PyInstaller bootstrap（PYZ 优先），便携 Python 走 PYTHONPATH 的
    # 平铺 .py，互不干扰。
    ("src/vibeocr", "vibeocr"),
    # update_replacer.py：主程序 --self-update 兜底模式需要 import 它（updater.exe
    # 坏时主程序自身充当替换器）。打入 .（_internal/ 根），由 main.py 的
    # _resolve_replacer_module_dir 通过 sys._MEIPASS 定位后注入 sys.path。
    # updater.exe 是独立 --onefile，与该文件同源打包，无需在此声明。
    ("scripts/update_replacer.py", "."),
]

# vibeocr 子模块通过 --collect-submodules 自动收集，此处只列出第三方包
#
# markdown 已移至 EXCLUDED_PACKAGES（由便携 Python 安装，主进程仅用 HTML_STYLE
# 字符串常量，import 下沉到函数内）。故不在此处声明 hidden-import。
HIDDEN_IMPORTS = [
    "shiboken6",
    "qasync",
    "httpx",
    "PIL",
    "numpy",
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


def get_commits_since_last_tag() -> list[tuple[str, str]]:
    """获取自上次 tag 以来的 git 提交（bump 时用于生成 CHANGELOG 条目）"""
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


def interactive_menu(current: tuple[int, int, int]) -> tuple[int, int, int] | str | None:
    """交互式操作选择菜单

    Args:
        current: 当前版本号三元组

    Returns:
        新版本号三元组 / 字符串 "build"（仅打包当前版本，不升级版本号）/
        None（取消）
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
    print("  0) 取消")
    print("请输入选项 [0-5]: ", end="", flush=True)

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
        # WebEngine 资源包版本（主包剔除 WebEngine 后单独下载）。
        # 用主版本号对齐：每次发版资源包都重打，客户端据此判断需重装。
        # 全量包（--bundle-webengine）时此处仍写版本号，但资源包 zip 不产出，
        # 客户端 is_webengine_ready() 为真即跳过下载。
        "webengine_assets_version": version,
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
    同时精简 PySide6/translations（仅保留 qtbase 中文翻译）、
    qtwebengine_locales（仅保留 zh-CN/en-US）以及 WebEngine 的 debug/devtools
    资源（release 运行时不加载，详见 Qt qtwebengine-deploying 文档）。

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

        # 精简 WebEngine 语言包：qtwebengine_locales 下 Chromium 的 53 种语言
        # .pak（~43MB）。Chromium 在缺某语言时会回退到 en-US.pak，故只保留
        # zh-CN（界面主语言）+ en-US（兜底）即可，其余删除。
        # 见 Qt 文档 qtwebengine-deploying：locale .pak 用于 Chromium 自身的
        # UI 文案（右键菜单、错误页等），缺失时静默回退，不影响页面渲染。
        webengine_locales_dir = trans_dir / "qtwebengine_locales"
        if webengine_locales_dir.is_dir():
            for pak in webengine_locales_dir.glob("*.pak"):
                if pak.name not in ("zh-CN.pak", "en-US.pak"):
                    freed_bytes += pak.stat().st_size
                    pak.unlink()
                    deleted += 1

    # 清理 WebEngine 的 debug/devtools 资源（~88MB）：
    # - qtwebengine_devtools_resources*.pak：Chromium DevTools 远程调试资源，
    #   仅 F12 远程调试需要，release 用户用不到（Qt 官方文档明确可删）。
    # - *.debug.pak / *.debug.bin：Debug 构建专用资源（含未压缩的 source map
    #   与调试符号），release 运行时完全不加载。
    # 保留：icudtl.dat、qtwebengine_resources.pak（Chromium 核心，删了会崩溃）、
    # v8_context_snapshot.bin（非 debug 版）。
    resources_dir = pyside6_dir / "resources"
    if resources_dir.is_dir():
        for res_file in resources_dir.iterdir():
            name = res_file.name
            if name.endswith((".debug.pak", ".debug.bin")) or name.startswith(
                "qtwebengine_devtools_resources"
            ):
                freed_bytes += res_file.stat().st_size
                res_file.unlink()
                deleted += 1

    freed_mb = freed_bytes / (1024 * 1024)
    print(f"  清理无用 Qt 文件: 删除 {deleted} 个，释放 {freed_mb:.1f} MB")


# WebEngine 独有文件清单（从主包拆出，按需下载）。
# 仅含 WebEngine/Chromium 专有文件；通用 Qt 模块（Qt6Core/Gui/Widgets/Svg/Pdf、
# 以及 WebEngine 的传递依赖 Qt6Qml/Quick/Network/OpenGL 等）保留在主包——
# 它们体积小且可能被 PySide6 其它子模块链接，移走有崩溃风险。
# WebEngine 独有文件缺失时，仅 result_view 的延迟 import 失败（已被改造为容错）。
_WEBENGINE_DLLS = (
    "Qt6WebEngineCore.dll",
    "Qt6WebEngineWidgets.dll",
    "Qt6WebChannel.dll",
    "QtWebEngineProcess.exe",
)


def _collect_webengine_files(pyside6_dir: Path) -> list[Path]:
    """收集 PySide6 目录下属于 WebEngine 的文件（绝对路径）。

    包括：WebEngine 独有 dll/exe、resources/ 全部（Chromium 资源）、
    translations/qtwebengine_locales/（Chromium 语言包）。
    """
    files: list[Path] = []
    # 1. WebEngine 独有 dll/exe
    for name in _WEBENGINE_DLLS:
        f = pyside6_dir / name
        if f.exists():
            files.append(f)
    # 2. resources/ 目录（Chromium 资源，全属 WebEngine）
    resources_dir = pyside6_dir / "resources"
    if resources_dir.is_dir():
        files.extend(p for p in resources_dir.rglob("*") if p.is_file())
    # 3. translations/qtwebengine_locales/
    locales_dir = pyside6_dir / "translations" / "qtwebengine_locales"
    if locales_dir.is_dir():
        files.extend(p for p in locales_dir.rglob("*") if p.is_file())
    return files


def _split_webengine_assets(dist_dir: Path, version: str) -> Path | None:
    """把 WebEngine 资源从主包拆到独立资源包 zip。

    将 _internal/PySide6/ 下的 WebEngine 独有文件移到临时目录（保持
    PySide6/ 相对结构），打成 VibeOCR-v<ver>-webengine-win64.zip + sha256。
    移动后主包不再含这些文件（由首启向导按需下载解压还原）。

    注意：通用 Qt dll（Core/Gui/Qml/Quick 等）保留在主包不动。
    """
    pyside6_dir = dist_dir / "_internal" / "PySide6"
    if not pyside6_dir.is_dir():
        print("  PySide6 目录不存在，跳过 WebEngine 拆分")
        return None

    webengine_files = _collect_webengine_files(pyside6_dir)
    if not webengine_files:
        print("  未找到 WebEngine 文件，跳过拆分（可能已是全量精简或无 WebEngine）")
        return None

    total_mb = sum(f.stat().st_size for f in webengine_files) / (1024 * 1024)
    print(f"  待拆分 WebEngine 文件: {len(webengine_files)} 个，{total_mb:.1f} MB")

    # 移到临时暂存目录（DIST_BASE_DIR 下），保持 PySide6/ 相对结构
    staging = DIST_BASE_DIR / f"_webengine_staging_v{version}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    moved = 0
    for src in webengine_files:
        rel = src.relative_to(pyside6_dir)  # e.g. resources/icudtl.dat
        dest = staging / "PySide6" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        moved += 1
    print(f"  已移出 {moved} 个文件到暂存目录")

    # 打资源包 zip（顶层目录为 PySide6/，客户端解压即归位到 _internal/PySide6/）
    zip_name = f"VibeOCR-v{version}-webengine-win64"
    zip_path = DIST_BASE_DIR / f"{zip_name}.zip"
    print(f"  打包资源包 {zip_path}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in (staging / "PySide6").rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(staging)  # PySide6/...
                zf.write(file_path, arcname)

    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sha256_path = DIST_BASE_DIR / f"{zip_name}.zip.sha256"
    sha256_path.write_text(f"{sha256}  {zip_name}.zip\n", encoding="utf-8")

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"  资源包 zip 大小: {size_mb:.1f} MB")
    print(f"  SHA256: {sha256}")

    # 清理暂存目录（文件已进 zip，主包不再需要）
    shutil.rmtree(staging, ignore_errors=True)
    return zip_path


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
        # 禁用 UPX 压缩：UPX 压缩的 DLL（尤其 PySide6 的 Qt6*.dll，数十 MB）
        # 每次启动都要在内存解压，是 .exe 启动慢的主因。
        # 牺牲约 30% 磁盘体积换取启动免解压提速。
        "--noupx",
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


def _run_build(version: str, force: bool = False, bundle_webengine: bool = False) -> bool:
    """执行完整构建流程

    Args:
        version: 版本号字符串
        force: True 时已存在的目标目录直接删除重建，不交互询问
            （CI/非交互场景用）。False 时遇到已存在目录会 input() 询问。
        bundle_webengine: True 时不拆分 WebEngine 资源包（全量打包）。
            False（默认）时把 WebEngine 移到独立资源包，首启向导按需下载。
    """
    if not _check_pyinstaller():
        print("\n错误: PyInstaller 未安装")
        print(f"请运行: {sys.executable} -m pip install pyinstaller")
        return False

    dist_name = f"VibeOCR-v{version}-win64-Windows10_11"
    dist_path = DIST_BASE_DIR / dist_name / "VibeOCR"

    if dist_path.exists():
        if force:
            print(f"\n目标目录已存在（--force）: {dist_path}，直接删除重建")
            shutil.rmtree(DIST_BASE_DIR / dist_name, ignore_errors=True)
        else:
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
    print("\n[4/6] 生成 version.json...")
    _generate_version_json(version, dist_path)

    # 5. 拆分 WebEngine 资源包（默认按需下载模式）
    webengine_zip_path: Path | None = None
    if bundle_webengine:
        print("\n[5/6] 跳过 WebEngine 拆分（--bundle-webengine 全量打包）")
    else:
        print("\n[5/6] 拆分 WebEngine 资源包...")
        webengine_zip_path = _split_webengine_assets(dist_path, version)
        if webengine_zip_path is None:
            print("  WebEngine 资源包拆分失败")
            return False

    # 6. 打主包 zip + SHA256
    print("\n[6/6] 打包 zip...")
    zip_path = _package_zip(dist_path, version)
    if zip_path is None:
        return False

    print(f"\n{'=' * 50}")
    print("构建完成!")
    print(f"  应用目录:   {dist_path}")
    print(f"  主分发包:   {zip_path}")
    if webengine_zip_path is not None:
        print(f"  WebEngine 包: {webengine_zip_path}")
    print(f"{'=' * 50}")
    return True


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


def _detect_upstream_remote() -> str:
    """返回当前分支上游所在的 remote（GitHub/main -> GitHub）。

    tag 必须推到与分支同一个 remote；不同仓库 remote 名不一（本仓叫
    GitHub 而非 origin），故据此动态探测，不再硬编码 origin。

    没有上游或读取失败时返回空串，由调用方提示手动推送。
    """
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            capture_output=True,
            text=True,
            check=True,
        )
        # 形如 "GitHub/main"，取首个 "/" 之前即 remote（分支名可含 /，不影响）
        return res.stdout.strip().split("/", 1)[0]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _push_release(version: str) -> bool:
    """推送当前分支与 tag 到远程，触发 CI 发版

    依次执行（remote 取自当前分支上游，如 GitHub）：
      git push <remote>              # 同步分支
      git push <remote> refs/tags/v{version}  # 触发 CI

    tag 一推，GitHub Actions（release.yml）即触发打包并发布到
    GitHub（代码另镜像到 CNB）。本地不再直接调用 GitHub Release API。

    Args:
        version: 版本号字符串（用于 tag 名）

    Returns:
        True=推送成功，False=失败（仅警告，不致命）
    """
    tag = f"v{version}"
    remote = _detect_upstream_remote()
    if not remote:
        print(
            "警告: 无法确定当前分支上游 remote（branch.<name>.remote 未配置），"
            f"请手动推送: git push <remote> && git push <remote> {tag}"
        )
        return False

    # 推送当前分支（让远程 main 与本地提交同步）
    try:
        subprocess.run(["git", "push", remote], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"警告: 推送分支失败: {e}")
        print(f"  可手动执行: git push {remote} && git push {remote} {tag}")
        return False

    # 推送 tag（触发 CI 发版）
    try:
        subprocess.run(["git", "push", remote, f"refs/tags/{tag}"], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"警告: 推送 tag {tag} 失败: {e}")
        print(f"  可手动执行: git push {remote} {tag}")
        return False

    print(f"  已推送 tag {tag}（remote={remote}），CI 将自动打包并发布")
    return True


class _Args(argparse.Namespace):
    """带类型注解的 Namespace，让静态检查器能识别 args 的各字段类型。

    argparse.Namespace 的属性是动态的，静态检查器只能看到
    __getattr__(name: str)，访问 args.build 等会被推断为 Literal['build']
    与 str 不兼容（PyCharm/Pyright 报 reportArgumentType）。这里显式声明
    每个字段的类型，并通过 parse_args(namespace=_Args()) 绑定。
    """

    build: bool
    no_edit: bool
    yes: bool
    no_build: bool
    force: bool
    bundle_webengine: bool
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
  %(prog)s minor --yes      升级并跳过推送/打包确认（脚本化用）
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
        "--yes", "-y",
        action="store_true",
        dest="yes",
        help="跳过推送确认直接 push（触发 CI 发版），脚本化/非交互场景用",
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
        "--force",
        action="store_true",
        help="打包时遇到已存在的目标目录直接删除重建，不交互询问（CI/非交互场景用）",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        dest="no_build",
        help="跳过打包提示",
    )
    parser.add_argument(
        "--bundle-webengine",
        action="store_true",
        dest="bundle_webengine",
        help="将 WebEngine 内置进主包（不拆分资源包）。默认按需下载模式会"
        "把 WebEngine 拆到独立资源包，首启向导下载；此开关用于回退到全量打包。",
    )

    args = parser.parse_args(namespace=_Args())

    # 模式1: 仅打包当前版本
    if args.build:
        try:
            current = read_current_version(PYPROJECT_TOML)
        except (FileNotFoundError, ValueError) as e:
            print(f"错误: {e}")
            return 1
        current_str = ".".join(map(str, current))

        # 未版本化提交警告（打包内容可能超出版本号标注）
        has_unversioned, n = check_unversioned_commits(current_str)
        if has_unversioned:
            print(
                f"警告: 当前版本 {current_str} 之后有 {n} 个未发版提交，"
                "打包内容将超出版本号标注。仍要打包？[y/N]: ",
                end="",
                flush=True,
            )
            if input().strip().lower() not in ("y", "yes"):
                print("已取消打包")
                return 0

        return 0 if _run_build(current_str, force=args.force, bundle_webengine=args.bundle_webengine) else 1

    # 模式2: 重新打包指定版本
    if args.rebuild:
        rebuild_version = args.rebuild
        if not SEMVER_RE.match(rebuild_version):
            print(f"错误: 无效版本号 '{rebuild_version}'")
            return 1
        return 0 if _run_build(rebuild_version, force=args.force, bundle_webengine=args.bundle_webengine) else 1

    # 模式3: 版本升级流程（在 main 上 bump → commit → tag）
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
            return 0 if _run_build(current_str, force=args.force, bundle_webengine=args.bundle_webengine) else 1
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

    # 更新版本号文件（pyproject.toml / __init__.py）
    update_file_version(PYPROJECT_TOML, current_str, new_str)
    print(f"  已更新 {PYPROJECT_TOML}")

    if INIT_PY.exists():
        update_file_version(INIT_PY, current_str, new_str)
        print(f"  已更新 {INIT_PY}")

    # 注意：main.py 通过 __version__ 引用版本号（无字面量），无需在此更新。

    # 同步 uv.lock（pyproject 版本号已变，锁文件需刷新避免滞后漂移）
    _sync_uv_lock(new_str)

    # 更新 CHANGELOG（生成条目，弹编辑器审阅，纳入 release 提交）
    commits = get_commits_since_last_tag()
    update_changelog(new_str, commits)
    print(f"  已更新 {CHANGELOG}")

    # 打开编辑器审阅 CHANGELOG（--no-edit 跳过）
    if not args.no_edit:
        _open_editor(CHANGELOG)

    # Git 操作：版本号 + CHANGELOG + uv.lock 进入同一个 release 提交，并打 tag。
    try:
        subprocess.run(["git", "add", str(PYPROJECT_TOML)], check=True)
        if INIT_PY.exists():
            subprocess.run(["git", "add", str(INIT_PY)], check=True)
        subprocess.run(["git", "add", str(CHANGELOG)], check=True)
        # uv.lock 与版本号同源，纳入同一 release 提交
        if UV_LOCK.exists():
            subprocess.run(["git", "add", str(UV_LOCK)], check=True)
        subprocess.run(["git", "commit", "-m", f"release: v{new_str}"], check=True)
        print(f"  已创建 git commit release: v{new_str}")
        subprocess.run(["git", "tag", f"v{new_str}"], check=True)
        print(f"  已打 tag v{new_str}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"警告: git 操作失败: {e}")
        return 1

    # 推送确认：推 tag 会触发 CI 打包发版（不可逆），故默认否。
    # --yes 跳过此确认直接推送。推送由 --yes 控制，与 --no-edit（仅跳编辑器）解耦。
    pushed = False
    if args.yes:
        pushed = _push_release(new_str)
    else:
        print(
            f"\n已创建 tag v{new_str}。是否推送到 GitHub/main 触发发版？[y/N]: ",
            end="",
            flush=True,
        )
        if input().strip().lower() in ("y", "yes"):
            pushed = _push_release(new_str)

    # 未推送时才问本地打包（已推送则 CI 会打包，本地打包多余）。
    # 本地打包确认默认否，与推送确认共用 --yes 跳过。
    if not pushed and not args.yes and not args.no_build and _ask_build(new_str):
        _run_build(new_str, force=args.force, bundle_webengine=args.bundle_webengine)

    print(f"\n完成! 版本已升级到 {new_str}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
