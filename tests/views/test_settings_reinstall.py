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
        # BackendOptionsWidget._load_state 读 detect_gpu_info()（含 vram/cuda
        # 等字段），必须返回真实结构而非默认 MagicMock，否则 vram >= 1024
        # 会因 MagicMock 与 int 比较抛 TypeError。此处配置无 GPU 的回退值。
        mock_em.detect_gpu_info.return_value = {
            "has_gpu": False,
            "name": "",
            "vram_mb": 0,
            "cuda": None,
        }
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

        def show(self):
            pass

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

        def show(self):
            pass

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


def test_install_missing_button_exists(controller):
    """补充安装缺失依赖按钮应在 UI 中可找到"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QPushButton

    btn = host.findChild(QPushButton, "btnInstallMissing")
    assert btn is not None, "btnInstallMissing 应存在"


def test_deps_status_table_exists(controller):
    """依赖状态表格应在 UI 中可找到"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QTableWidget

    table = host.findChild(QTableWidget, "tableDepsStatus")
    assert table is not None, "tableDepsStatus 应存在"


def test_click_install_missing_opens_dialog_with_missing_only(controller, monkeypatch):
    """点补充安装缺失依赖：应弹 BackendChoiceDialog(missing_only=True)"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton

    btn = host.findChild(QPushButton, "btnInstallMissing")
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

        def show(self):
            pass

        finished = MagicMock()
        install_succeeded = MagicMock()

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.BackendChoiceDialog", FakeDialog
    )

    btn.click()

    assert len(instances) == 1
    assert instances[0].get("missing_only") is True


def test_refresh_fills_deps_table(controller, monkeypatch):
    """_refresh_env_maintenance_state 应填充依赖状态表格"""
    ctrl, host = controller
    from PySide6.QtWidgets import QTableWidget

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_environment_mode",
        lambda root: "portable",
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_embedded_python_info",
        lambda root: {
            "path": "C:/app/python/python.exe",
            "mode": "portable",
            "ready": True,
        },
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_embedded_python_executable",
        lambda root: __import__("pathlib").Path("C:/app/python/python.exe"),
    )
    # mock 依赖状态检测：paddle 已装，其余未装
    # 注意：_populate_deps_table 现在用 _fresh 变体（忽略缓存，保证状态实时）
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.check_embedded_environment_dependencies_fresh",
        lambda root: {
            "paddlepaddle": True,
            "paddleocr": False,
            "mineru": False,
            "torch": False,
            "markdown": False,
        },
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_dependency_versions",
        lambda root: {
            "paddlepaddle": "3.3.1",
            "paddleocr": "",
            "mineru": "",
            "torch": "",
            "markdown": "",
        },
    )

    ctrl._refresh_env_maintenance_state()

    table = host.findChild(QTableWidget, "tableDepsStatus")
    assert table is not None
    # 行数应等于 OCR_CHECK_MODULES 的模块数（paddle/paddleocr/mineru/torch/markdown）。
    # 直接读源常量长度，避免新增模块时再改硬编码魔数。
    from vibeocr.services.env_config import OCR_CHECK_MODULES

    assert table.rowCount() == len(OCR_CHECK_MODULES), (
        f"应有 {len(OCR_CHECK_MODULES)} 行依赖，实际: {table.rowCount()}"
    )
    # 第一行 paddlepaddle 应标记已装
    status_item = table.item(0, 1)
    assert status_item is not None
    assert "已安装" in status_item.text() or "✓" in status_item.text(), (
        f"paddlepaddle 应已安装，实际: {status_item.text()}"
    )


def test_deps_table_uses_fresh_check(controller, qtbot, tmp_path, monkeypatch):
    """依赖状态表格应使用 fresh 检测（忽略缓存），保证装完即刷新

    回归（修复 4）：旧逻辑 _populate_deps_table 用 check_embedded_environment_dependencies
    （带缓存），装完依赖但缓存未刷新时表格显示过期状态。
    """
    ctrl, _host = controller

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_environment_mode",
        lambda root: "portable",
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_embedded_python_info",
        lambda root: {"path": "C:/app/python/python.exe", "mode": "portable", "ready": True},
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_embedded_python_executable",
        lambda root: __import__("pathlib").Path("C:/app/python/python.exe"),
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_dependency_versions",
        lambda root: {},
    )

    # 关键：验证 _populate_deps_table 走的是 _fresh 变体（忽略缓存）。
    # controller 仅 import 了 check_embedded_environment_dependencies_fresh，
    # 代码路径上没有对带缓存旧函数的调用，因此这里只断言 _fresh 被触发。
    fresh_called = {"count": 0}

    def _fresh_tracker(root):
        fresh_called["count"] += 1
        return {
            "paddlepaddle": True,
            "paddleocr": True,
            "mineru": True,
            "torch": True,
            "markdown": True,
        }

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.check_embedded_environment_dependencies_fresh",
        _fresh_tracker,
    )

    ctrl._refresh_env_maintenance_state()

    assert fresh_called["count"] >= 1, "应调用 _fresh 变体"
