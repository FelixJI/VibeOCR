"""main.py 纯逻辑辅助函数测试。

main.py 大量是 Qt UI 初始化（splash/tray/icon），测试成本高。本文件聚焦
可独立测试的纯/半纯函数：``_resolve_replacer_module_dir``、``_resolve_app_icon_path``、
``check_production_dependencies``、``_cleanup_leftover_old_exes``、
``_configure_standard_streams``、``_startup_lock_names``、``_finish_t3_smoke``、
``_on_tray_activated``、``_show_tray_settings``、``_setup_app_icon``、
``_show_another_product_running_dialog``。跳过 ``launch_application``/``main``
（不可单测）。这些函数此前完全未覆盖（main.py 仅 ~36% 覆盖）。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

from vibeocr.main import (
    _cleanup_leftover_old_exes,
    _configure_standard_streams,
    _finish_t3_smoke,
    _on_tray_activated,
    _resolve_app_icon_path,
    _resolve_replacer_module_dir,
    _setup_app_icon,
    _show_another_product_running_dialog,
    _show_tray_settings,
    _startup_lock_names,
    check_production_dependencies,
)


class TestResolveReplacerModuleDir:
    """_resolve_replacer_module_dir：定位 update_replacer.py 目录。"""

    def test_dev_mode_finds_scripts_dir(self, tmp_path, monkeypatch):
        """开发态：项目根下 scripts/update_replacer.py 存在时返回 scripts/。"""
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "update_replacer.py").write_text("# stub")

        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(
            "vibeocr.main.env_manager.get_project_root",
            MagicMock(return_value=tmp_path),
        )

        result = _resolve_replacer_module_dir()
        assert result == scripts

    def test_frozen_mode_finds_meipass(self, tmp_path, monkeypatch):
        """打包态：sys._MEIPASS 下有 update_replacer.py 时返回 _MEIPASS。"""
        meipass = tmp_path / "frozen"
        meipass.mkdir()
        (meipass / "update_replacer.py").write_text("# stub")

        monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

        result = _resolve_replacer_module_dir()
        assert result == meipass

    def test_returns_none_when_not_found(self, tmp_path, monkeypatch):
        """开发和打包态都找不到时返回 None。"""
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(
            "vibeocr.main.env_manager.get_project_root",
            MagicMock(return_value=tmp_path),
        )

        assert _resolve_replacer_module_dir() is None

    def test_meipass_without_replacer_falls_back_to_dev(self, tmp_path, monkeypatch):
        """_MEIPASS 存在但无 update_replacer.py 时，回退到开发态 scripts/。"""
        meipass = tmp_path / "frozen"
        meipass.mkdir()  # 无 update_replacer.py

        dev_root = tmp_path / "dev"
        scripts = dev_root / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "update_replacer.py").write_text("# stub")

        monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
        monkeypatch.setattr(
            "vibeocr.main.env_manager.get_project_root",
            MagicMock(return_value=dev_root),
        )

        result = _resolve_replacer_module_dir()
        assert result == scripts


class TestResolveAppIconPath:
    """_resolve_app_icon_path：解析应用图标路径。"""

    def test_returns_path_when_icon_exists(self, tmp_path, monkeypatch):
        resources = tmp_path / "resources"
        resources.mkdir()
        (resources / "app_icon.ico").write_bytes(b"icon")

        monkeypatch.setattr(
            "vibeocr.main.env_manager.get_bundled_resources_dir",
            MagicMock(return_value=resources),
        )

        result = _resolve_app_icon_path()
        assert result is not None
        assert result.name == "app_icon.ico"

    def test_returns_none_when_icon_missing(self, tmp_path, monkeypatch):
        resources = tmp_path / "resources"
        resources.mkdir()  # 无 app_icon.ico

        monkeypatch.setattr(
            "vibeocr.main.env_manager.get_bundled_resources_dir",
            MagicMock(return_value=resources),
        )

        assert _resolve_app_icon_path() is None


class TestCheckProductionDependencies:
    """check_production_dependencies：生产依赖检查。"""

    def test_returns_true_when_ready(self, monkeypatch):
        """is_production_environment_ready 返回就绪时返回 True，不打印缺失。"""
        monkeypatch.setattr(
            "vibeocr.main.env_manager.is_production_environment_ready",
            MagicMock(return_value=(True, [])),
        )
        assert check_production_dependencies() is True

    def test_returns_false_and_prints_missing(self, monkeypatch, capsys):
        """缺依赖时返回 False 并打印缺失项与安装指引。"""
        monkeypatch.setattr(
            "vibeocr.main.env_manager.is_production_environment_ready",
            MagicMock(return_value=(False, ["paddleocr", "mineru"])),
        )

        result = check_production_dependencies()

        assert result is False
        captured = capsys.readouterr()
        assert "paddleocr" in captured.out
        assert "mineru" in captured.out
        assert "vibeocr-install-backend" in captured.out


class TestCleanupLeftoverOldExes:
    """_cleanup_leftover_old_exes：清理 .old 残留（异常绝不阻断启动）。"""

    def test_no_replacer_dir_returns_silently(self, monkeypatch):
        """_resolve_replacer_module_dir 返回 None 时静默返回。"""
        monkeypatch.setattr(
            "vibeocr.main._resolve_replacer_module_dir", MagicMock(return_value=None)
        )
        # 不应抛异常
        _cleanup_leftover_old_exes()

    def test_exception_does_not_propagate(self, monkeypatch, tmp_path):
        """动态 import 或 cleanup 抛异常时仅打印，不阻断启动。"""
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        monkeypatch.setattr(
            "vibeocr.main._resolve_replacer_module_dir", MagicMock(return_value=scripts)
        )

        # 让动态 import 抛异常
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "update_replacer":
                raise RuntimeError("import failed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        # 不应抛异常（被 except 吞掉）
        _cleanup_leftover_old_exes()


class TestLaunchPrewarmScheduling:
    """launch_application 应在窗口 show + splash 收尾后调度 WebEngine 预热。

    见 .superpowers/sdd/fix-task2-brief.md（Task 2）：完整启动单测成本过高，
    这里用源码级断言确认预热调度存在（brief 明确允许的尽力而为方案）。
    """

    def test_launch_application_schedules_prewarm_after_splash(self):
        """launch_application 源码应包括 QTimer.singleShot(0, window.prewarm_result_webengine)。"""
        import inspect

        from vibeocr import main as main_module

        source = inspect.getsource(main_module.launch_application)
        # 调度预热：singleShot(0, ...) 在下一个事件循环空转触发。
        assert "singleShot(0, window.prewarm_result_webengine)" in source, (
            "launch_application 应在窗口显示 + splash 收尾后用 "
            "QTimer.singleShot(0, window.prewarm_result_webengine) 调度预热"
        )


class TestConfigureStandardStreams:
    """``_configure_standard_streams``：对 stdout/stderr reconfigure utf-8，异常静默。"""

    def test_reconfigures_both_streams(self, monkeypatch):
        stdout_mock = MagicMock()
        stderr_mock = MagicMock()
        monkeypatch.setattr(sys, "stdout", stdout_mock)
        monkeypatch.setattr(sys, "stderr", stderr_mock)

        _configure_standard_streams()

        stdout_mock.reconfigure.assert_called_once_with(
            encoding="utf-8", errors="replace"
        )
        stderr_mock.reconfigure.assert_called_once_with(
            encoding="utf-8", errors="replace"
        )

    def test_stream_without_reconfigure_attr_is_skipped(self, monkeypatch):
        """无 ``reconfigure`` 属性的流应被跳过，不抛 AttributeError。"""
        # 模拟一个没有 reconfigure 方法的流对象
        bare_stream = MagicMock(spec=[])  # spec=[] → 无任何属性
        monkeypatch.setattr(sys, "stdout", bare_stream)
        monkeypatch.setattr(sys, "stderr", bare_stream)
        # 不应抛异常
        _configure_standard_streams()

    def test_reconfigure_oserror_is_swallowed(self, monkeypatch):
        """reconfigure 抛 OSError 时静默吞掉（GUI 启动时流可能已关闭）。"""
        bad_stream = MagicMock()
        bad_stream.reconfigure.side_effect = OSError("stream closed")
        monkeypatch.setattr(sys, "stdout", bad_stream)
        monkeypatch.setattr(sys, "stderr", MagicMock())
        # 不应抛异常
        _configure_standard_streams()

    def test_reconfigure_valueerror_is_swallowed(self, monkeypatch):
        bad_stream = MagicMock()
        bad_stream.reconfigure.side_effect = ValueError("unsupported")
        monkeypatch.setattr(sys, "stdout", bad_stream)
        monkeypatch.setattr(sys, "stderr", MagicMock())
        _configure_standard_streams()


class TestStartupLockNames:
    """``_startup_lock_names``：生产锁 vs T6 自检进程唯一锁。"""

    def test_default_returns_production_locks(self, monkeypatch):
        monkeypatch.delenv("VIBEOCR_SELF_TEST_SMOKE", raising=False)
        name, mutex = _startup_lock_names()
        assert name == "VibeOCR"
        assert mutex is None

    def test_t6_smoke_returns_process_unique_locks(self, monkeypatch):
        monkeypatch.setenv("VIBEOCR_SELF_TEST_SMOKE", "t6")
        pid = os.getpid()
        name, mutex = _startup_lock_names()
        assert name == f"VibeOCR-SelfTest-{pid}"
        assert mutex == rf"Local\VibeOCR.Frontend.Exclusive.SelfTest.{pid}"


class TestFinishT3Smoke:
    """``_finish_t3_smoke``：flush startup 后 os._exit(0) 终止进程。"""

    def test_processes_events_flushes_and_exits(self, monkeypatch):
        app = MagicMock()
        exited = []
        monkeypatch.setattr(os, "_exit", lambda code: exited.append(code))

        with patch("vibeocr.main.flush_startup") as mock_flush:
            _finish_t3_smoke(app)

        app.processEvents.assert_called_once()
        mock_flush.assert_called_once()
        assert exited == [0]


class TestOnTrayActivated:
    """``_on_tray_activated``：Trigger 时按窗口可见性切换显示/隐藏。"""

    def test_trigger_hides_visible_window(self):
        from PySide6.QtWidgets import QSystemTrayIcon

        window = MagicMock()
        window.isVisible.return_value = True
        window.isMinimized.return_value = False

        _on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger, window)

        window.hide.assert_called_once()
        window.showNormal.assert_not_called()

    def test_trigger_shows_hidden_window(self):
        from PySide6.QtWidgets import QSystemTrayIcon

        window = MagicMock()
        window.isVisible.return_value = False

        _on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger, window)

        window.showNormal.assert_called_once()
        window.activateWindow.assert_called_once()
        window.raise_.assert_called_once()
        window.hide.assert_not_called()

    def test_trigger_shows_minimized_window(self):
        from PySide6.QtWidgets import QSystemTrayIcon

        window = MagicMock()
        window.isVisible.return_value = True
        window.isMinimized.return_value = True  # 最小化视为不可见

        _on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger, window)

        window.showNormal.assert_called_once()
        window.hide.assert_not_called()

    def test_non_trigger_reason_does_nothing(self):
        from PySide6.QtWidgets import QSystemTrayIcon

        window = MagicMock()
        # MiddleClick 等非 Trigger 原因 → 不动窗口
        _on_tray_activated(QSystemTrayIcon.ActivationReason.MiddleClick, window)
        window.showNormal.assert_not_called()
        window.hide.assert_not_called()


class TestShowTraySettings:
    """``_show_tray_settings``：显示窗口并切换到"设置"标签页。"""

    def test_switches_to_settings_tab_when_present(self):
        window = MagicMock()
        tab_widget = MagicMock()
        tab_widget.count.return_value = 3
        tab_widget.tabText.side_effect = ["常规", "设置", "关于"]
        ui = MagicMock()
        ui.tabWidget = tab_widget
        window._ui = ui

        _show_tray_settings(window)

        window.showNormal.assert_called_once()
        # 应切到索引 1（"设置"标签）
        tab_widget.setCurrentIndex.assert_called_once_with(1)

    def test_no_settings_tab_does_not_switch(self):
        window = MagicMock()
        tab_widget = MagicMock()
        tab_widget.count.return_value = 2
        tab_widget.tabText.side_effect = ["常规", "关于"]
        ui = MagicMock()
        ui.tabWidget = tab_widget
        window._ui = ui

        _show_tray_settings(window)

        window.showNormal.assert_called_once()
        tab_widget.setCurrentIndex.assert_not_called()

    def test_window_without_ui_attr_still_shows(self):
        # 无 _ui 属性 → 仅显示窗口，不抛异常
        window = MagicMock(spec=["showNormal", "activateWindow", "raise_"])
        _show_tray_settings(window)
        window.showNormal.assert_called_once()


class TestSetupAppIcon:
    """``_setup_app_icon``：图标缺失 / isNull / 成功三分支。"""

    def test_missing_icon_prints_warning_and_returns(self, qapp, monkeypatch, capsys):
        monkeypatch.setattr(
            "vibeocr.main._resolve_app_icon_path", MagicMock(return_value=None)
        )
        app = MagicMock()
        _setup_app_icon(app)
        captured = capsys.readouterr()
        assert "未找到应用图标" in captured.out
        app.setWindowIcon.assert_not_called()

    def test_null_icon_prints_warning_and_returns(
        self, qapp, tmp_path, monkeypatch, capsys
    ):
        # 写一个空文件 → QIcon 加载为 null
        icon_path = tmp_path / "app_icon.ico"
        icon_path.write_bytes(b"not a real icon")
        monkeypatch.setattr(
            "vibeocr.main._resolve_app_icon_path", MagicMock(return_value=icon_path)
        )
        app = MagicMock()
        _setup_app_icon(app)
        captured = capsys.readouterr()
        assert "图标加载失败" in captured.out
        app.setWindowIcon.assert_not_called()

    def test_valid_icon_sets_window_icon(self, qapp, tmp_path, monkeypatch):
        """用真实 PNG 生成有效 QIcon（避免依赖仓库 resources）。"""
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QColor, QImage, QPixmap

        # 构造一个有效的 1x1 PNG 文件
        pm = QPixmap(QSize(16, 16))
        pm.fill(QColor(255, 0, 0))
        icon_path = tmp_path / "app_icon.png"
        pm.save(str(icon_path), "PNG")
        monkeypatch.setattr(
            "vibeocr.main._resolve_app_icon_path", MagicMock(return_value=icon_path)
        )
        app = MagicMock()
        _setup_app_icon(app)
        app.setWindowIcon.assert_called_once()
        app.setApplicationName.assert_called_once_with("VibeOCR")


class TestShowAnotherProductRunningDialog:
    """``_show_another_product_running_dialog``：弹出 warning 提示退出 WinUI 版。"""

    def test_calls_qmessagebox_warning(self, qapp):
        with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
            _show_another_product_running_dialog()
        mock_warn.assert_called_once()
        # 第 3 个位置参数是文案，应提示"另一套 VibeOCR"
        _parent, title, text = mock_warn.call_args.args[0:3]
        assert title == "VibeOCR"
        assert "另一套 VibeOCR" in text
        assert "WinUI" in text

