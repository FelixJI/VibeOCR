"""SettingsManager 测试"""

import json

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
        assert manager.llm_config is None

    def test_manager_has_required_signals(self, manager):
        """测试管理器有必需的信号"""
        assert hasattr(manager, "llm_config_loaded")
        assert hasattr(manager, "llm_config_saved")
        assert hasattr(manager, "templates_loaded")
        assert hasattr(manager, "template_added")
        assert hasattr(manager, "template_deleted")

    def test_config_dir_created(self, tmp_path):
        """测试配置目录自动创建"""
        config_dir = tmp_path / "config"
        assert not config_dir.exists()

        manager = SettingsManager(tmp_path)

        assert manager._config_dir.exists()

    def test_load_llm_config_creates_default(self, manager):
        """测试加载 LLM 配置创建默认值"""
        config = manager.load_llm_config()

        assert config is not None
        assert manager.llm_config is not None

    def test_load_llm_config_from_file(self, manager):
        """测试从文件加载 LLM 配置"""
        # 创建配置文件
        config_data = {
            "service_url": "https://api.example.com",
            "model_name": "test-model",
            "api_key": "test-key",
        }
        config_path = manager._config_dir / "llm_config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f)

        config = manager.load_llm_config()

        assert config.service_url == "https://api.example.com"
        assert config.model_name == "test-model"
        assert config.api_key == "test-key"

    def test_save_llm_config(self, manager):
        """测试保存 LLM 配置"""
        # 先加载默认配置
        manager.load_llm_config()

        # 更新并保存
        manager.update_llm_config(
            service_url="https://new.api.com",
            model_name="new-model",
        )
        result = manager.save_llm_config()

        assert result is True

        # 验证文件内容
        config_path = manager._config_dir / "llm_config.json"
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["service_url"] == "https://new.api.com"
        assert data["model_name"] == "new-model"

    def test_load_templates(self, manager):
        """测试加载模板列表"""
        template_names = manager.load_templates()

        # 应该至少有默认模板
        assert len(template_names) > 0
        assert isinstance(template_names, list)

    def test_load_templates_with_custom(self, manager):
        """测试加载包含自定义模板的列表"""
        # 创建自定义模板文件
        templates_data = [{"name": "自定义模板", "keys": ["key1", "key2"]}]
        config_path = manager._config_dir / "templates.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(templates_data, f)

        template_names = manager.load_templates()

        # 应该包含自定义模板
        custom_found = any("[自定义]" in name for name in template_names)
        assert custom_found

    def test_add_template(self, manager):
        """测试添加模板"""
        result = manager.add_template("新模板", ["key1", "key2"])

        assert result is True

        # 验证文件内容
        config_path = manager._config_dir / "templates.json"
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 1
        assert data[0]["name"] == "新模板"
        assert data[0]["keys"] == ["key1", "key2"]

    def test_add_template_duplicate_name(self, manager):
        """测试添加重复名称的模板"""
        # 先添加一个模板
        manager.add_template("测试模板", ["key1"])

        # 尝试添加同名模板
        result = manager.add_template("测试模板", ["key2"])

        assert result is False

    def test_delete_template(self, manager):
        """测试删除模板"""
        # 先添加模板
        manager.add_template("要删除的模板", ["key1"])

        # 删除模板
        result = manager.delete_template("要删除的模板")

        assert result is True

        # 验证文件内容
        config_path = manager._config_dir / "templates.json"
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 0

    def test_delete_template_not_found(self, manager):
        """测试删除不存在的模板"""
        result = manager.delete_template("不存在的模板")

        assert result is False

    def test_get_template_keys_default(self, manager):
        """测试获取默认模板的键"""
        manager.load_templates()

        # 获取第一个默认模板的名称
        from vibeocr.models.extraction_template import DEFAULT_TEMPLATES

        if DEFAULT_TEMPLATES:
            first_template_name = DEFAULT_TEMPLATES[0].name
            keys = manager.get_template_keys(first_template_name)
            assert keys is not None
            assert isinstance(keys, list)
        else:
            # 如果没有默认模板，跳过测试
            pytest.skip("没有默认模板")

    def test_get_template_keys_custom(self, manager):
        """测试获取自定义模板的键"""
        # 添加自定义模板
        manager.add_template("测试键模板", ["key_a", "key_b"])
        manager.load_templates()

        keys = manager.get_template_keys("测试键模板")

        assert keys is not None
        assert keys == ["key_a", "key_b"]

    def test_get_template_keys_not_found(self, manager):
        """测试获取不存在模板的键"""
        manager.load_templates()

        keys = manager.get_template_keys("不存在的模板")

        assert keys is None


class TestSettingsManagerSignals:
    """SettingsManager 信号测试"""

    @pytest.fixture
    def manager(self, qapp, tmp_path):
        """创建 SettingsManager 实例"""
        return SettingsManager(tmp_path)

    def test_llm_config_loaded_signal(self, manager):
        """测试 LLM 配置加载信号"""
        signal_received = []

        def on_loaded(config):
            signal_received.append(config)

        manager.llm_config_loaded.connect(on_loaded)
        manager.load_llm_config()

        assert len(signal_received) == 1
        assert signal_received[0] is not None

    def test_llm_config_saved_signal(self, manager):
        """测试 LLM 配置保存信号"""
        signal_received = []

        def on_saved():
            signal_received.append(True)

        manager.llm_config_saved.connect(on_saved)
        manager.load_llm_config()
        manager.save_llm_config()

        assert len(signal_received) == 1

    def test_templates_loaded_signal(self, manager):
        """测试模板加载信号"""
        signal_received = []

        def on_loaded(names):
            signal_received.extend(names)

        manager.templates_loaded.connect(on_loaded)
        manager.load_templates()

        assert len(signal_received) > 0

    def test_template_added_signal(self, manager):
        """测试模板添加信号"""
        signal_received = []

        def on_added(name):
            signal_received.append(name)

        manager.template_added.connect(on_added)
        manager.add_template("信号测试模板", ["key1"])

        assert "信号测试模板" in signal_received

    def test_template_deleted_signal(self, manager):
        """测试模板删除信号"""
        signal_received = []

        def on_deleted(name):
            signal_received.append(name)

        manager.template_deleted.connect(on_deleted)
        manager.add_template("待删除模板", ["key1"])
        manager.delete_template("待删除模板")

        assert "待删除模板" in signal_received
