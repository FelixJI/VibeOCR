"""设置页重装入口测试"""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QWidget

from vibeocr.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.views.settings_page_controller import SettingsPageController


@pytest.fixture
def controller(qtbot, tmp_path):
    """构造带真实 UI 的 SettingsPageController

    connect_signals 会触发 _init_backend_options / _init_settings_page，
    这些会访问 ConfigManager、machine_cache、pipelines、BackendOptionsWidget。
    为保证测试隔离，patch 掉这些重依赖。
    """
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    with (
        # BackendOptionsWidget 构造读 env_manager / machine_cache
        patch("vibeocr.widgets.backend_options_widget.env_manager") as mock_em,
        patch(
            "vibeocr.widgets.backend_options_widget.is_cache_valid",
            return_value=(False, None),
        ),
        # _init_settings_page 读 ConfigManager / machine_cache / pipelines
        patch(
            "vibeocr.views.settings_page_controller.is_cache_valid",
            return_value=(False, None),
        ),
        patch("vibeocr.managers.config_manager.ConfigManager") as mock_cm,
        patch(
            "vibeocr.core.pipelines.get_preloadable_pipelines",
            return_value=[],
        ),
    ):
        mock_em.detect_gpu.return_value = (False, None)
        mock_cm.instance.return_value = MagicMock(
            get_pipeline_ttl_seconds=MagicMock(return_value=3600),
            get_preload_pipelines=MagicMock(return_value=[]),
            get_preload_enabled=MagicMock(return_value=False),
        )

        ctrl = SettingsPageController(
            ui=host,
            project_root=tmp_path,
            status_callback=lambda msg: None,
            ocr_ready_callback=lambda: True,
            subprocess_manager=MagicMock(),
        )
        ctrl.connect_signals()
    return ctrl, host


def test_reinstall_python_button_exists(controller):
    """重装 Python 按钮应在 UI 中可找到"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QPushButton

    btn = host.findChild(QPushButton, "btnReinstallPython")
    assert btn is not None, "btnReinstallPython 应存在"


def test_click_reinstall_python_confirms_then_opens_dialog(controller, monkeypatch):
    """点重装 Python：确认 Yes 后应弹 BackendChoiceDialog(reinstall_python=True)"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton

    # tmp_path 无 python/ → 按钮被禁用；测试模拟 portable 场景启用按钮
    btn = host.findChild(QPushButton, "btnReinstallPython")
    btn.setEnabled(True)

    # 模拟用户点"是"
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    # mock BackendChoiceDialog 避免真弹窗
    instances = []

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            instances.append(kwargs)
            self.reinstall_python = kwargs.get("reinstall_python", False)

        def exec(self):
            return 1

        finished = MagicMock()
        install_succeeded = MagicMock()

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.BackendChoiceDialog", FakeDialog
    )

    btn.click()

    assert len(instances) == 1, "应弹出一次对话框"
    assert instances[0].get("reinstall_python") is True


def test_click_reinstall_python_cancel_does_nothing(controller, monkeypatch):
    """点重装 Python：确认 No 后不应弹对话框"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton

    btn = host.findChild(QPushButton, "btnReinstallPython")
    btn.setEnabled(True)

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No
    )
    opened = []
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.BackendChoiceDialog",
        lambda *a, **kw: opened.append(kw),
    )

    btn.click()

    assert len(opened) == 0, "取消时不应弹对话框"


def test_click_reinstall_deps_opens_dialog_without_reinstall(controller, monkeypatch):
    """点重装 OCR 依赖：应弹 BackendChoiceDialog(reinstall_python=False)"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton

    btn = host.findChild(QPushButton, "btnReinstallDeps")
    btn.setEnabled(True)

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    instances = []

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            instances.append(kwargs)

        def exec(self):
            return 1

        finished = MagicMock()
        install_succeeded = MagicMock()

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.BackendChoiceDialog", FakeDialog
    )

    btn.click()

    assert len(instances) == 1
    assert instances[0].get("reinstall_python") is False


def test_buttons_disabled_in_non_portable_mode(controller, monkeypatch):
    """非 portable 模式（venv/none）时两按钮应禁用"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QPushButton

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_environment_mode",
        lambda root: "venv",
    )
    controller[0]._refresh_env_maintenance_state()

    btn_py = host.findChild(QPushButton, "btnReinstallPython")
    btn_deps = host.findChild(QPushButton, "btnReinstallDeps")
    assert not btn_py.isEnabled(), "venv 模式应禁用重装 Python"
    assert not btn_deps.isEnabled(), "venv 模式应禁用重装依赖"


def test_env_status_label_shows_python_info(controller, monkeypatch):
    """labelEnvStatus 应显示 Python 路径/就绪状态"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QLabel

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_embedded_python_info",
        lambda root: {
            "path": "C:/app/python/python.exe",
            "mode": "portable",
            "ready": True,
        },
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_environment_mode",
        lambda root: "portable",
    )
    controller[0]._refresh_env_maintenance_state()

    label = host.findChild(QLabel, "labelEnvStatus")
    text = label.text()
    assert "python.exe" in text or "就绪" in text or "已安装" in text, (
        f"应显示 Python 状态，实际: {text}"
    )
