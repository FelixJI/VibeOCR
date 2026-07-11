# tests/core/test_pipeline_formula.py
from vibeocr.core.pipelines.pipeline_formula import (
    FORMULA_RECOGNITION_SPEC,
    FormulaRecognitionOptions,
    _recognize_formula,
)


def test_formula_options_defaults():
    opts = FormulaRecognitionOptions()
    assert opts.pipeline == "FORMULA_RECOGNITION"
    assert opts.formula_recognition_batch_size == 1
    assert opts.formula_recognition_model_name is None


def test_formula_options_to_dict():
    opts = FormulaRecognitionOptions(formula_recognition_batch_size=4)
    d = opts.to_dict()
    assert d["formula_recognition_batch_size"] == 4
    assert d["pipeline"] == "FORMULA_RECOGNITION"


def test_formula_spec():
    assert FORMULA_RECOGNITION_SPEC.name == "FORMULA_RECOGNITION"
    assert FORMULA_RECOGNITION_SPEC.display_name == "公式识别"
    assert FORMULA_RECOGNITION_SPEC.options_class is FormulaRecognitionOptions


class _DictResult(dict):
    """模拟 PaddleX 结果：dict 子类，parsing_res_list 是 dict key。"""


class _LayoutBlock:
    """模拟 paddlex LayoutBlock（普通对象，属性访问）。"""

    def __init__(self, label, content, bbox=None, order_index=-1):
        self.label = label
        self.content = content
        self.bbox = bbox or [1, 2, 3, 4]
        self.order_index = order_index


class _FakePipeline:
    def __init__(self, result_list):
        self._result_list = result_list

    def predict(self, input, **kwargs):  # noqa: A002 — 模拟 PaddleOCR API（input 关键字参数）
        return list(self._result_list)


class _FakeService:
    def __init__(self, result_list):
        self._pipeline = _FakePipeline(result_list)

    def get_or_create_pipeline(self, name):
        return self._pipeline


def test_recognize_formula_extracts_from_dict_result():
    """回归：parsing_res_list 必须用下标访问（dict 子类）。

    修复前 getattr(res, "parsing_res_list", []) 对 dict 子类恒返回 []，
    导致公式识别永远返回空。
    """
    res = _DictResult(
        {
            "doc_preprocessor_res": None,
            "parsing_res_list": [
                _LayoutBlock(label="formula", content=r"a^2 + b^2"),
                _LayoutBlock(label="text", content="not a formula"),
            ],
        }
    )
    service = _FakeService([res])
    result = _recognize_formula(
        service, image=None, options=FormulaRecognitionOptions()
    )

    assert result.pipeline_type == "FORMULA_RECOGNITION"
    # 仅提取 label=="formula" 的块
    assert len(result.text_blocks) == 1
    assert result.text_blocks[0].label == "formula"
    assert "a^2 + b^2" in result.raw_text
    assert "$$" in result.markdown_text

