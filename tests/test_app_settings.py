"""AppSettings 单元测试"""

import json

import pytest

from vibeocr.utils.app_settings import AppSettings


@pytest.fixture
def config_dir(tmp_path):
    return tmp_path


@pytest.fixture
def settings(config_dir):
    return AppSettings(config_dir)


class TestAppSettingsDefaults:
    def test_show_toolbar_default_true(self, settings):
        assert settings.show_toolbar is True

    def test_auto_hide_toolbar_default_true(self, settings):
        assert settings.auto_hide_toolbar is True

    def test_hide_delay_default(self, settings):
        assert settings.hide_delay_ms == 500

    def test_toolbar_pos_default_none(self, settings):
        assert settings.toolbar_pos is None


class TestAppSettingsProperties:
    def test_set_show_toolbar(self, settings):
        settings.show_toolbar = False
        assert settings.show_toolbar is False

    def test_set_auto_hide_toolbar(self, settings):
        settings.auto_hide_toolbar = False
        assert settings.auto_hide_toolbar is False

    def test_set_toolbar_pos(self, settings):
        settings.toolbar_pos = {"x": 100, "y": 200}
        assert settings.toolbar_pos == {"x": 100, "y": 200}

    def test_set_toolbar_pos_none(self, settings):
        settings.toolbar_pos = {"x": 100, "y": 200}
        settings.toolbar_pos = None
        assert settings.toolbar_pos is None


class TestAppSettingsPersistence:
    def test_save_and_reload(self, config_dir):
        s1 = AppSettings(config_dir)
        s1.show_toolbar = False
        s1.auto_hide_toolbar = False
        s1.hide_delay_ms = 1000
        s1.toolbar_pos = {"x": 50, "y": 60}
        s1.save()

        s2 = AppSettings(config_dir)
        assert s2.show_toolbar is False
        assert s2.auto_hide_toolbar is False
        assert s2.hide_delay_ms == 1000
        assert s2.toolbar_pos == {"x": 50, "y": 60}


class TestAppSettingsBackwardCompat:
    def test_old_auto_hide_true(self, config_dir):
        """旧配置 auto_hide_toolbar=True → show_toolbar=True"""
        config_file = config_dir / "app_settings.json"
        config_file.write_text(json.dumps({"auto_hide_toolbar": True}), encoding="utf-8")
        s = AppSettings(config_dir)
        assert s.show_toolbar is True
        assert s.auto_hide_toolbar is True

    def test_old_auto_hide_false(self, config_dir):
        """旧配置 auto_hide_toolbar=False → show_toolbar=False"""
        config_file = config_dir / "app_settings.json"
        config_file.write_text(json.dumps({"auto_hide_toolbar": False}), encoding="utf-8")
        s = AppSettings(config_dir)
        assert s.show_toolbar is False
        assert s.auto_hide_toolbar is True
