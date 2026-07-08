"""scripts/update_replacer.py 单元测试 —— 架构重构新增逻辑。

加载方式遵循仓库脚本测试惯例（参考 tests/test_updater_main.py）：
importlib 按路径加载，避免依赖 src/vibeocr 包。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "update_replacer.py"


@pytest.fixture(scope="module")
def replacer():
    """按路径加载 scripts/update_replacer.py（纯 stdlib 模块）。"""
    spec = importlib.util.spec_from_file_location("update_replacer_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["update_replacer_test"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("update_replacer_test", None)


class TestDetectSelfExeNames:
    """_detect_self_exe_names：根据 updater 自身位置判断是否需避让 updater.exe。

    新架构下 updater 从暂存目录（data/cache/update/）运行，不在 app_dir，
    故 app_dir/updater.exe(旧) 无人运行、可直接覆盖，无需避让。
    旧路径（过渡期）updater 自身在 app_dir，仍需避让自己。
    """

    def test_updater_in_app_dir_needs_avoidance(self, replacer, monkeypatch, tmp_path):
        """旧路径：updater 自身在 app_dir → 返回 ('updater.exe',)。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        updater_exe = app_dir / "updater.exe"
        updater_exe.write_bytes(b"fake")
        monkeypatch.setattr("sys.argv", [str(updater_exe)])
        result = replacer._detect_self_exe_names(app_dir)
        assert result == ("updater.exe",)

    def test_updater_in_staging_no_avoidance(self, replacer, monkeypatch, tmp_path):
        """新路径：updater 在暂存目录（不在 app_dir）→ 返回 ()。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        staging = tmp_path / "data" / "cache" / "update"
        staging.mkdir(parents=True)
        updater_exe = staging / "updater.exe"
        updater_exe.write_bytes(b"fake")
        monkeypatch.setattr("sys.argv", [str(updater_exe)])
        result = replacer._detect_self_exe_names(app_dir)
        assert result == ()

    def test_no_argv0_fallback_to_avoidance(self, replacer, monkeypatch, tmp_path):
        """sys.argv[0] 无法解析为 app_dir 内文件时，保守返回旧路径（需避让）。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        monkeypatch.setattr("sys.argv", [""])  # 空 argv[0]
        result = replacer._detect_self_exe_names(app_dir)
        assert result == ("updater.exe",)

    def test_non_windows_no_avoidance(self, replacer, monkeypatch, tmp_path):
        """非 Windows：无 PE 映射锁问题，直接返回 ()（不避让）。

        新旧路径判定仅 Windows 有意义（PE 锁是 Windows 独有）。
        """
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        updater_exe = app_dir / "updater.exe"
        updater_exe.write_bytes(b"fake")
        monkeypatch.setattr("sys.argv", [str(updater_exe)])
        monkeypatch.setattr("os.name", "posix")
        result = replacer._detect_self_exe_names(app_dir)
        assert result == ()
