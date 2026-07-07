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


class TestAboutTabRepoUrls:
    """关于页 URL 应来自 env_config SSOT，且指向正确的 owner/repo"""

    def test_github_url_points_to_repo_root(self):
        """关于页 GitHub 链接应指向仓库主页（无 /releases 后缀）"""
        from vibeocr.views.tabs import about_tab

        assert about_tab._GITHUB_URL == "https://github.com/FelixJI/VibeOCR"

    def test_gitee_url_points_to_repo_root(self):
        """关于页 Gitee 链接应指向仓库主页（无 /releases 后缀）"""
        from vibeocr.views.tabs import about_tab

        assert about_tab._GITEE_URL == "https://gitee.com/felixjii/vibeocr"

    def test_urls_imported_from_env_config_ssot(self):
        """URL 常量应与 env_config repo base SSOT 完全一致"""
        from vibeocr.services.env_config import (
            GITHUB_REPO_BASE,
            GITEE_REPO_BASE,
        )
        from vibeocr.views.tabs import about_tab

        assert about_tab._GITHUB_URL == GITHUB_REPO_BASE
        assert about_tab._GITEE_URL == GITEE_REPO_BASE


class TestAboutTabUpdateTiming:
    """上次更新耗时详情卡片测试。

    替换器把各阶段耗时写入 progress.json（见 update_replacer._StageTimer），
    关于页据此展示。这里覆盖：无文件、有文件、慢/失败标记三类场景。
    """

    def test_no_card_when_progress_absent(self, qtbot, monkeypatch, tmp_path):
        """progress.json 不存在时（首次安装/未更新过）不应生成耗时卡片。"""
        from vibeocr.views.tabs import about_tab

        monkeypatch.setattr(
            about_tab, "get_update_progress_path", lambda: tmp_path / "missing.json"
        )
        assert about_tab._load_update_progress() is None

        tab = about_tab.AboutTab()
        qtbot.addWidget(tab)
        # 无 progress.json 时 _create_update_timing_card 返回 None
        assert tab._create_update_timing_card() is None

    def test_card_shows_stages_when_progress_exists(self, qtbot, monkeypatch, tmp_path):
        """progress.json 存在时卡片应渲染各阶段名与耗时。"""
        import json

        from vibeocr.views.tabs import about_tab

        progress_path = tmp_path / "progress.json"
        progress_path.write_text(
            json.dumps(
                {
                    "version": "0.4.15",
                    "success": True,
                    "total_seconds": 27.5,
                    "stages": [
                        {
                            "name": "解压更新包",
                            "seconds": 15.2,
                            "depth": 0,
                            "slow": True,
                            "failed": False,
                        },
                        {
                            "name": "启动新版主程序",
                            "seconds": 0.3,
                            "depth": 0,
                            "slow": False,
                            "failed": False,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(about_tab, "get_update_progress_path", lambda: progress_path)

        data = about_tab._load_update_progress()
        assert data is not None
        assert data["version"] == "0.4.15"

        tab = about_tab.AboutTab()
        qtbot.addWidget(tab)
        card = tab._create_update_timing_card()
        assert card is not None
        qtbot.addWidget(card)  # 持有 card，防止子控件被 GC
        # card 是 QFrame，内含 QTextBrowser；遍历子控件取 html
        from PySide6.QtWidgets import QTextBrowser

        browsers = card.findChildren(QTextBrowser)
        assert browsers, "耗时卡片应包含一个 QTextBrowser 展示阶段表"
        html = browsers[0].toHtml()
        assert "解压更新包" in html
        assert "启动新版主程序" in html
        assert "15.20" in html

    def test_slow_and_failed_flags_marked(self, qtbot, monkeypatch, tmp_path):
        """慢阶段（slow=True）和失败阶段（failed=True）应带标记。"""
        import json

        from vibeocr.views.tabs import about_tab

        progress_path = tmp_path / "progress.json"
        progress_path.write_text(
            json.dumps(
                {
                    "version": "0.4.16",
                    "success": False,
                    "total_seconds": 30.0,
                    "stages": [
                        {
                            "name": "复制新文件",
                            "seconds": 25.0,
                            "depth": 1,
                            "slow": True,
                            "failed": False,
                        },
                        {
                            "name": "校验 SHA256",
                            "seconds": 1.0,
                            "depth": 0,
                            "slow": False,
                            "failed": True,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(about_tab, "get_update_progress_path", lambda: progress_path)

        tab = about_tab.AboutTab()
        qtbot.addWidget(tab)
        from PySide6.QtWidgets import QTextBrowser

        # card 必须赋值持有，否则其子控件 QTextBrowser 会被 GC（C++ 对象已删除）。
        card = tab._create_update_timing_card()
        qtbot.addWidget(card)
        browsers = card.findChildren(QTextBrowser)
        html = browsers[0].toHtml()
        assert "[慢]" in html, "慢阶段应标 [慢]"
        assert "[失败]" in html, "失败阶段应标 [失败]"
