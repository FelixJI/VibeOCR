"""VibeOCR 更新替换逻辑（共享模块）

被两个调用方复用：
1. ``scripts/updater_main.py`` —— 独立 updater.exe（首选替换器），``self_exe_names=("updater.exe",)``。
2. ``src/vibeocr/main.py`` 的 ``--self-update`` 模式 —— 主程序自身充当兜底替换器，
   ``self_exe_names=("VibeOCR.exe", "updater.exe")``。

设计约束（重要）：
- **纯 stdlib**，不依赖 vibeocr 任何模块。原因：updater.exe 用 PyInstaller ``--onefile``
  打包且 ``pathex=[]``、无 ``--paths src``，无法 import ``src/vibeocr/`` 下任何模块。
  本模块放在 ``scripts/``，与 ``updater_main.py`` 同目录，updater 打包时自动收集；
  主程序侧把本文件作为 ``--add-data`` 资源打进 ``_internal/``，运行时注入 sys.path。
- 复用同一份替换逻辑，避免 updater 与 self-update 两条路径各自实现导致行为漂移。

核心流程：verify(sha256+zip) → signal_ready → extract → replace_app_files
        （备份-删除-复制-失败回滚）→ sync deps/webengine → cleanup → launch_app
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import traceback
import zipfile
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 更新时保留的目录
_PRESERVE_DIRS = {"python", "data", "config"}

logger = logging.getLogger("updater")


# ---------------------------------------------------------------------------
# 日志（与 updater_main / self-update 共用，写到 app_dir/data/logs/<name>.log）
# ---------------------------------------------------------------------------


def setup_logging(app_dir: Path, log_filename: str) -> None:
    """配置日志：写到 ``app_dir/data/logs/<log_filename>``。

    updater.exe / VibeOCR.exe 都是 ``console=False``（windowed），stdout/stderr 全部丢弃。
    不写文件的话，更新阶段一旦失败就完全没有现场（v0.1.13→v0.2.2 更新就是 updater
    在替换文件阶段崩溃，但因为没日志而无法排查）。同时对 stdout 也输出一份，开发态可见。
    """
    log_dir = app_dir / "data" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 连日志目录都建不出来时，退化到只输出 stdout（总比啥都没有强）
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        return

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_dir / log_filename,
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    stream_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


# ---------------------------------------------------------------------------
# 就绪握手信号
# ---------------------------------------------------------------------------


def signal_ready(app_dir: Path, ready_filename: str) -> None:
    """写就绪标记文件，供主程序端轮询确认替换器「活着」。

    替换器启动后、做任何替换前第一时间调用。这个文件证明：进程已起来、Python
    解释器已初始化、日志目录可写——即「能干活」。崩溃的替换器写不出这个文件，
    主程序端据此走兜底路径。

    Args:
        app_dir: 应用安装目录，ready 文件落在 ``app_dir/data/cache/update/``。
        ready_filename: 就绪文件名，调用方区分（如 ``updater.ready`` /
            ``self_update.ready``），避免两条路径互相误判。
    """
    try:
        ready_path = app_dir / "data" / "cache" / "update" / ready_filename
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.write_text(datetime.now().isoformat(), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        # 写 ready 失败不致命：最坏情况是主程序端误判握手失败、走兜底路径，
        # 兜底路径同样能完成替换。仅记录。
        logger.warning(f"写就绪信号失败（主程序端可能误判握手失败）: {e}")


# ---------------------------------------------------------------------------
# 校验与解压
# ---------------------------------------------------------------------------


def verify_zip(zip_path: Path) -> bool:
    if not zip_path.exists():
        logger.error(f"zip 文件不存在: {zip_path}")
        return False
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                logger.error(f"zip 文件损坏，损坏条目: {bad}")
                return False
        return True
    except zipfile.BadZipFile:
        logger.error("无效的 zip 文件")
        return False


def verify_sha256(zip_path: Path) -> bool:
    sha256_path = Path(str(zip_path) + ".sha256")
    if not sha256_path.exists():
        # 与主程序下载阶段（update_service.verify_sha256）保持一致：
        # 缺失校验文件即视为不可信，拒绝更新，而不是放行。
        # 此前这里「找不到就跳过」会让更新包在下载阶段之后绕过完整性校验。
        logger.error(f"未找到 SHA256 校验文件，拒绝更新: {sha256_path}")
        return False

    expected = sha256_path.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = hashlib.sha256(zip_path.read_bytes()).hexdigest().lower()

    if actual != expected:
        logger.error("SHA256 校验失败")
        logger.error(f"  expected: {expected}")
        logger.error(f"  actual:   {actual}")
        return False
    return True


def extract_zip(zip_path: Path, app_dir: Path) -> Path:
    tmp_dir = app_dir / "data" / "cache" / "update" / "tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    logger.info("解压更新包...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp_dir)

    # zip 内可能有一层 VibeOCR/ 目录
    contents = list(tmp_dir.iterdir())
    if len(contents) == 1 and contents[0].is_dir():
        return contents[0]
    return tmp_dir


# ---------------------------------------------------------------------------
# 文件替换（备份-删除-复制-回滚）
# ---------------------------------------------------------------------------


def rename_locked_self_exe(app_dir: Path, self_name: str) -> None:
    """处理正在运行、无法被删除/覆盖的可执行文件。

    Windows 允许对正在运行的可执行文件执行 rename，但禁止 delete/overwrite。
    替换器在替换 app_dir 时会试图 rmtree 正在运行的旧 exe（即它自己或调用方主程序），
    该删除会因文件被 OS 锁定而失败，导致替换流程中断、应用停在半残状态。

    本函数在替换前把旧 exe 改名（加 ``.old`` 后缀），让随后的复制能写入新版。
    改名后的旧文件由替换器在 cleanup 阶段删除（此刻旧进程已退出），或留待下次更新清理。

    Args:
        app_dir: 应用安装目录。
        self_name: 要避让的 exe 文件名，如 ``"updater.exe"`` 或 ``"VibeOCR.exe"``。
            通用化设计：updater.exe 只需避让自己；self-update 模式需同时避让
            ``VibeOCR.exe``（调用方主程序，已 sys.exit 但锁可能未及时释放）和
            ``updater.exe``（同目录另一个可能被锁的 exe）。仅处理 Windows。
    """
    if os.name != "nt":
        return
    self_path = app_dir / self_name
    if not self_path.exists():
        return
    old_path = app_dir / f"{self_name}.old"
    try:
        # 上次更新残留的 .old 优先清掉
        if old_path.exists():
            old_path.unlink(missing_ok=True)
        self_path.rename(old_path)
        logger.info(f"已重命名运行中的旧 {self_name} -> {old_path.name}")
    except OSError as e:
        # 改名失败不致命：可能旧 exe 已退出、锁已释放。记录后继续，让后续删除/复制
        # 流程按正常路径走（失败会被 replace_app_files 的回滚逻辑接住）。
        logger.warning(f"重命名 {self_name} 失败（继续按原流程替换）: {e}")


def replace_app_files(
    new_files_dir: Path,
    app_dir: Path,
    self_exe_names: tuple[str, ...] = ("updater.exe",),
) -> bool:
    """用新文件替换 app_dir 中的非保留内容。

    采用「先备份 → 删除旧 → 复制新 → 失败回滚」策略，确保 app_dir 永远不会
    处于半残状态（旧文件已删、新文件未拷全），否则用户机器上的应用将无法启动。

    Args:
        new_files_dir: 解压后的新文件目录（可能含一层 VibeOCR/ 子目录）。
        app_dir: 应用安装目录。
        self_exe_names: 替换前需「改名避让」的运行中 exe 列表。
            updater 调用传 ``("updater.exe",)``；self-update 调用传
            ``("VibeOCR.exe", "updater.exe")``。
    """
    logger.info("替换应用文件...")

    # 记录旧 version.json 的 dep_versions 与 webengine_assets_version
    # （用于依赖同步及 WebEngine 资源包重装判定）
    old_version_json = app_dir / "version.json"
    old_deps: dict = {}
    old_webengine_ver: str = ""
    if old_version_json.exists():
        try:
            old_data = json.loads(old_version_json.read_text(encoding="utf-8"))
            old_deps = old_data.get("dep_versions", {})
            old_webengine_ver = str(old_data.get("webengine_assets_version") or "")
        except Exception:
            pass

    # 运行中的 exe 必须先改名，否则下面 rmtree 删它必然失败
    for self_name in self_exe_names:
        rename_locked_self_exe(app_dir, self_name)

    # 待替换的旧条目（保留目录除外）
    old_items = [item for item in app_dir.iterdir() if item.name not in _PRESERVE_DIRS]

    # 1) 备份将要删除/覆盖的旧条目，以便复制失败时回滚
    backup_dir = app_dir / "data" / "cache" / "update" / "_backup"
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backed_up: list[tuple[Path, Path]] = []  # (原位置, 备份位置)
    try:
        for item in old_items:
            bak = backup_dir / item.name
            if item.is_dir():
                shutil.copytree(item, bak, dirs_exist_ok=True)
            else:
                shutil.copy2(item, bak)
            backed_up.append((item, bak))
    except Exception as e:
        logger.error(f"备份旧文件失败，中止更新: {e}")
        shutil.rmtree(backup_dir, ignore_errors=True)
        return False

    # 2) 删除旧条目
    for item in old_items:
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"删除 {item} 失败: {e}")

    # 3) 复制新文件；任一失败则回滚
    try:
        for item in new_files_dir.iterdir():
            if item.name in _PRESERVE_DIRS:
                continue
            dest = app_dir / item.name
            try:
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
            except Exception as e:
                # 此处 item 必然已绑定（来自上面的 for）
                logger.error(f"复制 {item} 失败: {e}")
                logger.info("正在回滚到更新前状态...")
                _restore_backup(app_dir, backed_up, backup_dir)
                return False
    except Exception as e:
        # iterdir() 自身失败（目录不存在/无权限等），item 此时未绑定
        logger.error(f"读取更新包内容失败: {e}")
        logger.info("正在回滚到更新前状态...")
        _restore_backup(app_dir, backed_up, backup_dir)
        return False

    # 4) 复制成功，清理备份
    shutil.rmtree(backup_dir, ignore_errors=True)

    # 5) 检查 AI 依赖版本变化 + WebEngine 资源包版本变化
    new_version_json = app_dir / "version.json"
    if new_version_json.exists():
        try:
            new_data = json.loads(new_version_json.read_text(encoding="utf-8"))
            _sync_dependencies(old_deps, new_data, app_dir)
            _sync_webengine(old_webengine_ver, new_data, app_dir)
        except Exception as e:
            logger.warning(f"检查依赖版本失败: {e}")

    return True


def _restore_backup(
    app_dir: Path, backed_up: list[tuple[Path, Path]], backup_dir: Path
) -> None:
    """从备份恢复 app_dir 中被删除/覆盖的条目。"""
    # 先清掉复制阶段可能已写入的残缺文件（非保留、非备份目录）
    for item in app_dir.iterdir():
        if item.name in _PRESERVE_DIRS:
            continue
        # 跳过备份目录自身（在 data/ 下，属于保留目录，这里保险起见再判一次）
        try:
            if backup_dir in item.parents or item == backup_dir:
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        except Exception:
            pass

    for original, bak in backed_up:
        try:
            if bak.is_dir():
                shutil.copytree(bak, original, dirs_exist_ok=True)
            else:
                shutil.copy2(bak, original)
        except Exception as e:
            logger.warning(f"回滚 {original} 失败: {e}")

    shutil.rmtree(backup_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 依赖与 WebEngine 同步（写标记，由新版主程序启动时执行）
# ---------------------------------------------------------------------------


def _sync_dependencies(old_deps: dict, new_data: dict, app_dir: Path) -> None:
    """检查 AI 依赖版本变化并写入"待同步"标记。

    替换器不能 import vibeocr（python/ 里没装 vibeocr，updater 是独立 --onefile
    打包；self-update 虽然在主程序进程内，但同样不应在此引入 vibeocr 重模块），
    因此不在替换器里直接 pip 安装。改为：若 dep_versions 有变化，把变更项
    写入 data/settings/pending_sync.json，由覆盖后的新版 VibeOCR 启动时用
    env_manager.install_embedded_dependencies（含 GPU/CUDA tag/镜像/PyPI 回退的完整
    逻辑）执行升级。这样避免替换器用裸 pip 走 PyPI 把 paddle/torch 装成 CPU 版。
    """
    new_deps = new_data.get("dep_versions", {})
    changed = {
        pkg: version
        for pkg, version in new_deps.items()
        if old_deps.get(pkg) != version
    }

    if not changed:
        logger.info("AI 依赖版本无变化")
        return

    logger.info(f"检测到依赖变化: {changed}")
    logger.info("写入待同步标记，将由新版 VibeOCR 启动时升级...")

    settings_dir = app_dir / "data" / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    pending_path = settings_dir / "pending_sync.json"

    pending = {
        "version": new_data.get("version", ""),
        "dep_versions": changed,
        "written_at": datetime.now().isoformat(),
    }
    try:
        pending_path.write_text(
            json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"已写入待同步标记: {pending_path}")
    except Exception as e:
        logger.warning(f"写入待同步标记失败（依赖将不会自动升级）: {e}")


def _sync_webengine(old_ver: str, new_data: dict, app_dir: Path) -> None:
    """检查 WebEngine 资源包版本变化并写入"待重装"标记。

    主包剔除 WebEngine 后，资源包单独下载解压。更新时 replace_app_files 会
    删除旧 _internal/PySide6/ 下的 WebEngine 文件（不在保留目录中），而新主包
    又不含 WebEngine。若新版资源包版本与本地已装版本不一致（或已被删除），
    写入 webengine_pending_reinstall.json，由新版 VibeOCR 启动时通过
    webengine_manager.download_and_install 重新下载。

    与 _sync_dependencies 同模式：替换器不 import vibeocr，仅写标记。
    """
    new_ver = str(new_data.get("webengine_assets_version") or "")
    if not new_ver:
        # 主包未声明 webengine 版本（全量包 --bundle-webengine 或旧版），
        # 无需外置下载，跳过。
        logger.info("新版未声明 webengine_assets_version，跳过 WebEngine 同步")
        return

    settings_dir = app_dir / "data" / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    reinstall_marker = settings_dir / "webengine_pending_reinstall.json"

    if old_ver == new_ver:
        # 版本未变，但旧文件可能已被 replace_app_files 删除——检测 DLL 是否在位，
        # 不在则需重新下载（PySide6 不在保留目录中，更新会整体替换）。
        pyside6_dir = app_dir / "_internal" / "PySide6"
        if (pyside6_dir / "Qt6WebEngineCore.dll").exists():
            logger.info("WebEngine 资源包版本无变化且文件在位，无需重装")
            return
        logger.info("WebEngine 文件已在更新中被删除，写入待重装标记")
        reason = "files_removed_by_update"
    else:
        logger.info(
            f"WebEngine 资源包版本变化: {old_ver or '(未安装)'} -> {new_ver}，写入待重装标记"
        )
        reason = "version_changed"

    try:
        reinstall_marker.write_text(
            json.dumps(
                {"version": new_ver, "reason": reason},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        logger.info(f"已写入 WebEngine 待重装标记: {reinstall_marker}")
    except Exception as e:
        logger.warning(f"写入 WebEngine 待重装标记失败: {e}")


# ---------------------------------------------------------------------------
# 收尾：清理临时文件 + 启动主程序
# ---------------------------------------------------------------------------


def cleanup(zip_path: Path, tmp_dir: Path | None) -> None:
    if tmp_dir and tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)

    zip_path.unlink(missing_ok=True)
    sha256_path = Path(str(zip_path) + ".sha256")
    sha256_path.unlink(missing_ok=True)

    # 清理 update 缓存目录（如果为空）
    update_dir = zip_path.parent
    if update_dir.exists():
        try:
            update_dir.rmdir()
        except OSError:
            pass

    # 清理上次更新残留的旧 *.exe.old（此刻旧进程已退出，可删除）。
    # zip_path 形如 <app>/data/cache/update/VibeOCR-vX-win64.zip，向上回溯到 app_dir。
    app_dir = zip_path.parents[3] if len(zip_path.parents) >= 4 else None
    if app_dir is not None and os.name == "nt":
        for exe_name in ("updater.exe", "VibeOCR.exe"):
            old_exe = app_dir / f"{exe_name}.old"
            if old_exe.exists():
                try:
                    old_exe.unlink(missing_ok=True)
                    logger.info(f"已清理上次更新残留: {old_exe.name}")
                except OSError as e:
                    # 仍被占用也无妨，下次更新会再清理
                    logger.debug(f"清理 {old_exe.name} 失败（忽略）: {e}")


def launch_app(app_dir: Path, exe_name: str = "") -> None:
    """启动主程序。exe_name 默认按平台取 VibeOCR.exe / VibeOCR。"""
    if not exe_name:
        exe_name = "VibeOCR.exe" if os.name == "nt" else "VibeOCR"
    exe_path = app_dir / exe_name
    if exe_path.exists():
        logger.info(f"启动 {exe_path}")
        subprocess.Popen(
            [str(exe_path)],
            creationflags=0x8 if os.name == "nt" else 0,
            cwd=str(app_dir),
        )
    else:
        logger.warning(f"未找到主程序 {exe_path}")


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------


def run_replacement(
    zip_path: Path,
    app_dir: Path,
    self_exe_names: tuple[str, ...] = ("updater.exe",),
    ready_filename: str = "updater.ready",
) -> int:
    """替换流程统一入口：校验 → 写就绪信号 → 解压 → 替换 → 清理 → 启动。

    Args:
        zip_path: 已下载的更新包 zip。
        app_dir: 应用安装目录。
        self_exe_names: 替换前需改名避让的运行中 exe（见 replace_app_files）。
        ready_filename: 就绪信号文件名（见 signal_ready），由调用方区分路径来源。

    Returns:
        0 成功，1 失败（已写日志，调用方据此 sys.exit）。
    """
    logger.info("VibeOCR 替换流程启动")
    logger.info(f"更新包: {zip_path}")
    logger.info(f"应用目录: {app_dir}")

    # 第一时间写就绪信号，让主程序端确认替换器「活着」。
    # 必须在任何可能失败的替换步骤之前，否则主程序端握不到手会误判。
    signal_ready(app_dir, ready_filename)

    try:
        if not verify_zip(zip_path):
            return 1
        if not verify_sha256(zip_path):
            return 1

        new_files_dir = extract_zip(zip_path, app_dir)

        if not replace_app_files(new_files_dir, app_dir, self_exe_names):
            logger.error("更新失败，请手动下载最新版本")
            return 1

        cleanup(
            zip_path, new_files_dir.parent if new_files_dir.name != "tmp" else new_files_dir
        )
        launch_app(app_dir)

        logger.info("更新完成!")
        return 0
    except Exception:
        # 兜底：任何未捕获异常都写进日志文件，避免「静默崩溃、无现场」。
        logger.error("更新过程中发生未捕获异常:\n%s", traceback.format_exc())
        return 1
