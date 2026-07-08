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

    from PySide6.QtGui import QCloseEvent
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
        # 主包匹配：排除 .sha256 校验文件，也排除历史 webengine 资源包
        # （旧版曾单独发布 VibeOCR-v*-webengine-win64.zip，现已内置主包；
        # 此排除守卫保留作历史 release asset 的防御）
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


async def _probe_github_reachable(timeout: float = 3.0) -> bool:
    """快速探测 GitHub API（api.github.com）是否可达。

    与 ``NetworkDetector`` 的国内/海外判定互补：那个判断用户所在网络环境（中国 vs
    海外），本函数判断「此刻能不能直连 GitHub」。典型场景：海外或代理环境下
    ``NetworkDetector`` 判 international（应直连 GitHub），但 GitHub 实际被墙/不稳定，
    此时下载应改走国内代理（gh-proxy / ghproxy）。仅在 international 分支调用：
    domestic 分支本就代理优先，无需再探测。

    用 HEAD 请求 + 3s 超时，失败（DNS/连接/SSL/超时/5xx）一律视为不可达。
    4xx（如 403 限流）仍视为可达——说明能连上 GitHub，只是 rate limited。
    """
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.head(
                GITHUB_API_LATEST,
                headers={"Accept": "application/vnd.github+json"},
            )
            return resp.status_code < 500
    except Exception as e:
        logger.debug(f"GitHub 可达性探测失败（视为不可达）: {e}")
        return False


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
# 用户主动取消（点对话框「取消」按钮或标题栏 X）。与上面失败原因并列，
# 让 download_update 的 (zip_path, fail_reasons) 返回结构在取消路径上仍统一；
# 上层 _do_download_and_update 据此短路退出整个更新流程，不弹重试框。
DOWNLOAD_REASON_CANCELLED = "cancelled"


class SourceAttempt(NamedTuple):
    ok: bool
    reason: str


def verify_sha256(file_path: Path, sha256_file: Path) -> bool:
    if not sha256_file.exists():
        logger.warning(f"SHA256 文件不存在: {sha256_file}")
        return False

    expected_hash = sha256_file.read_text(encoding="utf-8").strip().split()[0].lower()
    # 分块流式计算（与 update_replacer.verify_sha256 一致）：避免一次性把 ~227MB+ 的
    # zip 读进内存造成瞬时峰值。此处虽已被 asyncio.to_thread 搬到线程池、不冻结 UI，
    # 但内存峰值仍会与并发任务叠加。8MB 块恒定峰值，速度持平或略快。
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 23), b""):  # 8MB
            h.update(chunk)
    actual_hash = h.hexdigest().lower()

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
    cancel_event: asyncio.Event | None = None,
) -> SourceAttempt:
    """从单个源下载 zip + 对应 sha256 校验文件并校验。

    ``sha_url`` 由调用方从 release asset 列表精确匹配提供（同源同 tag），
    而非这里盲拼 ``{zip_url}.sha256``——后者可能下到无关/404 内容。

    ``cancel_event``：可选的协作式取消令牌。每写完一块就检查；被 set 时
    跳出流式循环，清理已落盘的 zip，返回 ``SourceAttempt(False, cancelled)``。
    这是下载链路里最细粒度的取消点——大文件分块下载时能在百 KB 级别响应取消。

    返回 SourceAttempt；失败时清理残留。供 download_update 在多源候选间逐个调用。
    """
    try:
        # 流式下载 zip（带进度回调）
        cancelled = False
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
                    # 每块写入后检查取消：用户点「取消」后无需等整包下完
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        logger.info(f"用户取消下载，清理残留：{zip_url}")
                        break
        if cancelled:
            zip_path.unlink(missing_ok=True)
            return SourceAttempt(False, DOWNLOAD_REASON_CANCELLED)

        # 下载 sha256
        sha_resp = await client.get(sha_url)
        if sha_resp.status_code != 200:
            logger.warning(f"sha256 下载失败({sha_resp.status_code})：{sha_url}")
            zip_path.unlink(missing_ok=True)
            return SourceAttempt(False, DOWNLOAD_REASON_SHA_MISSING)
        sha256_path.write_text(sha_resp.text, encoding="utf-8")

        # 校验。verify_sha256 同步读整个 zip（~50MB+）入内存算哈希，在 qasync 事件
        # 循环里会冻结 UI 与取消响应（弱网/慢盘下尤甚，曾表现为「下载完成后无响应」）。
        # 用 asyncio.to_thread 把重 CPU/IO 搬到默认线程池，事件循环继续转。
        if not await asyncio.to_thread(verify_sha256, zip_path, sha256_path):
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
    cancel_event: asyncio.Event | None = None,
) -> tuple[Path | None, list[str]]:
    """下载更新包（按网络环境多源回退）。

    不再直接用 update_info.download_url（API 返回的原始直链，仅作参考），
    而是由 env_config.build_asset_url_pairs 按 tag 重拼 (zip, sha256) 配对候选，
    逐个尝试，确保 GitHub 来源在国内有 gh 代理加速（gh-proxy / ghproxy）。
    校验文件 URL 与 zip 同源同 tag 精确匹配，不再盲拼 ``{zip}.sha256``。

    Args:
        source_switch_callback: 某源失败时回调 ``(failed_source_name, reason)``，
            供进度框实时显示「源 X 校验失败，切换源 Y…」。
        cancel_event: 可选的协作式取消令牌。在「清理残留」前、每次换源前
            检查；被 set 时立即返回 ``(None, [cancelled])``，不再尝试后续源。
            流式块级取消由 ``_download_zip_with_sha`` 在内部检查。

    Returns:
        (zip_path, fail_reasons)：成功时 fail_reasons 为空列表；全失败时 zip_path
        为 None，fail_reasons 为各源失败原因（供上层分桶提示用户）。
    """
    if not update_info.download_url:
        logger.error("下载 URL 为空")
        return None, [DOWNLOAD_REASON_HTTP_ERROR]

    # 进入函数即检查取消（用户在进度框一弹出就点取消）
    if cancel_event is not None and cancel_event.is_set():
        logger.info("下载开始前用户已取消")
        return None, [DOWNLOAD_REASON_CANCELLED]

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
    # 海外环境（NetworkDetector 判 international）默认直连 GitHub。但 GitHub 实际
    # 不可达时（被墙/不稳定），直连只会在所有源失败后才提示「网络问题」，体验差且
    # 浪费一次完整下载。此处主动探测 GitHub：不可达则降级走国内代理源序。
    # domestic 分支本就代理优先，无需探测（避免每次更新都多打一个请求）。
    if network_type == "international" and not await _probe_github_reachable():
        logger.info("GitHub 直连不可达，改用国内代理源序下载")
        network_type = "domestic"
    url_pairs = build_asset_url_pairs(
        network_type, update_info.version, zip_filename, sha_filename
    )

    fail_reasons: list[str] = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for url, sha_url in url_pairs:
            # 换源间隙检查取消（前一源失败后、下一源开始前）
            if cancel_event is not None and cancel_event.is_set():
                logger.info("换源间隙用户已取消下载")
                break
            source_name = _source_label(url)
            logger.info(f"尝试下载源：{url}")
            attempt = await _download_zip_with_sha(
                client,
                url,
                sha_url,
                zip_path,
                sha256_path,
                progress_callback,
                cancel_event=cancel_event,
            )
            if attempt.ok:
                logger.info(f"更新包下载完成：{zip_path}")
                return zip_path, []
            fail_reasons.append(attempt.reason)
            # 用户取消：不再换源，直接跳出（reason 已是 cancelled）
            if attempt.reason == DOWNLOAD_REASON_CANCELLED:
                break
            logger.warning(f"更新包下载/校验失败，换源：{url}")
            if source_switch_callback:
                source_switch_callback(source_name, attempt.reason)

    logger.error("所有更新包下载源均失败（或用户已取消）")
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

from PySide6.QtCore import Qt, Signal  # noqa: E402
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
    """下载进度对话框。

    支持两种中断方式，均走同一条协作式取消路径（emit ``cancel_requested``）：
    - 底部「取消」按钮；
    - 标题栏「关闭 X」（恢复可见，行为等同取消）。
    另支持标题栏「最小化」按钮，让用户在长下载过程中切回主界面操作。
    """

    # 用户请求取消（按钮或 X）。编排器 _do_download_and_update 连接此信号，
    # 在槽里 set 一个 asyncio.Event，下载协程检查后中止。对话框自身不直接
    # 关闭——必须等编排器 await download_update 返回后再 close()，
    # 避免对话框在下载协程仍引用它时被 Qt 提前销毁。
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("正在下载更新")
        self.setMinimumWidth(360)
        # 默认 QDialog 标题栏只有关闭按钮，且原代码用 & ~ 把关闭也去掉了——
        # 下载一旦开始就无法中止。这里恢复关闭 X 并加上最小化按钮：
        #   - 最小化：长下载时切回主界面，下载继续在后台跑；
        #   - 关闭 X：等同「取消」（见 closeEvent）。
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )

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

        # 取消按钮：右对齐，参照 install_dialog._on_cancel_clicked 的视觉反馈
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_layout.addWidget(self._cancel_btn)
        layout.addLayout(btn_layout)

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

    def _on_cancel_clicked(self) -> None:
        """取消按钮 / 关闭 X 的统一入口：发信号 + 即时视觉反馈。

        幂等：重复点击不会再 emit（按钮已 disabled），避免编排器端多次 set。
        """
        if not self._cancel_btn.isEnabled():
            return
        self.mark_cancelling()
        self.cancel_requested.emit()

    def mark_cancelling(self) -> None:
        """进入「正在取消」状态：禁用按钮、改文案，等编排器 await 返回后真正关闭。"""
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("正在取消...")
        self._status_label.setText("正在取消下载...")

    def mark_finished(self) -> None:
        """下载结束（成功/失败/已取消）后禁用取消入口，防止误触。"""
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setVisible(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        """拦截标题栏「关闭 X」：不当场关闭，转为协作式取消信号。

        直接 accept()/忽略事件都不对——前者会在下载协程仍引用对话框时销毁它，
        后者让 X 失效。正确做法：忽略关闭事件 + emit cancel_requested，让编排器
        在 await 返回后主动 close()。
        """
        event.ignore()
        self._on_cancel_clicked()


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

    # check_and_prompt 的进程级互斥锁（类属性，跨实例共享）。
    #
    # 背景与根因：两个调用点各自 ensure_future 起 check_and_prompt——
    #   1) main._check_update：frozen 态 loop.call_later(5) 启动自动检查；
    #   2) AboutTab._on_check_update：用户点「检查更新」按钮。
    # 二者并发时，第二个任务会在第一个任务阻塞于 QMessageBox.warning /
    # dialog.exec()（qasync 嵌套事件循环）期间被唤醒，触发 CPython
    # asyncio.tasks._enter_task 的重入保护：
    #   RuntimeError: Cannot enter into task <Task-1> while another task
    #   <Task-2> is being executed.
    #
    # 用类级（而非实例级）锁：两处调用点各 new 出独立 UpdateService 实例，
    # 实例锁无法互斥；必须进程级共享。惰性创建：asyncio.Lock() 在构造时
    # 绑定当前事件循环，模块 import 阶段尚无运行循环，故延后到首次使用。
    _check_lock: asyncio.Lock | None = None

    @classmethod
    def _get_check_lock(cls) -> asyncio.Lock:
        """惰性创建进程级互斥锁。绑定首次调用时的运行事件循环（qasync）。"""
        if cls._check_lock is None:
            cls._check_lock = asyncio.Lock()
        return cls._check_lock

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
        """异步检查更新并提示用户

        临界区（网络拉取 + 模态对话框）受类级 ``_check_lock`` 保护，串行化所有
        并发调用。否则启动自动检查与关于页按钮检查并发时，第二个任务会在
        第一个阻塞于 ``QMessageBox`` / ``dialog.exec()`` 的 qasync 嵌套事件循环
        期间被唤醒，触发 ``RuntimeError: Cannot enter into task ...``（asyncio
        ``_enter_task`` 重入保护）。
        """
        async with self._get_check_lock():
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
        # 进程内取消令牌：对话框 cancel_requested 信号 → set 此 event →
        # download_update / _download_zip_with_sha 检查后中止下载。
        # 用 asyncio.Event 而非 threading.Event：下载是 async 协程，Event 在
        # 同一事件循环内 set/is_set 无需锁，且 is_set() 在协程 await 点自然可见。
        cancel_event = asyncio.Event()
        for _attempt in range(1, max_attempts + 1):
            # 重试入口检查取消（上一次重试框用户可能已点取消并触发 set）
            if cancel_event.is_set():
                return
            progress_dialog = DownloadProgressDialog(parent)
            # 信号 → 令牌桥；同时给即时视觉反馈（按钮置灰 + 文案改「正在取消...」）
            progress_dialog.cancel_requested.connect(cancel_event.set)
            progress_dialog.cancel_requested.connect(progress_dialog.mark_cancelling)
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
                cancel_event=cancel_event,
            )

            progress_dialog.mark_finished()
            progress_dialog.close()

            # 用户主动取消：直接退出整个更新流程，不弹重试框、不弹任何后续消息。
            # 判定双保险：cancel_event.is_set()（信号触发）或 fail_reasons 含 cancelled
            # （download_update 因 event 跳出多源循环返回的语义原因）。
            if cancel_event.is_set() or (
                zip_path is None and DOWNLOAD_REASON_CANCELLED in fail_reasons
            ):
                logger.info("用户取消下载，退出更新流程")
                return

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

    def _verify_zip_integrity(self, zip_path: Path) -> bool:
        """校验 zip 完整性（testzip），确保能安全读出 updater 条目。

        旧主程序作为"递送员"，只做这个通用校验（不违反黄金法则——testzip 是格式
        无关的完整性检查）。真正的 SHA256 完整性校验留给新 updater（新代码校验
        自己要部署的包）。

        Args:
            zip_path: 已下载的更新包 zip。

        Returns:
            True 表示 zip 结构完整可读；False 表示损坏/不存在。
        """
        if not zip_path.exists():
            logger.error(f"zip 文件不存在: {zip_path}")
            return False
        try:
            import zipfile

            with zipfile.ZipFile(zip_path, "r") as zf:
                bad = zf.testzip()
                if bad is not None:
                    logger.error(f"zip 损坏，损坏条目: {bad}")
                    return False
            return True
        except zipfile.BadZipFile:
            logger.error(f"无效 zip 文件: {zip_path}")
            return False

    def _extract_updater_from_zip(self, zip_path: Path) -> Path:
        """从 zip 按 arcname 抽取新 updater 到暂存目录。

        新架构（黄金法则）核心：旧主程序不解压整包、不解释新格式，只把新版 updater
        从 zip 里取出来放到 ``data/cache/update/updater.exe``，由它（新代码）完成部署。

        zip 内 updater 在 ``VibeOCR/updater.exe``（与 VibeOCR.exe 同层，一层 VibeOCR/ 根目录）。
        只抽这一个条目，不解压整包（避免与 updater 端 extract 重复 I/O）。

        Args:
            zip_path: 已下载并通过 testzip 的更新包 zip。

        Returns:
            暂存 updater 路径 ``self._cache_dir / "updater.exe"``。

        Raises:
            RuntimeError: zip 内找不到 ``VibeOCR/updater.exe`` 条目。
        """
        import zipfile

        arcname = "VibeOCR/updater.exe"
        dest = self._cache_dir / "updater.exe"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # 先确认条目存在（namelist 比 getinfo 容错好）
                if arcname not in zf.namelist():
                    raise RuntimeError(
                        f"更新包内未找到 {arcname}，无法提取更新器。请手动下载最新版重装。"
                    )
                # zf.read 一次性读入内存——updater.exe 是 onefile 约 8-12MB，可接受。
                # 不用 extract(member)（会按 arcname 写到 cache_dir/VibeOCR/updater.exe），
                # 而是直接写到目标路径 cache_dir/updater.exe（扁平化）。
                dest.write_bytes(zf.read(arcname))
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"提取更新器失败: {e}") from e
        logger.info(f"已提取新 updater 到暂存目录: {dest}")
        return dest

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
            # 与 _handshake_launch 三态语义统一：替换器确认起不来 → crashed，
            # 调用方据此走 self-update 兜底 / 弹窗路径。不要 return False
            # （布尔）：调用方只识别 "ready"/"timeout"，布尔会绕过三态判断。
            return "crashed"

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
            # 同 _launch_updater：返回三态字符串 crashed 而非布尔，保持类型一致。
            return "crashed"

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
