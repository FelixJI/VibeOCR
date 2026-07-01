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

    def test_set_preload_pipelines_normalizes_case(self, manager):
        """回归 bug：保存时应把小写管道名规范化为枚举标准值。

        历史配置可能存了小写 'table_recognition'（枚举标准值为
        'TABLE_RECOGNITION'），导致 UI 勾选状态恢复失败（pipeline.value
        in saved 大小写不匹配），用户以为没勾选但实际加载了该管道。
        """
        manager._cm.set_preload_pipelines(["ocr", "table_recognition"])

        pipelines = manager._cm.get_preload_pipelines()

        # 小写应被规范化为大写枚举值
        assert "OCR" in pipelines
        assert "TABLE_RECOGNITION" in pipelines
        assert "ocr" not in pipelines
        assert "table_recognition" not in pipelines

    def test_set_preload_pipelines_keeps_unknown_as_is(self, manager):
        """未知管道名（不在枚举中）应原样保留，不丢失。"""
        manager._cm.set_preload_pipelines(["OCR", "some_future_pipeline"])

        pipelines = manager._cm.get_preload_pipelines()

        assert "OCR" in pipelines
        assert "some_future_pipeline" in pipelines
