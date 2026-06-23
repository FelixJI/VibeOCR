"""ConfigManager 新增字段测试（pipeline_ttl_seconds / max_heavy_pipelines）。"""

import pytest

from vibeocr.managers.config_manager import ConfigManager


@pytest.fixture
def cm(tmp_path):
    """每个测试独立的 ConfigManager 单例（隔离 tmp_path）。"""
    ConfigManager._instance = None
    return ConfigManager.instance(project_root=tmp_path)


def test_get_pipeline_ttl_default(cm):
    """默认 TTL 为 300 秒。"""
    assert cm.get_pipeline_ttl_seconds() == 300


def test_set_pipeline_ttl(cm):
    """设置并读取 TTL。"""
    assert cm.set_pipeline_ttl_seconds(600)
    assert cm.get_pipeline_ttl_seconds() == 600


def test_set_pipeline_ttl_clamps_negative(cm):
    """负值夹到 0（禁用）。"""
    cm.set_pipeline_ttl_seconds(-10)
    assert cm.get_pipeline_ttl_seconds() == 0


def test_set_pipeline_ttl_zero_disables(cm):
    """0 = 禁用。"""
    cm.set_pipeline_ttl_seconds(0)
    assert cm.get_pipeline_ttl_seconds() == 0


def test_get_max_heavy_pipelines_default_none(cm):
    """默认 None（按显存自动分档）。"""
    assert cm.get_max_heavy_pipelines() is None


def test_set_max_heavy_pipelines(cm):
    """设置并读取 max_heavy_pipelines。"""
    assert cm.set_max_heavy_pipelines(2)
    assert cm.get_max_heavy_pipelines() == 2


def test_set_max_heavy_pipelines_none(cm):
    """可以重置回 None。"""
    cm.set_max_heavy_pipelines(3)
    cm.set_max_heavy_pipelines(None)
    assert cm.get_max_heavy_pipelines() is None
