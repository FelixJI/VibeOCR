"""设置页预加载功能测试

回归 bug：复选框 objectName 曾为旧命名（chkPreloadOCR / chkPreloadTable /
chkPreloadFormula），与控制器查找的 chkPreload_{OCRPipeline.name} 不匹配，
导致 `_get_selected_preload_pipelines()` 永远返回空列表，点"立即预加载"始终
弹出"请至少选择一个要预加载的管道。"
"""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QCheckBox, QPushButton, QWidget

from vibeocr.core.pipelines import OCRPipeline, get_preloadable_pipelines
from vibeocr.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.views.settings_page_controller import SettingsPageController


@pytest.fixture
def controller(qtbot, tmp_path):
    """构造带真实 UI 的 SettingsPageController（预加载相关依赖被 mock）。"""
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    mock_cm = MagicMock()

    with (
        patch(
            "vibeocr.widgets.backend_options_widget.env_manager"
        ) as mock_em,
        patch(
            "vibeocr.widgets.backend_options_widget.is_cache_valid",
            return_value=(False, None),
        ),
        patch(
            "vibeocr.views.settings_page_controller.is_cache_valid",
            return_value=(False, None),
        ),
        patch(
            "vibeocr.managers.config_manager.ConfigManager"
        ) as cm_class,
    ):
        mock_em.detect_gpu.return_value = (False, None)
        mock_em.detect_gpu_info.return_value = {
            "has_gpu": False,
            "name": "",
            "vram_mb": 0,
            "cuda": None,
        }
        cm_class.instance.return_value = mock_cm
        mock_cm.get_pipeline_ttl_seconds.return_value = 3600
        mock_cm.get_preload_pipelines.return_value = []
        mock_cm.get_preload_enabled.return_value = False

        ctrl = SettingsPageController(
            ui=host,
            project_root=tmp_path,
            status_callback=lambda msg: None,
            ocr_ready_callback=lambda: True,
            subprocess_manager=MagicMock(),
        )
        ctrl.connect_signals()
    return ctrl, host


def test_all_preloadable_pipelines_have_checkboxes(controller):
    """每个可预加载管道都应在 UI 中有对应复选框 chkPreload_{name}。"""
    _ctrl, host = controller
    for pipeline in get_preloadable_pipelines():
        chk = host.findChild(QCheckBox, f"chkPreload_{pipeline.name}")
        assert chk is not None, (
            f"缺少复选框 chkPreload_{pipeline.name}（管道 {pipeline.value}）"
        )


def _check_silently(chk: QCheckBox, checked: bool) -> None:
    """勾选/取消复选框但不触发 toggled 保存副作用（避免依赖真实 ConfigManager）。"""
    chk.blockSignals(True)
    chk.setChecked(checked)
    chk.blockSignals(False)


def test_get_selected_preload_pipelines_reads_checked_boxes(controller):
    """勾选复选框后，_get_selected_preload_pipelines 返回对应 OCRPipeline。"""
    ctrl, host = controller

    # 勾选 OCR 与公式两个管道
    _check_silently(host.findChild(QCheckBox, "chkPreload_OCR"), True)
    _check_silently(
        host.findChild(QCheckBox, "chkPreload_FORMULA_RECOGNITION"), True
    )
    _check_silently(
        host.findChild(QCheckBox, "chkPreload_TABLE_RECOGNITION"), False
    )

    selected = ctrl._get_selected_preload_pipelines()
    assert OCRPipeline.OCR in selected
    assert OCRPipeline.FORMULA_RECOGNITION in selected
    assert OCRPipeline.TABLE_RECOGNITION not in selected


def test_preload_now_proceeds_when_pipeline_selected(controller, monkeypatch):
    """勾选管道后点'立即预加载'，应进入预热流程而非弹警告。"""
    ctrl, host = controller

    # 满足 OCR 就绪与子进程就绪前置条件
    ctrl._ocr_ready_callback = lambda: True
    ctrl._subprocess_manager.is_ready = True
    ctrl._subprocess_manager.service = MagicMock()

    _check_silently(host.findChild(QCheckBox, "chkPreload_OCR"), True)

    started: list = []
    monkeypatch.setattr(
        ctrl,
        "_start_manual_preload_with_warmup",
        lambda pipelines: started.append(pipelines),
    )
    # 真实点按钮（不应弹出警告）
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.QMessageBox.warning",
        lambda *a, **kw: pytest.fail("不应弹出警告"),
    )

    btn = host.findChild(QPushButton, "btnPreloadNow")
    btn.click()

    assert len(started) == 1, "应触发一次预加载"
    assert OCRPipeline.OCR in started[0]


def test_save_preload_pipelines_config_persisted_as_values(controller, monkeypatch):
    """保存配置时写入的应是 OCRPipeline.value 字符串列表。"""
    from vibeocr.managers import config_manager

    ctrl, host = controller

    _check_silently(host.findChild(QCheckBox, "chkPreload_OCR"), True)
    _check_silently(
        host.findChild(QCheckBox, "chkPreload_TABLE_RECOGNITION"), True
    )

    captured: dict = {}
    fake_cm = MagicMock()
    fake_cm.set_preload_pipelines.side_effect = lambda v: (
        captured.update({"value": v}) or True
    )
    monkeypatch.setattr(config_manager.ConfigManager, "instance", lambda: fake_cm)

    ctrl._save_preload_pipelines_config()

    assert "value" in captured
    assert "OCR" in captured["value"]
    assert "TABLE_RECOGNITION" in captured["value"]


def test_restore_preload_checkbox_case_insensitive(controller, monkeypatch):
    """回归 bug：历史小写配置应能正确恢复 UI 勾选状态。

    配置文件曾存小写 'table_recognition'（枚举标准值为
    'TABLE_RECOGNITION'）。_restore_preload_checkbox_state 用大小写不敏感
    匹配后，对应复选框应被勾选，避免"配置有但 UI 显示无"的不一致。
    """
    from vibeocr.managers import config_manager

    ctrl, host = controller

    # 模拟历史小写配置
    fake_cm = MagicMock()
    fake_cm.get_preload_pipelines.return_value = ["ocr", "table_recognition"]
    fake_cm.get_preload_enabled.return_value = True
    monkeypatch.setattr(
        config_manager.ConfigManager, "instance", lambda: fake_cm
    )

    # 先全部取消勾选
    for pipeline in get_preloadable_pipelines():
        chk = host.findChild(QCheckBox, f"chkPreload_{pipeline.name}")
        _check_silently(chk, False)

    ctrl._restore_preload_checkbox_state()

    # 小写配置应能正确勾选对应复选框
    assert host.findChild(QCheckBox, "chkPreload_OCR").isChecked()
    assert host.findChild(
        QCheckBox, "chkPreload_TABLE_RECOGNITION"
    ).isChecked()
    # 未在配置中的管道不应被勾选
    assert not host.findChild(
        QCheckBox, "chkPreload_FORMULA_RECOGNITION"
    ).isChecked()
