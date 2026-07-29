"""AppPaths 单一边界测试。

覆盖：
- resolve_app_paths() 在 source、PyInstaller onedir、旁路 profile、正式 portable 下的解析
- 路径带空格
- import vibeocr.classic.app_paths 不加载 PySide6
- profile="winui-dev" 解析到 data/profiles/winui-dev，不触碰正式配置
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from vibeocr.classic.app_paths import resolve_app_paths


class TestAppPathsDataclass:
    def test_apppaths_is_frozen(self):
        paths = resolve_app_paths(
            Path("/tmp/fake"), profile="production"
        )
        with pytest.raises(Exception):  # noqa: B017
            paths.install_root = Path("/other")  # type: ignore[misc]

    def test_apppaths_has_all_fields(self):
        paths = resolve_app_paths(
            Path("/tmp/fake"), profile="production"
        )
        assert isinstance(paths.install_root, Path)
        assert isinstance(paths.data_root, Path)
        assert isinstance(paths.runtime_root, Path)
        assert isinstance(paths.model_cache_root, Path)
        assert isinstance(paths.output_root, Path)
        assert isinstance(paths.config_file, Path)


class TestResolveAppPaths:
    def test_production_profile_paths(self, tmp_path):
        """正式 profile：路径都在 install_root 下。"""
        install = tmp_path / "VibeOCR"
        paths = resolve_app_paths(install, profile="production")
        assert paths.install_root == install.resolve()
        assert paths.data_root == (install / "data").resolve()
        assert paths.runtime_root == (install / "runtimes").resolve()
        assert paths.model_cache_root == (install / "models").resolve()
        assert paths.output_root == (install / "output").resolve()
        assert paths.config_file == (install / "config" / "app_settings.json").resolve()

    def test_winui_dev_profile_uses_data_profiles(self, tmp_path):
        """旁路 profile：data_root/runtime/model/output 解析到 data/profiles/winui-dev。"""
        install = tmp_path / "VibeOCR"
        paths = resolve_app_paths(install, profile="winui-dev")
        dev = install / "data" / "profiles" / "winui-dev"
        assert paths.data_root == dev.resolve()
        assert paths.runtime_root == (dev / "runtimes").resolve()
        assert paths.model_cache_root == (dev / "models").resolve()
        assert paths.output_root == (dev / "output").resolve()
        # config_file 也在旁路 profile 下
        assert paths.config_file == (dev / "config" / "app_settings.json").resolve()

    def test_winui_dev_does_not_touch_production_config(self, tmp_path):
        """旁路 profile 解析不应创建或修改正式配置文件。"""
        install = tmp_path / "VibeOCR"
        prod_config = install / "config" / "app_settings.json"
        prod_config.parent.mkdir(parents=True)
        prod_config.write_text('{"version": "prod"}', encoding="utf-8")

        # 解析旁路 profile
        resolve_app_paths(install, profile="winui-dev")

        # 正式配置文件内容不变
        assert prod_config.read_text(encoding="utf-8") == '{"version": "prod"}'

    def test_path_with_spaces(self, tmp_path):
        """带空格的路径应正确解析。"""
        install = tmp_path / "My VibeOCR App"
        paths = resolve_app_paths(install, profile="production")
        assert " " in str(paths.install_root)
        assert paths.data_root == (install / "data").resolve()

    def test_default_profile_is_production(self, tmp_path):
        """不传 profile 时默认 production。"""
        install = tmp_path / "VibeOCR"
        paths = resolve_app_paths(install)
        assert paths.data_root == (install / "data").resolve()

    def test_install_root_resolved_to_absolute(self, tmp_path):
        """install_root 应是绝对路径（resolve()）。"""
        install = tmp_path / "VibeOCR"
        paths = resolve_app_paths(install, profile="production")
        assert paths.install_root.is_absolute()


class TestImportBoundary:
    def test_import_app_paths_does_not_load_pyside6(self):
        """导入 vibeocr.classic.app_paths 不应触发 PySide6 加载。"""
        # 清除已加载的模块
        for mod in list(sys.modules):
            if mod.startswith("vibeocr.classic.app_paths") or mod == "vibeocr.classic.app_paths":
                del sys.modules[mod]
        if "PySide6" in sys.modules:
            del sys.modules["PySide6"]
            # 也清除 PySide6 子模块
            for mod in list(sys.modules):
                if mod.startswith("PySide6"):
                    del sys.modules[mod]

        importlib.import_module("vibeocr.classic.app_paths")
        assert "PySide6" not in sys.modules, (
            "导入 vibeocr.classic.app_paths 不应加载 PySide6（app_paths 是 UI-free 边界）"
        )

    def test_app_paths_module_has_no_qt_imports(self):
        """app_paths 模块源码不应 import 任何 PySide6/Qt 模块。"""
        import inspect

        from vibeocr.classic import app_paths

        source = inspect.getsource(app_paths)
        assert "PySide6" not in source
        assert "from PySide6" not in source
        assert "import PySide6" not in source


class TestBackwardCompatibility:
    def test_resolve_app_paths_accepts_executable_arg(self, tmp_path):
        """resolve_app_paths 接受 executable 参数（Path 或可转换为 Path）。"""
        install = tmp_path / "VibeOCR"
        # 传 install 目录
        paths1 = resolve_app_paths(install, profile="production")
        # 传 executable 文件路径（应自动取 parent）
        exe = install / "VibeOCR.exe"
        paths2 = resolve_app_paths(exe, profile="production")
        assert paths1.install_root == paths2.install_root


def test_normalize_executable_existing_non_exe_file_takes_parent(tmp_path):
    """已存在的非 exe 文件（如 python.exe 之外的脚本）取 parent（line 82）。"""
    from vibeocr.classic.app_paths import _normalize_executable

    # 创建一个存在的非 exe 文件
    f = tmp_path / "launcher"
    f.write_text("#!/bin/sh")
    result = _normalize_executable(str(f))
    assert result == f.resolve().parent


def test_resolve_app_paths_rejects_unknown_profile(tmp_path):
    """未知 profile raise ValueError（line 105-106）。"""
    import pytest

    from vibeocr.classic.app_paths import resolve_app_paths

    with pytest.raises(ValueError, match="unsupported profile"):
        resolve_app_paths(str(tmp_path), profile="bogus")

    with pytest.raises(ValueError, match="unsupported profile"):
        resolve_app_paths(str(tmp_path), profile="")
