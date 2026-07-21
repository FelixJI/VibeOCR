"""ConfigManager 测试（pipeline_ttls 字典 API + 迁移 / max_heavy_pipelines）。"""

import pytest

from vibeocr.managers.config_manager import ConfigManager


@pytest.fixture
def cm(tmp_path):
    """每个测试独立的 ConfigManager 单例（隔离 tmp_path）。"""
    ConfigManager._instance = None
    return ConfigManager.instance(project_root=tmp_path)


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


def test_log_level_defaults_to_info_and_persists(cm):
    assert cm.get_log_level() == "INFO"
    assert cm.set_log_level("debug")
    assert cm.get_log_level() == "DEBUG"


def test_invalid_log_level_falls_back_to_info(cm):
    assert cm.set_log_level("trace")
    assert cm.get_log_level() == "INFO"


# ---------------- per-pipeline TTL dict API + migration ----------------


_DEFAULT_TTLS = {
    "OCR": 0,
    "TABLE_RECOGNITION": 0,
    "FORMULA_RECOGNITION": 0,
    "PP-StructureV3": 300,
    "MinerU": 0,
    "PaddleOCR-VL": 300,
}


def test_migrate_legacy_single_ttl_value(cm, tmp_path):
    """旧 pipeline_ttl_seconds=600 → 重管道 600，轻管道 0，MinerU 0。"""
    config = tmp_path / "config" / "app_settings.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('{"pipeline_ttl_seconds": 600}', encoding="utf-8")

    ttls = cm.get_pipeline_ttls()
    assert ttls["OCR"] == 0
    assert ttls["TABLE_RECOGNITION"] == 0
    assert ttls["FORMULA_RECOGNITION"] == 0
    assert ttls["PP-StructureV3"] == 600
    assert ttls["PaddleOCR-VL"] == 600
    assert ttls["MinerU"] == 0

    # 旧字段已删除，新字段已写入
    import json

    data = json.loads(config.read_text(encoding="utf-8"))
    assert "pipeline_ttl_seconds" not in data
    assert "pipeline_ttls" in data


def test_default_ttls_for_fresh_user(cm):
    """新用户：轻=0, MinerU=0, paddle 重=300。"""
    ttls = cm.get_pipeline_ttls()
    assert ttls == _DEFAULT_TTLS


def test_partial_dict_filled_with_defaults(cm, tmp_path):
    """只配了部分管道，缺失的补默认。"""
    config = tmp_path / "config" / "app_settings.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        '{"pipeline_ttls": {"OCR": 100, "PP-StructureV3": 600}}',
        encoding="utf-8",
    )

    ttls = cm.get_pipeline_ttls()
    assert len(ttls) == 6
    assert ttls["OCR"] == 100
    assert ttls["PP-StructureV3"] == 600
    assert ttls["TABLE_RECOGNITION"] == 0  # 补默认
    assert ttls["MinerU"] == 0
    assert ttls["PaddleOCR-VL"] == 300  # 补默认


def test_set_pipeline_ttl_single(cm):
    """set_pipeline_ttl 改单个管道。"""
    assert cm.set_pipeline_ttl("OCR", 180) is True
    assert cm.get_pipeline_ttls()["OCR"] == 180


def test_set_pipeline_ttl_clamps_negative(cm):
    """负值夹到 0（持久）。"""
    assert cm.set_pipeline_ttl("OCR", -5) is True
    assert cm.get_pipeline_ttls()["OCR"] == 0


def test_set_pipeline_ttls_batch_writes_all(cm):
    """set_pipeline_ttls 批量写入。"""
    ttls = dict(_DEFAULT_TTLS)
    ttls["OCR"] = 60
    ttls["MinerU"] = 200
    assert cm.set_pipeline_ttls(ttls) is True
    result = cm.get_pipeline_ttls()
    assert result["OCR"] == 60
    assert result["MinerU"] == 200
    assert result["PP-StructureV3"] == 300  # 未变


def test_get_pipeline_ttls_rejects_non_int_values(cm, tmp_path):
    """损坏的 TTL 值（字符串/布尔）回退到默认。"""
    config = tmp_path / "config" / "app_settings.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        '{"pipeline_ttls": {"OCR": "oops", "PP-StructureV3": true, "MinerU": 7}}',
        encoding="utf-8",
    )

    ttls = cm.get_pipeline_ttls()
    assert ttls["OCR"] == 0  # 字符串 → 默认
    assert ttls["PP-StructureV3"] == 300  # bool → 默认
    assert ttls["MinerU"] == 7  # 合法 int 保留


def test_legacy_field_not_migrated_when_dict_present(cm, tmp_path):
    """新旧字段并存时，保留 dict，不迁移（dict 优先）。"""
    import json

    config = tmp_path / "config" / "app_settings.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        '{"pipeline_ttl_seconds": 600, "pipeline_ttls": {"OCR": 50}}',
        encoding="utf-8",
    )

    ttls = cm.get_pipeline_ttls()
    assert ttls["OCR"] == 50  # 来自 dict，不是 legacy
    assert ttls["PP-StructureV3"] == 300  # dict 缺失，补默认（不取 legacy 600）

    # dict 模式下 legacy 字段不被删（避免误删用户手动数据）
    data = json.loads(config.read_text(encoding="utf-8"))
    assert "pipeline_ttl_seconds" in data
