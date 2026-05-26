# tests/core/test_pipeline_table.py
from vibeocr.core.pipelines.pipeline_table import (
    TableRecognitionOptions,
    TABLE_RECOGNITION_SPEC,
)


def test_table_options_defaults():
    opts = TableRecognitionOptions()
    assert opts.pipeline == "TABLE_RECOGNITION"
    assert opts.use_wireless_table is True
    assert opts.formula_recognition_batch_size == 1


def test_table_options_to_dict():
    opts = TableRecognitionOptions(use_wireless_table=False)
    d = opts.to_dict()
    assert d["use_wireless_table"] is False
    assert d["pipeline"] == "TABLE_RECOGNITION"


def test_table_options_from_dict():
    d = {"pipeline": "TABLE_RECOGNITION", "use_wireless_table": False}
    opts = TableRecognitionOptions.from_dict(d)
    assert opts.use_wireless_table is False


def test_table_spec():
    assert TABLE_RECOGNITION_SPEC.name == "TABLE_RECOGNITION"
    assert TABLE_RECOGNITION_SPEC.display_name == "表格识别"
    assert TABLE_RECOGNITION_SPEC.options_class is TableRecognitionOptions
