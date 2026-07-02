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
    cell_box_list=None,
    ocr_polys=None,
):
    """构造模拟结果。

    cell_box_list 对应 PaddleX 真实字段：每个单元格的 [x1,y1,x2,y2]
    （原图坐标系）。PaddleX 的 SingleTableRecognitionResult 不含
    ``table_bbox`` 字段，表格整体框需从 cell_box_list 的并集推导。
    """
    table_entry = {"pred_html": pred_html, "table_region_id": 1}
    if cell_box_list is not None:
        table_entry["cell_box_list"] = cell_box_list
    res = {
        "table_res_list": [table_entry],
    }
    if ocr_texts is not None:
        ocr_entry = {
            "rec_texts": ocr_texts,
            "rec_scores": [0.9] * len(ocr_texts),
        }
        if ocr_polys is not None:
            ocr_entry["rec_polys"] = ocr_polys
        res["overall_ocr_res"] = ocr_entry
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


def test_recognize_table_filters_text_inside_table_bbox():
    """表格区域内的文字不应重复出现在 content_list 中。

    overall_ocr_res 包含整图所有文字（含表格内文字），需要过滤掉
    落在表格区域内的文本块，只保留表格外的文字。

    注意：PaddleX 的 SingleTableRecognitionResult 不含 ``table_bbox`` 字段，
    只有 ``cell_box_list``（各单元格 [x1,y1,x2,y2]，原图坐标系）。
    表格整体框需从 cell_box_list 的并集推导，否则过滤条件永远为空，
    所有文本都会被重复展示。
    """
    import numpy as np

    # 单元格覆盖 [10,10]-[500,200] 区域（与旧测试的 table_bbox 等价）
    cell_box_list = [
        [10.0, 10.0, 250.0, 100.0],
        [250.0, 10.0, 500.0, 100.0],
        [10.0, 100.0, 250.0, 200.0],
        [250.0, 100.0, 500.0, 200.0],
    ]
    # 两个文本：一个在表格内（中心点 100,50），一个在表格外（中心点 300,300）
    inside_poly = np.array([[80, 40], [120, 40], [120, 60], [80, 60]], dtype=float)
    outside_poly = np.array([[280, 290], [320, 290], [320, 310], [280, 310]], dtype=float)

    res = _make_table_result(
        ocr_texts=["表格内文字", "表格外文字"],
        cell_box_list=cell_box_list,
        ocr_polys=[inside_poly, outside_poly],
    )
    service = _FakeService([res])
    result = _recognize_table(service, image=None, options=TableRecognitionOptions())

    # 表格块
    assert any(b["type"] == "table" for b in result.content_list)
    # 表格外文字应保留
    assert any(b.get("text") == "表格外文字" for b in result.content_list)
    # 表格内文字应被过滤（已在 table 块中展示）
    assert not any(b.get("text") == "表格内文字" for b in result.content_list)


def test_recognize_table_assigns_bbox_from_cell_box_union():
    """表格块的 bbox 应从 cell_box_list 并集推导，而非 None。

    修复前 TextBlock.bbox 和 content_list[].bbox 均写死为 None，导致左侧
    画布无法绘制表格 bbox。修复后二者均应携带外接框（原图像素坐标）。
    """
    cell_box_list = [
        [10.0, 20.0, 250.0, 100.0],
        [250.0, 20.0, 500.0, 100.0],
        [10.0, 100.0, 250.0, 200.0],
        [250.0, 100.0, 500.0, 200.0],
    ]
    res = _make_table_result(cell_box_list=cell_box_list)
    service = _FakeService([res])
    result = _recognize_table(
        service, image=None, options=TableRecognitionOptions()
    )

    # TextBlock 的 bbox 应为单元格并集 [10,20,500,200]
    table_block = result.text_blocks[0]
    assert table_block.label == "table"
    assert table_block.bbox is not None
    x0, y0, x1, y1 = table_block.bbox
    assert (x0, y0, x1, y1) == (10.0, 20.0, 500.0, 200.0)

    # content_list 的 table 项也应携带 bbox
    cl = result.content_list[0]
    assert cl["type"] == "table"
    assert cl["bbox"] is not None
    assert cl["bbox"] == [10.0, 20.0, 500.0, 200.0]


def test_recognize_table_bbox_none_when_no_cell_box_list():
    """cell_box_list 缺失时表格 bbox 应为 None，但仍正常生成表格块。

    验证修复不会因缺少 cell_box_list 而崩溃或丢失表格内容。
    """
    res = _make_table_result()  # 不传 cell_box_list
    service = _FakeService([res])
    result = _recognize_table(
        service, image=None, options=TableRecognitionOptions()
    )

    assert len(result.text_blocks) == 1
    assert result.text_blocks[0].bbox is None
    assert result.content_list[0]["bbox"] is None
    assert "<table>" in result.text_blocks[0].text


def test_recognize_table_bbox_multiple_tables_no_misalignment():
    """多表格场景：每个表格 bbox 独立正确，不因列表错位而串框。

    回归保护：修复前 table_bboxes 只在 cell_box_list 有效时 append，
    与无条件创建的 text_blocks 会错位。修复后用局部变量即时赋值，
    即使某表格缺 cell_box_list 也不影响其他表格的 bbox。
    """
    # 两个表格：第一个有 cell_box_list，第二个没有
    res1 = _make_table_result(
        pred_html="<table><tr><td>A</td></tr></table>",
        cell_box_list=[[5.0, 5.0, 100.0, 50.0]],
    )
    res2 = _make_table_result(pred_html="<table><tr><td>B</td></tr></table>")
    service = _FakeService([res1, res2])
    result = _recognize_table(
        service, image=None, options=TableRecognitionOptions()
    )

    table_blocks = [b for b in result.text_blocks if b.label == "table"]
    assert len(table_blocks) == 2
    # 第一个表格有 bbox
    assert table_blocks[0].bbox == (5.0, 5.0, 100.0, 50.0)
    # 第二个表格 bbox 为 None（无 cell_box_list），但内容正常
    assert table_blocks[1].bbox is None
    assert "B" in table_blocks[1].text


