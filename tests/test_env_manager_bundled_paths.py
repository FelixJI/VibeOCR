"""打包态捆绑只读资源（resources/、CHANGELOG.md）路径解析测试。

背景：PyInstaller --onedir 的 --add-data 把只读捆绑数据放进 ``_internal/``
（= 运行时 ``sys._MEIPASS``）。早期代码用 ``get_project_root()``（打包态返回
exe 同级）去找这些资源，而 exe 同级只放运行时创建的可写目录（python/、config、
logs/），导致打包态读不到 resources/ 与 CHANGELOG.md——表现为关于页"暂无更新
日志"、状态栏图标不显示等。

本测试验证新增的 SSOT 辅助函数在 dev/frozen 两态下都解析正确。
"""

from __future__ import annotations

import sys

import pytest

from vibeocr.backend.env_manager import (
    get_bundled_changelog_path,
    get_bundled_resources_dir,
)


@pytest.fixture
def _unfrozen(monkeypatch):
    """确保处于开发态（非 frozen），并清除可能残留的 _MEIPASS。"""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


@pytest.fixture
def _frozen(monkeypatch, tmp_path):
    """模拟 PyInstaller 打包态：frozen=True 且 _MEIPASS 指向临时目录。

    tmp_path 充当 PyInstaller 的解包目录（onedir 布局下的 _internal/），
    --add-data 的资源（resources/、CHANGELOG.md）在此处被读取。
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    return tmp_path


class TestGetBundledResourcesDir:
    def test_dev_mode_returns_project_root_resources(self, _unfrozen):
        """开发态：resources 位于仓库根下。"""
        d = get_bundled_resources_dir()
        assert d.name == "resources"
        # client workspace 的 env_manager.py 向上定位仓库根，resources 在仓库根
        assert (d / "app_icon.ico").exists()

    def test_frozen_mode_returns_meipass_resources(self, _frozen):
        """打包态：resources 位于 sys._MEIPASS（_internal/）下。"""
        d = get_bundled_resources_dir()
        assert d == _frozen / "resources"


class TestGetBundledChangelogPath:
    def test_dev_mode_finds_changelog(self, _unfrozen):
        """开发态：CHANGELOG.md 位于仓库根。"""
        p = get_bundled_changelog_path()
        assert p is not None
        assert p.name == "CHANGELOG.md"
        assert p.exists()

    def test_frozen_mode_reads_meipass_changelog(self, _frozen):
        """打包态：CHANGELOG.md 由 --add-data 打入 _MEIPASS。"""
        (_frozen / "CHANGELOG.md").write_text("# frozen changelog", encoding="utf-8")

        p = get_bundled_changelog_path()
        assert p is not None
        assert p == _frozen / "CHANGELOG.md"
        assert p.read_text(encoding="utf-8") == "# frozen changelog"

    def test_frozen_mode_returns_none_when_absent(self, _frozen):
        """打包态且各处都找不到时返回 None（调用方回退占位文案）。"""
        assert get_bundled_changelog_path() is None
