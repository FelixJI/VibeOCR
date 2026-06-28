# tests/views/tabs/test_about_tab.py
"""关于标签页测试"""

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
