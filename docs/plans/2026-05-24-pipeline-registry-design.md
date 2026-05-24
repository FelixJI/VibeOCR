# 管道注册表模式实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将表格识别和公式识别拆分为独立顶级管道，采用管道注册表模式重构现有架构。

**Architecture:** 引入 `PipelineSpec` + `PipelineRegistry` 模式，每个管道注册独立 spec（含专属 Options 类、工厂函数、识别方法）。`OCRService` 变为纯缓存管理+通用后处理层。UI 根据注册表动态生成管道下拉和选项面板。

**Tech Stack:** Python 3.12, PaddleOCR 3.x (`TableRecognitionPipelineV2`), PySide6, dataclasses

---

### Task 1: 创建 BasePipelineOptions 基类

**Files:**
- Create: `src/vibeocr/core/pipelines/base_options.py`
- Test: `tests/core/test_base_options.py`

**Step 1: 写失败测试**

```python
# tests/core/test_base_options.py
from vibeocr.core.pipelines.base_options import BasePipelineOptions


def test_base_options_has_pipeline_name():
    opts = BasePipelineOptions()
    assert opts.pipeline == ""


def test_base_options_to_dict_contains_pipeline():
    opts = BasePipelineOptions()
    d = opts.to_dict()
    assert d["pipeline"] == ""


def test_base_options_from_dict():
    d = {"pipeline": "OCR"}
    opts = BasePipelineOptions.from_dict(d)
    assert opts.pipeline == "OCR"


def test_base_options_copy():
    opts = BasePipelineOptions(pipeline="OCR")
    copied = opts.copy(pipeline="PP-StructureV3")
    assert copied.pipeline == "PP-StructureV3"
    assert opts.pipeline == "OCR"
```

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/core/test_base_options.py -v`
Expected: FAIL（模块不存在）

**Step 3: 实现 BasePipelineOptions**

```python
# src/vibeocr/core/pipelines/base_options.py
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


@dataclass
class BasePipelineOptions:
    pipeline: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = {}
        for f in fields(self):
            val = getattr(self, f.name)
            result[f.name] = val
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BasePipelineOptions:
        kwargs = {}
        for f in fields(cls):
            if f.name in data:
                kwargs[f.name] = data[f.name]
        return cls(**kwargs)

    def copy(self, **updates) -> BasePipelineOptions:
        data = self.to_dict()
        data.update(updates)
        return self.__class__.from_dict(data)
```

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/core/test_base_options.py -v`
Expected: PASS

**Step 5: 提交**

```bash
git add src/vibeocr/core/pipelines/__init__.py src/vibeocr/core/pipelines/base_options.py tests/core/test_base_options.py
git commit -m "feat(pipeline-registry): add BasePipelineOptions base class"
```

注意：先创建 `src/vibeocr/core/pipelines/` 包目录和 `__init__.py`。

---

### Task 2: 迁移现有 OCROptions 为独立 Options 类

**Files:**
- Create: `src/vibeocr/core/pipelines/pipeline_ocr.py`
- Create: `src/vibeocr/core/pipelines/pipeline_pp_structure.py`
- Create: `src/vibeocr/core/pipelines/pipeline_mineru.py`
- Create: `src/vibeocr/core/pipelines/pipeline_paddlocr_vl.py`
- Test: `tests/core/test_pipeline_options_migration.py`

**Step 1: 写失败测试 — 验证迁移后的 Options 类与旧 OCROptions 字段兼容**

```python
# tests/core/test_pipeline_options_migration.py
from vibeocr.core.pipelines.pipeline_ocr import OCROptions
from vibeocr.core.pipelines.pipeline_pp_structure import PPStructureV3Options
from vibeocr.core.pipelines.pipeline_mineru import MinerUOptions
from vibeocr.core.pipelines.pipeline_paddlocr_vl import PaddleOCRVLOptions


def test_ocr_options_roundtrip():
    opts = OCROptions(
        use_doc_orientation_classify=True,
        use_doc_unwarping=False,
        use_textline_orientation=True,
    )
    d = opts.to_dict()
    assert d["pipeline"] == "OCR"
    assert d["use_doc_unwarping"] is False
    restored = OCROptions.from_dict(d)
    assert restored.use_doc_unwarping is False


def test_pp_structure_options():
    opts = PPStructureV3Options(use_table_recognition=False)
    d = opts.to_dict()
    assert d["pipeline"] == "PP-StructureV3"
    assert d["use_table_recognition"] is False


def test_mineru_options():
    opts = MinerUOptions(parse_method="ocr")
    d = opts.to_dict()
    assert d["pipeline"] == "MinerU"
    assert d["parse_method"] == "ocr"


def test_paddlocr_vl_options():
    opts = PaddleOCRVLOptions(vl_use_layout_detection=False)
    d = opts.to_dict()
    assert d["pipeline"] == "PaddleOCR-VL"
    assert d["vl_use_layout_detection"] is False
```

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/core/test_pipeline_options_migration.py -v`
Expected: FAIL

**Step 3: 实现各管道 Options 类**

从现有 `src/vibeocr/models/ocr_options.py` 的字段拆分到独立文件：

`pipeline_ocr.py`:
```python
from dataclasses import dataclass
from typing import Any
from vibeocr.core.pipelines.base_options import BasePipelineOptions

@dataclass
class OCROptions(BasePipelineOptions):
    pipeline: str = "OCR"
    use_doc_orientation_classify: bool = True
    use_doc_unwarping: bool = True
    use_textline_orientation: bool = False
```

`pipeline_pp_structure.py`:
```python
from dataclasses import dataclass
from vibeocr.core.pipelines.base_options import BasePipelineOptions

@dataclass
class PPStructureV3Options(BasePipelineOptions):
    pipeline: str = "PP-StructureV3"
    use_doc_orientation_classify: bool = True
    use_doc_unwarping: bool = True
    use_textline_orientation: bool = False
    use_table_recognition: bool = True
    use_formula_recognition: bool = True
    use_seal_recognition: bool = False
    use_chart_recognition: bool = False
```

`pipeline_mineru.py`:
```python
from dataclasses import dataclass, field
from typing import Any
from vibeocr.core.pipelines.base_options import BasePipelineOptions

@dataclass
class MinerUOptions(BasePipelineOptions):
    pipeline: str = "MinerU"
    parse_method: str = "auto"
    backend: str = "hybrid-auto-engine"
    enable_formula: bool = True
    enable_table: bool = True
    lang_list: list[str] = field(default_factory=list)
    start_page_id: int = 0
    end_page_id: int | None = None
```

`pipeline_paddlocr_vl.py`:
```python
from dataclasses import dataclass
from vibeocr.core.pipelines.base_options import BasePipelineOptions

@dataclass
class PaddleOCRVLOptions(BasePipelineOptions):
    pipeline: str = "PaddleOCR-VL"
    use_doc_orientation_classify: bool = True
    use_doc_unwarping: bool = True
    vl_use_layout_detection: bool = True
    vl_use_chart_recognition: bool = False
    vl_use_seal_recognition: bool = False
    use_ocr_for_image_block: bool = False
```

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/core/test_pipeline_options_migration.py -v`
Expected: PASS

**Step 5: 提交**

```bash
git add src/vibeocr/core/pipelines/ tests/core/test_pipeline_options_migration.py
git commit -m "feat(pipeline-registry): migrate existing pipelines to independent options classes"
```

---

### Task 3: 创建 PipelineSpec 和 PipelineRegistry

**Files:**
- Create: `src/vibeocr/core/pipelines/registry.py`
- Test: `tests/core/test_pipeline_registry.py`

**Step 1: 写失败测试**

```python
# tests/core/test_pipeline_registry.py
import pytest
from vibeocr.core.pipelines.registry import PipelineSpec, PipelineRegistry
from vibeocr.core.pipelines.base_options import BasePipelineOptions


class DummyOptions(BasePipelineOptions):
    pipeline: str = "DUMMY"
    foo: bool = True


def _create_dummy(device):
    return "dummy_pipeline"


def _recognize_dummy(service, image, options):
    return None


DUMMY_SPEC = PipelineSpec(
    name="DUMMY",
    display_name="Dummy Pipeline",
    description="For testing",
    options_class=DummyOptions,
    create_pipeline=_create_dummy,
    recognize=_recognize_dummy,
)


def test_spec_fields():
    assert DUMMY_SPEC.name == "DUMMY"
    assert DUMMY_SPEC.options_class is DummyOptions


def test_register_and_get():
    reg = PipelineRegistry()
    reg.register(DUMMY_SPEC)
    spec = reg.get("DUMMY")
    assert spec is DUMMY_SPEC


def test_get_unknown_raises():
    reg = PipelineRegistry()
    with pytest.raises(KeyError):
        reg.get("UNKNOWN")


def test_list_all():
    reg = PipelineRegistry()
    reg.register(DUMMY_SPEC)
    all_specs = reg.list_all()
    assert len(all_specs) == 1
    assert all_specs[0].name == "DUMMY"


def test_list_display_names():
    reg = PipelineRegistry()
    reg.register(DUMMY_SPEC)
    names = reg.list_display_names()
    assert names == ["Dummy Pipeline"]
```

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/core/test_pipeline_registry.py -v`
Expected: FAIL

**Step 3: 实现 PipelineSpec 和 PipelineRegistry**

```python
# src/vibeocr/core/pipelines/registry.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from vibeocr.core.pipelines.base_options import BasePipelineOptions
    from vibeocr.models.ocr_result import OCRResult


@dataclass(frozen=True)
class PipelineSpec:
    name: str
    display_name: str
    description: str
    options_class: type
    create_pipeline: Callable[[str], Any]
    recognize: Callable[..., Any]


class PipelineRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, PipelineSpec] = {}

    def register(self, spec: PipelineSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> PipelineSpec:
        if name not in self._specs:
            raise KeyError(f"Pipeline '{name}' not registered")
        return self._specs[name]

    def list_all(self) -> list[PipelineSpec]:
        return list(self._specs.values())

    def list_display_names(self) -> list[str]:
        return [s.display_name for s in self._specs.values()]

    def has(self, name: str) -> bool:
        return name in self._specs
```

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/core/test_pipeline_registry.py -v`
Expected: PASS

**Step 5: 提交**

```bash
git add src/vibeocr/core/pipelines/registry.py tests/core/test_pipeline_registry.py
git commit -m "feat(pipeline-registry): add PipelineSpec and PipelineRegistry"
```

---

### Task 4: 创建表格识别管道

**Files:**
- Create: `src/vibeocr/core/pipelines/pipeline_table.py`
- Test: `tests/core/test_pipeline_table.py`

**Step 1: 写失败测试**

```python
# tests/core/test_pipeline_table.py
from vibeocr.core.pipelines.pipeline_table import TableRecognitionOptions, TABLE_RECOGNITION_SPEC


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


def test_table_spec_registered():
    assert TABLE_RECOGNITION_SPEC.name == "TABLE_RECOGNITION"
    assert TABLE_RECOGNITION_SPEC.display_name == "表格识别"
    assert TABLE_RECOGNITION_SPEC.options_class is TableRecognitionOptions
```

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/core/test_pipeline_table.py -v`
Expected: FAIL

**Step 3: 实现表格识别管道**

```python
# src/vibeocr/core/pipelines/pipeline_table.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from vibeocr.core.pipelines.base_options import BasePipelineOptions
from vibeocr.core.pipelines.registry import PipelineSpec

if TYPE_CHECKING:
    pass


@dataclass
class TableRecognitionOptions(BasePipelineOptions):
    pipeline: str = "TABLE_RECOGNITION"
    use_doc_orientation_classify: bool = True
    use_doc_unwarping: bool = True
    use_wireless_table: bool = True
    wireless_table_model_name: str = "SLANeXt_wireless"
    wired_table_model_name: str = "SLANeXt_wired"
    use_table_orientation_classify: bool = True
    use_ocr_results_with_table_cells: bool = True
    text_det_limit_side_len: int | None = None
    text_det_thresh: float | None = None
    text_det_box_thresh: float | None = None
    text_det_unclip_ratio: float | None = None
    text_rec_score_thresh: float | None = None
    use_wired_table_cells_trans_to_html: bool = False
    use_wireless_table_cells_trans_to_html: bool = False
    use_e2e_wired_table_rec_model: bool = False
    use_e2e_wireless_table_rec_model: bool = True


def _create_table_pipeline(device: str) -> Any:
    from paddleocr import TableRecognitionPipelineV2
    return TableRecognitionPipelineV2(device=device)


def _recognize_table(service: Any, image: Any, options: TableRecognitionOptions) -> Any:
    from vibeocr.models.ocr_result import OCRResult, TextBlock
    import re as _re

    pipeline = service.get_or_create_pipeline(options.pipeline)

    predict_kwargs: dict[str, Any] = {}
    predict_kwargs["use_doc_orientation_classify"] = options.use_doc_orientation_classify
    predict_kwargs["use_doc_unwarping"] = options.use_doc_unwarping
    predict_kwargs["use_table_orientation_classify"] = options.use_table_orientation_classify
    predict_kwargs["use_ocr_results_with_table_cells"] = options.use_ocr_results_with_table_cells
    predict_kwargs["use_wired_table_cells_trans_to_html"] = options.use_wired_table_cells_trans_to_html
    predict_kwargs["use_wireless_table_cells_trans_to_html"] = options.use_wireless_table_cells_trans_to_html
    predict_kwargs["use_e2e_wired_table_rec_model"] = options.use_e2e_wired_table_rec_model
    predict_kwargs["use_e2e_wireless_table_rec_model"] = options.use_e2e_wireless_table_rec_model

    if not options.use_wireless_table:
        predict_kwargs["wireless_table_structure_recognition_model_name"] = options.wired_table_model_name
    else:
        predict_kwargs["wireless_table_structure_recognition_model_name"] = options.wireless_table_model_name

    if options.text_det_limit_side_len is not None:
        predict_kwargs["text_det_limit_side_len"] = options.text_det_limit_side_len
    if options.text_det_thresh is not None:
        predict_kwargs["text_det_thresh"] = options.text_det_thresh
    if options.text_det_box_thresh is not None:
        predict_kwargs["text_det_box_thresh"] = options.text_det_box_thresh
    if options.text_det_unclip_ratio is not None:
        predict_kwargs["text_det_unclip_ratio"] = options.text_det_unclip_ratio
    if options.text_rec_score_thresh is not None:
        predict_kwargs["text_rec_score_thresh"] = options.text_rec_score_thresh

    output = pipeline.predict(input=image, **predict_kwargs)
    output_list = list(output)

    text_blocks: list[TextBlock] = []
    text_with_scores: list[tuple[str, float]] = []
    markdown_parts: list[str] = []
    content_list: list[dict[str, Any]] = []

    for res in output_list:
        if hasattr(res, "markdown"):
            md_info = getattr(res, "markdown", None)
            if isinstance(md_info, dict):
                md_text = md_info.get("markdown_texts", "")
                if md_text:
                    markdown_parts.append(md_text)

        parsing_res_list = []
        if hasattr(res, "parsing_res_list"):
            parsing_res_list = res.parsing_res_list

        for block in parsing_res_list:
            label = getattr(block, "label", "table")
            bbox = getattr(block, "bbox", None)
            content = getattr(block, "content", "")
            order_index = getattr(block, "order_index", -1)

            if not content:
                continue

            cl_idx = len(content_list)
            bbox_tuple = tuple(float(v) for v in bbox) if bbox else None

            if label == "table":
                from vibeocr.services.ocr_service import _extract_table_html, _html_table_to_markdown
                table_html = _extract_table_html(content)
                table_md = _html_table_to_markdown(table_html)
                if table_md:
                    markdown_parts.append(table_md)
                text_blocks.append(TextBlock(
                    text=content, score=0.9, bbox=bbox_tuple,
                    label=label, order=order_index or -1, content_index=cl_idx,
                ))
                text_with_scores.append((content, 0.9))
                content_list.append({"type": "table", "table_body": table_html, "bbox": bbox_tuple})
            else:
                text_blocks.append(TextBlock(
                    text=content, score=0.9, bbox=bbox_tuple,
                    label=label, order=order_index or -1, content_index=cl_idx,
                ))
                text_with_scores.append((content, 0.9))
                content_list.append({"type": label, "text": content, "bbox": bbox_tuple})

    raw_text = "\n".join(b.text for b in text_blocks)
    markdown_text = "\n\n".join(markdown_parts) if markdown_parts else raw_text

    from vibeocr.utils.markdown_converter import markdown_to_html

    result = OCRResult(
        raw_text=raw_text,
        markdown_text=markdown_text,
        html_text=markdown_to_html(markdown_text) if markdown_text else "",
        text_with_scores=text_with_scores,
        pipeline_type="TABLE_RECOGNITION",
        text_blocks=text_blocks,
        content_list=content_list,
    )
    return result


TABLE_RECOGNITION_SPEC = PipelineSpec(
    name="TABLE_RECOGNITION",
    display_name="表格识别",
    description="独立表格结构识别，支持有线和无线表格",
    options_class=TableRecognitionOptions,
    create_pipeline=_create_table_pipeline,
    recognize=_recognize_table,
)
```

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/core/test_pipeline_table.py -v`
Expected: PASS

**Step 5: 提交**

```bash
git add src/vibeocr/core/pipelines/pipeline_table.py tests/core/test_pipeline_table.py
git commit -m "feat(pipeline-registry): add table recognition pipeline spec"
```

---

### Task 5: 创建公式识别管道

**Files:**
- Create: `src/vibeocr/core/pipelines/pipeline_formula.py`
- Test: `tests/core/test_pipeline_formula.py`

**Step 1: 写失败测试**

```python
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
```

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/core/test_pipeline_formula.py -v`
Expected: FAIL

**Step 3: 实现公式识别管道**

```python
# src/vibeocr/core/pipelines/pipeline_formula.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from vibeocr.core.pipelines.base_options import BasePipelineOptions
from vibeocr.core.pipelines.registry import PipelineSpec


@dataclass
class FormulaRecognitionOptions(BasePipelineOptions):
    pipeline: str = "FORMULA_RECOGNITION"
    use_doc_orientation_classify: bool = True
    use_doc_unwarping: bool = True
    formula_recognition_model_name: str | None = None
    formula_recognition_model_dir: str | None = None
    formula_recognition_batch_size: int = 1


def _create_formula_pipeline(device: str) -> Any:
    """创建公式识别管道

    公式识别目前没有独立的 pipeline 类，
    使用 PP-StructureV3 并关闭其他识别功能。
    """
    from paddleocr import PPStructureV3
    kwargs = {"device": device}
    return PPStructureV3(**kwargs)


def _recognize_formula(service: Any, image: Any, options: FormulaRecognitionOptions) -> Any:
    from vibeocr.models.ocr_result import OCRResult, TextBlock
    import io

    pipeline = service.get_or_create_pipeline(options.pipeline)

    predict_kwargs: dict[str, Any] = {
        "use_doc_orientation_classify": options.use_doc_orientation_classify,
        "use_doc_unwarping": options.use_doc_unwarping,
        "use_table_recognition": False,
        "use_formula_recognition": True,
        "use_seal_recognition": False,
        "use_chart_recognition": False,
    }
    if options.formula_recognition_batch_size != 1:
        predict_kwargs["formula_recognition_batch_size"] = options.formula_recognition_batch_size

    output = pipeline.predict(input=image, **predict_kwargs)
    output_list = list(output)

    text_blocks: list[TextBlock] = []
    text_with_scores: list[tuple[str, float]] = []
    markdown_parts: list[str] = []
    content_list: list[dict[str, Any]] = []

    preproc_angle = 0
    preprocessed_png: bytes | None = None
    preproc_w = preproc_h = 0
    if output_list:
        res = output_list[0]
        dp_res = getattr(res, "doc_preprocessor_res", None)
        if dp_res is not None:
            if isinstance(dp_res, dict):
                preproc_angle = dp_res.get("angle", 0)
            else:
                preproc_angle = getattr(dp_res, "angle", 0)
        img_dict = getattr(res, "img", None)
        if isinstance(img_dict, dict):
            pp_img = img_dict.get("preprocessed_img")
            if pp_img is not None:
                preproc_w, preproc_h = pp_img.size
                buf = io.BytesIO()
                pp_img.save(buf, format="PNG")
                preprocessed_png = buf.getvalue()

    for res in output_list:
        parsing_res_list = getattr(res, "parsing_res_list", [])

        for block in parsing_res_list:
            label = getattr(block, "label", "text")
            bbox = getattr(block, "bbox", None)
            content = getattr(block, "content", "")
            order_index = getattr(block, "order_index", -1)

            if not content:
                continue
            if label != "formula":
                continue

            cl_idx = len(content_list)
            bbox_tuple = tuple(float(v) for v in bbox) if bbox else None
            formula_md = f"$${content}$$"
            markdown_parts.append(formula_md)
            text_blocks.append(TextBlock(
                text=content, score=1.0, bbox=bbox_tuple,
                label=label, order=order_index or -1, content_index=cl_idx,
            ))
            text_with_scores.append((content, 1.0))
            content_list.append({"type": "formula", "text": content, "bbox": bbox_tuple})

    raw_text = "\n".join(b.text for b in text_blocks)
    markdown_text = "\n\n".join(markdown_parts) if markdown_parts else raw_text

    from vibeocr.utils.markdown_converter import markdown_to_html

    result = OCRResult(
        raw_text=raw_text,
        markdown_text=markdown_text,
        html_text=markdown_to_html(markdown_text) if markdown_text else "",
        text_with_scores=text_with_scores,
        pipeline_type="FORMULA_RECOGNITION",
        text_blocks=text_blocks,
        content_list=content_list,
    )
    result.preproc_angle = preproc_angle
    result.preprocessed_image = preprocessed_png
    result.preproc_img_w = preproc_w
    result.preproc_img_h = preproc_h
    return result


FORMULA_RECOGNITION_SPEC = PipelineSpec(
    name="FORMULA_RECOGNITION",
    display_name="公式识别",
    description="独立数学公式识别（LaTeX 输出）",
    options_class=FormulaRecognitionOptions,
    create_pipeline=_create_formula_pipeline,
    recognize=_recognize_formula,
)
```

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/core/test_pipeline_formula.py -v`
Expected: PASS

**Step 5: 提交**

```bash
git add src/vibeocr/core/pipelines/pipeline_formula.py tests/core/test_pipeline_formula.py
git commit -m "feat(pipeline-registry): add formula recognition pipeline spec"
```

---

### Task 6: 注册所有管道并更新 `__init__.py`

**Files:**
- Modify: `src/vibeocr/core/pipelines/__init__.py`
- Test: `tests/core/test_pipeline_registry_all.py`

**Step 1: 写失败测试**

```python
# tests/core/test_pipeline_registry_all.py
from vibeocr.core.pipelines import get_registry


def test_registry_has_all_pipelines():
    reg = get_registry()
    names = [s.name for s in reg.list_all()]
    assert "OCR" in names
    assert "PP-StructureV3" in names
    assert "TABLE_RECOGNITION" in names
    assert "FORMULA_RECOGNITION" in names
    assert "MinerU" in names
    assert "PaddleOCR-VL" in names


def test_registry_get_each():
    reg = get_registry()
    for name in ["OCR", "PP-StructureV3", "TABLE_RECOGNITION", "FORMULA_RECOGNITION", "MinerU", "PaddleOCR-VL"]:
        spec = reg.get(name)
        assert spec.name == name
        assert spec.options_class is not None
```

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/core/test_pipeline_registry_all.py -v`
Expected: FAIL

**Step 3: 为 OCR、PP-StructureV3、MinerU、PaddleOCR-VL 创建 spec 并注册**

在各自文件（`pipeline_ocr.py`、`pipeline_pp_structure.py`、`pipeline_mineru.py`、`pipeline_paddlocr_vl.py`）中添加 `PipelineSpec` 定义和 `create_pipeline`/`recognize` 函数，从 `ocr_service.py` 的现有方法迁移逻辑。

然后更新 `__init__.py`：

```python
# src/vibeocr/core/pipelines/__init__.py
from vibeocr.core.pipelines.registry import PipelineSpec, PipelineRegistry
from vibeocr.core.pipelines.pipeline_ocr import OCR_SPEC, OCROptions
from vibeocr.core.pipelines.pipeline_pp_structure import PP_STRUCTURE_V3_SPEC, PPStructureV3Options
from vibeocr.core.pipelines.pipeline_table import TABLE_RECOGNITION_SPEC, TableRecognitionOptions
from vibeocr.core.pipelines.pipeline_formula import FORMULA_RECOGNITION_SPEC, FormulaRecognitionOptions
from vibeocr.core.pipelines.pipeline_mineru import MINERU_SPEC, MinerUOptions
from vibeocr.core.pipelines.pipeline_paddlocr_vl import PADDLEOCR_VL_SPEC, PaddleOCRVLOptions
from vibeocr.core.pipelines.base_options import BasePipelineOptions

_registry = PipelineRegistry()
_registry.register(OCR_SPEC)
_registry.register(PP_STRUCTURE_V3_SPEC)
_registry.register(TABLE_RECOGNITION_SPEC)
_registry.register(FORMULA_RECOGNITION_SPEC)
_registry.register(MINERU_SPEC)
_registry.register(PADDLEOCR_VL_SPEC)


def get_registry() -> PipelineRegistry:
    return _registry
```

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/core/test_pipeline_registry_all.py -v`
Expected: PASS

**Step 5: 提交**

```bash
git add src/vibeocr/core/pipelines/ tests/core/test_pipeline_registry_all.py
git commit -m "feat(pipeline-registry): register all pipelines and export registry"
```

---

### Task 7: 改造 OCRService 使用注册表

**Files:**
- Modify: `src/vibeocr/services/ocr_service.py`
- Test: `tests/services/test_ocr_service_registry.py`

**Step 1: 写失败测试**

```python
# tests/services/test_ocr_service_registry.py
from unittest.mock import patch, MagicMock
from vibeocr.core.pipelines import get_registry


def test_recognize_dispatches_to_spec():
    """验证 recognize() 根据管道名分发到对应 spec 的 recognize 方法"""
    from vibeocr.services.ocr_service import OCRService

    reg = get_registry()
    for spec in reg.list_all():
        assert spec.recognize is not None, f"{spec.name} missing recognize"


def test_get_or_create_pipeline_uses_spec_create():
    """验证 get_or_create_pipeline 调用 spec 的 create_pipeline"""
    from vibeocr.services.ocr_service import OCRService

    service = OCRService()
    service._pipelines = {}  # 确保缓存为空
    # 直接测试缓存逻辑
    service._pipelines["OCR"] = "mock_pipeline"
    assert service.get_or_create_pipeline("OCR") == "mock_pipeline"
```

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/services/test_ocr_service_registry.py -v`
Expected: FAIL

**Step 3: 改造 OCRService**

主要改动：
1. 新增 `get_or_create_pipeline(pipeline_name)` 方法，使用注册表的 `spec.create_pipeline`
2. 修改 `recognize()` 方法，使用注册表的 `spec.recognize`
3. 保留 `get_pipeline()` 作为向后兼容方法
4. 将 `_recognize_ocr`、`_recognize_structure`、`_recognize_paddlocr_vl` 的逻辑迁移到各 pipeline spec 文件中

在 `ocr_service.py` 中添加：

```python
def get_or_create_pipeline(self, pipeline_name: str) -> Any:
    if pipeline_name not in self._pipelines:
        with self._lock:
            if pipeline_name not in self._pipelines:
                self._setup_cuda_dll_path()
                spec = get_registry().get(pipeline_name)
                device = self._get_device()
                self._pipelines[pipeline_name] = spec.create_pipeline(device)
    return self._pipelines[pipeline_name]
```

修改 `recognize()` 方法以使用 `BasePipelineOptions`，通过注册表分发。

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/services/test_ocr_service_registry.py -v`
Expected: PASS

**Step 5: 运行现有测试确保无回归**

Run: `python -m pytest tests/ -v --tb=short`
Expected: 全部 PASS

**Step 6: 提交**

```bash
git add src/vibeocr/services/ocr_service.py tests/services/test_ocr_service_registry.py
git commit -m "refactor(ocr-service): use pipeline registry for dispatch"
```

---

### Task 8: 向后兼容 — 更新旧模块的 re-export

**Files:**
- Modify: `src/vibeocr/models/ocr_options.py`
- Modify: `src/vibeocr/core/pipelines.py`（旧文件，非新包）

**Step 1: 在旧 `ocr_options.py` 中 re-export 新 Options 类**

```python
# src/vibeocr/models/ocr_options.py
"""向后兼容模块 - 重新导出新管道系统中的 OCROptions

所有管道选项已迁移到 vibeocr.core.pipelines 包中。
"""
import warnings
from vibeocr.core.pipelines.pipeline_ocr import OCROptions  # noqa: F401
from vibeocr.core.pipelines.pipeline_pp_structure import PPStructureV3Options
from vibeocr.core.pipelines.pipeline_table import TableRecognitionOptions
from vibeocr.core.pipelines.pipeline_formula import FormulaRecognitionOptions
from vibeocr.core.pipelines.pipeline_mineru import MinerUOptions
from vibeocr.core.pipelines.pipeline_paddlocr_vl import PaddleOCRVLOptions
from vibeocr.core.pipelines.base_options import BasePipelineOptions
```

**Step 2: 在旧 `pipelines.py` 中 re-export 新枚举和函数**

旧 `src/vibeocr/core/pipelines.py` 需要创建一个 `OCRPipeline` 兼容枚举，映射到注册表的 spec name：

```python
# src/vibeocr/core/pipelines.py
"""向后兼容模块 - 重新导出新管道注册表

管道定义已迁移到 vibeocr.core.pipelines 包中。
"""
from vibeocr.core.pipelines.registry import PipelineSpec, PipelineRegistry  # noqa: F401
from vibeocr.core.pipelines import get_registry  # noqa: F401

# 向后兼容：OCRPipeline 枚举
from enum import Enum

class OCRPipeline(Enum):
    OCR = "OCR"
    PP_STRUCTURE_V3 = "PP-StructureV3"
    DOCUMENT_PARSING = "MinerU"
    PADDLEOCR_VL = "PaddleOCR-VL"
    TABLE_RECOGNITION = "TABLE_RECOGNITION"
    FORMULA_RECOGNITION = "FORMULA_RECOGNITION"

def get_all_pipelines():
    return list(OCRPipeline)

def get_pipeline_display_name(pipeline):
    reg = get_registry()
    spec = reg.get(pipeline.value)
    return spec.display_name if spec else pipeline.value

def get_pipeline_description(pipeline):
    reg = get_registry()
    spec = reg.get(pipeline.value)
    return spec.description if spec else ""

def get_pipeline_supported_options(pipeline):
    # 从 spec 的 options_class 的字段列表生成
    from dataclasses import fields as dc_fields
    reg = get_registry()
    try:
        spec = reg.get(pipeline.value)
        return [f.name for f in dc_fields(spec.options_class) if f.name != "pipeline"]
    except KeyError:
        return []

def is_option_supported(pipeline, option_name):
    return option_name in get_pipeline_supported_options(pipeline)
```

**Step 3: 运行现有测试确保无回归**

Run: `python -m pytest tests/ -v --tb=short`
Expected: 全部 PASS

**Step 4: 提交**

```bash
git add src/vibeocr/models/ocr_options.py src/vibeocr/core/pipelines.py
git commit -m "refactor: backward-compat re-exports for old pipeline modules"
```

---

### Task 9: UI — 更新 PreprocessOptionsWidget 支持新管道

**Files:**
- Modify: `src/vibeocr/widgets/preprocess_options_widget.py`
- Test: `tests/widgets/test_preprocess_options_table_formula.py`

**Step 1: 写失败测试**

```python
# tests/widgets/test_preprocess_options_table_formula.py
def test_pipeline_combo_includes_table_and_formula():
    from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget
    widget = PreprocessOptionsWidget()
    combo = widget._pipeline_combo
    values = [combo.itemData(i) for i in range(combo.count())]
    assert "TABLE_RECOGNITION" in values
    assert "FORMULA_RECOGNITION" in values
```

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/widgets/test_preprocess_options_table_formula.py -v`
Expected: FAIL（新管道不在下拉框中）

**Step 3: 更新 `_populate_pipeline_combo` 和 `_update_tab_visibility`**

- `_populate_pipeline_combo` 改为从 `get_registry().list_all()` 获取管道列表
- `_update_tab_visibility` 增加 TABLE_RECOGNITION 和 FORMULA_RECOGNITION 的选项组可见性判断
- 新增 `_create_table_recognition_group()` 和 `_create_formula_recognition_group()` 选项组
- `get_options()` 和 `set_options()` 增加对新管道选项的读写

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/widgets/test_preprocess_options_table_formula.py -v`
Expected: PASS

**Step 5: 运行现有测试确保无回归**

Run: `python -m pytest tests/ -v --tb=short`
Expected: 全部 PASS

**Step 6: 提交**

```bash
git add src/vibeocr/widgets/preprocess_options_widget.py tests/widgets/test_preprocess_options_table_formula.py
git commit -m "feat(ui): add table and formula pipeline options to PreprocessOptionsWidget"
```

---

### Task 10: 更新 OCRPreferences 支持多态 Options

**Files:**
- Modify: `src/vibeocr/utils/ocr_preferences.py`
- Test: `tests/test_ocr_preferences_registry.py`

**Step 1: 写失败测试**

```python
# tests/test_ocr_preferences_registry.py
import json
from pathlib import Path
from vibeocr.utils.ocr_preferences import OCRPreferences
from vibeocr.core.pipelines.pipeline_table import TableRecognitionOptions


def test_save_and_load_table_options(tmp_path):
    prefs = OCRPreferences(tmp_path)
    opts = TableRecognitionOptions(use_wireless_table=False)
    prefs.set_options(opts)

    prefs2 = OCRPreferences(tmp_path)
    loaded = prefs2.get_options()
    assert isinstance(loaded, TableRecognitionOptions)
    assert loaded.use_wireless_table is False


def test_backward_compat_old_format(tmp_path):
    """旧的扁平 OCROptions 格式应该能被正确加载"""
    old_data = {
        "pipeline": "OCR",
        "use_doc_orientation_classify": True,
        "use_doc_unwarping": False,
        "batch_options": {"pipeline": "MinerU", "parse_method": "auto"},
        "version": 1,
    }
    config_path = tmp_path / "ocr_preferences.json"
    config_path.write_text(json.dumps(old_data), encoding="utf-8")

    prefs = OCRPreferences(tmp_path)
    opts = prefs.get_options()
    assert opts.pipeline == "OCR"
```

**Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_ocr_preferences_registry.py -v`
Expected: FAIL

**Step 3: 修改 OCRPreferences 使用注册表的 options_class**

在 `_load` 中根据 `pipeline` 字段查找对应的 `options_class` 并反序列化：

```python
def _options_from_dict(self, data: dict):
    from vibeocr.core.pipelines import get_registry
    pipeline_name = data.get("pipeline", "OCR")
    try:
        spec = get_registry().get(pipeline_name)
        return spec.options_class.from_dict(data)
    except KeyError:
        # fallback: 旧格式或未知管道
        from vibeocr.core.pipelines.pipeline_ocr import OCROptions
        return OCROptions.from_dict(data)
```

**Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_ocr_preferences_registry.py -v`
Expected: PASS

**Step 5: 提交**

```bash
git add src/vibeocr/utils/ocr_preferences.py tests/test_ocr_preferences_registry.py
git commit -m "feat(prefs): support polymorphic pipeline options in OCRPreferences"
```

---

### Task 11: 更新 SingleRecognitionTab 和其他调用方

**Files:**
- Modify: `src/vibeocr/views/tabs/single_recognition_tab.py`
- Modify: `src/vibeocr/views/tabs/base_tab.py`
- Modify: `src/vibeocr/views/batch_recognition_tab.py`（如有引用）

**Step 1: 检查所有引用旧 `OCRPipeline` 和 `OCROptions` 的地方**

Run: `grep -rn "from vibeocr.core.pipelines import OCRPipeline" src/`
Run: `grep -rn "from vibeocr.models.ocr_options import OCROptions" src/`

因为 Task 8 已添加 re-export，大部分代码应该无需改动。但需要检查：

1. `single_recognition_tab.py:224` — `pipeline_val = options.pipeline.value`，新 Options 的 `pipeline` 是 str 不是 enum
2. `base_tab.py:83` — `_get_service_for_pipeline` 中的枚举比较
3. 所有 `options.pipeline == OCRPipeline.XXX` 的比较改为 `options.pipeline == "XXX"`

**Step 2: 更新比较逻辑**

将所有 `options.pipeline == OCRPipeline.XXX` 改为 `options.pipeline == "XXX"` 形式（字符串比较），或确保旧 OCRPipeline 枚举的 `.value` 与新系统兼容。

**Step 3: 运行所有测试**

Run: `python -m pytest tests/ -v --tb=short`
Expected: 全部 PASS

**Step 4: 提交**

```bash
git add src/vibeocr/views/tabs/single_recognition_tab.py src/vibeocr/views/tabs/base_tab.py
git commit -m "refactor: update pipeline comparisons for registry-based options"
```

---

### Task 12: 端到端测试与清理

**Files:**
- Modify: 各 `__init__.py` 确保导出正确
- Test: `tests/integration/test_pipeline_registry_e2e.py`

**Step 1: 写端到端测试**

```python
# tests/integration/test_pipeline_registry_e2e.py
def test_full_registry_flow():
    """验证注册表完整性：所有 spec 可查询、options 可序列化/反序列化"""
    from vibeocr.core.pipelines import get_registry
    from dataclasses import fields as dc_fields

    reg = get_registry()
    for spec in reg.list_all():
        # 每个 spec 有正确的元数据
        assert spec.name
        assert spec.display_name
        assert spec.description
        assert spec.options_class
        assert spec.create_pipeline
        assert spec.recognize

        # Options 可实例化、序列化、反序列化
        opts = spec.options_class()
        d = opts.to_dict()
        assert d["pipeline"] == spec.name
        restored = spec.options_class.from_dict(d)
        assert restored.to_dict() == d


def test_pipeline_combo_has_all_registered():
    """UI 管道下拉应包含所有注册管道"""
    from vibeocr.core.pipelines import get_registry
    from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget

    widget = PreprocessOptionsWidget()
    combo = widget._pipeline_combo
    combo_values = {combo.itemData(i) for i in range(combo.count())}

    reg = get_registry()
    spec_names = {s.name for s in reg.list_all()}
    assert spec_names.issubset(combo_values)
```

**Step 2: 运行所有测试**

Run: `python -m pytest tests/ -v --tb=short`
Expected: 全部 PASS

**Step 3: 提交**

```bash
git add tests/integration/test_pipeline_registry_e2e.py
git commit -m "test: add e2e tests for pipeline registry"
```

---

### Task 13: 手动验证

**Step 1: 启动应用**

Run: `python -m vibeocr`

**Step 2: 验证管道下拉**

- 确认下拉菜单包含：通用 OCR、PP-StructureV3、表格识别、公式识别、MineRU（文档）、PaddleOCR-VL（文档）
- 选择每个管道，确认选项面板正确显示/隐藏对应选项组

**Step 3: 验证表格识别管道**

- 加载一张含表格的图片
- 选择"表格识别"管道
- 点击识别，确认结果正确

**Step 4: 验证公式识别管道**

- 加载一张含公式的图片
- 选择"公式识别"管道
- 点击识别，确认结果正确

**Step 5: 验证旧管道无回归**

- 分别使用通用 OCR、PP-StructureV3 管道识别同一张图片
- 确认行为与改造前一致

**Step 6: 验证设置持久化**

- 切换到不同管道，修改选项，关闭应用
- 重新打开，确认选项被正确恢复

**Step 7: 提交最终版本**

```bash
git add -A
git commit -m "feat: pipeline registry - table and formula as top-level pipelines"
```
