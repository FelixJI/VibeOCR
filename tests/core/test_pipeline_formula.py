# tests/core/test_pipeline_formula.py
from vibeocr.core.pipelines.pipeline_formula import FormulaRecognitionOptions, FORMULA_RECOGNITION_SPEC


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
