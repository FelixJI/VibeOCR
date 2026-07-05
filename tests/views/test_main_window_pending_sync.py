"""MainWindow 启动时消费 pending_sync.json 的逻辑测试

不构造完整 MainWindow（UI 重），而是把 _check_pending_sync / _on_sync_finished /
_delete_pending_sync 作为未绑定方法在最小 stub 上调用。

updater 更新时写 pending_sync.json（依赖版本变更标记），新版 VibeOCR 启动时
通过 _check_pending_sync 检测并用 install_embedded_dependencies 升级 python/，
成功后删除标记。这避免 updater 用裸 pip 走 PyPI 把 paddle/torch 装成 CPU 版。
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from vibeocr.views.main_window import MainWindow


class _StubWindow:
    """MainWindow 的最小 stub，提供 pending_sync 相关方法需要的属性"""

    def __init__(self) -> None:
        self._project_root = Path.cwd()
        self._ocr_ready = False
        self._statusbar = MagicMock()
        self._dependency_manager = MagicMock()
        # P4：_check_pending_sync 缓存 removed 供 _on_sync_finished 清理
        self._pending_removed: list[str] = []

    # 借用 MainWindow 的未绑定方法
    _check_pending_sync = MainWindow._check_pending_sync
    _on_sync_finished = MainWindow._on_sync_finished
    _delete_pending_sync = MainWindow._delete_pending_sync
    _refresh_settings_env_state = MainWindow._refresh_settings_env_state
    _increment_sync_attempts = MainWindow._increment_sync_attempts
    _cleanup_removed_deps = MainWindow._cleanup_removed_deps


def _write_pending(path: Path, dep_versions: dict, version: str = "0.2.0") -> None:
    """写一个 pending_sync.json 到给定路径"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"version": version, "dep_versions": dep_versions, "written_at": "now"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# _check_pending_sync：检测标记
# ---------------------------------------------------------------------------


def test_returns_false_when_no_marker(tmp_path):
    """无 pending_sync.json 时返回 False（走常规依赖检查）"""
    stub = _StubWindow()
    with patch(
        "vibeocr.services.env_config.get_pending_sync_path",
        return_value=tmp_path / "pending_sync.json",  # 不存在
    ):
        result = stub._check_pending_sync()
    assert result is False


def test_returns_false_and_cleans_empty_marker(tmp_path):
    """标记存在但 dep_versions 为空时，清理标记并返回 False"""
    pending = tmp_path / "pending_sync.json"
    _write_pending(pending, {})
    stub = _StubWindow()
    with patch(
        "vibeocr.services.env_config.get_pending_sync_path", return_value=pending
    ):
        result = stub._check_pending_sync()
    assert result is False
    assert not pending.exists(), "空标记应被删除"


def test_returns_false_and_cleans_corrupt_marker(tmp_path):
    """标记文件损坏（非法 JSON）时，清理标记并返回 False"""
    pending = tmp_path / "pending_sync.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text("{ not valid json", encoding="utf-8")
    stub = _StubWindow()
    with patch(
        "vibeocr.services.env_config.get_pending_sync_path", return_value=pending
    ):
        result = stub._check_pending_sync()
    assert result is False
    assert not pending.exists(), "损坏标记应被删除"


def test_shows_dialog_when_marker_present(tmp_path):
    """标记存在且 dep_versions 非空时，应弹 InstallDialog 并返回 True"""
    pending = tmp_path / "pending_sync.json"
    _write_pending(pending, {"paddlepaddle": "3.3.1"})
    stub = _StubWindow()

    mock_dialog = MagicMock()
    mock_dialog.exec.return_value = 0  # 不实际阻塞
    with (
        patch(
            "vibeocr.services.env_config.get_pending_sync_path",
            return_value=pending,
        ),
        patch("vibeocr.widgets.install_dialog.InstallDialog", return_value=mock_dialog),
    ):
        result = stub._check_pending_sync()

    assert result is True
    mock_dialog.setWindowTitle.assert_called_once_with("同步 OCR 依赖更新")
    mock_dialog.exec.assert_called_once()
    mock_dialog.finished.connect.assert_called_once()


# ---------------------------------------------------------------------------
# _on_sync_finished：成功删除标记并重检，失败保留标记
# ---------------------------------------------------------------------------


def test_sync_success_deletes_marker_and_rechecks(tmp_path):
    """同步成功（result=1）时应删除标记并重新触发依赖检查"""
    pending = tmp_path / "pending_sync.json"
    _write_pending(pending, {"paddlepaddle": "3.3.1"})
    stub = _StubWindow()

    with (
        patch(
            "vibeocr.services.env_config.get_pending_sync_path", return_value=pending
        ),
        patch("vibeocr.env_manager._dep_specs_cache", {"old": "x"}, create=True),
    ):
        stub._on_sync_finished(1)

    assert not pending.exists(), "成功后标记应删除"
    stub._dependency_manager.reset.assert_called_once()
    stub._dependency_manager.check_dependencies.assert_called_once()


def test_sync_failure_preserves_marker(tmp_path):
    """同步失败（result=0）时应保留标记供下次启动重试"""
    pending = tmp_path / "pending_sync.json"
    _write_pending(pending, {"paddlepaddle": "3.3.1"})
    stub = _StubWindow()

    with patch(
        "vibeocr.services.env_config.get_pending_sync_path", return_value=pending
    ):
        stub._on_sync_finished(0)

    assert pending.exists(), "失败时标记应保留"
    assert stub._ocr_ready is False
    stub._dependency_manager.reset.assert_not_called()


def test_sync_failure_increments_attempts(tmp_path):
    """P2：同步失败时应递增 pending_sync.json 的 attempts 字段。"""
    pending = tmp_path / "pending_sync.json"
    _write_pending(pending, {"paddlepaddle": "3.3.1"})
    # 初始 attempts 为 1（模拟 updater 首次写入）
    data = json.loads(pending.read_text(encoding="utf-8"))
    data["attempts"] = 1
    pending.write_text(json.dumps(data), encoding="utf-8")

    stub = _StubWindow()
    with patch(
        "vibeocr.services.env_config.get_pending_sync_path", return_value=pending
    ):
        stub._on_sync_finished(0)

    updated = json.loads(pending.read_text(encoding="utf-8"))
    assert updated["attempts"] == 2, f"attempts 应递增到 2，实际: {updated.get('attempts')}"


def test_sync_failure_threshold_shows_reinstall_hint(tmp_path):
    """P2：attempts 达 SYNC_MAX_ATTEMPTS 时状态栏应提示重装 Python。"""
    from vibeocr.services.env_config import SYNC_MAX_ATTEMPTS

    pending = tmp_path / "pending_sync.json"
    _write_pending(pending, {"paddlepaddle": "3.3.1"})
    # attempts 设为阈值 - 1，递增后恰好达到阈值
    data = json.loads(pending.read_text(encoding="utf-8"))
    data["attempts"] = SYNC_MAX_ATTEMPTS - 1
    pending.write_text(json.dumps(data), encoding="utf-8")

    stub = _StubWindow()
    with patch(
        "vibeocr.services.env_config.get_pending_sync_path", return_value=pending
    ):
        stub._on_sync_finished(0)

    # 状态栏消息应含"重装"提示
    msg = stub._statusbar.showMessage.call_args[0][0]
    assert "重装" in msg, f"达阈值应提示重装 Python，实际消息: {msg}"


# ---------------------------------------------------------------------------
# _delete_pending_sync：清理标记
# ---------------------------------------------------------------------------


def test_delete_marker_removes_file(tmp_path):
    """_delete_pending_sync 应删除标记文件"""
    pending = tmp_path / "pending_sync.json"
    _write_pending(pending, {"paddlepaddle": "3.3.1"})
    stub = _StubWindow()
    with patch(
        "vibeocr.services.env_config.get_pending_sync_path", return_value=pending
    ):
        stub._delete_pending_sync()
    assert not pending.exists()


def test_delete_marker_missing_file_is_noop(tmp_path):
    """标记不存在时 _delete_pending_sync 不报错"""
    pending = tmp_path / "pending_sync.json"  # 不存在
    stub = _StubWindow()
    with patch(
        "vibeocr.services.env_config.get_pending_sync_path", return_value=pending
    ):
        stub._delete_pending_sync()  # 不应抛异常
