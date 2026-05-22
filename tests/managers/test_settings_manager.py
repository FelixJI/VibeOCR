"""SettingsManager 测试"""

import pytest

from vibeocr.managers.settings_manager import SettingsManager


class TestSettingsManager:
    """SettingsManager 测试"""

    @pytest.fixture
    def manager(self, qapp, tmp_path):
        """创建 SettingsManager 实例"""
        return SettingsManager(tmp_path)

    def test_manager_creation(self, manager):
        """测试管理器创建"""
        assert manager._project_root is not None
        assert manager._config_dir is not None

    def test_config_dir_created(self, tmp_path):
        """测试配置目录自动创建"""
        config_dir = tmp_path / "config"
        assert not config_dir.exists()

        manager = SettingsManager(tmp_path)

        assert manager._config_dir.exists()

    def test_get_preload_config_default(self, manager):
        """测试默认预加载配置"""
        config = manager.get_preload_config()

        assert "enabled" in config
        assert "pipelines" in config
        assert config["enabled"] is False
        assert config["pipelines"] == []

    def test_get_preload_config_with_pipelines(self, manager):
        """测试有管道时的预加载配置"""
        manager._cm.set_preload_pipelines(["OCR", "PP-StructureV3"])

        config = manager.get_preload_config()

        assert config["enabled"] is True
        assert "OCR" in config["pipelines"]
        assert "PP-StructureV3" in config["pipelines"]
