"""应用更新服务

负责检测新版本、下载更新包、显示更新对话框、启动 updater.exe。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

# 发布仓库标识与下载源选择收敛到 env_config（SSOT）。
# 发布渠道：CNB 仅镜像代码；产物唯一源 GitHub。
# 客户端按 NetworkDetector 选源：国内走 gh 代理加速（gh-proxy / ghproxy）→ GitHub 裸连；
# 海外直连 GitHub。CNB OpenAPI 需 token 鉴权，客户端无法匿名访问，不用于更新。
from vibeocr.services.env_config import (  # noqa: E402
    GITHUB_API_LATEST,
    GITHUB_RELEASES_BASE,
    build_asset_url_pairs,
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
            # GitHub Release asset 用 browser_download_url；download_url 兜底防御
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
    """检查是否有新版本（GitHub Release API）

    Returns:
        (update_info, fetch_ok)：
        - fetch_ok=False 表示请求 GitHub 失败（上层应提示手动下载）。
        - fetch_ok=True 且 update_info=None 表示已是最新。
    """
    sources = [
        (GITHUB_API_LATEST, {"Accept": "application/vnd.github+json"}),
    ]

    release: dict | None = None
    for url, headers in sources:
        release = await _fetch_release(url, headers)
        if release is not None:
            break

    if release is None:
        logger.info("无法获取远程版本信息（GitHub 失败）")
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


# 单个下载源的尝试结果。reason 用于汇总失败原因，让上层向用户呈现真实原因，
# 而不是一律甩锅「网络问题」。OK 时 reason == "ok"。
DOWNLOAD_REASON_OK = "ok"
DOWNLOAD_REASON_HTTP_ERROR = "http_error"  # zip 非 200 等
DOWNLOAD_REASON_SHA_MISSING = "sha_missing"  # 校验文件下不到（404/非 200）
DOWNLOAD_REASON_SHA_MISMATCH = "sha_mismatch"  # 校验文件在但哈希对不上
DOWNLOAD_REASON_EXCEPTION = "exception"


class SourceAttempt(NamedTuple):
    ok: bool
    reason: str


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
    sha_url: str,
    zip_path: Path,
    sha256_path: Path,
    progress_callback: Callable[[int, int], None] | None,
) -> SourceAttempt:
    """从单个源下载 zip + 对应 sha256 校验文件并校验。

    ``sha_url`` 由调用方从 release asset 列表精确匹配提供（同源同 tag），
    而非这里盲拼 ``{zip_url}.sha256``——后者可能下到无关/404 内容。

    返回 SourceAttempt；失败时清理残留。供 download_update 在多源候选间逐个调用。
    """
    try:
        # 流式下载 zip（带进度回调）
        async with client.stream("GET", zip_url) as resp:
            if resp.status_code != 200:
                logger.warning(f"zip 下载失败({resp.status_code})：{zip_url}")
                return SourceAttempt(False, DOWNLOAD_REASON_HTTP_ERROR)
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
            return SourceAttempt(False, DOWNLOAD_REASON_SHA_MISSING)
        sha256_path.write_text(sha_resp.text, encoding="utf-8")

        # 校验
        if not verify_sha256(zip_path, sha256_path):
            logger.warning(f"SHA256 校验失败，换源：{zip_url}")
            zip_path.unlink(missing_ok=True)
            sha256_path.unlink(missing_ok=True)
            return SourceAttempt(False, DOWNLOAD_REASON_SHA_MISMATCH)
        return SourceAttempt(True, DOWNLOAD_REASON_OK)
    except Exception as e:
        logger.warning(f"下载异常，换源：{zip_url}: {e}")
        zip_path.unlink(missing_ok=True)
        sha256_path.unlink(missing_ok=True)
        return SourceAttempt(False, DOWNLOAD_REASON_EXCEPTION)


async def download_update(
    update_info: UpdateInfo,
    cache_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    source_switch_callback: Callable[[str, str], None] | None = None,
) -> tuple[Path | None, list[str]]:
    """下载更新包（按网络环境多源回退）。

    不再直接用 update_info.download_url（API 返回的原始直链，仅作参考），
    而是由 env_config.build_asset_url_pairs 按 tag 重拼 (zip, sha256) 配对候选，
    逐个尝试，确保 GitHub 来源在国内有 gh 代理加速（gh-proxy / ghproxy）。
    校验文件 URL 与 zip 同源同 tag 精确匹配，不再盲拼 ``{zip}.sha256``。

    Args:
        source_switch_callback: 某源失败时回调 ``(failed_source_name, reason)``，
            供进度框实时显示「源 X 校验失败，切换源 Y…」。

    Returns:
        (zip_path, fail_reasons)：成功时 fail_reasons 为空列表；全失败时 zip_path
        为 None，fail_reasons 为各源失败原因（供上层分桶提示用户）。
    """
    if not update_info.download_url:
        logger.error("下载 URL 为空")
        return None, [DOWNLOAD_REASON_HTTP_ERROR]

    cache_dir.mkdir(parents=True, exist_ok=True)

    # 清理残留文件
    for old_file in cache_dir.iterdir():
        if old_file.is_file():
            try:
                old_file.unlink()
            except OSError:
                pass

    zip_filename = f"VibeOCR-v{update_info.version}-win64.zip"
    sha_filename = f"{zip_filename}.sha256"
    zip_path = cache_dir / zip_filename
    sha256_path = cache_dir / sha_filename
    network_type = _detect_network_type()
    url_pairs = build_asset_url_pairs(
        network_type, update_info.version, zip_filename, sha_filename
    )

    fail_reasons: list[str] = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for url, sha_url in url_pairs:
            source_name = _source_label(url)
            logger.info(f"尝试下载源：{url}")
            attempt = await _download_zip_with_sha(
                client, url, sha_url, zip_path, sha256_path, progress_callback
            )
            if attempt.ok:
                logger.info(f"更新包下载完成：{zip_path}")
                return zip_path, []
            fail_reasons.append(attempt.reason)
            logger.warning(f"更新包下载/校验失败，换源：{url}")
            if source_switch_callback:
                source_switch_callback(source_name, attempt.reason)

    logger.error("所有更新包下载源均失败")
    return None, fail_reasons


def _source_label(url: str) -> str:
    """从 URL 提取人类可读的源名，用于换源提示文案。

    同时被 env_manager.download_artifact_multi_source（同步多源下载编排器）复用，
    保持同步/异步两套下载链路的源名提示一致。修改此处会同时影响两者。
    """
    for label, marker in (
        ("gh-proxy", "gh-proxy.com"),
        ("ghproxy", "ghproxy.com"),
        ("GitHub", "github.com"),
    ):
        if marker in url:
            return label
    return url


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

        # 换源状态：默认隐藏，仅在下载过程中某源失败时显示
        self._source_status_label = QLabel("")
        self._source_status_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
        self._source_status_label.setWordWrap(True)
        self._source_status_label.setVisible(False)
        layout.addWidget(self._source_status_label)

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

    def set_source_status(self, text: str) -> None:
        """显示换源提示，如『gh-proxy 校验失败，切换 GitHub…』"""
        self._source_status_label.setText(text)
        self._source_status_label.setVisible(bool(text))


# ---------------------------------------------------------------------------
# 失败原因 → 用户文案映射
# ---------------------------------------------------------------------------
# reason → 进度框换源提示里的简短短语
_DOWNLOAD_REASON_HINTS: dict[str, str] = {
    DOWNLOAD_REASON_HTTP_ERROR: "连接失败",
    DOWNLOAD_REASON_SHA_MISSING: "缺少校验文件",
    DOWNLOAD_REASON_SHA_MISMATCH: "校验失败",
    DOWNLOAD_REASON_EXCEPTION: "失败",
}


def _format_failure_message(fail_reasons: list[str]) -> str:
    """把各源失败原因汇总成给用户的分桶文案，按「最坏」原因决定主语义。

    优先级：完整性校验失败 > 缺少校验文件 > 连接/异常。
    这样镜像被篡改/损坏（sha_mismatch）会优先明确告知用户，
    而不是淹没在「网络问题」里——避免装作成功式的敷衍。
    """
    manual_url = GITHUB_RELEASES_BASE
    tail = f"\n\n如持续失败，可前往手动下载（覆盖安装前请先退出本程序）：\n{manual_url}"

    if DOWNLOAD_REASON_SHA_MISMATCH in fail_reasons:
        return (
            "更新包完整性校验失败，下载源文件可能损坏或被篡改。\n"
            "请稍后重试，或手动下载。" + tail
        )
    if DOWNLOAD_REASON_SHA_MISSING in fail_reasons:
        return "服务端缺少 SHA256 校验文件，更新暂不可用。请稍后重试。" + tail
    # 全是连接/异常类
    return "下载更新包失败（无法连接服务器）。请检查网络后重试。" + tail


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
        # 主程序本体路径：握手失败（updater.exe 坏）时，启动 [VibeOCR.exe --self-update]
        # 让主程序自身充当兜底替换器。VibeOCR.exe 是最不可能坏的 exe（它若坏，应用
        # 根本启动不了，谈更新无意义），用它的另一启动模式做兜底等于建立在最稳定基座上。
        self._self_exe_path = (
            app_dir / "VibeOCR.exe" if os.name == "nt" else app_dir / "VibeOCR"
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
        if not fetch_ok:
            manual_url = GITHUB_RELEASES_BASE
            QMessageBox.warning(
                parent,
                "检查更新",
                "自动检查更新失败，可能是网络问题。\n\n"
                "可前往 GitHub 手动下载对应版本，"
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
        # 重试上限，防用户连点导致无限下载循环；用户可在失败框主动取消。
        max_attempts = 3
        for _attempt in range(1, max_attempts + 1):
            progress_dialog = DownloadProgressDialog(parent)
            progress_dialog.show()

            # 把 progress_dialog 作为默认参数显式绑定，避免闭包按引用捕获循环变量
            # （B023：循环内定义的闭包共享最后一次迭代的 progress_dialog）。
            def progress_cb(
                downloaded: int, total: int, dialog: DownloadProgressDialog = progress_dialog
            ) -> None:
                dialog.update_progress(downloaded, total)

            def on_source_switch(
                source_name: str,
                reason: str,
                dialog: DownloadProgressDialog = progress_dialog,
            ) -> None:
                # reason 映射成用户能理解的短语
                hint = _DOWNLOAD_REASON_HINTS.get(reason, "失败")
                dialog.set_source_status(
                    f"{source_name} {hint}，正在切换备用源…"
                )

            zip_path, fail_reasons = await download_update(
                info,
                self._cache_dir,
                progress_callback=progress_cb,
                source_switch_callback=on_source_switch,
            )

            progress_dialog.close()

            if zip_path is not None:
                break

            # 全失败：按真实原因分桶，给出重试 / 取消
            msg = _format_failure_message(fail_reasons)
            retry_btn = QMessageBox.StandardButton.Retry
            cancel_btn = QMessageBox.StandardButton.Cancel
            reply = QMessageBox.warning(
                parent, "更新失败", msg, retry_btn | cancel_btn
            )
            if reply != retry_btn:
                return
        else:
            # 重试用尽仍未成功
            return

        reply = QMessageBox.information(
            parent,
            "更新已下载",
            "更新包已下载完成，点击确定重启应用以完成更新。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        # 先尝试 updater.exe（首选替换器），握手确认它「活着」再退出。
        # 握手三态：
        #   ready/timeout → 替换器确认在工作（ready=快，timeout=慢但仍在跑）→ 退出，释放文件锁。
        #   crashed       → updater 确认坏了 → 启动 [VibeOCR.exe --self-update] 兜底。
        # 关键：timeout 不能误判为坏，否则并发启 self-update 会与仍在工作的 updater
        # 抢着替换 app_dir，导致文件损坏。
        result = await self._launch_updater(zip_path)
        if result in ("ready", "timeout"):
            self._force_quit()

        # updater 确认崩溃（crashed）→ 主程序自身充当兜底替换器。
        logger.warning("updater.exe 握手失败（crashed），改用主程序自带更新模式（--self-update）兜底")
        result = await self._launch_self_update(zip_path)
        if result in ("ready", "timeout"):
            self._force_quit()

        # 极端罕见：连主程序自身都起不来（VibeOCR.exe 若坏，应用本就无法启动）。
        # 不退出主程序，明确告知用户手动重装——避免「应用关了什么都不发生」的困惑。
        manual_url = GITHUB_RELEASES_BASE
        QMessageBox.critical(
            parent,
            "更新失败",
            "更新助手与自带更新器均无法启动。\n\n"
            "请手动下载最新版，覆盖安装前请先退出本程序：\n"
            f"{manual_url}",
        )

    def _force_quit(self) -> None:
        """强制退出主程序，把 VibeOCR.exe 及 _internal/*.dll 的文件锁释放给替换器。

        不能用 ``sys.exit``：本方法运行于 qasync 调度的 asyncio Task 内
        （``_do_download_and_update`` 由 ``asyncio.ensure_future`` 挂载）。``SystemExit``
        被 Task 当成普通异常吞进 ``Task.exception()``，进程不会终止——日志中
        「删除 VibeOCR.exe 失败: WinError 5」的根因正是主程序没退出、文件锁未释放，
        替换器在锁定状态下替换必然失败且回滚也失败。

        用 ``os._exit(0)`` 跳过解释器常规关闭流程（与 main.launch_application 末尾的
        ``os._exit(0)`` 一致），确保进程立即终止、句柄立刻释放。Qt 对象不析构无妨：
        替换器会覆盖整个应用目录，旧实例的资源回收没有意义。
        """
        logger.info("握手成功，主程序退出以释放文件锁，交给替换器完成更新")
        # 先尝试关闭事件循环，给 Qt/asyncio 一个快速收尾机会；再 os._exit 兜底。
        try:
            loop = asyncio.get_event_loop()
            loop.stop()
        except Exception:
            pass
        # 短暂让出 CPU，确保子进程（替换器）已真正接管；随后硬退出。
        time.sleep(0.1)
        os._exit(0)

    # 握手超时（秒）：替换器需在此窗口内写出就绪信号文件。
    # onefile 解压 + Python 初始化在慢机器上可能数秒，给 15s 余量。
    _HANDSHAKE_TIMEOUT = 15.0
    _HANDSHAKE_POLL_INTERVAL = 0.2

    async def _launch_updater(self, zip_path: Path) -> str:
        """启动 updater.exe 并握手确认它「活着」。返回握手三态（见 _handshake_launch）。

        替代旧的 fire-and-forget + 立即 sys.exit：旧设计下 updater 崩溃时主程序已退出、
        用户看到「应用关了什么都没发生」且无任何 UI 反馈。握手协议下，updater 启动后
        会第一时间写就绪信号文件（data/cache/update/updater.ready），主程序端轮询该
        文件 + 进程存活，确认替换器确实在干活后才退出；确认崩溃（crashed）才走兜底。
        """
        if not self._updater_path.exists():
            logger.error(f"updater 不存在: {self._updater_path}")
            return False

        return await self._handshake_launch(
            exe_path=self._updater_path,
            extra_args=["--update", str(zip_path), "--app-dir", str(self._app_dir)],
            ready_filename="updater.ready",
            label="updater.exe",
        )

    async def _launch_self_update(self, zip_path: Path) -> str:
        """启动 [VibeOCR.exe --self-update] 兜底替换器并握手。返回握手三态。

        仅在 updater.exe 确认崩溃（crashed）时调用。复用同一握手协议，就绪信号文件用
        self_update.ready 区分。注意：此处启动的是主程序的「另一个实例」，启动后
        本主程序实例会 sys.exit，把 VibeOCR.exe 文件锁释放给兜底实例去覆盖。
        """
        if not self._self_exe_path.exists():
            logger.error(f"主程序不存在，无法走 self-update 兜底: {self._self_exe_path}")
            return False

        return await self._handshake_launch(
            exe_path=self._self_exe_path,
            extra_args=["--self-update", str(zip_path), "--app-dir", str(self._app_dir)],
            ready_filename="self_update.ready",
            label="VibeOCR.exe --self-update",
        )

    async def _handshake_launch(
        self,
        exe_path: Path,
        extra_args: list[str],
        ready_filename: str,
        label: str,
    ) -> str:
        """通用握手启动：清理旧 ready → 启动进程 → 轮询 ready 文件 + 进程存活。

        返回三态（避免「超时但进程仍在跑」被误判为崩溃、进而误启 self-update 与
        正常 updater 并发替换导致文件损坏）：

        - ``"ready"``：就绪信号文件出现 → 替换器确认活着，调用方 sys.exit 放心。
        - ``"crashed"``：进程已退出且无就绪信号 → 替换器确认坏了，调用方走兜底。
        - ``"timeout"``：超时但进程仍在跑 → 替换器可能只是慢（慢机/杀软扫描），
          不能判定为坏。调用方应继续等待（视为 ready，sys.exit），**绝不**启 self-update。

        Args:
            exe_path: 要启动的替换器 exe（updater.exe 或 VibeOCR.exe）。
            extra_args: 传给 exe 的参数（不含 exe 本身）。
            ready_filename: 替换器写出的就绪信号文件名（updater.ready / self_update.ready）。
            label: 日志/UI 中的人类可读标签。
        """
        ready_path = self._cache_dir / ready_filename
        try:
            ready_path.unlink(missing_ok=True)  # 清理上次残留，避免读到旧信号误判
        except OSError:
            pass

        detached = 0x8 if os.name == "nt" else 0
        cmd = [str(exe_path), *extra_args]
        logger.info(f"启动 {label}：{cmd}")
        try:
            proc = subprocess.Popen(cmd, creationflags=detached)
        except OSError as e:
            logger.error(f"启动 {label} 失败: {e}")
            return "crashed"

        # 轮询放后台线程，主事件循环不阻塞。
        return await asyncio.to_thread(
            self._poll_ready, proc, ready_path, label, self._HANDSHAKE_TIMEOUT
        )

    def _poll_ready(
        self, proc: subprocess.Popen, ready_path: Path, label: str, timeout: float
    ) -> str:
        """阻塞轮询 ready 文件 + 进程存活，返回三态（见 _handshake_launch 文档）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ready_path.exists():
                logger.info(f"{label} 握手成功（就绪信号已收到）")
                return "ready"
            if proc.poll() is not None:
                # 进程已退出且无就绪信号：替换器崩溃/起不来，确认坏了。
                logger.warning(
                    f"{label} 启动后立即退出（退出码 {proc.returncode}），确认握手失败"
                )
                return "crashed"
            time.sleep(self._HANDSHAKE_POLL_INTERVAL)
        # 超时但进程仍在跑：替换器可能只是慢，不能误判为坏（否则并发启 self-update
        # 会与这个仍在工作的 updater 抢着替换文件，导致 app_dir 损坏）。
        # 视为「正在工作」，让主程序 sys.exit 把现场交给它。
        logger.warning(
            f"{label} 握手超时（{timeout}s 未收到就绪信号，但进程仍在运行），"
            f"视为工作中（不触发兜底，避免并发替换）"
        )
        return "timeout"
