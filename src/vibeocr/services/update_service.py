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

# GitHub 仓库信息（CNB 的 OpenAPI 需 token 鉴权，客户端无法匿名访问，
# 故更新检查与下载统一走 GitHub；CNB 仅作 CI 镜像与产物托管目标）。
_GITHUB_OWNER = "FelixJI"
_GITHUB_REPO = "VibeOCR"
_CNB_RELEASES_URL = "https://cnb.cool/feljii/VibeOCR/-/releases"

_GITHUB_API_URL = (
    f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}/releases/latest"
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
    def from_gitee_release(cls, release: dict) -> UpdateInfo:
        return cls(
            version=release["tag_name"].lstrip("v"),
            download_url=_find_asset_url(release, ".zip"),
            sha256_url=_find_asset_url(release, ".sha256"),
            changelog=release.get("body", ""),
            file_size=_find_asset_size(release, ".zip"),
        )

    @classmethod
    def from_github_release(cls, release: dict) -> UpdateInfo:
        return cls.from_gitee_release(release)


def _find_asset_url(release: dict, suffix: str) -> str:
    for asset in release.get("assets", []):
        name = asset["name"]
        if suffix == ".zip" and name.endswith(".zip") and ".sha256" not in name:
            return asset["browser_download_url"]
        if suffix == ".sha256" and name.endswith(".sha256"):
            return asset["browser_download_url"]
    return ""


def _find_asset_size(release: dict, suffix: str) -> int:
    for asset in release.get("assets", []):
        name = asset["name"]
        if suffix == ".zip" and name.endswith(".zip") and ".sha256" not in name:
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


async def _fetch_gitee_release() -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(_GITEE_API_URL)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        logger.debug("Gitee API 请求失败")
    return None


async def _fetch_github_release() -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                _GITHUB_API_URL,
                headers={"Accept": "application/vnd.github+json"},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        logger.debug("GitHub API 请求失败")
    return None


async def check_for_updates(
    current_version: str,
    prefer_gitee: bool = True,
) -> UpdateInfo | None:
    """检查是否有新版本"""
    release = None

    if prefer_gitee:
        release = await _fetch_gitee_release()
        if release is None:
            release = await _fetch_github_release()
    else:
        release = await _fetch_github_release()
        if release is None:
            release = await _fetch_gitee_release()

    if release is None:
        logger.info("无法获取远程版本信息")
        return None

    remote = UpdateInfo.from_gitee_release(release)

    if compare_versions(remote.version, current_version) <= 0:
        logger.debug(f"当前版本 {current_version} 已是最新")
        return None

    if not remote.download_url:
        logger.warning("未找到下载链接")
        return None

    return remote


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


async def download_update(
    update_info: UpdateInfo,
    cache_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path | None:
    """下载更新包"""
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

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # 下载 zip
            async with client.stream("GET", update_info.download_url) as resp:
                if resp.status_code != 200:
                    logger.error(f"下载失败，状态码: {resp.status_code}")
                    return None
                total = int(resp.headers.get("content-length", 0))
                with open(zip_path, "wb") as f:
                    downloaded = 0
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total)

            # 下载 sha256
            sha256_path = cache_dir / f"{zip_filename}.sha256"
            if update_info.sha256_url:
                sha_resp = await client.get(update_info.sha256_url)
                if sha_resp.status_code == 200:
                    sha256_path.write_text(sha_resp.text, encoding="utf-8")

                    if not verify_sha256(zip_path, sha256_path):
                        logger.error("下载文件 SHA256 校验失败")
                        zip_path.unlink(missing_ok=True)
                        sha256_path.unlink(missing_ok=True)
                        return None

        logger.info(f"更新包下载完成: {zip_path}")
        return zip_path

    except Exception as e:
        logger.error(f"下载更新失败: {e}")
        zip_path.unlink(missing_ok=True)
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

        try:
            from vibeocr.network_detector import NetworkDetector

            nd = NetworkDetector(self._app_dir)
            prefer_gitee = nd.network_type == "domestic"
        except Exception:
            prefer_gitee = True

        update_info = await check_for_updates(current, prefer_gitee=prefer_gitee)
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
