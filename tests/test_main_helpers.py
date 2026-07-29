"""main.py 纯逻辑辅助函数测试。

main.py 大量是 Qt UI 初始化（splash/tray/icon），测试成本高。本文件聚焦
可独立测试的纯逻辑函数：_resolve_replacer_module_dir、_resolve_app_icon_path、
check_production_dependencies、_cleanup_leftover_old_exes。这些函数此前完全
未覆盖（main.py 仅 ~27% 覆盖）。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from vibeocr.main import (
    _cleanup_leftover_old_exes,
    _resolve_app_icon_path,
    _resolve_replacer_module_dir,
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
        """launch_application 源码应包含 QTimer.singleShot(0, window.prewarm_result_webengine)。"""
        import inspect

        from vibeocr import main as main_module

        source = inspect.getsource(main_module.launch_application)
        # 调度预热：singleShot(0, ...) 在下一个事件循环空转触发。
        assert "singleShot(0, window.prewarm_result_webengine)" in source, (
            "launch_application 应在窗口显示 + splash 收尾后用 "
            "QTimer.singleShot(0, window.prewarm_result_webengine) 调度预热"
        )
