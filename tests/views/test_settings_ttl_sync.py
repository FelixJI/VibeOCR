"""设置页 TTL 下发同步测试

回归 bug：worker 正在加载模型/执行 OCR 时，TTL 更新走轻量控制 RPC
（execute_control，lock_timeout=15.0），而预加载重任务持有同一把 _shm_lock
可达数分钟。TTL 等锁 15s 超时 → handler 抛 WorkerError → 客户端 TimeoutError
→ FunctionTask 打 `logger.exception("后台任务执行失败")` 完整 traceback，
并把失败呈现为红色错误。

根因修复：TTL 已通过 ConfigManager.set_pipeline_ttls 持久化到 app_settings.json，
worker 下次启动时 PreloadTask 自动读取并下发。所以实时下发失败**不影响最终生效**，
应降级为「已保存，重启后生效」的友好提示，而非错误。
"""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QLabel, QWidget

from vibeocr.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.views.settings_page_controller import SettingsPageController


@pytest.fixture
def controller(qtbot, tmp_path, monkeypatch):
    """构造带真实 UI 的 SettingsPageController（预加载相关依赖被 mock）。"""
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    mock_cm = MagicMock()
    mock_cm.get_pipeline_ttls.return_value = {
        "OCR": 0,
        "TABLE_RECOGNITION": 0,
        "FORMULA_RECOGNITION": 0,
        "PP-StructureV3": 300,
        "MinerU": 0,
        "PaddleOCR-VL": 300,
    }
    mock_cm.set_pipeline_ttl.return_value = True
    mock_cm.get_preload_pipelines.return_value = []
    mock_cm.get_preload_enabled.return_value = False

    mock_cm_class = MagicMock()
    mock_cm_class.instance.return_value = mock_cm
    # _sync_configured_pipeline_ttls 内部 `from vibeocr.pyside.runtime
    # import ConfigManager` 读的是这个 proxy 对象；用 monkeypatch 让它在
    # 整个测试期间（含方法调用）都被替换，避免触发真实 ConfigManager。
    monkeypatch.setattr(
        "vibeocr.pyside.runtime.ConfigManager", mock_cm_class
    )

    with (
        patch(
            "vibeocr.widgets.backend_options_widget.env_manager"
        ) as mock_em,
        patch(
            "vibeocr.widgets.backend_options_widget.load_cache",
            return_value=None,
        ),
        patch(
            "vibeocr.views.settings_page_controller.is_cache_valid",
            return_value=(False, None),
        ),
    ):
        mock_em.detect_gpu.return_value = (False, None)
        mock_em.detect_gpu_info.return_value = {
            "has_gpu": False,
            "name": "",
            "vram_mb": 0,
            "cuda": None,
        }

        ctrl = SettingsPageController(
            ui=host,
            project_root=tmp_path,
            status_callback=lambda msg: None,
            ocr_ready_callback=lambda: True,
            subprocess_manager=MagicMock(),
        )
        ctrl.connect_signals()
    return ctrl, host


def _stub_status() -> dict:
    """worker 真实回读状态（get_pipeline_cache_status 返回值）。"""
    return {
        "ready": True,
        "pipeline_ttls": {"OCR": 0, "PP-StructureV3": 300},
        "max_heavy": 2,
        "loaded_pipelines": ["OCR"],
        "last_used_unix_ms": {},
    }


def test_ttl_update_failure_shows_pending_message_not_error(controller, qtbot):
    """set_pipeline_ttls 抛超时异常时，应显示「重启后生效」而非红色失败+traceback。

    场景：worker 正在预加载（持有 _shm_lock），TTL 控制RPC 等锁 15s 超时
    → set_pipeline_ttls 抛 TimeoutError。修复后该失败非致命：
    - 不触发 error 信号（无 traceback）
    - 成功回调 prefix 含「重启后生效」
    - 仍调 get_pipeline_cache_status 刷新 worker 真实状态
    """
    ctrl, host = controller
    service = MagicMock()
    service.set_pipeline_ttls.side_effect = TimeoutError(
        "控制 RPC 等待 _shm_lock 超时（15.0s）"
    )
    service.get_pipeline_cache_status.return_value = _stub_status()
    ctrl._subprocess_manager.is_ready = True
    ctrl._subprocess_manager.service = service

    ctrl._sync_configured_pipeline_ttls()

    status_label = host.findChild(QLabel, "labelPipelineCacheStatus")
    release_label = host.findChild(QLabel, "labelReleaseStatus")
    qtbot.waitUntil(
        lambda: "重启后生效" in status_label.text(),
        timeout=2000,
    )
    # 不应出现「失败」红色文案
    assert "失败" not in status_label.text()
    assert "失败" not in release_label.text()
    # 仍刷新了 worker 真实状态
    assert "驻留 1 个" in status_label.text()
    service.get_pipeline_cache_status.assert_called_once()


def test_ttl_update_not_accepted_shows_pending_message(controller, qtbot):
    """set_pipeline_ttls 返回 False（worker 未接受）时也应降级为「重启后生效」。

    场景：execute_control 锁超时后 ocr_service_subprocess.set_pipeline_ttls
    捕获异常返回 False。旧逻辑 raise RuntimeError("Worker 未接受 TTL 更新")
    → 走错误回调。修复后同超时一样降级。
    """
    ctrl, host = controller
    service = MagicMock()
    service.set_pipeline_ttls.return_value = False
    service.get_pipeline_cache_status.return_value = _stub_status()
    ctrl._subprocess_manager.is_ready = True
    ctrl._subprocess_manager.service = service

    ctrl._sync_configured_pipeline_ttls()

    status_label = host.findChild(QLabel, "labelPipelineCacheStatus")
    qtbot.waitUntil(
        lambda: "重启后生效" in status_label.text(),
        timeout=2000,
    )
    assert "失败" not in status_label.text()
    service.get_pipeline_cache_status.assert_called_once()


def test_ttl_update_success_keeps_updated_prefix(controller, qtbot):
    """set_pipeline_ttls 成功时 prefix 仍为「TTL 已更新」（不回归）。"""
    ctrl, host = controller
    service = MagicMock()
    service.set_pipeline_ttls.return_value = True
    service.get_pipeline_cache_status.return_value = _stub_status()
    ctrl._subprocess_manager.is_ready = True
    ctrl._subprocess_manager.service = service

    ctrl._sync_configured_pipeline_ttls()

    status_label = host.findChild(QLabel, "labelPipelineCacheStatus")
    qtbot.waitUntil(
        lambda: "TTL 已更新" in status_label.text(),
        timeout=2000,
    )
    assert "重启后生效" not in status_label.text()
