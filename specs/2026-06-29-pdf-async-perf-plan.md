# PDF 异步化与性能优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 PDF 处理界面所有 fitz CPU 密集操作从主线程搬到后台线程，修复删除文字层遗漏，并按结构改动分流保存策略。

**Architecture:** 新增 3 个 QThread worker（PdfMutateWorker / PdfRenderWorker / PdfExportWorker）+ 改造 PdfOcrWorker 为 queue 流式消费。PdfService 层做算法修正（词级 redact + 循环验证）和保存分流。PdfDocument 增加 has_structural_change 标志。底层 → worker → manager → UI 自底向上分 4 阶段实现。

**Tech Stack:** Python 3, PySide6 (QThread/Signal), PyMuPDF (fitz), numpy, pytest

**Spec:** `specs/2026-06-29-pdf-async-perf-design.md`

**测试约定**（所有 worker 测试复用）：
- `qapp` fixture（conftest.py）提供 QApplication
- `wait_worker` fixture（conftest.py:67）返回 `_wait(worker, timeout)` 等待 QThread 完成
- 信号连接用 `Qt.ConnectionType.DirectConnection` 以便同步捕获
- mock OCR service 用 `unittest.mock.MagicMock`

---

## 阶段 1：PdfService 算法层 + 数据模型（无 Qt 依赖，可独立测试）

### Task 1: PdfDocument 增加 has_structural_change 字段

**Files:**
- Modify: `src/vibeocr/models/pdf_document.py:40-47`
- Test: `tests/models/test_pdf_document_structural.py` (Create)

- [ ] **Step 1: Write the failing test**

```python
# tests/models/test_pdf_document_structural.py
"""PdfDocument.has_structural_change 标志测试。"""

from vibeocr.models.pdf_document import PdfDocument


class TestHasStructuralChange:
    def test_default_false(self):
        doc = PdfDocument()
        assert doc.has_structural_change is False

    def test_can_set_true(self):
        doc = PdfDocument()
        doc.has_structural_change = True
        assert doc.has_structural_change is True

    def test_independent_from_is_modified(self):
        """has_structural_change 与 is_modified 正交。"""
        doc = PdfDocument()
        doc.is_modified = True
        assert doc.has_structural_change is False
        doc.has_structural_change = True
        assert doc.is_modified is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/models/test_pdf_document_structural.py -v`
Expected: FAIL with `AttributeError: 'PdfDocument' object has no attribute 'has_structural_change'`

- [ ] **Step 3: Add the field**

```python
# src/vibeocr/models/pdf_document.py — PdfDocument dataclass
# 在 is_modified 字段后添加：
@dataclass
class PdfDocument:
    """PDF 文档状态"""

    file_path: str | None = None
    pages: list[PdfPageInfo] = field(default_factory=list)
    is_modified: bool = False
    has_structural_change: bool = False  # 结构性改动（删页/插页/重排），影响保存策略
    render_dpi: int = 300
    thumbnail_dpi: int = 96
    # ... 其余不变
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/models/test_pdf_document_structural.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/models/pdf_document.py tests/models/test_pdf_document_structural.py
git commit -m "feat(model): PdfDocument 增加 has_structural_change 标志"
```

---

### Task 2: 结构改动操作置位 has_structural_change

**Files:**
- Modify: `src/vibeocr/services/pdf_service.py` (delete_pages, insert_blank_page, insert_pages_from, move_page, reorder_pages)
- Test: `tests/services/test_pdf_service_structural_flag.py` (Create)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_pdf_service_structural_flag.py
"""结构性操作应置 has_structural_change=True，纯文字层/旋转操作不置。"""

import fitz
import pytest

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.services.pdf_service import PdfService


def _open_doc(num_pages=3):
    doc = fitz.open()
    for i in range(num_pages):
        doc.new_page(width=612, height=792)
    pdf_doc = PdfDocument()
    pdf_doc.pages = [PdfPageInfo(page_index=i) for i in range(num_pages)]
    return doc, pdf_doc


class TestStructuralFlag:
    def test_delete_pages_sets_flag(self):
        doc, pdf_doc = _open_doc(3)
        PdfService.delete_pages(doc, pdf_doc, [0])
        assert pdf_doc.has_structural_change is True
        doc.close()

    def test_reorder_pages_sets_flag(self):
        doc, pdf_doc = _open_doc(3)
        PdfService.reorder_pages(doc, pdf_doc, [2, 0, 1])
        assert pdf_doc.has_structural_change is True
        doc.close()

    def test_rotate_pages_does_not_set_flag(self):
        """旋转是页属性修改，incremental save 支持，不置结构标志。"""
        doc, pdf_doc = _open_doc(2)
        PdfService.rotate_pages(doc, pdf_doc, [0], 90)
        assert pdf_doc.has_structural_change is False
        doc.close()

    def test_insert_blank_page_sets_flag(self):
        doc, pdf_doc = _open_doc(1)
        PdfService.insert_blank_page(doc, pdf_doc, 0)
        assert pdf_doc.has_structural_change is True
        doc.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_pdf_service_structural_flag.py -v`
Expected: FAIL（`has_structural_change` 默认 False，操作未置位）

- [ ] **Step 3: 在结构性操作中置位**

在 `delete_pages`、`insert_blank_page`、`insert_pages_from`、`move_page`、`reorder_pages` 中，紧邻 `pdf_document.is_modified = True` 处添加：

```python
pdf_document.is_modified = True
pdf_document.has_structural_change = True
```

**注意**：`rotate_pages` 中只保留 `pdf_document.is_modified = True`，**不加** `has_structural_change`。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/services/test_pdf_service_structural_flag.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/services/pdf_service.py tests/services/test_pdf_service_structural_flag.py
git commit -m "feat(pdf-service): 结构性操作置位 has_structural_change"
```

---

### Task 3: delete_text_layers 词级 redact + 循环验证

**Files:**
- Modify: `src/vibeocr/services/pdf_service.py:577-603` (delete_text_layers)
- Test: `tests/services/test_pdf_service_delete_layer.py` (Create)

这是痛点 4（删除遗漏）的核心修复。

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_pdf_service_delete_layer.py
"""delete_text_layers 词级 redact + 循环验证至清零。"""

import fitz
import pytest

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.services.pdf_service import PdfService


def _make_pdf_with_text(path, texts):
    """创建单页含多段文字的 PDF。"""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for i, text in enumerate(texts):
        page.insert_text((72, 72 + i * 30), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


class TestDeleteTextLayersWordLevel:
    def test_returns_tuple_with_residual_flag(self):
        """返回 (deleted_count, rounds_used, has_residual) 三元组。"""
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "t.pdf"
            _make_pdf_with_text(path, ["Hello World", "Second Line"])
            doc = fitz.open(str(path))
            pdf_doc = PdfDocument()
            pdf_doc.pages = [PdfPageInfo(page_index=0, has_text_layer=True)]
            result = PdfService.delete_text_layers(doc, pdf_doc, 0)
            assert isinstance(result, tuple)
            assert len(result) == 3
            deleted, rounds, residual = result
            assert deleted > 0
            assert rounds >= 1
            assert residual is False
            doc.close()

    def test_clears_all_text_no_residual(self):
        """删除后该页 get_text() 应为空。"""
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "t.pdf"
            _make_pdf_with_text(path, ["Line A", "Line B", "Line C"])
            doc = fitz.open(str(path))
            pdf_doc = PdfDocument()
            pdf_doc.pages = [PdfPageInfo(page_index=0, has_text_layer=True)]
            PdfService.delete_text_layers(doc, pdf_doc, 0)
            assert doc[0].get_text().strip() == ""
            doc.close()

    def test_page_without_text_returns_zero(self):
        """无文字页返回 (0, 0, False)，不做 redact。"""
        doc = fitz.open()
        doc.new_page(width=612, height=792)
        pdf_doc = PdfDocument()
        pdf_doc.pages = [PdfPageInfo(page_index=0)]
        result = PdfService.delete_text_layers(doc, pdf_doc, 0)
        assert result == (0, 0, False)
        doc.close()

    def test_clears_page_info_flags(self):
        """删除后 has_text_layer=False, text_layers=[], ocr_text_blocks=[]。"""
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "t.pdf"
            _make_pdf_with_text(path, ["text"])
            doc = fitz.open(str(path))
            pdf_doc = PdfDocument()
            info = PdfPageInfo(page_index=0, has_text_layer=True)
            pdf_doc.pages = [info]
            PdfService.delete_text_layers(doc, pdf_doc, 0)
            assert info.has_text_layer is False
            assert info.text_layers == []
            assert info.ocr_text_blocks == []
            doc.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_pdf_service_delete_layer.py -v`
Expected: FAIL（当前返回 None，非三元组）

- [ ] **Step 3: 重写 delete_text_layers**

替换 `src/vibeocr/services/pdf_service.py` 的 `delete_text_layers` 方法（约 577-603 行）：

```python
    # 词级 redact 的最大循环轮数。绝大多数页 1 轮清零；仅嵌套/合并异常的
    # 文本结构需多轮。内部常量，不暴露为用户配置。
    _DELETE_LAYER_MAX_ROUNDS = 5

    @staticmethod
    def delete_text_layers(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        page_index: int,
    ) -> tuple[int, int, bool]:
        """删除整页文字层（词级 redact + 循环验证至清零）。

        用 get_text("words") 取词级 bbox 建 redact（比 block 级精确，避免
        嵌套/合并文本块遗漏）。每轮 redact 后重新检测残留，仅当仍有文字才
        继续下一轮，最多 _DELETE_LAYER_MAX_ROUNDS 轮（防死循环）。

        Returns:
            (deleted_count, rounds_used, has_residual)
            deleted_count: 删除的词数（第 1 轮）；rounds_used: 实际执行轮数；
            has_residual: 多轮后是否仍有残留（True 需 UI 提示用户）。
        """
        page = doc[page_index]
        words = page.get_text("words")
        if not words:
            # 无文字 → 不做 redact，直接清状态
            PdfService._clear_page_layer_info(pdf_document, page_index)
            return 0, 0, False

        deleted_count = len(words)
        rounds_used = 0
        for round_idx in range(PdfService._DELETE_LAYER_MAX_ROUNDS):
            current_words = page.get_text("words")
            if not current_words:
                break
            for w in current_words:
                page.add_redact_annot(fitz.Rect(w[:4]), fill=None)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)  # type: ignore[attr-defined]
            rounds_used = round_idx + 1

        has_residual = bool(page.get_text().strip())
        if has_residual:
            logger.warning(
                "page %d 经 %d 轮 redact 仍有残留: %r",
                page_index,
                rounds_used,
                page.get_text()[:50],
            )

        pdf_document.is_modified = True
        PdfService._clear_page_layer_info(pdf_document, page_index)
        return deleted_count, rounds_used, has_residual

    @staticmethod
    def _clear_page_layer_info(pdf_document: PdfDocument, page_index: int) -> None:
        """删除文字层后清空页状态（替代旧的 update_page_info 冗余重检）。"""
        if page_index >= len(pdf_document.pages):
            return
        info = pdf_document.pages[page_index]
        info.has_text_layer = False
        info.text_layers = []
        info.is_scanned = False
        info.ocr_text_blocks = []
        info.ocr_preproc_angle = 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/services/test_pdf_service_delete_layer.py -v`
Expected: PASS

- [ ] **Step 5: Run existing pdf_service tests to check no regression**

Run: `python -m pytest tests/services/test_pdf_service.py tests/services/test_pdf_text_layer_rotation.py -v`
Expected: PASS（若有因返回值变更失败的测试，下一步修）

- [ ] **Step 6: 修适配现有调用方**

`delete_text_layers` 的返回值从 None 变为三元组。检查现有调用方：
- `pdf_service.py` 内 `rewrite_text_layer`（约 562 行）调 `delete_text_layers` 不取返回值 → 无需改
- `pdf_service.py` 内 `add_text_layer`（约 335 行）调 `delete_text_layers` 不取返回值 → 无需改
- `pdf_tab.py:1259` 直接调 → Task 18 改造为异步时统一处理，本步暂保留（返回值被忽略，不报错）

Run: `python -m pytest tests/ -k "delete_text or rewrite or add_text" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/vibeocr/services/pdf_service.py tests/services/test_pdf_service_delete_layer.py
git commit -m "fix(pdf-service): 删除文字层改为词级 redact + 循环验证至清零

修复 block 级 bbox 遗漏嵌套/合并文本块的问题。get_text('words')
词级 bbox 精确覆盖，循环至 get_text() 为空或达 5 轮上限。"
```

---

### Task 4: open_doc 砍掉主线程 rotation 遍历

**Files:**
- Modify: `src/vibeocr/services/pdf_service.py:26-44` (open_doc)
- Test: `tests/services/test_pdf_service.py` (追加 open_doc 测试)

这是痛点 1 的核心修复。

- [ ] **Step 1: Write the failing test**

在 `tests/services/test_pdf_service.py` 末尾追加：

```python
class TestOpenDocNoRotationRead:
    def test_placeholder_pages_have_zero_rotation(self, tmp_path):
        """open_doc 创建的占位页 rotation=0，不读 doc[i].rotation。"""
        path = tmp_path / "rot.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), "text", fontsize=12)
        page.set_rotation(90)  # 真实 rotation=90
        doc.save(str(path))
        doc.close()

        opened_doc, pdf_doc = PdfService.open_doc(str(path))
        # 占位页 rotation 应为 0（不读真实值），由 LoadWorker 后台覆盖
        assert pdf_doc.pages[0].rotation == 0
        assert opened_doc[0].rotation == 90  # fitz 侧真实值不变
        opened_doc.close()

    def test_placeholder_page_count_matches(self, tmp_path):
        path = tmp_path / "multi.pdf"
        _create_test_pdf(path, num_pages=5)
        opened_doc, pdf_doc = PdfService.open_doc(str(path))
        assert len(pdf_doc.pages) == 5
        opened_doc.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_pdf_service.py::TestOpenDocNoRotationRead -v`
Expected: FAIL（当前 open_doc 读 `doc[i].rotation`，占位页 rotation=90）

- [ ] **Step 3: 精简 open_doc**

替换 `src/vibeocr/services/pdf_service.py` 的 `open_doc`（约 26-44 行）：

```python
    @staticmethod
    def open_doc(file_path: str) -> tuple[fitz.Document, PdfDocument]:
        """打开 PDF 并返回 (fitz.Document, PdfDocument)。

        主线程只做 fitz.open + 创建轻量占位页（rotation=0，不逐页读 doc[i]）。
        真实 rotation 及文字层信息由 PdfLoadWorker 在后台逐页填充，
        避免打开大 PDF 时主线程遍历每页冻结 UI。
        """
        if not Path(file_path).exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        doc = fitz.open(file_path)
        if doc.is_encrypted:
            doc.close()
            raise RuntimeError("不支持加密 PDF 文件")

        pdf_document = PdfDocument(file_path=file_path)
        pdf_document.pages = [
            PdfPageInfo(page_index=i) for i in range(doc.page_count)
        ]
        return doc, pdf_document
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/services/test_pdf_service.py::TestOpenDocNoRotationRead -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/services/pdf_service.py tests/services/test_pdf_service.py
git commit -m "perf(pdf-service): open_doc 砍掉主线程 rotation 遍历

占位页 rotation=0，真实值由 LoadWorker 后台覆盖，避免大 PDF 打开时
主线程逐页 doc[i].rotation 冻结 UI。"
```

---

### Task 5: save_with_rewrite + 保存策略分流

**Files:**
- Modify: `src/vibeocr/services/pdf_service.py` (新增 save_with_rewrite, 改造 save)
- Test: `tests/services/test_pdf_service_save.py` (Create)

这是痛点 5 的核心修复。

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_pdf_service_save.py
"""save_with_rewrite: rewrite + 按结构改动分流落盘。"""

import fitz
import pytest

from vibeocr.models.ocr_result import OCRResult, TextBlock
from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.services.pdf_service import PdfService


def _make_scanned_pdf(path):
    import numpy as np
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    img = np.ones((792, 612, 3), dtype=np.uint8) * 240
    cs = fitz.Colorspace(fitz.CS_RGB)
    pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
    page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
    doc.save(str(path))
    doc.close()
    return path


class TestSaveWithRewrite:
    def test_resets_is_modified_and_structural_flag(self, tmp_path):
        path = tmp_path / "scan.pdf"
        _make_scanned_pdf(path)
        doc = fitz.open(str(path))
        pdf_doc = PdfDocument(file_path=str(path))
        info = PdfPageInfo(page_index=0)
        pdf_doc.pages = [info]

        # 模拟 OCR 注入文字块
        result = OCRResult(
            raw_text="Hello",
            text_blocks=[TextBlock(text="Hello", score=0.9, bbox=(50, 50, 300, 100))],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)

        pdf_doc.is_modified = True
        # 纯文字层编辑，无结构改动
        save_result = PdfService.save_with_rewrite(doc, pdf_doc, path=None)
        assert pdf_doc.is_modified is False
        assert pdf_doc.has_structural_change is False
        doc.close()

    def test_save_as_writes_new_file(self, tmp_path):
        path = tmp_path / "src.pdf"
        _make_scanned_pdf(path)
        doc = fitz.open(str(path))
        pdf_doc = PdfDocument(file_path=str(path))
        pdf_doc.pages = [PdfPageInfo(page_index=0)]

        dest = tmp_path / "out.pdf"
        PdfService.save_with_rewrite(doc, pdf_doc, path=str(dest))
        assert dest.exists()
        doc.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_pdf_service_save.py -v`
Expected: FAIL（`save_with_rewrite` 不存在，AttributeError）

- [ ] **Step 3: 实现 save_with_rewrite + SaveResult**

在 `src/vibeocr/services/pdf_service.py` 的 `save` 方法后添加。先在文件顶部 dataclass 区域（或直接用 NamedTuple）添加结果类型：

```python
from typing import Any, NamedTuple


class SaveResult(NamedTuple):
    rewritten_pages: list[int]
    path: str | None
```

在 `save` 方法后添加：

```python
    @staticmethod
    def save_with_rewrite(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        path: str | None = None,
        pdf_settings: object | None = None,
    ) -> SaveResult:
        """对所有有 OCR 块的页重写文字层后落盘（保存/另存为共用）。

        rewrite 阶段用词级 redact（delete_text_layers 循环验证），
        落盘按 has_structural_change 分流：无结构改动 → incremental（快），
        有结构改动 → full save（garbage+deflate）。另存为永远 full save。

        Args:
            doc: fitz.Document 实例。
            pdf_document: PdfDocument 状态对象。
            path: None=覆盖原文件；str=另存为到该路径。
            pdf_settings: PdfGlobalSettings（rewrite 用）。

        Returns:
            SaveResult(rewritten_pages, path)。
        """
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        settings = pdf_settings if pdf_settings is not None else PdfGlobalSettings()
        rewritten: list[int] = []
        for info in pdf_document.pages:
            if not info.ocr_text_blocks:
                continue
            PdfService.rewrite_text_layer(
                doc,
                pdf_document,
                info.page_index,
                info.ocr_text_blocks,
                info.ocr_preproc_angle,
                pdf_settings=settings,
            )
            rewritten.append(info.page_index)

        if path is None:
            save_path = pdf_document.file_path
            if save_path is None:
                pdf_document.is_modified = False
                pdf_document.has_structural_change = False
                return SaveResult(rewritten, None)
            backup_path = save_path + ".bak"
            shutil.copy2(save_path, backup_path)
            try:
                if pdf_document.has_structural_change:
                    # 结构改动不能用 incremental，全量写
                    doc.save(save_path, garbage=4, deflate=True)
                else:
                    doc.save(save_path, incremental=True, encryption=0)
                Path(backup_path).unlink(missing_ok=True)
            except Exception:
                shutil.copy2(backup_path, save_path)
                Path(backup_path).unlink(missing_ok=True)
                raise
        else:
            doc.save(path, deflate=True)

        pdf_document.is_modified = False
        pdf_document.has_structural_change = False
        return SaveResult(rewritten, path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/services/test_pdf_service_save.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/services/pdf_service.py tests/services/test_pdf_service_save.py
git commit -m "feat(pdf-service): save_with_rewrite 按结构改动分流保存策略

无结构改动走 incremental（快），有结构改动走 full save（garbage+deflate）。
另存为永远 full save。rewrite 复用词级 redact。"
```

---

## 阶段 2：Worker 层

### Task 6: MutateTask + TaskKind 数据结构

**Files:**
- Create: `src/vibeocr/workers/pdf_mutate_worker.py`
- Test: `tests/workers/test_pdf_mutate_worker.py` (Create, 仅数据结构部分)

- [ ] **Step 1: Write the failing test**

```python
# tests/workers/test_pdf_mutate_worker.py
"""PdfMutateWorker 测试。"""

from unittest.mock import MagicMock

import fitz
import pytest
from PySide6.QtCore import Qt

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.workers.pdf_mutate_worker import MutateTask, TaskKind


class TestMutateTaskDataclass:
    def test_delete_text_layer_task(self):
        task = MutateTask(kind=TaskKind.DELETE_TEXT_LAYER, page_indices=[0, 1])
        assert task.kind == TaskKind.DELETE_TEXT_LAYER
        assert task.page_indices == [0, 1]

    def test_rotate_task(self):
        task = MutateTask(kind=TaskKind.ROTATE, page_indices=[0], angle=90)
        assert task.angle == 90

    def test_save_task_default_path_none(self):
        task = MutateTask(kind=TaskKind.SAVE)
        assert task.path is None

    def test_save_as_task(self):
        task = MutateTask(kind=TaskKind.SAVE_AS, path="/tmp/out.pdf")
        assert task.path == "/tmp/out.pdf"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/workers/test_pdf_mutate_worker.py::TestMutateTaskDataclass -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 创建 MutateTask + TaskKind**

```python
# src/vibeocr/workers/pdf_mutate_worker.py
"""PDF 通用变更 Worker — 后台执行 fitz 重活（删除文字层/旋转/保存等）。

承接所有原本阻塞主线程的 fitz CPU 密集操作，协作式取消。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    import threading

    import fitz

    from vibeocr.models.pdf_document import PdfDocument

logger = logging.getLogger(__name__)


class TaskKind(Enum):
    """变更任务类型。"""

    DELETE_TEXT_LAYER = "delete_text_layer"
    ROTATE = "rotate"
    DELETE_PAGES = "delete_pages"
    REORDER = "reorder"
    INSERT_BLANK = "insert_blank"
    INSERT_FROM = "insert_from"
    SAVE = "save"
    SAVE_AS = "save_as"


@dataclass
class MutateTask:
    """单次变更任务描述（frozen 语义：构造后不改字段）。

    各 kind 所需字段：
        DELETE_TEXT_LAYER: page_indices
        ROTATE: page_indices, angle
        DELETE_PAGES: page_indices
        REORDER: new_order
        INSERT_BLANK: after_index, width, height
        INSERT_FROM: source_path, after_index
        SAVE: path(=None), pdf_settings
        SAVE_AS: path, pdf_settings
    """

    kind: TaskKind
    page_indices: list[int] = field(default_factory=list)
    angle: int = 0
    new_order: list[int] = field(default_factory=list)
    after_index: int = 0
    width: float = 612.0
    height: float = 792.0
    source_path: str | None = None
    path: str | None = None
    pdf_settings: object | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/workers/test_pdf_mutate_worker.py::TestMutateTaskDataclass -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/workers/pdf_mutate_worker.py tests/workers/test_pdf_mutate_worker.py
git commit -m "feat(worker): MutateTask + TaskKind 数据结构"
```

---

### Task 7: PdfMutateWorker 核心框架 + DELETE_TEXT_LAYER

**Files:**
- Modify: `src/vibeocr/workers/pdf_mutate_worker.py` (追加 PdfMutateWorker 类)
- Modify: `tests/workers/test_pdf_mutate_worker.py` (追加 worker 测试)

- [ ] **Step 1: Write the failing test**

在 `tests/workers/test_pdf_mutate_worker.py` 追加：

```python
def _open_text_pdf(num_pages=3):
    """创建含文字层的测试 PDF（每页有文字）。"""
    import tempfile, pathlib
    td = tempfile.mkdtemp()
    path = pathlib.Path(td) / "t.pdf"
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Page {i}", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


class TestDeleteTextLayerTask:
    def test_deletes_text_and_emits_page_done(self, qapp, wait_worker):
        path = _open_text_pdf(3)
        doc = fitz.open(path)
        pdf_doc = PdfDocument(file_path=path)
        pdf_doc.pages = [PdfPageInfo(page_index=i, has_text_layer=True) for i in range(3)]
        from threading import RLock

        task = MutateTask(kind=TaskKind.DELETE_TEXT_LAYER, page_indices=[0, 1, 2])
        worker = PdfMutateWorker(
            session_id=path, doc=doc, pdf_document=pdf_doc,
            doc_lock=RLock(), task=task,
        )
        done_pages: list = []
        all_done: list = []
        worker.page_done.connect(
            lambda i, p: done_pages.append((i, p)), Qt.ConnectionType.DirectConnection
        )
        worker.all_done.connect(
            lambda sid, r: all_done.append((sid, r)), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert len(done_pages) == 3
        # 全部页无残留
        residual = [r for _, r in all_done if isinstance(r, dict)]
        assert residual and residual[0].get("residual_pages") == []
        for i in range(3):
            assert doc[i].get_text().strip() == ""
        doc.close()

    def test_skips_pages_without_text(self, qapp, wait_worker):
        """无文字的页不进 redact，但仍 emit page_done。"""
        doc = fitz.open()
        doc.new_page(width=612, height=792)  # 空白页
        pdf_doc = PdfDocument()
        pdf_doc.pages = [PdfPageInfo(page_index=0)]
        from threading import RLock

        task = MutateTask(kind=TaskKind.DELETE_TEXT_LAYER, page_indices=[0])
        worker = PdfMutateWorker(
            session_id="blank.pdf", doc=doc, pdf_document=pdf_doc,
            doc_lock=RLock(), task=task,
        )
        done_pages: list = []
        worker.page_done.connect(
            lambda i, p: done_pages.append((i, p)), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)
        assert len(done_pages) == 1
        assert done_pages[0][1] == (0, 0, False)  # 无文字
        doc.close()

    def test_cancel_stops_early(self, qapp, wait_worker):
        path = _open_text_pdf(5)
        doc = fitz.open(path)
        pdf_doc = PdfDocument(file_path=path)
        pdf_doc.pages = [PdfPageInfo(page_index=i, has_text_layer=True) for i in range(5)]
        from threading import RLock

        task = MutateTask(kind=TaskKind.DELETE_TEXT_LAYER, page_indices=list(range(5)))
        worker = PdfMutateWorker(
            session_id=path, doc=doc, pdf_document=pdf_doc,
            doc_lock=RLock(), task=task,
        )
        done_pages: list = []

        def on_done(i, p):
            done_pages.append(i)
            if len(done_pages) == 1:
                worker.cancel()

        worker.page_done.connect(on_done, Qt.ConnectionType.DirectConnection)
        worker.start()
        wait_worker(worker)
        assert worker.isFinished()
        assert len(done_pages) <= 3  # 取消后很快停
        doc.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/workers/test_pdf_mutate_worker.py::TestDeleteTextLayerTask -v`
Expected: FAIL（PdfMutateWorker 不存在）

- [ ] **Step 3: 实现 PdfMutateWorker + DELETE_TEXT_LAYER 分派**

在 `src/vibeocr/workers/pdf_mutate_worker.py` 末尾（MutateTask 之后）追加：

```python
class PdfMutateWorker(QThread):
    """通用 PDF 变更 Worker（单 doc 绑定，一次任务一实例）。

    Signals:
        page_done(page_index: int, payload: object)  逐页任务
        progress(current: int, total: int)
        all_done(session_id: str, result: object)     成功
        failed(session_id: str, error_msg: str)       整体失败
    """

    page_done = Signal(int, object)
    progress = Signal(int, int)
    all_done = Signal(str, object)
    failed = Signal(str, str)

    def __init__(
        self,
        session_id: str,
        doc: fitz.Document,
        pdf_document: PdfDocument,
        doc_lock: threading.RLock,
        task: MutateTask,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._doc = doc
        self._pdf_document = pdf_document
        self._doc_lock = doc_lock
        self._task = task
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def session_id(self) -> str:
        return self._session_id

    def run(self) -> None:
        try:
            handler = self._dispatch()
            handler()
        except Exception as e:
            logger.error("PdfMutateWorker 任务失败: %s", e, exc_info=True)
            self.failed.emit(self._session_id, str(e))

    def _dispatch(self):
        kind = self._task.kind
        if kind == TaskKind.DELETE_TEXT_LAYER:
            return self._run_delete_text_layer
        # 其余 kind 在后续 Task 添加
        raise ValueError(f"未支持的任务类型: {kind}")

    def _run_delete_text_layer(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        indices = self._task.page_indices
        total = len(indices)
        residual_pages: list[int] = []
        for n, page_index in enumerate(indices):
            if self._cancelled:
                break
            try:
                with self._doc_lock:
                    page = self._doc[page_index]
                    if not page.get_text().strip():
                        # 无文字 → 跳过 redact，仍清状态
                        PdfService.delete_text_layers(
                            self._doc, self._pdf_document, page_index
                        )
                        self.page_done.emit(page_index, (0, 0, False))
                    else:
                        deleted, rounds, residual = PdfService.delete_text_layers(
                            self._doc, self._pdf_document, page_index
                        )
                        self.page_done.emit(page_index, (deleted, rounds, residual))
                        if residual:
                            residual_pages.append(page_index)
            except Exception as e:
                logger.error("删除页 %d 文字层失败: %s", page_index, e)
                self.page_done.emit(page_index, None)
            self.progress.emit(n + 1, total)
        self.all_done.emit(self._session_id, {"residual_pages": residual_pages})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/workers/test_pdf_mutate_worker.py::TestDeleteTextLayerTask -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/workers/pdf_mutate_worker.py tests/workers/test_pdf_mutate_worker.py
git commit -m "feat(worker): PdfMutateWorker 核心框架 + DELETE_TEXT_LAYER 任务"
```

---

### Task 8: PdfMutateWorker — ROTATE / DELETE_PAGES / REORDER / INSERT

**Files:**
- Modify: `src/vibeocr/workers/pdf_mutate_worker.py` (扩展 _dispatch + 各 handler)
- Modify: `tests/workers/test_pdf_mutate_worker.py`

- [ ] **Step 1: Write failing tests**

在 `tests/workers/test_pdf_mutate_worker.py` 追加：

```python
class TestRotateTask:
    def test_rotates_pages_and_emits_done(self, qapp, wait_worker):
        from threading import RLock
        doc = fitz.open()
        for _ in range(2):
            doc.new_page(width=612, height=792)
        pdf_doc = PdfDocument()
        pdf_doc.pages = [PdfPageInfo(page_index=i) for i in range(2)]
        task = MutateTask(kind=TaskKind.ROTATE, page_indices=[0, 1], angle=90)
        worker = PdfMutateWorker("rot.pdf", doc, pdf_doc, RLock(), task)
        done: list = []
        worker.all_done.connect(
            lambda sid, r: done.append(sid), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)
        assert done == ["rot.pdf"]
        assert doc[0].rotation == 90
        assert doc[1].rotation == 90
        doc.close()


class TestDeletePagesTask:
    def test_deletes_pages(self, qapp, wait_worker):
        from threading import RLock
        path = _open_text_pdf(3)
        doc = fitz.open(path)
        pdf_doc = PdfDocument(file_path=path)
        pdf_doc.pages = [PdfPageInfo(page_index=i) for i in range(3)]
        task = MutateTask(kind=TaskKind.DELETE_PAGES, page_indices=[1])
        worker = PdfMutateWorker(path, doc, pdf_doc, RLock(), task)
        done: list = []
        worker.all_done.connect(
            lambda sid, r: done.append(sid), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)
        assert done == [path]
        assert doc.page_count == 2
        doc.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/workers/test_pdf_mutate_worker.py::TestRotateTask tests/workers/test_pdf_mutate_worker.py::TestDeletePagesTask -v`
Expected: FAIL（ValueError 未支持的任务类型）

- [ ] **Step 3: 扩展 _dispatch + handlers**

在 `pdf_mutate_worker.py` 的 `_dispatch` 方法中补充映射，并在类中添加 handlers：

```python
    def _dispatch(self):
        kind = self._task.kind
        handlers = {
            TaskKind.DELETE_TEXT_LAYER: self._run_delete_text_layer,
            TaskKind.ROTATE: self._run_rotate,
            TaskKind.DELETE_PAGES: self._run_delete_pages,
            TaskKind.REORDER: self._run_reorder,
            TaskKind.INSERT_BLANK: self._run_insert_blank,
            TaskKind.INSERT_FROM: self._run_insert_from,
            TaskKind.SAVE: self._run_save,
            TaskKind.SAVE_AS: self._run_save_as,
        }
        handler = handlers.get(kind)
        if handler is None:
            raise ValueError(f"未支持的任务类型: {kind}")
        return handler

    def _run_rotate(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        indices = self._task.page_indices
        total = len(indices)
        with self._doc_lock:
            PdfService.rotate_pages(
                self._doc, self._pdf_document, indices, self._task.angle
            )
        for n, idx in enumerate(indices):
            if self._cancelled:
                break
            self.page_done.emit(idx, None)
            self.progress.emit(n + 1, total)
        self.all_done.emit(self._session_id, None)

    def _run_delete_pages(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        with self._doc_lock:
            PdfService.delete_pages(
                self._doc, self._pdf_document, self._task.page_indices
            )
        self.all_done.emit(self._session_id, None)

    def _run_reorder(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        with self._doc_lock:
            PdfService.reorder_pages(
                self._doc, self._pdf_document, self._task.new_order
            )
        self.all_done.emit(self._session_id, None)

    def _run_insert_blank(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        with self._doc_lock:
            PdfService.insert_blank_page(
                self._doc, self._pdf_document,
                self._task.after_index, self._task.width, self._task.height,
            )
        self.all_done.emit(self._session_id, None)

    def _run_insert_from(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        with self._doc_lock:
            PdfService.insert_pages_from(
                self._doc, self._pdf_document,
                self._task.source_path, self._task.after_index,
            )
        self.all_done.emit(self._session_id, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/workers/test_pdf_mutate_worker.py::TestRotateTask tests/workers/test_pdf_mutate_worker.py::TestDeletePagesTask -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/workers/pdf_mutate_worker.py tests/workers/test_pdf_mutate_worker.py
git commit -m "feat(worker): PdfMutateWorker 支持 ROTATE/DELETE_PAGES/REORDER/INSERT"
```

---

### Task 9: PdfMutateWorker — SAVE / SAVE_AS

**Files:**
- Modify: `src/vibeocr/workers/pdf_mutate_worker.py` (_run_save / _run_save_as)
- Modify: `tests/workers/test_pdf_mutate_worker.py`

- [ ] **Step 1: Write failing test**

```python
class TestSaveTask:
    def test_save_resets_modified_flag(self, qapp, wait_worker, tmp_path):
        from threading import RLock
        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.services.pdf_service import PdfService

        # 扫描件 PDF + 注入文字层
        import numpy as np
        path = tmp_path / "scan.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pm = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pm)
        doc.save(str(path))
        doc.close()

        doc = fitz.open(str(path))
        pdf_doc = PdfDocument(file_path=str(path))
        pdf_doc.pages = [PdfPageInfo(page_index=0)]
        result = OCRResult(
            raw_text="Hi",
            text_blocks=[TextBlock(text="Hi", score=0.9, bbox=(50, 50, 200, 100))],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        pdf_doc.is_modified = True

        task = MutateTask(kind=TaskKind.SAVE)
        worker = PdfMutateWorker(str(path), doc, pdf_doc, RLock(), task)
        done: list = []
        worker.all_done.connect(
            lambda sid, r: done.append(r), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)
        assert len(done) == 1
        assert pdf_doc.is_modified is False
        # 验证落盘内容
        verify = fitz.open(str(path))
        assert "Hi" in verify[0].get_text()
        verify.close()
        doc.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/workers/test_pdf_mutate_worker.py::TestSaveTask -v`
Expected: FAIL（_run_save 未实现）

- [ ] **Step 3: 实现 _run_save / _run_save_as**

在 `pdf_mutate_worker.py` 的 PdfMutateWorker 类中添加。直接调 `save_with_rewrite`（内部已含 rewrite + 落盘），避免双重 rewrite：

```python
    def _run_save(self) -> None:
        self._do_save(path=None)

    def _run_save_as(self) -> None:
        self._do_save(path=self._task.path)

    def _do_save(self, path: str | None) -> None:
        from vibeocr.services.pdf_service import PdfService

        try:
            with self._doc_lock:
                save_result = PdfService.save_with_rewrite(
                    self._doc, self._pdf_document, path=path,
                    pdf_settings=self._task.pdf_settings,
                )
            # save_with_rewrite 内部已 rewrite，一次性 emit 进度
            total = len(save_result.rewritten_pages)
            self.progress.emit(total, total)
            self.all_done.emit(self._session_id, save_result)
        except Exception as e:
            logger.error("保存失败: %s", e, exc_info=True)
            self.failed.emit(self._session_id, str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/workers/test_pdf_mutate_worker.py::TestSaveTask -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/workers/pdf_mutate_worker.py tests/workers/test_pdf_mutate_worker.py
git commit -m "feat(worker): PdfMutateWorker 支持 SAVE/SAVE_AS"
```

---

### Task 10: PdfRenderWorker

**Files:**
- Create: `src/vibeocr/workers/pdf_render_worker.py`
- Test: `tests/workers/test_pdf_render_worker.py` (Create)

- [ ] **Step 1: Write failing test**

```python
# tests/workers/test_pdf_render_worker.py
"""PdfRenderWorker: 后台逐页渲染，推入 queue。"""

from queue import Queue
from threading import RLock
from unittest.mock import MagicMock

import fitz
import numpy as np
import pytest
from PySide6.QtCore import Qt

from vibeocr.workers.pdf_render_worker import PdfRenderWorker


def _open_pdf(num_pages=3):
    import tempfile, pathlib
    td = tempfile.mkdtemp()
    path = pathlib.Path(td) / "t.pdf"
    doc = fitz.open()
    for i in range(num_pages):
        doc.new_page(width=200, height=200)
    doc.save(str(path))
    doc.close()
    return str(path)


class TestPdfRenderWorker:
    def test_renders_all_pages_to_queue(self, qapp, wait_worker):
        path = _open_pdf(3)
        doc = fitz.open(path)
        q: Queue = Queue(maxsize=5)
        progress: list = []
        worker = PdfRenderWorker(
            session_id=path, doc=doc, doc_lock=RLock(),
            page_indices=[0, 1, 2], pdf_settings=None, render_queue=q,
        )
        worker.render_progress.connect(
            lambda sid, c, t: progress.append((c, t)), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)

        # 取出队列项：应有 3 个数组 + 1 个哨兵
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        arrays = [it for it in items if it is not None]
        assert len(arrays) == 3
        # 最后一项是哨兵 None
        assert items[-1] is None
        # 每项是 (page_index, np.ndarray)
        for idx, arr in arrays:
            assert isinstance(idx, int)
            assert isinstance(arr, np.ndarray) and arr.size > 0
        doc.close()

    def test_cancel_pushes_sentinel(self, qapp, wait_worker):
        """取消后必须推哨兵，避免 OCR worker queue.get() 永久阻塞。"""
        path = _open_pdf(5)
        doc = fitz.open(path)
        q: Queue = Queue(maxsize=10)
        worker = PdfRenderWorker(
            session_id=path, doc=doc, doc_lock=RLock(),
            page_indices=list(range(5)), pdf_settings=None, render_queue=q,
        )
        # 启动后立即取消
        worker.start()
        worker.cancel()
        wait_worker(worker)
        # 必须有哨兵
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert items[-1] is None
        doc.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/workers/test_pdf_render_worker.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 PdfRenderWorker**

```python
# src/vibeocr/workers/pdf_render_worker.py
"""PDF 渲染 Worker — 后台逐页渲染为 numpy 数组，推入 queue 供 OCR worker 消费。

解决批量 OCR 前置渲染阻塞主线程的问题。queue 有背压（maxsize），
内存峰值受控。
"""

from __future__ import annotations

import logging
from queue import Queue
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    import threading

    import fitz

logger = logging.getLogger(__name__)


class PdfRenderWorker(QThread):
    """逐页渲染 Worker。

    Signals:
        render_progress(session_id: str, current: int, total: int)
        all_done(session_id: str)
    """

    render_progress = Signal(str, int, int)
    all_done = Signal(str)

    def __init__(
        self,
        session_id: str,
        doc: fitz.Document,
        doc_lock: threading.RLock,
        page_indices: list[int],
        pdf_settings: object | None,
        render_queue: Queue,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._doc = doc
        self._doc_lock = doc_lock
        self._page_indices = page_indices
        self._pdf_settings = pdf_settings
        self._queue = render_queue
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def session_id(self) -> str:
        return self._session_id

    def run(self) -> None:
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings
        from vibeocr.services.pdf_service import PdfService

        settings = self._pdf_settings if self._pdf_settings is not None else PdfGlobalSettings()
        total = len(self._page_indices)
        try:
            for n, page_idx in enumerate(self._page_indices):
                if self._cancelled:
                    break
                try:
                    with self._doc_lock:
                        page = self._doc[page_idx]
                        adjusted_dpi = settings.adjust_dpi(
                            page.rect.width, page.rect.height
                        )
                        img_array = PdfService.render_page_as_array(
                            self._doc, page_idx, dpi=adjusted_dpi
                        )
                    if img_array.size > 0:
                        self._queue.put((page_idx, img_array))
                except Exception as e:
                    logger.error("渲染页 %d 失败: %s", page_idx, e)
                    self._queue.put((page_idx, None))
                self.render_progress.emit(self._session_id, n + 1, total)
        finally:
            # 无论完成还是取消，都推哨兵通知 OCR worker 结束
            self._queue.put(None)
            self.all_done.emit(self._session_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/workers/test_pdf_render_worker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/workers/pdf_render_worker.py tests/workers/test_pdf_render_worker.py
git commit -m "feat(worker): PdfRenderWorker 后台逐页渲染 + queue 背压"
```

---

### Task 11: PdfOcrWorker 改造为 queue 流式消费

**Files:**
- Modify: `src/vibeocr/workers/pdf_ocr_worker.py`
- Modify: `tests/workers/test_pdf_ocr_worker.py` (适配 + 新增流式测试)

这是痛点 2 的核心修复。PdfOcrWorker 从接收预渲染列表改为消费 queue。

- [ ] **Step 1: Write failing test (流式模式)**

在 `tests/workers/test_pdf_ocr_worker.py` 追加：

```python
class TestPdfOcrWorkerStreaming:
    """queue 流式消费模式：从 render_queue 取 (idx, array) 识别。"""

    def test_consumes_queue_until_sentinel(self, qapp, wait_worker):
        from queue import Queue
        mock_service = MagicMock()
        mock_service.recognize_batch.return_value = [_mk_result("a"), _mk_result("b")]
        q: Queue = Queue()
        q.put((0, np.ones((10, 10, 3), dtype=np.uint8)))
        q.put((1, np.ones((10, 10, 3), dtype=np.uint8)))
        q.put(None)  # 哨兵

        done_pages: list = []
        done_summary: list = []
        worker = PdfOcrWorker(
            session_id="stream.pdf", ocr_service=mock_service,
            ocr_options=None, render_queue=q,
        )
        worker.page_done.connect(
            lambda i, r: done_pages.append((i, r.raw_text)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.all_done.connect(
            lambda sid, s, f: done_summary.append((sid, s, f)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert worker.isFinished()
        assert done_pages == [(0, "a"), (1, "b")]
        assert done_summary == [("stream.pdf", 2, 0)]

    def test_handles_none_array_as_fail(self, qapp, wait_worker):
        """渲染失败的页（array=None）计为 fail，不调 recognize。"""
        from queue import Queue
        mock_service = MagicMock()
        q: Queue = Queue()
        q.put((0, None))  # 渲染失败
        q.put(None)

        done_pages: list = []
        done_summary: list = []
        worker = PdfOcrWorker(
            session_id="fail.pdf", ocr_service=mock_service,
            ocr_options=None, render_queue=q,
        )
        worker.page_done.connect(
            lambda i, r: done_pages.append((i, r)), Qt.ConnectionType.DirectConnection
        )
        worker.all_done.connect(
            lambda sid, s, f: done_summary.append((sid, s, f)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.start()
        wait_worker(worker)

        assert done_pages == [(0, None)]
        assert done_summary == [("fail.pdf", 0, 1)]
        mock_service.recognize_batch.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/workers/test_pdf_ocr_worker.py::TestPdfOcrWorkerStreaming -v`
Expected: FAIL（PdfOcrWorker 不接受 render_queue 参数）

- [ ] **Step 3: 改造 PdfOcrWorker 支持 queue 模式**

改造 `src/vibeocr/workers/pdf_ocr_worker.py`。保留旧的 `pages` 列表模式（向后兼容现有测试），新增 `render_queue` 模式：

```python
class PdfOcrWorker(QThread):
    """异步 OCR Worker。

    支持两种输入模式：
    1. 预渲染列表（pages）：向后兼容，主线程预渲染所有页后传入。
    2. queue 流式（render_queue）：从 PdfRenderWorker 推入的 queue 消费，
       边渲染边识别，内存峰值低。收到哨兵 None 结束。

    Signals:
        page_done(page_index: int, result: OCRResult | None)
        progress(current: int, total: int)
        all_done(session_id: str, success_count: int, fail_count: int)
    """

    page_done = Signal(int, object)
    progress = Signal(int, int)
    all_done = Signal(str, int, int)

    def __init__(
        self,
        session_id: str,
        ocr_service: OCRServiceBase,
        ocr_options: OCROptions | None = None,
        pages: list | None = None,
        render_queue=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._ocr_service = ocr_service
        self._ocr_options = ocr_options
        self._pages = pages or []
        self._render_queue = render_queue
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def session_id(self) -> str:
        return self._session_id

    DEFAULT_BATCH_SIZE = 10

    # _compute_batch_size 不变（保留现有实现）

    def run(self) -> None:
        from vibeocr.models.ocr_options import OCROptions

        options = self._ocr_options if self._ocr_options is not None else OCROptions()

        if self._render_queue is not None:
            self._run_streaming(options)
        else:
            self._run_batch(options)

    def _run_batch(self, options) -> None:
        """原批量模式（向后兼容）。"""
        # 把原 run() 的 body 移到这里（从 total = len(self._pages) 开始）
        total = len(self._pages)
        if total == 0:
            self.all_done.emit(self._session_id, 0, 0)
            return
        use_gpu = os.environ.get("VIBEOCR_USE_GPU", "").lower() == "true"
        batch_size = self._compute_batch_size(self._pages, use_gpu=use_gpu)
        logger.info("[PdfOcrWorker] 批量模式 batch=%d 页数=%d", batch_size, total)
        success = 0
        fail = 0
        processed = 0
        for batch_start in range(0, total, batch_size):
            if self._cancelled:
                break
            batch_end = min(batch_start + batch_size, total)
            batch_pages = self._pages[batch_start:batch_end]
            batch_indices = [idx for idx, _ in batch_pages]
            batch_images = [img for _, img in batch_pages]
            results = self._recognize_batch(batch_images, options)
            for _i, (page_index, result) in enumerate(
                zip(batch_indices, results, strict=False)
            ):
                if self._cancelled:
                    break
                processed += 1
                self.progress.emit(processed, total)
                if result is not None:
                    self.page_done.emit(page_index, result)
                    success += 1
                else:
                    self.page_done.emit(page_index, None)
                    fail += 1
        self.all_done.emit(self._session_id, success, fail)

    def _run_streaming(self, options) -> None:
        """queue 流式模式：边渲染边识别。"""
        success = 0
        fail = 0
        processed = 0
        pending: list[tuple[int, object]] = []  # 攒 batch：(page_index, array)

        def flush():
            nonlocal success, fail, processed
            if not pending:
                return
            indices = [idx for idx, _ in pending]
            images = [arr for _, arr in pending]
            results = self._recognize_batch(images, options)
            for page_index, result in zip(indices, results, strict=False):
                processed += 1
                self.progress.emit(processed, processed)  # 流式 total 未知
                if result is not None:
                    self.page_done.emit(page_index, result)
                    success += 1
                else:
                    self.page_done.emit(page_index, None)
                    fail += 1
            pending.clear()

        while not self._cancelled:
            item = self._render_queue.get()  # 阻塞等待
            if item is None:
                break  # 哨兵
            page_index, array = item
            if array is None:
                # 渲染失败页
                processed += 1
                self.progress.emit(processed, processed)
                self.page_done.emit(page_index, None)
                fail += 1
                continue
            pending.append((page_index, array))
            # 攒到 batch_size 就 flush（流式下 batch 较小，控制延迟）
            if len(pending) >= self.DEFAULT_BATCH_SIZE:
                flush()
        if self._cancelled:
            pending.clear()
        else:
            flush()  # flush 剩余
        self.all_done.emit(self._session_id, success, fail)

    # _recognize_batch 不变
```

- [ ] **Step 4: Run new tests**

Run: `python -m pytest tests/workers/test_pdf_ocr_worker.py::TestPdfOcrWorkerStreaming -v`
Expected: PASS

- [ ] **Step 5: Run existing ocr worker tests to verify backward compat**

Run: `python -m pytest tests/workers/test_pdf_ocr_worker.py -v`
Expected: PASS（旧的 pages 模式测试全部通过）

- [ ] **Step 6: Commit**

```bash
git add src/vibeocr/workers/pdf_ocr_worker.py tests/workers/test_pdf_ocr_worker.py
git commit -m "feat(worker): PdfOcrWorker 支持 queue 流式消费模式

保留 pages 列表模式向后兼容。流式模式从 render_queue 取页识别，
内存峰值从全部页降到 1-2 页，主线程零渲染。"
```

---

### Task 12: PdfExportWorker

**Files:**
- Create: `src/vibeocr/workers/pdf_export_worker.py`
- Test: `tests/workers/test_pdf_export_worker.py` (Create)

- [ ] **Step 1: Write failing test**

```python
# tests/workers/test_pdf_export_worker.py
"""PdfExportWorker: 跨 session 批量导出。"""

from threading import RLock
from unittest.mock import MagicMock

import fitz
import pytest
from PySide6.QtCore import Qt

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.models.pdf_session import PdfSession
from vibeocr.workers.pdf_export_worker import PdfExportWorker


def _make_session(path, modified=True):
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    pdf_doc = PdfDocument(file_path=path)
    pdf_doc.pages = [PdfPageInfo(page_index=0)]
    pdf_doc.is_modified = modified
    return PdfSession(file_path=path, doc=doc, pdf_document=pdf_doc)


class TestPdfExportWorker:
    def test_exports_modified_sessions(self, qapp, wait_worker, tmp_path):
        s1 = _make_session(str(tmp_path / "a.pdf"))
        s2 = _make_session(str(tmp_path / "b.pdf"))
        out = tmp_path / "out"
        out.mkdir()
        worker = PdfExportWorker([s1, s2], str(out))
        exported: list = []
        worker.done.connect(
            lambda paths: exported.extend(paths), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)
        assert len(exported) == 2
        assert (out / "a.pdf").exists()
        assert (out / "b.pdf").exists()
        s1.doc.close()
        s2.doc.close()

    def test_skips_unmodified(self, qapp, wait_worker, tmp_path):
        s1 = _make_session(str(tmp_path / "a.pdf"), modified=True)
        s2 = _make_session(str(tmp_path / "b.pdf"), modified=False)
        out = tmp_path / "out"
        out.mkdir()
        worker = PdfExportWorker([s1, s2], str(out))
        exported: list = []
        worker.done.connect(
            lambda paths: exported.extend(paths), Qt.ConnectionType.DirectConnection
        )
        worker.start()
        wait_worker(worker)
        assert len(exported) == 1
        s1.doc.close()
        s2.doc.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/workers/test_pdf_export_worker.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 PdfExportWorker**

```python
# src/vibeocr/workers/pdf_export_worker.py
"""PDF 批量导出 Worker — 跨 session 遍历，各经 doc_lock。

与 PdfMutateWorker（单 doc 绑定）正交：导出需遍历多个 session。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    from vibeocr.models.pdf_session import PdfSession

logger = logging.getLogger(__name__)


class PdfExportWorker(QThread):
    """跨 session 批量导出 Worker。

    Signals:
        progress(current: int, total: int, file_name: str)
        done(exported_paths: list[str])
    """

    progress = Signal(int, int, str)
    done = Signal(list)

    def __init__(
        self,
        sessions: list[tuple[str, PdfSession]] | list[PdfSession],
        output_dir: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        # 统一为 list[PdfSession]（兼容 (path, session) 和 session 两种入参）
        self._sessions = [
            s if not isinstance(s, tuple) else s[1] for s in sessions
        ]
        self._output_dir = output_dir
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        from vibeocr.services.pdf_service import PdfService

        out = Path(self._output_dir)
        out.mkdir(parents=True, exist_ok=True)
        to_export = [s for s in self._sessions if s.is_modified]
        total = len(to_export)
        exported: list[str] = []

        for n, session in enumerate(to_export):
            if self._cancelled:
                break
            name = Path(session.file_path).name
            dest = out / name
            if dest.exists():
                stem = dest.stem
                counter = 1
                while (out / f"{stem}_{counter}{dest.suffix}").exists():
                    counter += 1
                dest = out / f"{stem}_{counter}{dest.suffix}"
            try:
                with session.doc_lock:
                    PdfService.save_with_rewrite(
                        session.doc, session.pdf_document, path=str(dest),
                    )
                exported.append(str(dest))
            except Exception as e:
                logger.error("导出失败 %s: %s", session.file_path, e)
            self.progress.emit(n + 1, total, name)
        self.done.emit(exported)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/workers/test_pdf_export_worker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/workers/pdf_export_worker.py tests/workers/test_pdf_export_worker.py
git commit -m "feat(worker): PdfExportWorker 跨 session 批量导出"
```

---

## 阶段 3：Manager 层编排

### Task 13: PdfSessionManager — start_ocr 流式编排

**Files:**
- Modify: `src/vibeocr/managers/pdf_session_manager.py` (start_ocr, cancel_ocr, 新增信号)
- Modify: `tests/managers/test_pdf_session_manager.py`

这是痛点 2 在 manager 层的修复。

- [ ] **Step 1: Write failing test**

在 `tests/managers/test_pdf_session_manager.py` 追加：

```python
class TestStartOcrStreaming:
    def test_uses_render_worker_and_queue(self, manager, test_pdf_a, monkeypatch):
        """start_ocr 应启动 PdfRenderWorker + PdfOcrWorker(queue 模式)。"""
        from queue import Queue
        from unittest.mock import MagicMock

        from vibeocr.services.pdf_service import PdfService

        session = manager.open_session(str(test_pdf_a))
        with session.doc_lock:
            PdfService.build_page_infos(session.doc, session.pdf_document)

        render_created = []
        ocr_created = []
        monkeypatch.setattr(
            "vibeocr.managers.pdf_session_manager.PdfRenderWorker",
            lambda *a, **k: render_created.append(k) or MagicMock(),
        )
        monkeypatch.setattr(
            "vibeocr.managers.pdf_session_manager.PdfOcrWorker",
            lambda *a, **k: ocr_created.append(k) or MagicMock(),
        )
        manager._ocr_service = MagicMock()

        manager.start_ocr([0])
        assert len(render_created) == 1
        assert len(ocr_created) == 1
        # ocr worker 应以 render_queue 参数构造
        assert "render_queue" in ocr_created[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/managers/test_pdf_session_manager.py::TestStartOcrStreaming -v`
Expected: FAIL（仍用旧 pages 列表模式）

- [ ] **Step 3: 改造 start_ocr**

修改 `src/vibeocr/managers/pdf_session_manager.py`：

顶部 import 添加：
```python
from queue import Queue
from vibeocr.workers.pdf_render_worker import PdfRenderWorker
```

新增信号（在现有信号定义区）：
```python
    render_progress = Signal(str, int, int)  # (file_path, current, total)
```

替换 `start_ocr` 方法（206-259 行），去掉主线程渲染循环：

```python
    def start_ocr(
        self,
        page_indices: list[int],
        ocr_options: OCROptions | None = None,
        pdf_settings: PdfGlobalSettings | None = None,
        overwrite: bool = False,
    ) -> None:
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        if pdf_settings is None:
            pdf_settings = PdfGlobalSettings()

        session = self.active_session
        if session is None or self._ocr_service is None:
            return

        if self._is_mineru_first_use(ocr_options):
            if not self._ensure_mineru_models_blocking(session.file_path):
                return

        self._cancel_ocr_pipeline()

        self._pdf_settings = pdf_settings
        self._overwrite_text_layer = overwrite
        session.reset_ocr_stats()

        # 流式：render worker 后台逐页渲染 → queue → ocr worker 消费
        render_queue: Queue = Queue(maxsize=2)
        self._render_worker = PdfRenderWorker(
            session_id=session.file_path,
            doc=session.doc,
            doc_lock=session.doc_lock,
            page_indices=page_indices,
            pdf_settings=pdf_settings,
            render_queue=render_queue,
        )
        self._render_worker.render_progress.connect(self._on_render_progress)

        self._ocr_worker = PdfOcrWorker(
            session_id=session.file_path,
            ocr_service=self._ocr_service,
            ocr_options=ocr_options,
            render_queue=render_queue,
        )
        self._ocr_worker.page_done.connect(self._on_ocr_page_done)
        self._ocr_worker.progress.connect(self._on_ocr_progress)
        self._ocr_worker.all_done.connect(self._on_ocr_all_done)

        self._render_worker.start()
        self._ocr_worker.start()

    def _on_render_progress(self, session_id: str, current: int, total: int) -> None:
        self.render_progress.emit(session_id, current, total)
```

- [ ] **Step 4: 改造 cancel_ocr 同时取消 render + ocr**

替换 `_cancel_ocr_worker`（306-310 行）为 `_cancel_ocr_pipeline`：

```python
    def cancel_ocr(self) -> None:
        self._cancel_ocr_pipeline()

    def _cancel_ocr_pipeline(self) -> None:
        """取消 render + ocr worker。render 取消后推哨兵，ocr 自然结束。"""
        if self._render_worker is not None:
            self._render_worker.cancel()
            _wait_thread(self._render_worker, timeout=5000)
            self._render_worker = None
        if self._ocr_worker is not None:
            self._ocr_worker.cancel()
            _wait_thread(self._ocr_worker, timeout=5000)
            self._ocr_worker = None
```

在 `__init__` 中初始化 `self._render_worker: PdfRenderWorker | None = None`。

在 `switch_session` / `shutdown` 中把 `_cancel_ocr_worker()` 调用改为 `_cancel_ocr_pipeline()`。

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/managers/test_pdf_session_manager.py::TestStartOcrStreaming -v`
Expected: PASS

- [ ] **Step 6: Run full manager test suite**

Run: `python -m pytest tests/managers/test_pdf_session_manager.py -v`
Expected: PASS（修适配任何因 _cancel_ocr_worker 改名失败的测试）

- [ ] **Step 7: Commit**

```bash
git add src/vibeocr/managers/pdf_session_manager.py tests/managers/test_pdf_session_manager.py
git commit -m "feat(manager): start_ocr 改为 render+ocr 流式编排

主线程零渲染，内存峰值从全部页降到 1-2 页。cancel_ocr_pipeline
同时取消两个 worker。"
```

---

### Task 14: PdfSessionManager — save_async / delete_text_layers_async / mutate 编排

**Files:**
- Modify: `src/vibeocr/managers/pdf_session_manager.py`
- Modify: `tests/managers/test_pdf_session_manager.py`

- [ ] **Step 1: Write failing test**

在 `tests/managers/test_pdf_session_manager.py` 追加：

```python
class TestSaveAsync:
    def test_save_async_starts_mutate_worker(self, manager, test_pdf_a, monkeypatch):
        from unittest.mock import MagicMock
        from vibeocr.workers.pdf_mutate_worker import MutateTask, TaskKind

        session = manager.open_session(str(test_pdf_a))
        pdf_doc = session.pdf_document
        pdf_doc.is_modified = True

        created_tasks = []
        fake_worker = MagicMock()
        fake_worker.session_id = session.file_path
        monkeypatch.setattr(
            "vibeocr.managers.pdf_session_manager.PdfMutateWorker",
            lambda *a, **k: created_tasks.append(k.get("task")) or fake_worker,
        )

        manager.save_async()
        assert len(created_tasks) == 1
        assert created_tasks[0].kind == TaskKind.SAVE


class TestDeleteTextLayerAsync:
    def test_starts_mutate_worker(self, manager, test_pdf_a, monkeypatch):
        from unittest.mock import MagicMock
        from vibeocr.workers.pdf_mutate_worker import MutateTask, TaskKind

        session = manager.open_session(str(test_pdf_a))
        fake_worker = MagicMock()
        fake_worker.session_id = session.file_path
        monkeypatch.setattr(
            "vibeocr.managers.pdf_session_manager.PdfMutateWorker",
            lambda *a, **k: fake_worker,
        )
        manager.delete_text_layers_async([0])
        # worker 已构造（mock 不真实 start，验证 task kind 即可）
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/managers/test_pdf_session_manager.py::TestSaveAsync tests/managers/test_pdf_session_manager.py::TestDeleteTextLayerAsync -v`
Expected: FAIL（方法不存在）

- [ ] **Step 3: 实现异步方法 + 信号**

在 `pdf_session_manager.py` 添加 import 和方法：

```python
from vibeocr.workers.pdf_mutate_worker import PdfMutateWorker, MutateTask, TaskKind
from vibeocr.workers.pdf_export_worker import PdfExportWorker
```

新增信号：
```python
    mutate_progress = Signal(str, int, int)
    mutate_done = Signal(str, object)
    mutate_failed = Signal(str, str)
    save_done = Signal(str)
    delete_layer_done = Signal(str, list)  # (file_path, residual_pages)
```

在 `__init__` 添加：`self._mutate_worker: PdfMutateWorker | None = None`

方法实现：

```python
    def save_async(self, path: str | None = None, pdf_settings=None) -> None:
        """异步保存（rewrite + 落盘在后台）。path=None 覆盖原文件。"""
        session = self.active_session
        if session is None:
            return
        self._cancel_mutate_worker()
        kind = TaskKind.SAVE_AS if path is not None else TaskKind.SAVE
        task = MutateTask(kind=kind, path=path, pdf_settings=pdf_settings)
        self._start_mutate(session, task)

    def delete_text_layers_async(self, page_indices: list[int]) -> None:
        """异步删除文字层（逐页词级 redact 在后台）。"""
        session = self.active_session
        if session is None:
            return
        self._cancel_mutate_worker()
        task = MutateTask(kind=TaskKind.DELETE_TEXT_LAYER, page_indices=page_indices)
        self._start_mutate(session, task)

    def rotate_pages_async(self, page_indices: list[int], angle: int) -> None:
        session = self.active_session
        if session is None:
            return
        self._cancel_mutate_worker()
        task = MutateTask(kind=TaskKind.ROTATE, page_indices=page_indices, angle=angle)
        self._start_mutate(session, task)

    def delete_pages_async(self, page_indices: list[int]) -> None:
        session = self.active_session
        if session is None:
            return
        self._cancel_mutate_worker()
        task = MutateTask(kind=TaskKind.DELETE_PAGES, page_indices=page_indices)
        self._start_mutate(session, task)

    def _start_mutate(self, session, task: MutateTask) -> None:
        self._mutate_worker = PdfMutateWorker(
            session_id=session.file_path,
            doc=session.doc,
            pdf_document=session.pdf_document,
            doc_lock=session.doc_lock,
            task=task,
        )
        self._mutate_worker.page_done.connect(self._on_mutate_page_done)
        self._mutate_worker.progress.connect(self._on_mutate_progress)
        self._mutate_worker.all_done.connect(self._on_mutate_all_done)
        self._mutate_worker.failed.connect(self._on_mutate_failed)
        self._mutate_worker.start()

    def _cancel_mutate_worker(self) -> None:
        if self._mutate_worker is not None:
            self._mutate_worker.cancel()
            _wait_thread(self._mutate_worker, timeout=5000)
            self._mutate_worker = None

    def _on_mutate_progress(self, current: int, total: int) -> None:
        session = self.active_session
        if session:
            self.mutate_progress.emit(session.file_path, current, total)

    def _on_mutate_page_done(self, page_index: int, payload) -> None:
        """逐页完成 → 通知 UI 更新（中转到 PdfTab）。"""
        # PdfTab 通过 mutate_done 的逐页信号或直接连 worker 处理；
        # 这里转发为通用 mutate_done（payload 含 page_index）
        session = self.active_session
        if session:
            self.mutate_done.emit(session.file_path, {"page": page_index, "payload": payload})

    def _on_mutate_all_done(self, session_id: str, result) -> None:
        worker = self._mutate_worker
        if worker is not None:
            self._mutate_worker = None
        # 按任务类型转发专用信号
        session = self._sessions.get(session_id)
        if session is None:
            return
        # 删除文字层：result 含 residual_pages
        if isinstance(result, dict) and "residual_pages" in result:
            self.delete_layer_done.emit(session_id, result["residual_pages"])
        elif result is not None:
            # SAVE/SAVE_AS：result 是 SaveResult
            self.save_done.emit(session_id)
        self.mutate_done.emit(session_id, result)

    def _on_mutate_failed(self, session_id: str, error: str) -> None:
        if self._mutate_worker is not None:
            self._mutate_worker = None
        self.mutate_failed.emit(session_id, error)
```

- [ ] **Step 4: 在 switch_session / shutdown 中也取消 mutate worker**

在 `switch_session` 的 `_cancel_ocr_worker()`（现 `_cancel_ocr_pipeline()`）后加 `self._cancel_mutate_worker()`。
在 `shutdown` 中加 `self._cancel_mutate_worker()`。

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/managers/test_pdf_session_manager.py::TestSaveAsync tests/managers/test_pdf_session_manager.py::TestDeleteTextLayerAsync -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/vibeocr/managers/pdf_session_manager.py tests/managers/test_pdf_session_manager.py
git commit -m "feat(manager): save_async/delete_text_layers_async 等 mutate 异步编排"
```

---

### Task 15: PdfSessionManager — export_all_async

**Files:**
- Modify: `src/vibeocr/managers/pdf_session_manager.py`
- Modify: `tests/managers/test_pdf_session_manager.py`

- [ ] **Step 1: Write failing test**

```python
class TestExportAllAsync:
    def test_starts_export_worker(self, manager, test_pdf_a, monkeypatch):
        from unittest.mock import MagicMock

        session = manager.open_session(str(test_pdf_a))
        session.pdf_document.is_modified = True

        created = []
        monkeypatch.setattr(
            "vibeocr.managers.pdf_session_manager.PdfExportWorker",
            lambda sessions, out, **k: created.append((sessions, out)) or MagicMock(),
        )
        manager.export_all_async("/tmp/out")
        assert len(created) == 1
        sessions_arg, out_arg = created[0]
        assert out_arg == "/tmp/out"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/managers/test_pdf_session_manager.py::TestExportAllAsync -v`
Expected: FAIL

- [ ] **Step 3: 实现 export_all_async**

新增信号：
```python
    export_progress = Signal(int, int, str)
    export_done = Signal(list)
```

`__init__` 添加：`self._export_worker: PdfExportWorker | None = None`

方法：
```python
    def export_all_async(self, output_dir: str) -> None:
        """异步批量导出所有 modified session。"""
        sessions = [s for _, s in self.get_modified_sessions()]
        if not sessions:
            self.export_done.emit([])
            return
        self._export_worker = PdfExportWorker(sessions, output_dir)
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.done.connect(self._on_export_done)
        self._export_worker.start()

    def _on_export_progress(self, current: int, total: int, file_name: str) -> None:
        self.export_progress.emit(current, total, file_name)

    def _on_export_done(self, exported_paths: list) -> None:
        self._export_worker = None
        self.export_done.emit(exported_paths)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/managers/test_pdf_session_manager.py::TestExportAllAsync -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/managers/pdf_session_manager.py tests/managers/test_pdf_session_manager.py
git commit -m "feat(manager): export_all_async 异步批量导出"
```

---

## 阶段 4：UI 层（PdfTab）

### Task 16: PdfTab — 连接 manager 新信号

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py` (_connect_manager_signals)
- Test: `tests/views/tabs/test_pdf_tab.py` (若存在，追加；否则手动验证)

- [ ] **Step 1: 连接新信号**

在 `_connect_manager_signals`（约 310-321 行）追加：

```python
        mgr.mutate_progress.connect(self._on_mutate_progress)
        mgr.mutate_done.connect(self._on_mutate_done)
        mgr.mutate_failed.connect(self._on_mutate_failed)
        mgr.save_done.connect(self._on_save_done)
        mgr.delete_layer_done.connect(self._on_delete_layer_done)
        mgr.render_progress.connect(self._on_render_progress_update)
        mgr.export_progress.connect(self._on_export_progress)
        mgr.export_done.connect(self._on_export_done)
```

- [ ] **Step 2: 添加信号处理方法（占位，逻辑在后续 Task 填充）**

在 PdfTab 类中添加各 handler 的骨架（具体实现在 Task 17-20）。先添加：

```python
    def _on_render_progress_update(self, file_path: str, current: int, total: int) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(current)
        self._status_label.setText(f"正在渲染页面 {current}/{total}…")
```

- [ ] **Step 3: Run pdf_tab 相关测试确保无破坏**

Run: `python -m pytest tests/views/tabs/ -v -k pdf`
Expected: PASS（或仅 import 错误，下一步填 handler 修复）

- [ ] **Step 4: Commit**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat(ui): PdfTab 连接 manager 异步信号"
```

---

### Task 17: PdfTab — 删除文字层改异步

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py` (_on_delete_text_layer, _on_delete_layer_done)
- 这是痛点 3 在 UI 层的修复。

- [ ] **Step 1: 改造 _on_delete_text_layer**

替换 `_on_delete_text_layer`（约 1239-1263 行）：

```python
    def _on_delete_text_layer(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        indices = self._get_selected_page_indices()
        if not indices:
            QMessageBox.information(self, "删除文字层", "请先选择页面。")
            return

        reply = QMessageBox.question(
            self,
            "删除文字层",
            f"将删除选中 {len(indices)} 页的文字层。\n建议先另存为备份。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 异步：后台逐页词级 redact，主线程不阻塞
        self._progress_bar.setRange(0, len(indices))
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._set_file_buttons_enabled(False)

        self._session_mgr.delete_text_layers_async(indices)

    def _on_mutate_progress(self, file_path: str, current: int, total: int) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
            self._status_label.setText(f"正在处理 {current}/{total}…")

    def _on_delete_layer_done(self, file_path: str, residual_pages: list) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        self._progress_bar.setVisible(False)
        self._set_file_buttons_enabled(True)
        self._update_status()
        if residual_pages:
            QMessageBox.warning(
                self,
                "删除文字层",
                f"第 {', '.join(str(p + 1) for p in residual_pages)} 页经多轮删除"
                f"仍有少量残留文字，\n可能是特殊字体或嵌入图片文字，建议手动检查。",
            )
        else:
            self._status_label.setText("文字层删除完成")
```

> 注：逐页 grid 变灰由 `_on_mutate_done`（page 级 payload）驱动，见 Task 16 的 `_on_mutate_done`。

- [ ] **Step 2: 添加 _on_mutate_done（逐页 grid 更新）**

```python
    def _on_mutate_done(self, file_path: str, result) -> None:
        """mutate 任务逐页/整体完成回调。"""
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        # 逐页 payload（_on_mutate_page_done 转发）：更新 grid 格子
        if isinstance(result, dict) and "page" in result:
            self._update_layer_grid_page(result["page"])
```

- [ ] **Step 3: 添加 _on_mutate_failed**

```python
    def _on_mutate_failed(self, file_path: str, error: str) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        self._progress_bar.setVisible(False)
        self._set_file_buttons_enabled(True)
        QMessageBox.warning(self, "操作失败", error)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ -k "pdf_tab or delete_text" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat(ui): 删除文字层改异步 + 残留 warning 提示"
```

---

### Task 18: PdfTab — 保存/另存为改异步

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py` (_on_save, _on_save_as, _on_save_done)
- 这是痛点 5 在 UI 层的修复。

- [ ] **Step 1: 改造 _on_save / _on_save_as**

替换 `_on_save`（约 818-830 行）和 `_on_save_as`（约 832-847 行）：

```python
    def _on_save(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        self._set_file_buttons_enabled(False)
        self._btn_open.setEnabled(False)
        self._btn_add_file.setEnabled(False)
        self._progress_bar.setRange(0, 0)  # 不确定进度（rewrite+落盘）
        self._progress_bar.setVisible(True)
        self._status_label.setText("正在保存…")
        pdf_settings, _ = self._load_ocr_prefs()
        self._session_mgr.save_async(path=None, pdf_settings=pdf_settings)

    def _on_save_as(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "另存为", "", "PDF 文件 (*.pdf)")
        if not path:
            return
        self._set_file_buttons_enabled(False)
        self._btn_open.setEnabled(False)
        self._btn_add_file.setEnabled(False)
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setVisible(True)
        self._status_label.setText("正在保存…")
        pdf_settings, _ = self._load_ocr_prefs()
        self._session_mgr.save_async(path=path, pdf_settings=pdf_settings)

    def _on_save_done(self, file_path: str) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        self._progress_bar.setVisible(False)
        self._set_file_buttons_enabled(True)
        self._btn_open.setEnabled(True)
        self._btn_add_file.setEnabled(True)
        self._update_status()
        self._status_label.setText(f"{Path(file_path).name} 保存完成")
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/ -k "pdf_tab or save" -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat(ui): 保存/另存为改异步，保存期间禁用按钮+进度提示"
```

---

### Task 19: PdfTab — 加载提示

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py` (_connect_manager_signals 连 load_progress, _on_load_progress)
- 这是痛点 1 的 UI 部分。

- [ ] **Step 1: 连接 load_progress + 添加 handler**

在 `_connect_manager_signals` 追加：
```python
        mgr.load_progress.connect(self._on_load_progress)
```

添加 handler：
```python
    def _on_load_progress(self, file_path: str, loaded: int, total: int) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        self._status_label.setText(f"{Path(file_path).name} 正在加载 {loaded}/{total} 页…")
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/ -k "pdf_tab or load" -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat(ui): 加载阶段状态栏提示'正在加载 N/M 页'"
```

---

### Task 20: PdfTab — 批量导出改异步

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py` (_on_export_all, _on_export_progress, _on_export_done)

- [ ] **Step 1: 改造 _on_export_all**

替换 `_on_export_all`（约 849-865 行）：

```python
    def _on_export_all(self) -> None:
        mgr = self._session_mgr
        modified_paths = [p for p, _ in mgr.get_modified_sessions()]
        if not modified_paths:
            QMessageBox.information(self, "批量导出", "没有需要导出的修改文件。")
            return

        dir_path = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not dir_path:
            return

        self._set_file_buttons_enabled(False)
        self._progress_bar.setRange(0, len(modified_paths))
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._status_label.setText("正在批量导出…")
        mgr.export_all_async(dir_path)

    def _on_export_progress(self, current: int, total: int, file_name: str) -> None:
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(current)
        self._status_label.setText(f"正在导出 {file_name} ({current}/{total})…")

    def _on_export_done(self, exported_paths: list) -> None:
        self._progress_bar.setVisible(False)
        self._set_file_buttons_enabled(True)
        QMessageBox.information(
            self,
            "批量导出完成",
            f"成功导出 {len(exported_paths)} 个文件。",
        )
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/ -k "pdf_tab or export" -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat(ui): 批量导出改异步 + 进度提示"
```

---

### Task 21: 全量回归测试

- [ ] **Step 1: 运行全部测试**

Run: `python -m pytest tests/ -v --tb=short`
Expected: 全部 PASS（修任何因异步化导致的既存测试失败）

- [ ] **Step 2: 修适配既存测试**

重点关注：
- `tests/services/test_pdf_service.py` 中调 `delete_text_layers` 取返回值的（现为三元组）
- `tests/managers/test_pdf_session_manager.py` 中 `_cancel_ocr_worker` 改名为 `_cancel_ocr_pipeline`
- `tests/views/tabs/test_single_recognition_tab.py` 等可能间接受影响

- [ ] **Step 3: 再次全量测试确认绿色**

Run: `python -m pytest tests/ -q`
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: 适配 PDF 异步化的既存测试回归"
```

---

## 完成标准

- [ ] 打开大 PDF（如 1000+ 页）主线程不冻结，状态栏显示加载进度
- [ ] 批量添加文字层（如 50 页）主线程不冻结，先显示"渲染中"再显示"识别中"
- [ ] 集中删除文字层（如 30 页）主线程不冻结，逐页 grid 变灰
- [ ] 删除文字层无遗漏（构造嵌套文本块 PDF 验证循环清零）
- [ ] 保存/另存为主线程不冻结，保存期间按钮禁用 + 进度提示
- [ ] 纯文字层编辑保存走 incremental（快），结构改动走 full save
- [ ] 批量导出异步进行 + 进度提示
- [ ] 全量测试 PASS
