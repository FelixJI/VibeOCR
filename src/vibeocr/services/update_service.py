"""应用更新服务

负责检测新版本、下载更新包、显示更新对话框、启动 updater.exe。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

# 发布仓库标识与下载源选择收敛到 env_config（SSOT）。
# 发布渠道（方案 C）：CNB 仅镜像代码；产物主源 GitHub，镜像源 Gitee。
# 客户端按 NetworkDetector 选源：国内优先 Gitee（匿名可读 Release），
# 回退 gh 代理加速（gh-proxy / ghproxy）→ GitHub 裸连；海外直连 GitHub，回退 Gitee。
# CNB OpenAPI 需 token 鉴权，客户端无法匿名访问，不用于更新。
from vibeocr.services.env_config import (  # noqa: E402
    GITHUB_API_LATEST,
    GITHUB_RELEASES_BASE,
    GITEE_API_LATEST,
    GITEE_RELEASES_BASE,
    build_github_asset_urls,
)


# ---------------------------------------------------------------------------
# 版本比较与数据模型
# ---------------------------------------------------------------------------


def compare_versions(v1: str, v2: str) -> int:
    """比较两个语义化版本号

    Returns:
        1 if v1 > v2, 0 if v1 == v2, -1 if v1 < v2
    """
    parts1 = [int(x) for x in v1.lstrip("v").split(".")]
    parts2 = [int(x) for x in v2.lstrip("v").split(".")]
    for a, b in zip(parts1, parts2):
        if a > b:
            return 1
        if a < b:
            return -1
    if len(parts1) > len(parts2):
        return 1
    if len(parts1) < len(parts2):
        return -1
    return 0


@dataclass
class UpdateInfo:
    """远程版本信息"""

    version: str
    download_url: str
    sha256_url: str
    changelog: str
    file_size: int = 0

    @classmethod
    def from_release(cls, release: dict) -> UpdateInfo:
        return cls(
            version=release["tag_name"].lstrip("v"),
            download_url=_find_asset_url(release, ".zip"),
            sha256_url=_find_asset_url(release, ".sha256"),
            changelog=release.get("body", ""),
            file_size=_find_asset_size(release, ".zip"),
        )


def _find_asset_url(release: dict, suffix: str) -> str:
    for asset in release.get("assets", []):
        name = asset["name"]
        # 主包匹配：排除 .sha256 校验文件，也排除 webengine 资源包
        # （资源包单独命名 VibeOCR-v*-webengine-win64.zip，由 webengine_manager 处理）
        if (
            suffix == ".zip"
            and name.endswith(".zip")
            and ".sha256" not in name
            and "-webengine-" not in name
        ):
            # GitHub 用 browser_download_url，Gitee 用 download_url
            return asset.get("browser_download_url") or asset.get("download_url", "")
        # sha256 校验文件同理排除 webengine 的
        if (
            suffix == ".sha256"
            and name.endswith(".sha256")
            and "-webengine-" not in name
        ):
            return asset.get("browser_download_url") or asset.get("download_url", "")
    return ""


def _find_asset_size(release: dict, suffix: str) -> int:
    for asset in release.get("assets", []):
        name = asset["name"]
        if (
            suffix == ".zip"
            and name.endswith(".zip")
            and ".sha256" not in name
            and "-webengine-" not in name
        ):
            return asset.get("size", 0)
    return 0


def read_local_version(version_json_path: Path) -> str:
    """读取本地 version.json 中的版本号"""
    if not version_json_path.exists():
        return "0.0.0"
    try:
        data = json.loads(version_json_path.read_text(encoding="utf-8"))
        return data.get("version", "0.0.0")
    except (json.JSONDecodeError, OSError):
        return "0.0.0"


# ---------------------------------------------------------------------------
# 远程版本检查
# ---------------------------------------------------------------------------


async def _fetch_release(url: str, headers: dict | None = None) -> dict | None:
    """通用：获取单个 release API 端点的 JSON，失败返回 None。"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers or {})
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        logger.debug(f"release API 请求失败: {url}")
    return None


def _detect_network_type() -> str:
    """读取 NetworkDetector 的网络类型；探测失败默认 international。"""
    try:
        from vibeocr.env_manager import get_project_root
        from vibeocr.network_detector import NetworkDetector

        return NetworkDetector(get_project_root()).network_type
    except Exception:
        return "international"


async def check_for_updates(
    current_version: str,
) -> tuple[UpdateInfo | None, bool]:
    """检查是否有新版本（按网络环境选源：国内 Gitee，海外 GitHub）

    按网络类型确定 API 端点优先级，逐个尝试：
    - domestic：Gitee（匿名可读）→ GitHub（回退）
    - international：GitHub → Gitee（回退）

    Returns:
        (update_info, fetch_ok)：
        - fetch_ok=False 表示所有源均请求失败（上层应提示手动下载）。
        - fetch_ok=True 且 update_info=None 表示已是最新。
    """
    network_type = _detect_network_type()
    if network_type == "domestic":
        sources = [
            (GITEE_API_LATEST, None),
            (GITHUB_API_LATEST, {"Accept": "application/vnd.github+json"}),
        ]
    else:
        sources = [
            (GITHUB_API_LATEST, {"Accept": "application/vnd.github+json"}),
            (GITEE_API_LATEST, None),
        ]

    release: dict | None = None
    for url, headers in sources:
        release = await _fetch_release(url, headers)
        if release is not None:
            break

    if release is None:
        logger.info("无法获取远程版本信息（Gitee/GitHub 均失败）")
        return None, False

    remote = UpdateInfo.from_release(release)

    if compare_versions(remote.version, current_version) <= 0:
        logger.debug(f"当前版本 {current_version} 已是最新")
        return None, True

    if not remote.download_url:
        logger.warning("未找到下载链接")
        return None, True

    return remote, True


# ---------------------------------------------------------------------------
# 下载与校验
# ---------------------------------------------------------------------------


def verify_sha256(file_path: Path, sha256_file: Path) -> bool:
    if not sha256_file.exists():
        logger.warning(f"SHA256 文件不存在: {sha256_file}")
        return False

    expected_hash = sha256_file.read_text(encoding="utf-8").strip().split()[0].lower()
    actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest().lower()

    if actual_hash != expected_hash:
        logger.error(f"SHA256 校验失败: expected={expected_hash}, actual={actual_hash}")
        return False

    return True


async def _download_zip_with_sha(
    client: httpx.AsyncClient,
    zip_url: str,
    zip_path: Path,
    sha256_path: Path,
    progress_callback: Callable[[int, int], None] | None,
) -> bool:
    """从单个 URL 下载 zip + 对应 ``{zip_url}.sha256`` 并校验。

    成功返回 True；失败（状态码非 200 / SHA256 不匹配 / 异常）清理残留返回 False。
    供 download_update 在多源候选间逐个调用。
    """
    sha_url = f"{zip_url}.sha256"
    try:
        # 流式下载 zip（带进度回调）
        async with client.stream("GET", zip_url) as resp:
            if resp.status_code != 200:
                logger.warning(f"zip 下载失败({resp.status_code})：{zip_url}")
                return False
            total = int(resp.headers.get("content-length", 0))
            with open(zip_path, "wb") as f:
                downloaded = 0
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)

        # 下载 sha256
        sha_resp = await client.get(sha_url)
        if sha_resp.status_code != 200:
            logger.warning(f"sha256 下载失败({sha_resp.status_code})：{sha_url}")
            zip_path.unlink(missing_ok=True)
            return False
        sha256_path.write_text(sha_resp.text, encoding="utf-8")

        # 校验
        if not verify_sha256(zip_path, sha256_path):
            logger.warning(f"SHA256 校验失败，换源：{zip_url}")
            zip_path.unlink(missing_ok=True)
            sha256_path.unlink(missing_ok=True)
            return False
        return True
    except Exception as e:
        logger.warning(f"下载异常，换源：{zip_url}: {e}")
        zip_path.unlink(missing_ok=True)
        sha256_path.unlink(missing_ok=True)
        return False


async def download_update(
    update_info: UpdateInfo,
    cache_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path | None:
    """下载更新包（按网络环境多源回退）。

    不再直接用 update_info.download_url（API 返回的原始直链，仅作参考），
    而是由 env_config.build_github_asset_urls 按 tag 重拼候选列表逐个尝试，
    确保 GitHub 来源在国内有 gh 代理加速（gh-proxy / ghproxy）。
    """
    if not update_info.download_url:
        logger.error("下载 URL 为空")
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)

    # 清理残留文件
    for old_file in cache_dir.iterdir():
        if old_file.is_file():
            try:
                old_file.unlink()
            except OSError:
                pass

    zip_filename = f"VibeOCR-v{update_info.version}-win64.zip"
    zip_path = cache_dir / zip_filename
    sha256_path = cache_dir / f"{zip_filename}.sha256"
    network_type = _detect_network_type()
    urls = build_github_asset_urls(
        network_type, update_info.version, zip_filename
    )

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for url in urls:
            logger.info(f"尝试下载源：{url}")
            ok = await _download_zip_with_sha(
                client, url, zip_path, sha256_path, progress_callback
            )
            if ok:
                logger.info(f"更新包下载完成：{zip_path}")
                return zip_path
            logger.warning(f"更新包下载/校验失败，换源：{url}")

    logger.error("所有更新包下载源均失败")
    return None


# ---------------------------------------------------------------------------
# 跳过版本管理
# ---------------------------------------------------------------------------


def load_skip_version(settings_path: Path) -> str:
    if not settings_path.exists():
        return ""
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        return data.get("skip_version", "")
    except (json.JSONDecodeError, OSError):
        return ""


def save_skip_version(version: str, settings_path: Path) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data["skip_version"] = version
    settings_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def should_skip_version(version: str, settings_path: Path) -> bool:
    return load_skip_version(settings_path) == version


# ---------------------------------------------------------------------------
# 更新对话框 UI
# ---------------------------------------------------------------------------

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from vibeocr.ui import theme  # noqa: E402


class UpdateDialog(QDialog):
    """更新提示对话框"""

    def __init__(
        self,
        update_info: UpdateInfo,
        current_version: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("发现新版本")
        self.setMinimumWidth(420)
        self._action: str = "cancel"
        self._setup_ui(update_info, current_version)

    def _setup_ui(self, info: UpdateInfo, current_version: str) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        version_label = QLabel(
            f"当前版本: v{current_version}\n最新版本: v{info.version}"
        )
        version_label.setStyleSheet("font-size: 14px;")
        layout.addWidget(version_label)

        if info.changelog:
            changelog_label = QLabel("更新内容:")
            changelog_label.setStyleSheet("font-weight: bold;")
            layout.addWidget(changelog_label)

            lines = []
            for line in info.changelog.splitlines():
                line = line.strip().lstrip("#*- ")
                if line:
                    lines.append(line)
            changelog_text = "\n".join(f"· {line}" for line in lines[:10])
            cl_label = QLabel(changelog_text)
            cl_label.setWordWrap(True)
            layout.addWidget(cl_label)

        if info.file_size > 0:
            size_mb = info.file_size / (1024 * 1024)
            size_label = QLabel(f"更新包大小: {size_mb:.1f} MB")
            size_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
            layout.addWidget(size_label)

        layout.addSpacing(8)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._update_btn = QPushButton("立即更新")
        self._update_btn.setDefault(True)
        self._update_btn.clicked.connect(self._on_update)
        btn_layout.addWidget(self._update_btn)

        later_btn = QPushButton("稍后提醒")
        later_btn.clicked.connect(self._on_later)
        btn_layout.addWidget(later_btn)

        skip_btn = QPushButton("跳过此版本")
        skip_btn.clicked.connect(self._on_skip)
        btn_layout.addWidget(skip_btn)

        layout.addLayout(btn_layout)

    def _on_update(self) -> None:
        self._action = "update"
        self.accept()

    def _on_later(self) -> None:
        self._action = "cancel"
        self.reject()

    def _on_skip(self) -> None:
        self._action = "skip"
        self.reject()

    @property
    def user_action(self) -> str:
        return self._action


class DownloadProgressDialog(QDialog):
    """下载进度对话框"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("正在下载更新")
        self.setMinimumWidth(360)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        self._status_label = QLabel("正在下载...")
        layout.addWidget(self._status_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        layout.addWidget(self._progress_bar)

    def update_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            pct = int(downloaded / total * 100)
            self._progress_bar.setValue(pct)
            dl_mb = downloaded / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            self._status_label.setText(
                f"正在下载... {dl_mb:.1f} / {total_mb:.1f} MB ({pct}%)"
            )
        else:
            dl_mb = downloaded / (1024 * 1024)
            self._status_label.setText(f"正在下载... {dl_mb:.1f} MB")
            self._progress_bar.setRange(0, 0)


# ---------------------------------------------------------------------------
# UpdateService 编排器
# ---------------------------------------------------------------------------


class UpdateService:
    """应用更新服务编排器"""

    def __init__(self, app_dir: Path) -> None:
        self._app_dir = app_dir
        self._version_json_path = app_dir / "version.json"
        self._updater_path = (
            app_dir / "updater.exe" if os.name == "nt" else app_dir / "updater"
        )
        from vibeocr.services.env_config import (
            get_update_cache_dir,
            get_update_settings_path,
        )

        self._cache_dir = get_update_cache_dir()
        self._settings_path = get_update_settings_path()

    async def check_and_prompt(self, parent: QWidget | None = None) -> None:
        """异步检查更新并提示用户"""
        current = read_local_version(self._version_json_path)
        if current == "0.0.0":
            logger.debug("无法读取本地版本，跳过更新检查")
            return

        update_info, fetch_ok = await check_for_updates(current)

        # 自动检查失败：提示用户去下载页手动下载并覆盖安装（需先退出程序）。
        # 按网络环境给主源链接（国内 Gitee，海外 GitHub）。
        if not fetch_ok:
            network_type = _detect_network_type()
            manual_url = (
                GITEE_RELEASES_BASE
                if network_type == "domestic"
                else GITHUB_RELEASES_BASE
            )
            source_label = "Gitee" if network_type == "domestic" else "GitHub"
            QMessageBox.warning(
                parent,
                "检查更新",
                "自动检查更新失败，可能是网络问题。\n\n"
                f"可前往 {source_label} 手动下载对应版本，"
                "覆盖安装前请先退出本程序：\n"
                f"{manual_url}",
            )
            return

        if update_info is None:
            return

        if should_skip_version(update_info.version, self._settings_path):
            logger.debug(f"用户已跳过版本 {update_info.version}")
            return

        dialog = UpdateDialog(update_info, current, parent)
        dialog.exec()

        if dialog.user_action == "skip":
            save_skip_version(update_info.version, self._settings_path)
            return

        if dialog.user_action == "update":
            await self._do_download_and_update(update_info, parent)

    async def _do_download_and_update(
        self, info: UpdateInfo, parent: QWidget | None
    ) -> None:
        progress_dialog = DownloadProgressDialog(parent)
        progress_dialog.show()

        def progress_cb(downloaded: int, total: int) -> None:
            progress_dialog.update_progress(downloaded, total)

        zip_path = await download_update(
            info, self._cache_dir, progress_callback=progress_cb
        )

        progress_dialog.close()

        if zip_path is None:
            QMessageBox.warning(
                parent, "更新失败", "下载更新包失败，请检查网络连接后重试。"
            )
            return

        reply = QMessageBox.information(
            parent,
            "更新已下载",
            "更新包已下载完成，点击确定重启应用以完成更新。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        self._launch_updater(zip_path)

    def _launch_updater(self, zip_path: Path) -> None:
        if not self._updater_path.exists():
            logger.error(f"updater 不存在: {self._updater_path}")
            return

        cmd = [
            str(self._updater_path),
            "--update",
            str(zip_path),
            "--app-dir",
            str(self._app_dir),
        ]
        detached = 0x8 if os.name == "nt" else 0
        subprocess.Popen(cmd, creationflags=detached)
        sys.exit(0)
