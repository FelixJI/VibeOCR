#!/usr/bin/env python3
"""语义化版本管理与 WinUI 发布打包脚本

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
    python scripts/bump_version.py --build      # 打包当前 WinUI 版本
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

# ---------------------------------------------------------------------------
# sys.path 注入 src/：CI 只装 build-shell.lock（PyInstaller + 壳依赖），不安装
# vibeocr 包本身（避免拉 GB 级 paddle/torch）。但 _package_zip 需要 import
# vibeocr.build_manifest 生成/校验产物清单。本地 editable 安装时无需此举，加上
# 是幂等的——与 tests/conftest.py 的处理方式一致。必须在任何 from vibeocr...
# 之前执行。
_SRC_DIR = PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

VERSION_RE = re.compile(r'version\s*=\s*"(\d+)\.(\d+)\.(\d+)"')
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# 包名归一映射（lock/pyproject → 运行时一致 key）。
# uv.lock / pyproject 用 paddlepaddle-gpu，便携环境检测用 paddlepaddle。
# _generate_version_json 与 _read_uv_lock_versions 共用此映射保持一致。
_KEY_ALIASES_LOCK = {"paddlepaddle-gpu": "paddlepaddle"}

# ---------------------------------------------------------------------------
# 打包常量
# ---------------------------------------------------------------------------
APP_ICON = PROJECT_ROOT / "resources" / "app_icon.ico"
DIST_BASE_DIR = PROJECT_ROOT / "dist"
# 产物清单文件名（写入 ZIP 根目录，供 verify_archive 校验）
_MANIFEST_FILENAME = "artifact-manifest.json"

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
# lxml / chardet / aiohttp 等：均为 paddleocr/mineru/paddlex 的核心传递依赖，
# 仅 OCR 子进程使用，主进程 UI 零 import。便携 Python 安装 paddleocr/mineru
# 时 pip 自动带入，无需显式安装或检测。
# 注意：已核实 httpx 0.28 不依赖 chardet（依赖 anyio/certifi/httpcore/idna），
# 故排除 chardet 不影响主进程 update_service/mineru_service 的 httpx 调用。
# aiohttp 卫星包（multidict/yarl/frozenlist/propcache 等）一并排除。
# 注意：pydantic / pydantic_core 不可放此处（见下方列表内联注释）。
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
    # 注意：pydantic / pydantic_core 不能排除。PDF 模块进程化后，主进程
    # vibeocr.ipc.schemas 在启动时顶层 import pydantic（main → main_window →
    # pdf_tab → pdf_session_manager → model_bridge → schemas），排除会导致
    # ModuleNotFoundError。子进程(便携 Python)侧自带，不影响。
    "chardet",
    "aiohttp",
    "multidict",
    "yarl",
    "frozenlist",
    "propcache",
    "aiosignal",
    # fastapi / uvicorn:仅 PDF 后端子进程(pdf_backend_process.py)用,
    # 该模块由嵌入式 Python 经 `python -m` 加载,不进主 exe import 链。
    "fastapi",
    "uvicorn",
    # pymupdf(import 名 fitz):仅 PDF 后端子进程用(pdf_service.py 顶层 import)。
    # bbox_to_pixel 已抽到 utils/pdf_coords.py,主进程不再加载 pdf_service,
    # 故 fitz 可从主 exe 排除,由便携 Python 安装供子进程用。
    "pymupdf",
    "fitz",
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
#
# 以下均为"主进程延迟 import"（import 在函数体内，PyInstaller 静态分析虽
# 多数能追踪到，但若处于 try/except 或条件分支中有漏报风险），显式声明
# 保险：触发相应功能时不会 ModuleNotFoundError。
#   - pydantic：vibeocr.ipc.schemas 顶层依赖（PDF 进程化后主进程必用）。
#   - openpyxl / docx：导出 Excel / Word（export_service）。
#   - fontTools：CJK 字体回退解析（utils/cjk_font_resolver）。
#   - pyzbar：二维码解码（qrcode_decode_service，venv 未装但代码引用）。
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
    "pydantic",
    "openpyxl",
    "docx",
    "fontTools",
    "pyzbar",
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
    # --no-merges：剔除 merge commit（"Merge branch 'fix/xxx'" 这类），
    # 只保留真实的功能/修复提交。团队规范要求不在 main 上直接提交、走分支合并，
    # 不加该开关会让 CHANGELOG 充斥一堆 merge 噪音。
    cmd = ["git", "log", "--no-merges", "--pretty=format:%h %s", rev_range]
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


def _parse_dependencies(pyproject_path: Path) -> dict[str, str]:
    """解析 pyproject.toml 的 [project.dependencies]，返回 {规范化包名: 完整规格}。

    规范化：小写 + 剥 extras（paddleocr[doc-parser] → paddleocr）。
    用于 dep diff：按包名对齐新旧两版，比较完整规格字符串。

    Args:
        pyproject_path: pyproject.toml 路径

    Returns:
        {"paddlepaddle-gpu": "paddlepaddle-gpu>=3.3.1", "torch": "torch>=2.6.0", ...}
        文件不存在或解析失败时返回空 dict（CI 浅克隆等场景降级）。
    """
    if not pyproject_path.exists():
        return {}
    try:
        import tomllib

        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    deps = data.get("project", {}).get("dependencies", [])
    result: dict[str, str] = {}
    for dep in deps:
        dep = dep.strip()
        if dep.startswith("#") or not dep:
            continue
        m = re.match(r"^([a-zA-Z0-9_.-]+)", dep)
        if m:
            pkg = m.group(1).lower()
            result[pkg] = dep
    return result


def _compute_dep_diff(
    old_deps: dict[str, str], new_deps: dict[str, str]
) -> dict[str, list[str]]:
    """对比新旧依赖列表，返回分类变更文案。

    分类：
    - "upgraded"：版本约束变化（如 paddlepaddle-gpu 3.3.1 → 3.4.0）
    - "added"：新增依赖
    - "removed"：移除依赖

    Args:
        old_deps: 旧版 _parse_dependencies 结果
        new_deps: 新版 _parse_dependencies 结果

    Returns:
        {"upgraded": [...], "added": [...], "removed": [...]}
        每项为人类可读文案字符串。全空时表示无依赖变更。
    """
    upgraded: list[str] = []
    added: list[str] = []
    removed: list[str] = []

    # 提取完整 constraint 串用于展示（含 local version +cu126、多段、!= 等）。
    # spec 形如 "paddlepaddle-gpu>=3.3.1" / "torch==2.6.0+cu126" / "x>=1,<2"。
    # 无版本约束时返回 "(无版本约束)"。
    def _extract_constraint(spec: str) -> str:
        m = re.search(r"(==|!=|>=|<=|~=|>|<).+$", spec)
        return m.group(0) if m else "(无版本约束)"

    for pkg, new_spec in new_deps.items():
        if pkg not in old_deps:
            added.append(f"新增 {new_spec}")
        elif old_deps[pkg] != new_spec:
            old_c = _extract_constraint(old_deps[pkg])
            new_c = _extract_constraint(new_deps[pkg])
            upgraded.append(f"升级 {pkg} {old_c} → {new_c}")

    for pkg, old_spec in old_deps.items():
        if pkg not in new_deps:
            removed.append(f"移除 {old_spec}")

    return {"upgraded": upgraded, "added": added, "removed": removed}


def _get_last_release_pyproject_deps(
    version: str, cwd: Path | None = None
) -> dict[str, str]:
    """从上一个 release tag 读取 pyproject.toml 的依赖列表。

    用 ``git show v{last_tag}:pyproject.toml`` 取旧版内容到临时解析。
    失败场景（首次发版无 tag、浅克隆无历史）返回空 dict，CHANGELOG 不含依赖段。

    Args:
        version: 当前版本号（用于查找上一个 tag）
        cwd: git 仓库目录；None 表示用 PROJECT_ROOT（发版场景）。
            测试传 tmp_path 隔离。

    Returns:
        {规范化包名: 完整规格}，失败时空 dict。
    """
    if cwd is None:
        cwd = PROJECT_ROOT
    try:
        current_tag = f"v{version}"
        last_tag = ""
        # 候选 ref（按优先级）：v{version}^（release commit 的父，bump 流程）→
        # HEAD^（CI checkout tag 后，HEAD 即 release commit）→ 最近 tag（--build 路径，
        # HEAD 是上个 release，取其本身）。最后一个用 --all 匹配最近 tag 名。
        for ref in [f"{current_tag}^", "HEAD^"]:
            result = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0", ref],
                capture_output=True,
                encoding="utf-8",
                cwd=str(cwd),
            )
            if result.returncode == 0:
                last_tag = result.stdout.strip()
                # 不能等于当前版本（否则 diff 无意义）
                if last_tag != current_tag:
                    break
                last_tag = ""

        # 回退：取仓库里最近的 tag（--build 路径：HEAD 即上个 release 本身）
        if not last_tag:
            result = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                capture_output=True,
                encoding="utf-8",
                cwd=str(cwd),
            )
            if result.returncode == 0:
                candidate = result.stdout.strip()
                if candidate and candidate != current_tag:
                    last_tag = candidate

        if not last_tag:
            return {}
        # 取旧版 pyproject.toml 内容
        show = subprocess.run(
            ["git", "show", f"{last_tag}:pyproject.toml"],
            capture_output=True,
            encoding="utf-8",
            cwd=str(cwd),
        )
        if show.returncode != 0:
            return {}
        # 写临时文件解析（复用 _parse_dependencies 的 tomllib 路径）
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(show.stdout)
            tmp_path = Path(tf.name)
        try:
            return _parse_dependencies(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception:
        return {}


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
    version: str,
    commits: list[tuple[str, str]],
    dep_diff: dict[str, list[str]] | None = None,
) -> str:
    """生成 CHANGELOG 条目文本

    Args:
        version: 新版本号字符串
        commits: 提交列表
        dep_diff: 依赖变更分类（{"upgraded": [...], "added": [...], "removed": [...]}）。
            非空时在条目末尾追加 "### Dependencies" 段。None 或全空时省略。

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

    # 依赖变更段（P3）：发版者升级/新增/移除依赖时让用户在 CHANGELOG 可见
    if dep_diff and any(dep_diff.values()):
        lines.append("### Dependencies")
        for label, items in (
            ("升级", dep_diff.get("upgraded", [])),
            ("新增", dep_diff.get("added", [])),
            ("移除", dep_diff.get("removed", [])),
        ):
            if not items:
                continue
            lines.append(f"- {label}:")
            for item in items:
                lines.append(f"  - {item}")
        lines.append("")

    return "\n".join(lines)


def update_changelog(
    version: str,
    commits: list[tuple[str, str]],
    dep_diff: dict[str, list[str]] | None = None,
) -> None:
    """更新 CHANGELOG.md，在第一个 ## 标题之前插入新条目

    如果文件不存在则创建。

    Args:
        version: 新版本号字符串
        commits: 提交列表
        dep_diff: 依赖变更（传给 generate_changelog_entry）
    """
    entry = generate_changelog_entry(version, commits, dep_diff)

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


def _read_uv_lock_versions() -> dict[str, str]:
    """解析 uv.lock，返回 {归一包名: 锁定版本}。

    uv.lock 是 TOML，每个包为 ``[[package]]`` 表，含 ``name`` 与 ``version``。
    本函数读取全部包的锁定版本（含 local label，如 torch 的 ``2.12.1+cu126``），
    并把 ``paddlepaddle-gpu`` 归一为 ``paddlepaddle``（与 _KEY_ALIASES 一致），
    使运行时 detect_dependency_updates 能拿到便携环境应装的真实版本。

    用于版本更新检测：pyproject 的 ``>=3.4.0`` 只是下界，无法表达"实际锁定 3.4.2"，
    便携环境已装 3.4.0 会被误判为最新。锁定版作为权威比较基准解决此问题。

    Returns:
        {包名: 版本串}；uv.lock 不存在或解析失败时返回空 dict。
    """
    import tomllib

    if not UV_LOCK.exists():
        return {}
    try:
        lock = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}
    versions: dict[str, str] = {}
    for pkg in lock.get("package", []):
        name = pkg.get("name", "")
        version = pkg.get("version", "")
        if name and version:
            # 归一：paddlepaddle-gpu → paddlepaddle（便携用 GPU 版）
            key = _KEY_ALIASES_LOCK.get(name.lower()) or name.lower()
            versions[key] = version
    return versions


def _generate_version_json(version: str, dist_dir: Path) -> None:
    """生成 version.json 到输出目录

    字段说明：
    - dep_versions：追踪包 → 约束串（如 ">=3.3.1"、"==3.3.1+cu126"、">=2.6,<3"）。
        完整保留 PEP 440 规格（含 local version +cu126、多段约束、!= / ~> 等），
        读端拼接 ``{pkg}{constraint}`` 即得合法 pip requirement。
        向后兼容三层：旧裸版本号 str（"3.3.1"）按 ">=3.3.1"；曾用 {version,op} dict
        按 "{op}{version}"；新约束串 str 直接用。
    - dep_extras：追踪包 → extras 列表（如 paddleocr → ["doc-parser"]）。
        extras 是包名一部分，重建 spec 时拼回 ``pkg[extra1,extra2]``。
        无 extras 的包不出现于此 dict；该字段整体缺失时按"无 extras"处理（旧版兼容）。
    - dep_locked_versions：追踪包 → uv.lock 锁定版本（如 "3.4.2"、torch "2.12.1+cu126"）。
        作为更新检测的权威基准：便携环境已装版本 < 锁定版即报更新，避免只比 ``>=`` 下界
        漏掉 3.4.0 → 3.4.2 这类下界内的升级。缺失该字段时运行时回退下界（旧版兼容）。
    - removed：自上个 release tag 起从依赖中移除的包名（P4，主程序据此 pip uninstall）。
        无移除时省略 removed 字段。
    """
    import re as _re
    import tomllib

    tomldata = tomllib.loads(PYPROJECT_TOML.read_text(encoding="utf-8"))
    deps = tomldata.get("project", {}).get("dependencies", [])

    # 需要追踪版本的包前缀（对应 EXCLUDED_PACKAGES 中排除的大依赖）
    # PDF 后端依赖(pymupdf/fastapi/uvicorn/pydantic/fonttools)已从主 exe 排除,
    # 由便携 Python 安装,故需追踪版本写入 version.json,供打包态 _load_dep_specs 读取。
    # markdown 同理(从 exe 排除,供 OCR/MinerU worker 子进程 markdown_to_html 用),
    # 必须追踪否则便携环境 _load_dep_specs 取不到约束→裸包名安装(丢失 >= 下界),
    # 且 dep_locked_versions 缺基准→detect_dependency_updates 漏报下界内升级。
    _TRACKED_PREFIXES = (
        "paddle", "paddleocr", "mineru", "torch", "nvidia",
        "pymupdf", "fastapi", "uvicorn", "pydantic", "fonttools",
        "markdown",
    )
    _KEY_ALIASES = _KEY_ALIASES_LOCK  # 模块级常量，与 _read_uv_lock_versions 共用

    dep_versions: dict[str, str] = {}
    dep_extras: dict[str, list[str]] = {}
    for dep in deps:
        dep = dep.strip()
        if dep.startswith("#"):
            continue
        # PEP 508 规格形如：name[extra1,extra2]<op><version>[,<op2><v2>]...
        # 先分离 name + extras（[] 内）与后续 constraint。
        m = _re.match(
            r"^([a-zA-Z0-9_.-]+)"  # 包名
            r"(?:\[([^\]]*)\])?"  # 可选 extras（逗号分隔）
            r"(.+)?$",  # 后续 constraint（含操作符）
            dep,
        )
        if not m:
            continue
        pkg_raw = m.group(1).lower()
        extras_str = m.group(2)  # 形如 "doc-parser" 或 "a,b" 或 None
        constraint = (m.group(3) or "").strip()
        if not any(pkg_raw.startswith(p) for p in _TRACKED_PREFIXES):
            continue
        # dict.get 在 _KEY_ALIASES 命中时返回别名，否则回退 pkg（恒非 None）；
        # 静态签名是 str|None，故用 pkg 默认值并显式断言收窄。
        key: str = _KEY_ALIASES.get(pkg_raw) or pkg_raw
        # constraint 必须以合法 PEP 440 操作符开头，否则视为无版本约束
        if constraint and _re.match(r"^(==|!=|>=|<=|~=|>|<)", constraint):
            dep_versions[key] = constraint
        else:
            # 无版本约束（仅 "mineru"），记录为空串占位（读端按"无约束"处理）
            dep_versions[key] = ""
        if extras_str:
            extras = [e.strip() for e in extras_str.split(",") if e.strip()]
            if extras:
                dep_extras[key] = extras

    # python_version 读自 .python-version（单一源，避免与 pyproject requires-python 漂移）
    dot_python_version_path = PROJECT_ROOT / ".python-version"
    if dot_python_version_path.exists():
        python_version = dot_python_version_path.read_text(encoding="utf-8").strip()
    else:
        python_version = "3.13"  # fallback：与 .python-version 默认值一致

    # P4：计算自上个 release tag 起被移除的追踪包。
    # 从上个 tag 的 pyproject 取旧依赖 → 归一化包名 → 找出现版已不存在的追踪包。
    # 用 _KEY_ALIASES 反向映射（paddlepaddle-gpu → paddlepaddle）以与 dep_versions 的
    # key 一致；失败时（首次发版/浅克隆）返回空，省略 removed 字段。
    old_full_deps = _get_last_release_pyproject_deps(version, cwd=PROJECT_ROOT)
    old_tracked_keys = set()
    for pkg_name in old_full_deps:
        bare = pkg_name.split("[", 1)[0]
        if any(bare.startswith(p) for p in _TRACKED_PREFIXES):
            key = _KEY_ALIASES.get(bare) or bare
            old_tracked_keys.add(key)
    removed = sorted(old_tracked_keys - set(dep_versions.keys()))

    # dep_locked_versions：从 uv.lock 取每个追踪包的锁定版本，作为更新检测权威基准。
    # 仅记录 dep_versions 中存在且在 lock 里找到的包；找不到的省略（运行时回退下界）。
    locked_versions_raw = _read_uv_lock_versions()
    dep_locked_versions: dict[str, str] = {
        key: locked_versions_raw[key]
        for key in dep_versions
        if key in locked_versions_raw
    }

    data: dict = {
        "version": version,
        "channel": "stable",
        "python_version": python_version,
        "dep_versions": dep_versions,
    }
    # extras 单列，避免与 constraint 串混在一起难以解析。
    # 仅在有包带 extras 时写入（旧版兼容：缺失按"无 extras"）。
    if dep_extras:
        data["dep_extras"] = dep_extras
    if dep_locked_versions:
        data["dep_locked_versions"] = dep_locked_versions
    if removed:
        data["removed"] = removed
    version_path = dist_dir / "version.json"
    version_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  已生成 {version_path}")


def _generate_version_file(
    version: str, output_dir: Path, *, target: str = "main"
) -> Path:
    """生成 PyInstaller --version-file 所需的版本信息 Python 文件

    从 pyproject.toml 读取元数据（名称、版本、描述、作者），生成
    VSVersionInfo 格式文件，使打包出的 exe 在 Windows 属性页显示完整信息。

    主程序与 updater 是两个独立 exe，其 OriginalFilename / InternalName /
    FileDescription 等元数据必须区分，否则 updater.exe 的属性页会错误地
    显示 "VibeOCR.exe / A screenshot OCR application" 等主程序字段。

    Args:
        version: 版本号字符串（如 "0.4.5"）
        output_dir: 输出目录（通常为 dist 临时目录）
        target: 目标产物，"main"（VibeOCR 主程序）或 "updater"（updater.exe）

    Returns:
        生成的版本信息文件路径
    """
    import tomllib

    tomldata = tomllib.loads(PYPROJECT_TOML.read_text(encoding="utf-8"))
    project = tomldata.get("project", {})
    authors = project.get("authors", [])
    company = authors[0].get("name", "") if authors else ""
    # 版权年份与 LICENSE / README / 关于页（about_tab.py）保持一致：
    # 首版固定 2025，当前年份取系统日期，不同时显示为 "2025–当前" 区间
    # （en dash，U+2012... 实为 U+2013），单年时退化为 "2025"。
    first_year = 2025
    current_year = date.today().year
    year_range = (
        str(first_year)
        if current_year <= first_year
        else f"{first_year}–{current_year}"
    )

    # 各 exe 的特有元数据：主程序对外是 VibeOCR 应用本体，
    # updater 是后台自动更新组件，两者文件名与说明不应混淆。
    if target == "updater":
        original_filename = "updater.exe"
        internal_name = "updater"
        product_name = "VibeOCR Updater"
        file_description = "VibeOCR auto-updater"
        out_name = "version_info_updater.py"
    else:
        original_filename = "VibeOCR.exe"
        internal_name = "VibeOCR"
        product_name = "VibeOCR"
        # 主程序对外显示名固定为 "VibeOCR"（任务管理器/右键属性→详细信息→文件说明）。
        # 不用 pyproject.toml 的 description（'A screenshot OCR application'）——
        # 那是 PyPI 包描述，作为进程显示名既不准确也不利于用户识别。
        file_description = "VibeOCR"
        out_name = "version_info.py"

    parts = [int(x) for x in version.split(".")]
    while len(parts) < 4:
        parts.append(0)
    ver_tuple = tuple(parts[:4])

    content = (
        "# Auto-generated by bump_version.py — do not edit manually\n"
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(\n"
        f"    filevers={ver_tuple!r},\n"
        f"    prodvers={ver_tuple!r},\n"
        f"    mask=0x3f,\n"
        f"    flags=0x0,\n"
        f"    OS=0x40004,\n"
        f"    fileType=0x1,\n"
        f"    subtype=0x0,\n"
        f"    date=(0, 0),\n"
        f"  ),\n"
        f"  kids=[\n"
        f"    StringFileInfo(\n"
        f"      [StringTable(\n"
        f"        '040904B0',\n"
        f"        [StringStruct('CompanyName', {company!r}),\n"
        f"         StringStruct('FileDescription', {file_description!r}),\n"
        f"         StringStruct('FileVersion', {version!r}),\n"
        f"         StringStruct('InternalName', {internal_name!r}),\n"
        f"         StringStruct('LegalCopyright', 'Copyright (c) {year_range} {company}'),\n"
        f"         StringStruct('OriginalFilename', {original_filename!r}),\n"
        f"         StringStruct('ProductName', {product_name!r}),\n"
        f"         StringStruct('ProductVersion', {version!r})]\n"
        f"      )]\n"
        f"    ),\n"
        f"    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])\n"
        f"  ]\n"
        f")\n"
    )

    version_file = output_dir / out_name
    # CI 等全新克隆环境下 dist/ 不存在（已 gitignore），write_text 不会自动
    # 创建父目录，故须显式 mkdir，否则 FileNotFoundError。
    version_file.parent.mkdir(parents=True, exist_ok=True)
    version_file.write_text(content, encoding="utf-8")
    print(f"  已生成版本信息文件 ({target}): {version_file}")
    return version_file


def _build_updater(dist_dir: Path, version_file: Path | None = None) -> bool:
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

    if version_file is not None:
        cmd.extend(["--version-file", str(version_file)])

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

    # 清理 __pycache__ 目录：src/vibeocr 经 --add-data 作为 datas 收集（worker
    # 子进程用便携 Python 以原始 .py 形式 import，见 PACKAGE_DATA 注释），而
    # PyInstaller 会把源码树下的 __pycache__/*.pyc 一并复制进 _internal/vibeocr。
    # manifest 校验将 __pycache__ 视为禁止路径（见 build_manifest.FORBIDDEN_TOP_NAMES），
    # 若不清理会导致 _package_zip 的 verify_archive 自检失败。字节码缓存对运行
    # 无意义（便携 Python 会按需重新生成），此处递归删除整个 dist 内的 __pycache__。
    pycache_dirs = [p for p in dist_dir.rglob("__pycache__") if p.is_dir()]
    pycache_deleted = 0
    for pyc in pycache_dirs:
        shutil.rmtree(pyc, ignore_errors=True)
        pycache_deleted += 1
    if pycache_deleted:
        print(f"  清理 __pycache__: 删除 {pycache_deleted} 个目录")


def _package_zip(dist_dir: Path, version: str) -> Path | None:
    """将 dist_dir 打包为 zip，内嵌 artifact-manifest.json，并计算 SHA256。

    manifest 记录每个文件的相对路径、字节数和 SHA-256，用于校验发布包完整性
    和防止运行产物（output/ 等）泄漏。打包后立即调用 verify_archive 自检。
    """
    from vibeocr.build_manifest import create_manifest, verify_archive

    zip_name = f"VibeOCR-v{version}-win64"
    zip_path = DIST_BASE_DIR / f"{zip_name}.zip"

    # 解压后顶层文件夹命名为 VibeOCR（而非版本号目录），方便用户手动拖出。
    # zip 文件名本身保留版本号，便于分发与归档。
    top_folder = "VibeOCR"

    # 生成 manifest：纳入 dist_dir 下全部文件（"." 代表整个目录）。
    # create_manifest 会自动排除 output/、.venv/ 等禁止路径。
    manifest = create_manifest(dist_dir, allowed_roots=(".",))

    print(f"打包 {zip_path}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in dist_dir.rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(dist_dir).as_posix()
                arcname = f"{top_folder}/{relative}"
                zf.write(file_path, arcname)
        # manifest 放 ZIP 根目录（VibeOCR/artifact-manifest.json）
        zf.writestr(
            f"{top_folder}/{_MANIFEST_FILENAME}",
            json.dumps(manifest, ensure_ascii=False),
        )

    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    sha256_path = DIST_BASE_DIR / f"{zip_name}.zip.sha256"
    sha256_path.write_text(f"{sha256}  {zip_name}.zip\n", encoding="utf-8")

    # 自检：刚打的包必须通过 manifest 校验
    try:
        verify_archive(zip_path)
    except (ValueError, FileNotFoundError) as e:
        print(f"\n  错误: 产物 manifest 校验失败: {e}")
        return None

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    entry_count = manifest.get("entry_count", 0)
    print(f"  zip 大小: {size_mb:.1f} MB")
    print(f"  文件数: {entry_count}")
    print(f"  SHA256: {sha256}")
    print("  manifest 校验: OK")
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


def _get_pyinstaller_cmd(
    version: str, version_file: Path | None = None
) -> list[str]:
    """构建 PyInstaller 命令行参数

    Args:
        version: 版本号字符串
        version_file: PyInstaller --version-file 路径（可选）

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
        # 字节码优化等级 2（等价 python -OO）：移除 assert 与 __doc__，减小 PYZ 归档、
        # 略微加速字节码加载。本项目 assert 全为类型收窄防御（无运行时逻辑依赖），
        # GUI 进程不暴露 __doc__；worker 子进程走 datas 原始 .py 不受影响。
        # 需 PyInstaller >= 6.6（--optimize 参数自此版本引入）。
        "--optimize",
        "2",
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

    if version_file is not None:
        cmd.extend(["--version-file", str(version_file)])

    return cmd


def _run_build(version: str, force: bool = False) -> bool:
    """构建并验证正式 WinUI release artifact。

    Args:
        version: 版本号字符串
        force: True 时已存在的目标目录直接删除重建，不交互询问
            （CI/非交互场景用）。False 时遇到已存在目录会 input() 询问。
    """
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

    build_script = PROJECT_ROOT / "scripts" / "build_winui_release.ps1"
    verifier = PROJECT_ROOT / "scripts" / "verify_winui_artifact.ps1"
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        print("\n错误: 找不到 Windows PowerShell，无法构建 WinUI release")
        return False

    # 1. 发布 WinUI App + Bootstrapper，并装配 UI-free WorkerHost source。
    print(f"\n[1/4] 构建 WinUI release VibeOCR v{version}...")
    try:
        subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(build_script),
                "-OutputDir",
                str(dist_path),
                "-Version",
                version,
            ],
            check=True,
            cwd=PROJECT_ROOT,
        )
    except subprocess.CalledProcessError as e:
        print(f"\nWinUI release 构建失败，退出码: {e.returncode}")
        return False

    # 2. 生成版本/依赖元数据与纯 stdlib updater，并对 staging 做结构门禁。
    print("\n[2/4] 生成 version.json、updater.exe 并验证 release layout...")
    _generate_version_json(version, dist_path)
    updater_version_file = _generate_version_file(
        version,
        DIST_BASE_DIR / f"build-{version}",
        target="updater",
    )
    if not _build_updater(dist_path, version_file=updater_version_file):
        return False
    try:
        subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(verifier),
                "-Artifact",
                str(dist_path),
            ],
            check=True,
            cwd=PROJECT_ROOT,
        )
    except subprocess.CalledProcessError as e:
        print(f"\nWinUI staging 验证失败，退出码: {e.returncode}")
        return False

    # 3. 打 ZIP + SHA256 + artifact manifest。
    print("\n[3/4] 打包 zip...")
    zip_path = _package_zip(dist_path, version)
    if zip_path is None:
        return False

    # 4. 对最终 ZIP 再执行 WinUI required/forbidden layout 门禁。
    print("\n[4/4] 验证最终 WinUI artifact...")
    try:
        subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(verifier),
                "-Artifact",
                str(zip_path),
            ],
            check=True,
            cwd=PROJECT_ROOT,
        )
    except subprocess.CalledProcessError as e:
        print(f"\nWinUI ZIP 验证失败，退出码: {e.returncode}")
        return False

    print(f"\n{'=' * 50}")
    print("构建完成!")
    print(f"  应用目录:   {dist_path}")
    print(f"  主分发包:   {zip_path}")
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
    print("是否立即构建 WinUI release? [Y/n]: ", end="", flush=True)
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

        return 0 if _run_build(current_str, force=args.force) else 1

    # 模式2: 重新打包指定版本
    if args.rebuild:
        rebuild_version = args.rebuild
        if not SEMVER_RE.match(rebuild_version):
            print(f"错误: 无效版本号 '{rebuild_version}'")
            return 1
        return 0 if _run_build(rebuild_version, force=args.force) else 1

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
            return 0 if _run_build(current_str, force=args.force) else 1
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
    # P3：对比上个 release tag 的依赖，生成依赖变更说明
    old_deps = _get_last_release_pyproject_deps(current_str)
    new_deps = _parse_dependencies(PYPROJECT_TOML)
    dep_diff = _compute_dep_diff(old_deps, new_deps)
    if any(dep_diff.values()):
        diff_summary = sum(len(v) for v in dep_diff.values())
        print(f"  检测到 {diff_summary} 项依赖变更，将写入 CHANGELOG")
    update_changelog(new_str, commits, dep_diff)
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
        _run_build(new_str, force=args.force)

    print(f"\n完成! 版本已升级到 {new_str}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
