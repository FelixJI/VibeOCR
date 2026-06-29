# tests/views/tabs/test_about_tab.py
"""关于标签页测试"""

import sys

import pytest

from vibeocr import __version__


@pytest.fixture
def about_tab(qtbot):
    from vibeocr.views.tabs.about_tab import AboutTab

    tab = AboutTab()
    qtbot.addWidget(tab)
    return tab


class TestAboutTab:
    def test_version_label_shows_current_version(self, about_tab):
        assert __version__ in about_tab._version_label.text()

    def test_app_name_displayed(self, about_tab):
        text = about_tab._name_label.text()
        assert "VibeOCR" in text

    def test_changelog_browser_exists(self, about_tab):
        assert about_tab._changelog_browser is not None

    def test_changelog_has_content(self, about_tab):
        html = about_tab._changelog_browser.toHtml()
        assert len(html) > 0


class TestAboutTabFrozen:
    """打包态（PyInstaller frozen）回归测试。

    客户端安装后关于页显示"暂无更新日志"的根因：CHANGELOG.md 由 --add-data
    打入 sys._MEIPASS（_internal/），而旧代码用 get_project_root()（exe 同级）
    查找，永远找不到。这里模拟 frozen 态验证走 _MEIPASS 能正确读到内容。
    """

    def test_changelog_loaded_from_meipass(self, qtbot, monkeypatch, tmp_path):
        # 把假 CHANGELOG.md 放进模拟的 _MEIPASS
        (tmp_path / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [9.9.9] - 2099-01-01\n\n### Added\n- frozen-test\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        from vibeocr.views.tabs.about_tab import AboutTab

        tab = AboutTab()
        qtbot.addWidget(tab)

        html = tab._changelog_browser.toHtml()
        assert "9.9.9" in html, "打包态应从 _MEIPASS 读到 CHANGELOG，而非显示占位文案"
        assert "frozen-test" in html

    def test_changelog_shows_placeholder_when_absent(self, qtbot, monkeypatch, tmp_path):
        """打包态且各处都无 CHANGELOG.md 时回退占位文案。"""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        from vibeocr.views.tabs.about_tab import AboutTab

        tab = AboutTab()
        qtbot.addWidget(tab)

        text = tab._changelog_browser.toPlainText()
        assert "暂无更新日志" in text
