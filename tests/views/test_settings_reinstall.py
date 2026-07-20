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


def test_env_status_label_shows_python_info(controller, monkeypatch, qtbot):
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
    qtbot.waitUntil(lambda: "python.exe" in label.text(), timeout=3000)
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


def test_deps_status_tree_exists(controller):
    """依赖状态树（QTreeWidget）应在 UI 中可找到"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QTreeWidget

    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    assert tree is not None, "treeDepsStatus 应存在"


def test_reinstall_selected_button_exists(controller):
    """重装选中项按钮应在 UI 中可找到，且初始禁用"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QPushButton

    btn = host.findChild(QPushButton, "btnReinstallSelected")
    assert btn is not None, "btnReinstallSelected 应存在"
    assert not btn.isEnabled(), "无选中时重装选中项按钮应禁用"


def test_click_install_missing_opens_dialog_with_missing_only(controller, monkeypatch):
    """点补充安装缺失依赖：走当前后端，弹 InstallDialog(missing_only=True)

    回归（问题4）：补装不再二次提示选择 GPU/CPU（旧逻辑弹 BackendChoiceDialog）。
    改为直接读 resolve_use_gpu 当前后端，用 InstallDialog 跑增量补装。
    """
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

        def show(self):
            pass

        finished = MagicMock()
        install_succeeded = MagicMock()

    # 补装现在走 InstallDialog（非 BackendChoiceDialog），用当前后端
    monkeypatch.setattr(
        "vibeocr.widgets.install_dialog.InstallDialog", FakeDialog
    )
    # resolve_use_gpu 返回 False（CPU），验证 force_backend 被透传
    monkeypatch.setattr(
        "vibeocr.env_manager.resolve_use_gpu", lambda root: False
    )

    btn.click()

    assert len(instances) == 1, f"应打开一个 InstallDialog，实际: {instances}"
    assert instances[0].get("missing_only") is True, "应为 missing_only 模式"
    assert instances[0].get("force_backend") == "cpu", (
        f"应用当前后端 cpu，实际: {instances[0].get('force_backend')}"
    )


def test_refresh_fills_deps_tree(controller, monkeypatch, qtbot):
    """_refresh_env_maintenance_state 应填充依赖状态树

    顶层 OCR 依赖每个一行（QTreeWidget 顶层节点），状态列三态：
    完整安装 / 已安装，缺 xxx / 未安装。
    """
    ctrl, host = controller
    from PySide6.QtWidgets import QTreeWidget

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
    from vibeocr.services.env_config import OCR_CHECK_MODULES

    # 三元状态：paddle 完整安装；mineru 已装但缺 torch（间接依赖未完成）；
    # paddleocr 未安装。
    def _detailed(root):
        return {
            "paddlepaddle": (True, True, None),
            "mineru": (True, False, "torch"),
            "paddleocr": (False, False, None),
        }

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.check_dependencies_status_detailed",
        _detailed,
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_direct_dependencies",
        lambda exe, pkg: [],
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_dependency_versions",
        lambda root: {"paddlepaddle": "3.3.1"},
    )

    ctrl._refresh_env_maintenance_state()

    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    assert tree is not None
    qtbot.waitUntil(
        lambda: tree.topLevelItemCount() == len(OCR_CHECK_MODULES), timeout=3000
    )
    # 顶层节点数 == OCR_CHECK_MODULES 数量
    assert tree.topLevelItemCount() == len(OCR_CHECK_MODULES), (
        f"应有 {len(OCR_CHECK_MODULES)} 个顶层节点，实际: {tree.topLevelItemCount()}"
    )
    # 第一行 paddlepaddle：完整安装
    top0 = tree.topLevelItem(0)
    assert "完整安装" in top0.text(1), f"paddlepaddle 应完整安装，实际: {top0.text(1)}"
    # 找到 mineru 行：应显示"已安装，缺 torch"
    statuses = [tree.topLevelItem(i).text(1) for i in range(tree.topLevelItemCount())]
    mineru_status = next(
        (s for s in statuses if "缺 torch" in s), None
    )
    assert mineru_status is not None, (
        f"mineru 应显示'已安装，缺 torch'，实际状态列: {statuses}"
    )


def test_deps_tree_uses_detailed_fresh_check(controller, monkeypatch, qtbot):
    """依赖状态树应使用 check_dependencies_status_detailed（fresh，含缺失模块名）

    回归：旧逻辑用 check_embedded_environment_dependencies_fresh（仅布尔），
    无法在状态列显示"已安装，缺 xxx"。改用 detailed 三元检测。
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
        "vibeocr.views.settings_page_controller.get_direct_dependencies",
        lambda exe, pkg: [],
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_dependency_versions",
        lambda root: {},
    )

    detailed_called = {"count": 0}

    def _tracker(root):
        detailed_called["count"] += 1
        return {"paddlepaddle": (True, True, None)}

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.check_dependencies_status_detailed",
        _tracker,
    )

    ctrl._refresh_env_maintenance_state()

    qtbot.waitUntil(lambda: detailed_called["count"] >= 1, timeout=3000)
    assert detailed_called["count"] >= 1, "应调用 detailed 三元检测"


def test_deps_tree_expands_direct_dependencies(controller, monkeypatch, qtbot):
    """依赖树顶层节点应可展开显示动态推导的直接依赖子节点"""
    ctrl, host = controller
    from PySide6.QtWidgets import QTreeWidget

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
        "vibeocr.views.settings_page_controller.check_dependencies_status_detailed",
        lambda root: {"mineru": (True, True, None)},
    )
    # mineru 有两个直接依赖
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_direct_dependencies",
        lambda exe, pkg: ["opencv-python", "rapid-table"] if pkg == "mineru" else [],
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_dependency_versions",
        lambda root: {},
    )

    ctrl._refresh_env_maintenance_state()

    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    qtbot.waitUntil(lambda: tree.topLevelItemCount() > 0, timeout=3000)
    # 遍历找到 mineru 节点（OCR_CHECK_MODULES 顺序中它不是第 0 个）
    mineru_item = None
    for i in range(tree.topLevelItemCount()):
        it = tree.topLevelItem(i)
        if "MinerU" in it.text(0):
            mineru_item = it
            break
    assert mineru_item is not None, "应找到 MinerU 顶层节点"
    assert mineru_item.childCount() == 2, (
        f"mineru 应有 2 个直接依赖子节点，实际: {mineru_item.childCount()}"
    )
    child0 = mineru_item.child(0).text(0)
    assert "opencv-python" in child0 or "rapid-table" in child0


def test_click_reinstall_selected_batch_reinstalls(controller, monkeypatch, qtbot):
    """选中顶层节点后点"重装选中项"：确认后走 InstallDialog(packages=[...])"""
    ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton, QTreeWidget

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
        "vibeocr.views.settings_page_controller.check_dependencies_status_detailed",
        lambda root: {"mineru": (False, False, None), "paddleocr": (False, False, None)},
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_direct_dependencies",
        lambda exe, pkg: [],
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_dependency_versions",
        lambda root: {},
    )
    ctrl._refresh_env_maintenance_state()

    tree = host.findChild(QTreeWidget, "treeDepsStatus")
    qtbot.waitUntil(lambda: tree.topLevelItemCount() >= 2, timeout=3000)
    # 选中两个顶层节点
    tree.topLevelItem(0).setSelected(True)
    tree.topLevelItem(1).setSelected(True)

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    instances = []

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            instances.append(kwargs)

        def show(self):
            pass

        finished = MagicMock()
        install_succeeded = MagicMock()

    monkeypatch.setattr(
        "vibeocr.widgets.install_dialog.InstallDialog", FakeDialog
    )

    btn = host.findChild(QPushButton, "btnReinstallSelected")
    btn.click()

    assert len(instances) == 1
    pkgs = instances[0].get("packages")
    assert pkgs is not None and len(pkgs) == 2, (
        f"应批量重装 2 个包，实际 packages: {pkgs}"
    )
