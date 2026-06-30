"""WebEngine 资源按需下载管理器

主安装包剔除 WebEngine（Qt6WebEngineCore.dll 等 ~280MB 未压缩）后，
首启向导中按需下载资源包（瘦身后 ~80MB）并解压到 ``_internal/PySide6/``。

设计要点：
- 检测就绪：``_internal/PySide6/Qt6WebEngineCore.dll`` 是否存在
- 版本对齐：version.json 的 ``webengine_assets_version`` 与本地 marker 比对
- 下载源选择：按 NetworkDetector.network_type 选 Gitee/GitHub（与 update_service 共享 SSOT）
- 解压：仿 updater_main.extract_zip + 路径穿越防护
- 可写性回退：_internal/PySide6/ 不可写时回退到 python/webengine_assets/

复用基础设施：
- env_manager.download_file_with_progress（断点续传 + 重试）
- update_service.verify_sha256（校验）
- services/env_config 的路径助手（marker SSOT）
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from vibeocr.env_manager import (
    _get_meipass,
    download_artifact_multi_source,
    get_project_root,
)
from vibeocr.services.env_config import (
    build_asset_url_pairs,
    get_webengine_assets_path,
    get_webengine_cache_dir,
    get_webengine_reinstall_marker_path,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from vibeocr.network_detector import NetworkDetector

logger = logging.getLogger(__name__)

# WebEngine 就绪的核心判据 DLL（与 _ensure_web_view 延迟 import 的模块对应）
_WEBENGINE_CORE_DLL = "Qt6WebEngineCore.dll"

# 资源包内顶层目录名（解压后应直接归位到 PySide6/）
_PYSIDE6_DIRNAME = "PySide6"


# ---------------------------------------------------------------------------
# 路径定位
# ---------------------------------------------------------------------------


def _get_pyside6_target_dir() -> Path:
    """WebEngine 资源解压目标目录：打包态 _internal/PySide6/，开发态跳过。

    打包态（onedir）：``_MEIPASS`` == ``<app>/_internal/``，PySide6 平铺于其下，
    是磁盘真实目录（非 onefile 的只读 SFX），可写。
    """
    meipass = _get_meipass()
    if meipass is not None:
        return meipass / _PYSIDE6_DIRNAME
    # 开发态无 _MEIPASS，返回 PySide6 的实际安装位置（site-packages）
    import PySide6

    return Path(PySide6.__file__).resolve().parent


def _get_pyside6_fallback_dir() -> Path:
    """可写性回退目录：python/webengine_assets/（exe 同级，必可写）。"""
    return get_project_root() / "python" / "webengine_assets"


def _is_target_writable(target_dir: Path) -> bool:
    """探测目标目录是否可写（创建临时文件测试）。"""
    if not target_dir.exists():
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
    probe = target_dir / ".webengine_writeprobe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 就绪检测与版本对齐
# ---------------------------------------------------------------------------


def is_webengine_ready() -> bool:
    """检测 WebEngine 是否就绪（核心 DLL 存在即可）。

    运行时 result_view_widget._ensure_web_view 的延迟 import 会真实加载 DLL，
    此处的文件检测是低成本的预判（避免每次 import 试错）。
    """
    target = _get_pyside6_target_dir()
    return (target / _WEBENGINE_CORE_DLL).exists()


def _read_version_json_webengine_ver() -> str | None:
    """读 version.json 的 webengine_assets_version；缺失返回 None。"""
    meipass = _get_meipass()
    if meipass is None:
        return None
    vj = meipass / "version.json"
    if not vj.exists():
        return None
    try:
        data = json.loads(vj.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    ver = data.get("webengine_assets_version")
    return str(ver) if ver else None


def _read_installed_marker() -> str | None:
    """读本地已装资源包版本（webengine_assets.json）；缺失返回 None。"""
    path = get_webengine_assets_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return str(data.get("assets_version") or "") or None


def _write_installed_marker(version: str) -> None:
    path = get_webengine_assets_path()
    data = {"assets_version": version}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def needs_install() -> bool:
    """是否需要下载安装 WebEngine（未就绪，或版本与主包不一致，或待重装标记存在）。"""
    if get_webengine_reinstall_marker_path().exists():
        return True
    if not is_webengine_ready():
        return True
    expected = _read_version_json_webengine_ver()
    installed = _read_installed_marker()
    # 主包未声明版本时，以 DLL 存在为准（向后兼容旧版/全量包）
    if expected is None:
        return False
    return expected != installed


def clear_reinstall_marker() -> None:
    """安装成功后清理待重装标记。"""
    marker = get_webengine_reinstall_marker_path()
    if marker.exists():
        try:
            marker.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 下载源选择（与 update_service 共享逻辑）
# ---------------------------------------------------------------------------


def _build_assets_filename(version: str) -> str:
    """资源包文件名：VibeOCR-v<ver>-webengine-win64.zip"""
    return f"VibeOCR-v{version}-webengine-win64.zip"


# ---------------------------------------------------------------------------
# 下载与解压
# ---------------------------------------------------------------------------


def _safe_extract_zip(zip_path: Path, target_dir: Path) -> bool:
    """安全解压 zip 到目标目录，带路径穿越防护。

    资源包内顶层目录为 ``PySide6/``（_PYSIDE6_DIRNAME），本函数会剥掉该前缀，
    使文件直接落入 target_dir（即 target_dir 充当 PySide6/ 目录）。
    这样无论 target 是 ``_internal/PySide6`` 还是回退目录 ``python/webengine_assets``，
    文件都归位正确。

    仿 env_manager.install_embedded_python 的成员遍历 + 穿越校验。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    base = target_dir.resolve()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                # 剥掉顶层 PySide6/ 前缀
                name = member.filename
                prefix = _PYSIDE6_DIRNAME + "/"
                if name.startswith(prefix):
                    name = name[len(prefix):]
                elif name.startswith(_PYSIDE6_DIRNAME + "\\"):
                    name = name[len(_PYSIDE6_DIRNAME) + 1:]
                if not name:
                    continue
                # 防路径穿越：解析后必须仍在 base 之下
                dest = (base / name).resolve()
                try:
                    dest.relative_to(base)
                except ValueError:
                    logger.warning(f"跳过可疑路径成员: {member.filename}")
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as out:
                    out.write(src.read())
        return True
    except (zipfile.BadZipFile, OSError) as e:
        logger.error(f"解压 WebEngine 资源包失败: {e}")
        return False


def download_and_install(
    detector: NetworkDetector | None = None,
    report_fn: Callable[[str, int, int], None] | None = None,
) -> bool:
    """下载并安装 WebEngine 资源包。

    Args:
        detector: 网络检测器（None 时内部按默认网络类型推断）。
        report_fn: 进度回调 ``(message, downloaded, total)``，可 None。

    Returns:
        True 安装成功（is_webengine_ready 为 True），False 失败。
    """
    # 版本号：优先用主包 version.json 声明，否则标记为 unknown
    version = _read_version_json_webengine_ver() or "0.0.0"

    # 网络类型
    if detector is not None:
        network_type = detector.network_type
    else:
        try:
            from vibeocr.network_detector import NetworkDetector

            network_type = NetworkDetector(get_project_root()).network_type
        except Exception:
            network_type = "international"

    # 用 build_asset_url_pairs 生成同源 (zip, sha) 候选对，确保校验文件与
    # 被校验文件同源同 tag（替代旧的 select_assets_source + 盲拼 {url}.sha256）。
    fname = _build_assets_filename(version)
    sha_fname = f"{fname}.sha256"
    url_pairs = build_asset_url_pairs(network_type, version, fname, sha_fname)
    zip_urls = [p[0] for p in url_pairs]
    sha_urls = [p[1] for p in url_pairs]

    cache_dir = get_webengine_cache_dir()
    zip_path = cache_dir / fname
    sha_path = cache_dir / sha_fname

    if report_fn:
        report_fn(f"正在下载 WebEngine 渲染组件（{len(zip_urls)} 个源）…", 0, 0)

    # 多源回退 + 成对同源 SHA 校验 + 结构化失败原因，统一走 download_artifact_multi_source
    downloaded, _reason = download_artifact_multi_source(
        zip_urls,
        zip_path,
        description="WebEngine 资源包",
        max_retries=3,
        sha_candidates=sha_urls,
        sha_dest_path=sha_path,
        source_switch_fn=(
            (lambda src, r: report_fn(f"{src} 失败，切换下一个源…", 0, 0))
            if report_fn
            else None
        ),
    )

    if not downloaded:
        if report_fn:
            report_fn("WebEngine 资源包下载失败，请检查网络后重试。", 0, 0)
        return False

    # 解压到目标目录（带可写性回退）
    target_dir = _get_pyside6_target_dir()
    if not _is_target_writable(target_dir):
        logger.warning(
            f"{target_dir} 不可写，回退解压到 python/webengine_assets/"
        )
        target_dir = _get_pyside6_fallback_dir()
        # 回退场景：解压后需运行时 os.add_dll_directory 补路径
        # 此处仅解压，路径补充由启动逻辑处理（标记 needs_dll_path_patch）
        _write_installed_marker(version + "+fallback")
    else:
        _write_installed_marker(version)

    if report_fn:
        report_fn("正在解压 WebEngine 组件…", 0, 0)

    if not _safe_extract_zip(zip_path, target_dir):
        return False

    # 清理缓存
    zip_path.unlink(missing_ok=True)
    sha_path.unlink(missing_ok=True)

    clear_reinstall_marker()

    ready = is_webengine_ready()
    if ready and report_fn:
        report_fn("WebEngine 组件安装完成。", 0, 0)
    elif not ready and report_fn:
        report_fn("WebEngine 组件已解压但未检测到核心 DLL，请重试。", 0, 0)
    return ready


def maybe_patch_dll_search_path() -> bool:
    """若使用了可写性回退目录，启动时补充 DLL 搜索路径。

    打包态若资源解压到了 python/webengine_assets/（而非 _internal/PySide6/），
    PySide6 延迟 import 仍找不到 DLL，需把回退目录加入 Windows DLL 搜索路径。
    返回 True 表示已补充路径（需在 import QtWebEngineWidgets 之前调用）。
    """
    import os

    installed = _read_installed_marker() or ""
    if "+fallback" not in installed:
        return False
    fallback = _get_pyside6_fallback_dir()
    if not fallback.exists():
        return False
    if os.name == "nt":
        try:
            os.add_dll_directory(str(fallback))
            logger.info(f"已补充 DLL 搜索路径: {fallback}")
            return True
        except OSError as e:
            logger.warning(f"补充 DLL 搜索路径失败: {e}")
    return False
