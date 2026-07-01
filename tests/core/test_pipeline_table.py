# tests/core/test_pipeline_table.py
from vibeocr.core.pipelines.pipeline_table import (
    TABLE_RECOGNITION_SPEC,
    TableRecognitionOptions,
    _recognize_table,
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


class _DictResult(dict):
    """模拟 PaddleX TableRecognitionResult：dict 子类，键需下标访问。

    真实结构 keys: input_path, page_index, doc_preprocessor_res, layout_det_res,
    overall_ocr_res, table_res_list, model_settings。
    表格内容在 table_res_list[i].pred_html，不是 parsing_res_list。
    """


class _FakePipeline:
    def __init__(self, result_list):
        self._result_list = result_list

    def predict(self, input, **kwargs):
        return list(self._result_list)


class _FakeService:
    def __init__(self, result_list):
        self._pipeline = _FakePipeline(result_list)

    def get_or_create_pipeline(self, name):
        return self._pipeline


def _make_table_result(
    pred_html="<html><body><table><tr><td>Name</td><td>Age</td></tr></table></body></html>",
    ocr_texts=None,
):
    res = {
        "table_res_list": [{"pred_html": pred_html, "table_region_id": 1}],
    }
    if ocr_texts is not None:
        res["overall_ocr_res"] = {
            "rec_texts": ocr_texts,
            "rec_scores": [0.9] * len(ocr_texts),
        }
    return _DictResult(res)


def test_recognize_table_extracts_pred_html_from_table_res_list():
    """回归：表格内容在 table_res_list[].pred_html，不是 parsing_res_list。

    修复前代码读 parsing_res_list（表格管道无此字段），永远返回空，
    表现为"未识别到文字"。
    """
    service = _FakeService([_make_table_result()])
    result = _recognize_table(service, image=None, options=TableRecognitionOptions())

    assert result.pipeline_type == "TABLE_RECOGNITION"
    assert len(result.text_blocks) == 1
    assert result.text_blocks[0].label == "table"
    assert "<table>" in result.raw_text
    assert len(result.content_list) == 1
    assert result.content_list[0]["type"] == "table"
    # markdown 表格已转换
    assert "Name" in result.markdown_text and "Age" in result.markdown_text


def test_recognize_table_extracts_surrounding_ocr_text():
    """overall_ocr_res 中的文字（表格外）也应被提取。"""
    res = _make_table_result(ocr_texts=["标题", "脚注"])
    service = _FakeService([res])
    result = _recognize_table(service, image=None, options=TableRecognitionOptions())

    texts = [b.text for b in result.text_blocks]
    assert "标题" in texts
    assert "脚注" in texts
    # 仍含表格块
    assert any(b.label == "table" for b in result.text_blocks)


def test_recognize_table_empty_result():
    """无表格块时返回空但不报错。"""
    service = _FakeService([_DictResult({"table_res_list": []})])
    result = _recognize_table(service, image=None, options=TableRecognitionOptions())
    assert result.raw_text == ""
    assert result.content_list == []


