# PDF 文字层修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the PDF "添加文字层" feature so Chinese text actually gets written, the status wording is accurate, preview works after completion, and the UI is resizable with an embedded preview.

**Architecture:** Service layer returns `(written, skipped)` counts and uses the built-in `china-s` CJK CID font; a new `ocr_stats_ready` signal carries aggregated stats to the UI; `PdfTab` is restructured with a nested H+V `QSplitter` (persisted via the existing JSON prefs backend) hosting an extracted `PreviewCanvas` that auto-previews after OCR.

**Tech Stack:** Python 3.13, PySide6 (pytest-qt + qtbot), PyMuPDF (`pymupdf>=1.27.2.3`, exposes CJK CID fonts `china-s`/`china-ss`), pytest + caplog (new pattern for this codebase).

**Spec:** `docs/superpowers/specs/2026-06-19-pdf-text-layer-fix-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/vibeocr/services/pdf_service.py` | `add_text_layer`: CJK font, return counts, log failures | Modify |
| `src/vibeocr/models/pdf_session.py` | `PdfSession`: add `_ocr_stats` field | Modify |
| `src/vibeocr/managers/pdf_session_manager.py` | Accumulate stats, emit `ocr_stats_ready`, reset on `start_ocr` | Modify |
| `src/vibeocr/views/pdf_preview_window.py` | Extract `_PreviewCanvas` → public `PreviewCanvas` | Modify |
| `src/vibeocr/views/tabs/pdf_tab.py` | Wording, scrollable status, nested splitters, embedded preview, auto-preview, QSettings persistence via prefs | Modify |
| `src/vibeocr/utils/ocr_preferences.py` | Add `pdf_splitter_state` key + get/set | Modify |
| `tests/services/test_pdf_service.py` | CJK write + skip-count tests | Modify |
| `tests/managers/test_pdf_session_manager.py` | stats accumulation test | Modify |
| `tests/views/tabs/test_pdf_tab.py` | (new) splitter structure + wording tests | Create |
| `tests/utils/test_ocr_preferences.py` | splitter-state round-trip | Modify |

**Conventions to follow:**
- Tests use `qtbot` fixture for widgets, `qapp` for services/workers (`tests/conftest.py`).
- `pytest-qt` config: `qt_api = "pyside6"`.
- Logging via module `logger = logging.getLogger(__name__)`; assert with `caplog` (no precedent — this plan introduces it).
- fitz test PDFs built with `fitz.open()` + `doc.new_page()`; image-only pages via `page.insert_image`.
- `docs/` is gitignored — commit doc/test/src files with `git add -f` only for paths under `docs/`.

---

## Task 1: CJK font + return counts in `add_text_layer`

**Files:**
- Modify: `src/vibeocr/services/pdf_service.py:292-353` (`add_text_layer`)
- Test: `tests/services/test_pdf_service.py` (class `TestPdfServiceTextLayer`)

- [ ] **Step 1: Write failing test — Chinese text gets written and is extractable**

Append to `tests/services/test_pdf_service.py` inside `class TestPdfServiceTextLayer`:

```python
def test_add_text_layer_writes_chinese_text(self, tmp_path):
    import numpy as np

    from vibeocr.models.ocr_result import OCRResult, TextBlock

    path = tmp_path / "scan_cn.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    img = np.ones((792, 612, 3), dtype=np.uint8) * 240
    cs = fitz.Colorspace(fitz.CS_RGB)
    pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
    page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
    doc.save(str(path))
    doc.close()

    doc, pdf_doc = PdfService.open_doc(str(path))
    chinese = "你好世界，这是一段测试文字。"
    result = OCRResult(
        raw_text=chinese,
        text_blocks=[
            TextBlock(text=chinese, score=0.99,
                      bbox=(50.0, 50.0, 500.0, 120.0), page_idx=0),
        ],
    )
    written, skipped = PdfService.add_text_layer(doc, pdf_doc, 0, result)
    assert written == 1
    assert skipped == 0
    # 中文必须能被回读（验证 china-s 字体生效）
    extracted = doc[0].get_text()
    assert "你好世界" in extracted
    doc.close()
```

- [ ] **Step 2: Write failing test — skip count + warning on too-small bbox**

Append to the same test class:

```python
def test_add_text_layer_skips_tiny_bbox_with_warning(self, tmp_path, caplog):
    import logging

    import numpy as np

    from vibeocr.models.ocr_result import OCRResult, TextBlock

    path = tmp_path / "scan_tiny.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    img = np.ones((792, 612, 3), dtype=np.uint8) * 240
    cs = fitz.Colorspace(fitz.CS_RGB)
    pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
    page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
    doc.save(str(path))
    doc.close()

    doc, pdf_doc = PdfService.open_doc(str(path))
    good = TextBlock(text="正常文字", score=0.9,
                     bbox=(50.0, 50.0, 400.0, 100.0), page_idx=0)
    # 宽高均 < 1 point → 会被跳过
    tiny = TextBlock(text="小", score=0.9,
                     bbox=(10.0, 10.0, 10.5, 10.5), page_idx=0)
    result = OCRResult(raw_text="x", text_blocks=[good, tiny])

    with caplog.at_level(logging.WARNING, logger="vibeocr.services.pdf_service"):
        written, skipped = PdfService.add_text_layer(doc, pdf_doc, 0, result)

    assert written == 1
    assert skipped == 1
    assert any("skipped" in rec.message for rec in caplog.records)
    doc.close()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/services/test_pdf_service.py::TestPdfServiceTextLayer::test_add_text_layer_writes_chinese_text tests/services/test_pdf_service.py::TestPdfServiceTextLayer::test_add_text_layer_skips_tiny_bbox_with_warning -v`
Expected: FAIL — first test fails because current `add_text_layer` returns `None` (cannot unpack) and Chinese not extractable; second fails on unpack.

- [ ] **Step 4: Implement — rewrite `add_text_layer` with CJK font + counts + logging**

In `src/vibeocr/services/pdf_service.py`, replace the entire `add_text_layer` static method (lines 292–353) with:

```python
    @staticmethod
    def add_text_layer(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        page_index: int,
        ocr_result: object,
        pdf_settings: object | None = None,
    ) -> tuple[int, int]:
        """将 OCR 结果作为隐形文字层写入 PDF 页面。

        使用内置 china-s CJK CID 字体，确保中文等字符可被写入并被阅读器提取。

        Args:
            doc: fitz.Document 实例。
            pdf_document: PdfDocument 状态对象。
            page_index: 页码索引。
            ocr_result: OCRResult 实例。
            pdf_settings: PdfGlobalSettings 实例（None 则使用默认值）。

        Returns:
            (written, skipped) 成功写入与被跳过的文本块数量。
        """
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        settings = pdf_settings if pdf_settings is not None else PdfGlobalSettings()

        page = doc[page_index]
        page_rect = page.rect
        preproc_angle = getattr(ocr_result, "preproc_angle", 0)

        written = 0
        skipped = 0
        text_blocks = getattr(ocr_result, "text_blocks", [])
        for block in text_blocks:
            if block.text is None or not block.text.strip():
                continue
            bbox = block.bbox
            if bbox is None:
                skipped += 1
                continue

            # 逆旋转 + 归一化到 PDF 页面坐标
            rect = PdfService._denormalize_and_unrotate_bbox(
                bbox, preproc_angle, page_rect
            )
            if rect.is_empty or rect.width < 1 or rect.height < 1:
                logger.warning(
                    "page %d block skipped (rect too small): rect=%s text=%r",
                    page_index, rect, block.text[:30],
                )
                skipped += 1
                continue

            fontsize = rect.height * settings.font_size_ratio
            if fontsize < 1:
                logger.warning(
                    "page %d block skipped (fontsize < 1): rect=%s text=%r",
                    page_index, rect, block.text[:30],
                )
                skipped += 1
                continue

            render_mode = 0 if settings.text_layer_visible else 3
            inserted = False
            for _ in range(settings.font_size_retry_count):
                rc = page.insert_textbox(
                    rect,
                    block.text,
                    fontsize=fontsize,
                    fontname="china-s",
                    color=(0, 0, 0),
                    render_mode=render_mode,
                )
                if rc >= 0:
                    inserted = True
                    break
                fontsize *= settings.font_size_shrink_factor
                if fontsize < 1:
                    break

            if inserted:
                written += 1
            else:
                logger.warning(
                    "page %d block skipped (font retry exhausted): rect=%s text=%r",
                    page_index, rect, block.text[:30],
                )
                skipped += 1

        pdf_document.is_modified = True
        PdfService.update_page_info(doc, pdf_document, page_index)
        return written, skipped
```

Ensure the module has `logger` defined near the top of `pdf_service.py`. If not present, add after imports:
```python
import logging
logger = logging.getLogger(__name__)
```
(Verify it already exists before adding — grep `logger = logging.getLogger` in the file.)

- [ ] **Step 5: Run the two new tests to verify they pass**

Run: `pytest tests/services/test_pdf_service.py::TestPdfServiceTextLayer -v`
Expected: PASS for both new tests.

- [ ] **Step 6: Run the full test_pdf_service suite to check for regressions**

Run: `pytest tests/services/test_pdf_service.py -v`
Expected: ALL PASS. The existing `test_add_text_layer_from_ocr_result` still passes (it ignores the new return value). If any existing test asserted on the old `None` return, update it to unpack or ignore — but none should.

- [ ] **Step 7: Commit**

```bash
git add src/vibeocr/services/pdf_service.py tests/services/test_pdf_service.py
git commit -m "feat: write Chinese text layer with china-s CID font, return write/skip counts"
```

---

## Task 2: `PdfSession._ocr_stats` field

**Files:**
- Modify: `src/vibeocr/models/pdf_session.py:23-27`
- Test: `tests/models/test_pdf_session.py`

- [ ] **Step 1: Write failing test — default stats are zero**

Append to `tests/models/test_pdf_session.py`:

```python
def test_session_has_default_ocr_stats(single_page_doc, pdf_document):
    from vibeocr.models.pdf_session import PdfSession

    session = PdfSession(
        file_path="test.pdf", doc=single_page_doc, pdf_document=pdf_document
    )
    assert session.ocr_stats == {"written": 0, "skipped": 0}
```

(If `single_page_doc` / `pdf_document` fixtures are not already defined in that file, copy the construction style from existing tests in the file — they use `PdfSession(file_path=..., doc=..., pdf_document=...)`. Check the file first and reuse its fixtures rather than inventing new ones.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/models/test_pdf_session.py::test_session_has_default_ocr_stats -v`
Expected: FAIL — `AttributeError: 'PdfSession' object has no attribute 'ocr_stats'`.

- [ ] **Step 3: Implement — add field + property**

In `src/vibeocr/models/pdf_session.py`, add a field after `doc_lock` (line 27):

```python
    doc_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _ocr_stats: dict[str, int] = field(
        default_factory=lambda: {"written": 0, "skipped": 0}, repr=False
    )
```

And add a public property after `load_progress`:

```python
    @property
    def ocr_stats(self) -> dict[str, int]:
        return self._ocr_stats

    def reset_ocr_stats(self) -> None:
        self._ocr_stats = {"written": 0, "skipped": 0}

    def add_ocr_stats(self, written: int, skipped: int) -> None:
        self._ocr_stats["written"] += written
        self._ocr_stats["skipped"] += skipped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/models/test_pdf_session.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/models/pdf_session.py tests/models/test_pdf_session.py
git commit -m "feat: add ocr_stats tracking to PdfSession"
```

---

## Task 3: Accumulate stats + emit `ocr_stats_ready` in manager

**Files:**
- Modify: `src/vibeocr/managers/pdf_session_manager.py` (signals ~line 53, `start_ocr` ~line 208, `_on_ocr_page_done` ~line 246-262, `_on_ocr_all_done` ~line 273-275)
- Test: `tests/managers/test_pdf_session_manager.py`

- [ ] **Step 1: Write failing test — stats accumulate across pages and signal fires**

Append to `tests/managers/test_pdf_session_manager.py`:

```python
class TestPdfSessionManagerOcrStats:
    def test_ocr_stats_accumulate_and_signal(self, manager, test_pdf_a, monkeypatch):
        """模拟 OCR worker 逐页回调，验证 stats 累加与 ocr_stats_ready 信号。"""
        from vibeocr.models.ocr_result import OCRResult, TextBlock

        session = manager.open_session(str(test_pdf_a))

        # 直接调用私有回调模拟 worker 产出（不启动真实线程/OCR 服务）
        emitted = []
        manager.ocr_stats_ready.connect(
            lambda sid, w, s: emitted.append((sid, w, s))
        )

        # 第一页写入 1 块
        result = OCRResult(
            raw_text="Hello",
            text_blocks=[
                TextBlock(text="Hello", score=0.9,
                          bbox=(50.0, 50.0, 300.0, 100.0), page_idx=0),
            ],
        )
        manager._on_ocr_page_done(0, result)
        # 第二页 result=None（模拟失败页）
        manager._on_ocr_page_done(1, None)

        assert session.ocr_stats["written"] == 1
        assert session.ocr_stats["skipped"] == 0

        manager._on_ocr_all_done(session.file_path, 1, 1)
        assert len(emitted) == 1
        sid, w, s = emitted[0]
        assert sid == session.file_path
        assert w == 1
        assert s == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/managers/test_pdf_session_manager.py::TestPdfSessionManagerOcrStats -v`
Expected: FAIL — `AttributeError: 'PdfSessionManager' object has no attribute 'ocr_stats_ready'`.

- [ ] **Step 3: Implement — add signal, reset in start_ocr, accumulate in page_done, emit in all_done**

In `src/vibeocr/managers/pdf_session_manager.py`:

(a) Add the signal after `ocr_done` (line 53):

```python
    ocr_done = Signal(str, int, int)
    ocr_stats_ready = Signal(str, int, int)  # (session_id/file_path, written, skipped)
```

(b) In `start_ocr`, after `self._pdf_settings = pdf_settings` is set and before the worker is created, reset stats (around line 224, right after `self._pdf_settings = pdf_settings`):

```python
        session.reset_ocr_stats()
```

(Locate the exact line: it's right after `self._pdf_settings = pdf_settings` near line 224. Read the surrounding lines before editing to place it correctly.)

(c) In `_on_ocr_page_done` (lines 246-262), accumulate stats. Replace the body that calls `add_text_layer` to also accumulate. Current code:

```python
        if result is not None:
            with session.doc_lock:
                PdfService.add_text_layer(
                    session.doc,
                    session.pdf_document,
                    page_index,
                    result,
                    pdf_settings=self._pdf_settings,
                )
```

Replace with:

```python
        if result is not None:
            with session.doc_lock:
                written, skipped = PdfService.add_text_layer(
                    session.doc,
                    session.pdf_document,
                    page_index,
                    result,
                    pdf_settings=self._pdf_settings,
                )
            session.add_ocr_stats(written, skipped)
```

(d) In `_on_ocr_all_done` (lines 273-275), emit stats before resetting the worker. Current:

```python
    def _on_ocr_all_done(self, session_id: str, success: int, fail: int) -> None:
        self.ocr_done.emit(session_id, success, fail)
        self._ocr_worker = None
```

Replace with:

```python
    def _on_ocr_all_done(self, session_id: str, success: int, fail: int) -> None:
        self.ocr_done.emit(session_id, success, fail)
        session = self._sessions.get(session_id)
        if session is not None:
            stats = session.ocr_stats
            self.ocr_stats_ready.emit(
                session_id, stats["written"], stats["skipped"]
            )
        self._ocr_worker = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/managers/test_pdf_session_manager.py -v`
Expected: ALL PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/managers/pdf_session_manager.py tests/managers/test_pdf_session_manager.py
git commit -m "feat: accumulate OCR write/skip stats and emit ocr_stats_ready"
```

---

## Task 4: Extract `PreviewCanvas` as a public reusable class

**Files:**
- Modify: `src/vibeocr/views/pdf_preview_window.py:21-136` (rename `_PreviewCanvas` → `PreviewCanvas`)
- Test: `tests/views/test_pdf_preview_window.py` (new or existing — check first)

- [ ] **Step 1: Check for existing preview window tests**

Run: `ls tests/views/` and grep `PreviewCanvas\|PdfPreviewWindow` in `tests/`.
If a `test_pdf_preview_window.py` exists, append to it; otherwise create it. Note the result for Step 2.

- [ ] **Step 2: Write failing test — public PreviewCanvas instantiates and PdfPreviewWindow still works**

Create or append `tests/views/test_pdf_preview_window.py`:

```python
"""Tests for PreviewCanvas extraction."""

from vibeocr.views.pdf_preview_window import PdfPreviewWindow, PreviewCanvas


class TestPreviewCanvasExtraction:
    def test_public_preview_canvas_exists(self, qtbot):
        canvas = PreviewCanvas()
        qtbot.addWidget(canvas)
        assert canvas is not None

    def test_pdf_preview_window_uses_preview_canvas(self, qtbot):
        win = PdfPreviewWindow()
        qtbot.addWidget(win)
        # 弹窗内部应使用公开的 PreviewCanvas
        assert isinstance(win._canvas, PreviewCanvas)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/views/test_pdf_preview_window.py -v`
Expected: FAIL — `ImportError: cannot import name 'PreviewCanvas'` (it's currently `_PreviewCanvas`).

- [ ] **Step 4: Implement — rename the class and its internal reference**

In `src/vibeocr/views/pdf_preview_window.py`:

(a) Rename `class _PreviewCanvas(QWidget):` (line 21) to `class PreviewCanvas(QWidget):`.

(b) In `PdfPreviewWindow.__init__` (line 147), change:
```python
        self._canvas = _PreviewCanvas()
```
to:
```python
        self._canvas = PreviewCanvas()
```

(c) Add a backward-compatible alias at module level (after the class definition, before `PdfPreviewWindow`) so any external reference to `_PreviewCanvas` keeps working:
```python
# Backward-compatible alias.
_PreviewCanvas = PreviewCanvas
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/views/test_pdf_preview_window.py -v`
Expected: PASS.

- [ ] **Step 6: Run full views test suite for regressions**

Run: `pytest tests/views/ -v`
Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add src/vibeocr/views/pdf_preview_window.py tests/views/test_pdf_preview_window.py
git commit -m "refactor: extract PreviewCanvas as public reusable class"
```

---

## Task 5: Splitter state persistence in `OCRPreferences`

**Files:**
- Modify: `src/vibeocr/utils/ocr_preferences.py` (add field + get/set + save/load keys)
- Test: `tests/utils/test_ocr_preferences.py`

- [ ] **Step 1: Write failing test — round-trip of splitter state bytes**

Append to `tests/utils/test_ocr_preferences.py`:

```python
def test_splitter_state_round_trip(tmp_path):
    from vibeocr.utils.ocr_preferences import OCRPreferences

    OCRPreferences.reset_instance()
    prefs = OCRPreferences(tmp_path)
    state = b"\x00\x00\x00\xc8\x00\xff"  # 任意 bytes（模拟 QSplitter.saveState().data()）
    prefs.set_pdf_splitter_state(state)
    prefs.save()

    OCRPreferences.reset_instance()
    prefs2 = OCRPreferences(tmp_path)
    loaded = prefs2.get_pdf_splitter_state()
    assert loaded == state
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_ocr_preferences.py::test_splitter_state_round_trip -v`
Expected: FAIL — `AttributeError: 'OCRPreferences' object has no attribute 'set_pdf_splitter_state'`.

- [ ] **Step 3: Implement — add field, get/set, save/load**

In `src/vibeocr/utils/ocr_preferences.py`:

(a) In `__init__` (around line 65, after the existing `self._pdf_settings = ...` assignment), add:
```python
        self._pdf_splitter_state: bytes | None = None
```

(b) Add get/set methods next to `get_pdf_settings`/`set_pdf_settings` (around line 218):
```python
    def get_pdf_splitter_state(self) -> bytes | None:
        return self._pdf_splitter_state

    def set_pdf_splitter_state(self, state: bytes | None) -> None:
        self._pdf_splitter_state = state
```

(c) In the `save()` method's dict-building (around line 196 where `pdf_settings` is added), add a sibling key. Since JSON cannot store raw bytes, encode with base64:
```python
        import base64

        # ... existing save_data dict construction ...
        if self._pdf_splitter_state is not None:
            save_data["pdf_splitter_state"] = base64.b64encode(
                self._pdf_splitter_state
            ).decode("ascii")
```
(Read the `save()` method first to see how `save_data` is built and where `pdf_settings` is inserted, then place the new key right after it. Use the same dict-building style.)

(d) In `_load()` (around line 130-132 where `pdf_settings` is read), add:
```python
        import base64

        b64_state = data.get("pdf_splitter_state")
        if b64_state:
            self._pdf_splitter_state = base64.b64decode(b64_state)
        else:
            self._pdf_splitter_state = None
```
(Move the `import base64` to the top of the file if preferred — match the file's existing import style. Check whether `base64` is already imported.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_ocr_preferences.py -v`
Expected: ALL PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/utils/ocr_preferences.py tests/utils/test_ocr_preferences.py
git commit -m "feat: persist pdf splitter state in OCRPreferences"
```

---

## Task 6: `PdfTab` layout — nested splitters + scrollable status + embedded preview

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py:57-70` (`_setup_ui`), `:82` (setFixedWidth), `:149-165` (text_group), `_create_operation_panel`
- Test: `tests/views/tabs/test_pdf_tab.py` (new)

- [ ] **Step 1: Write failing test — splitter structure + scrollable status + no fixed width**

Create `tests/views/tabs/test_pdf_tab.py`:

```python
"""Tests for PdfTab structure."""

from PySide6.QtWidgets import QLabel, QScrollArea, QSplitter, QListWidget

from vibeocr.views.tabs.pdf_tab import PdfTab


@pytest.fixture
def pdf_tab(qtbot):
    import pytest  # noqa

    tab = PdfTab()
    qtbot.addWidget(tab)
    return tab


class TestPdfTabStructure:
    def test_has_two_nested_splitters(self, pdf_tab):
        splitters = pdf_tab.findChildren(QSplitter)
        assert len(splitters) >= 2
        # main splitter horizontal, right splitter vertical
        horiz = [s for s in splitters if s.orientation() == 1]  # Qt.Horizontal=1/2?
        # Use enum to be safe:
        from PySide6.QtCore import Qt

        horiz = [
            s for s in splitters
            if s.orientation() == Qt.Orientation.Horizontal
        ]
        vert = [
            s for s in splitters
            if s.orientation() == Qt.Orientation.Vertical
        ]
        assert len(horiz) >= 1
        assert len(vert) >= 1

    def test_thumbnail_list_has_no_fixed_width(self, pdf_tab):
        lst = pdf_tab.findChild(QListWidget)
        assert lst is not None
        # minimumWidth 允许，但 fixed width 应为 0（未固定）
        # 没有 setFixedWidth 时，maximumWidth 默认 16777215
        assert lst.maximumWidth() > 300  # 不是被钉死的 200

    def test_layer_status_in_scroll_area(self, pdf_tab):
        scroll = pdf_tab.findChild(QScrollArea)
        assert scroll is not None
```

Fix the fixture import: add `import pytest` at top of the file (the inline `import pytest  # noqa` is a placeholder — move it to the top).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/views/tabs/test_pdf_tab.py -v`
Expected: FAIL — `test_has_two_nested_splitters` (only 1 splitter currently), `test_thumbnail_list_has_no_fixed_width` (maximumWidth == 200), `test_layer_status_in_scroll_area` (no QScrollArea).

- [ ] **Step 3: Implement — restructure `_setup_ui`**

In `src/vibeocr/views/tabs/pdf_tab.py`:

(a) Add imports at top: add `QScrollArea` to the `PySide6.QtWidgets` import list.

(b) Replace `_setup_ui` (lines 57-70) with:

```python
    def _setup_ui(self) -> None:
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setObjectName("mainSplitter")

        left_panel = self._create_thumbnail_panel()
        self._main_splitter.addWidget(left_panel)

        self._right_splitter = QSplitter(Qt.Orientation.Vertical)
        self._right_splitter.setChildrenCollapsible(False)
        self._right_splitter.setObjectName("rightSplitter")

        right_panel = self._create_operation_panel()
        self._right_splitter.addWidget(right_panel)

        # 内嵌预览区（默认折叠为小尺寸）
        self._preview_canvas = PreviewCanvas()
        preview_container = QScrollArea()
        preview_container.setWidget(self._preview_canvas)
        preview_container.setWidgetResizable(False)
        self._right_splitter.addWidget(preview_container)

        self._main_splitter.addWidget(self._right_splitter)
        self._main_splitter.setSizes([200, 600])
        # 预览区默认折叠：操作区大、预览区小
        self._right_splitter.setSizes([500, 40])

        # 恢复持久化的布局
        self._restore_splitter_state()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._main_splitter)
```

(c) Add the import for `PreviewCanvas` near the existing `PdfPreviewWindow` import (line 31):
```python
from vibeocr.views.pdf_preview_window import PdfPreviewWindow, PreviewCanvas
```

(d) In `_create_thumbnail_panel` (line 82), replace:
```python
        self._thumbnail_list.setFixedWidth(200)
```
with:
```python
        self._thumbnail_list.setMinimumWidth(120)
```

(e) In `_create_operation_panel`, wrap the status label in a scroll area. Replace the block around lines 163-164:
```python
        self._layer_status_label = QLabel("未打开文件")
        text_layout.addWidget(self._layer_status_label)
```
with:
```python
        self._layer_status_label = QLabel("未打开文件")
        self._layer_status_label.setWordWrap(True)
        self._layer_status_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        status_scroll = QScrollArea()
        status_scroll.setWidgetResizable(True)
        status_scroll.setWidget(self._layer_status_label)
        status_scroll.setMinimumHeight(120)
        text_layout.addWidget(status_scroll)
```

(f) Add `_restore_splitter_state` and a `_save_splitter_state` method to the class:

```python
    def _restore_splitter_state(self) -> None:
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            prefs = OCRPreferences.instance()
            state = prefs.get_pdf_splitter_state()
            if state:
                self._main_splitter.restoreState(state)
        except RuntimeError:
            pass

    def _save_splitter_state(self) -> None:
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            prefs = OCRPreferences.instance()
            prefs.set_pdf_splitter_state(self._main_splitter.saveState().data())
            prefs.save()
        except RuntimeError:
            pass
```

Note: `QSplitter.saveState()` returns a `QByteArray`; `.data()` yields `bytes`. We persist only the main splitter (covers horizontal ratio + the right splitter sizes, since restoreState of a top-level splitter restores nested child splitter geometry too — but verify this in Step 5; if nested isn't restored, persist both via a tuple and adjust `OCRPreferences` to store two keys). Prefer persisting only main first and confirm.

- [ ] **Step 4: Run structure test to verify it passes**

Run: `pytest tests/views/tabs/test_pdf_tab.py -v`
Expected: PASS for all three tests.

- [ ] **Step 5: Verify splitter state restores nested geometry (manual check via test)**

Add a temporary check or manual run: instantiate `PdfTab`, drag splitters, call `_save_splitter_state()`, create a new `PdfTab`, confirm `_restore_splitter_state()` brings back BOTH horizontal and vertical ratios. If the vertical splitter does NOT restore, update `_save_splitter_state`/`_restore_splitter_state` and `OCRPreferences` to handle two state blobs (main + right). Document the outcome in the commit message.

- [ ] **Step 6: Wire save on close / tab switch**

Connect the splitter's moved signal to save (debounced). In `_setup_ui`, after creating splitters:
```python
        # 拖动后保存（拖动结束触发）
        self._main_splitter.splitterMoved.connect(self._save_splitter_state)
```
(Keep it simple: save on `splitterMoved`. If perf is a concern later, debounce — YAGNI for now.)

- [ ] **Step 7: Run full pdf_tab + qrcode_tab tests for regressions**

Run: `pytest tests/views/tabs/ -v`
Expected: ALL PASS.

- [ ] **Step 8: Commit**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py tests/views/tabs/test_pdf_tab.py
git commit -m "feat: nested resizable splitters, scrollable status, embedded preview in PdfTab"
```

---

## Task 7: Status wording fix

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py:342` (`_update_layer_status`)
- Test: `tests/views/tabs/test_pdf_tab.py` (append)

- [ ] **Step 1: Write failing test — wording**

Append to `tests/views/tabs/test_pdf_tab.py`:

```python
class TestPdfTabLayerStatus:
    def test_status_wording_for_text_layer(self, pdf_tab, tmp_path, monkeypatch):
        """_update_layer_status 对有文字层的页应输出“已添加文字层(N 个文本块)”。"""
        import fitz

        from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo, TextLayerInfo
        from vibeocr.models.pdf_session import PdfSession

        # 构造一个有文字层的 page_info
        page_info = PdfPageInfo(
            page_index=0,
            has_text_layer=True,
            text_layers=[
                TextLayerInfo(
                    index=i, text_preview="t", char_count=1,
                    bbox=(0, 0, 1, 1), color_id=i,
                )
                for i in range(12)
            ],
        )
        doc = fitz.open()
        doc.new_page()
        pdf_doc = PdfDocument(file_path="x.pdf", pages=[page_info])
        session = PdfSession(file_path="x.pdf", doc=doc, pdf_document=pdf_doc)
        monkeypatch.setattr(
            pdf_tab._session_mgr, "active_session", session, raising=False
        )

        pdf_tab._update_layer_status()
        text = pdf_tab._layer_status_label.text()
        assert "第1页" in text
        assert "已添加文字层" in text
        assert "12 个文本块" in text
        doc.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/views/tabs/test_pdf_tab.py::TestPdfTabLayerStatus -v`
Expected: FAIL — current wording is "12层文字层".

- [ ] **Step 3: Implement — fix wording**

In `src/vibeocr/views/tabs/pdf_tab.py`, in `_update_layer_status` (line 342), replace:
```python
                lines.append(f"第{p.page_index + 1}页: {len(p.text_layers)}层文字层")
```
with:
```python
                lines.append(
                    f"第{p.page_index + 1}页: 已添加文字层"
                    f"({len(p.text_layers)} 个文本块)"
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/views/tabs/test_pdf_tab.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py tests/views/tabs/test_pdf_tab.py
git commit -m "fix: correct misleading text-layer status wording to block count"
```

---

## Task 8: Auto-preview + failure summary on OCR completion

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py` (connect `ocr_stats_ready`, `_on_ocr_done` summary, auto-preview into embedded `PreviewCanvas`)
- Test: `tests/views/tabs/test_pdf_tab.py` (append)

- [ ] **Step 1: Write failing test — summary message + auto-preview invocation**

Append to `tests/views/tabs/test_pdf_tab.py`:

```python
class TestPdfTabOcrCompletion:
    def test_completion_summary_with_skips(self, pdf_tab, monkeypatch):
        """skipped>0 时应弹出 information 提示。"""
        called = []
        monkeypatch.setattr(
            "vibeocr.views.tabs.pdf_tab.QMessageBox.information",
            lambda *a, **k: called.append(a),
        )
        # 模拟 manager 发出 stats 信号
        pdf_tab._session_mgr.ocr_stats_ready.emit("sid", 5, 2)
        assert len(called) == 1
        msg = called[0][2]
        assert "成功 5 块" in msg
        assert "跳过 2 块" in msg

    def test_auto_preview_after_completion(self, pdf_tab, monkeypatch):
        """完成后应调用内嵌预览画布显示高亮。"""
        # 仅断言 _show_embedded_preview 被调用（具体渲染在 Task 4 已测 PreviewCanvas）
        called = []
        monkeypatch.setattr(
            pdf_tab, "_show_embedded_preview", lambda: called.append(True)
        )
        pdf_tab._session_mgr.ocr_stats_ready.emit("sid", 3, 0)
        assert called == [True]
```

Note: these tests assert behavior wired in `_connect_manager_signals`. If `ocr_stats_ready` is connected to a handler `_on_ocr_stats_ready(self, session_id, written, skipped)`, the handler shows summary + calls `_show_embedded_preview`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/views/tabs/test_pdf_tab.py::TestPdfTabOcrCompletion -v`
Expected: FAIL — no `_on_ocr_stats_ready` handler / `_show_embedded_preview` yet.

- [ ] **Step 3: Implement — connect signal, add handlers**

In `src/vibeocr/views/tabs/pdf_tab.py`:

(a) In `_connect_manager_signals` (find it — it's called in `__init__` line 51), add:
```python
        self._session_mgr.ocr_stats_ready.connect(self._on_ocr_stats_ready)
```
(Read the existing method to match the connect-call style for the other signals.)

(b) Add the handler methods:

```python
    def _on_ocr_stats_ready(
        self, session_id: str, written: int, skipped: int
    ) -> None:
        if skipped > 0:
            QMessageBox.information(
                self,
                "文字层已添加",
                f"成功写入 {written} 块，跳过 {skipped} 块（详见日志）。",
            )
        else:
            self._status_label.setText(f"文字层已添加（{written} 块）")
        self._update_layer_status()
        self._refresh_thumbnails()
        self._show_embedded_preview()

    def _show_embedded_preview(self) -> None:
        """在内嵌预览画布显示当前页文字层高亮。"""
        session = self._session_mgr.active_session
        if session is None:
            return
        indices = self._get_selected_page_indices()
        page_idx = indices[0] if indices else 0
        page_info = session.pdf_document.get_page(page_idx)
        if page_info is None or not page_info.text_layers:
            return
        with session.doc_lock:
            pixmap = PdfService.render_page(session.doc, page_idx, dpi=150)
            page_rect = session.doc[page_idx].rect
        self._preview_canvas.set_pixmap(pixmap)
        self._preview_canvas.set_highlight_layers(
            page_info.text_layers,
            render_dpi=150,
            page_rect=page_rect,
            source="pdf",
        )
```

(c) Ensure existing `_on_ocr_done` (the handler for `ocr_done` signal) does NOT also do the summary/preview — check it and remove any duplication so stats handler is the single source. Read `_connect_manager_signals` and `_on_ocr_done` first.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/views/tabs/test_pdf_tab.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Run full test suite**

Run: `pytest -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py tests/views/tabs/test_pdf_tab.py
git commit -m "feat: auto-preview and failure summary after text-layer OCR completion"
```

---

## Task 9: Manual verification + spec doc update

**Files:**
- Modify: `docs/superpowers/specs/2026-06-19-pdf-text-layer-fix-design.md` (mark delivered)

- [ ] **Step 1: Run full test suite one more time**

Run: `pytest -v`
Expected: ALL PASS.

- [ ] **Step 2: Manual smoke test (requires GUI + a Chinese scanned PDF)**

Launch the app, open a Chinese scanned PDF, click "添加文字层", and verify:
1. Status shows "第1页: 已添加文字层(N 个文本块)" with accurate N.
2. Embedded preview shows highlighted blocks over the page (color rectangles).
3. Both splitters (horizontal thumbnail↔right, vertical operation↔preview) are draggable.
4. Restart the app → splitter layout is restored.
5. Save the PDF, open in SumatraPDF → Ctrl+F searches a Chinese phrase and finds it.

- [ ] **Step 3: Update spec doc to mark delivered**

In `docs/superpowers/specs/2026-06-19-pdf-text-layer-fix-design.md`, change the status line at the top from `状态:已批准,待实现规划` to `状态:已交付`.

- [ ] **Step 4: Commit**

```bash
git add -f docs/superpowers/specs/2026-06-19-pdf-text-layer-fix-design.md
git commit -m "docs: mark PDF text layer fix as delivered"
```

---

## Notes for implementer

- **Task ordering is sequential** — Task 1 must complete before Task 3 (manager unpacks the new return value); Task 4 before Task 6 (PdfTab uses `PreviewCanvas`); Task 5 before Task 6 (PdfTab restores state).
- **caplog is new to this codebase** — Task 1 introduces it. Use `caplog.at_level(logging.WARNING, logger="vibeocr.services.pdf_service")` to scope.
- **`docs/` is gitignored** — only the spec doc in Task 9 needs `git add -f`; all `src/` and `tests/` paths use normal `git add`.
- **`china-s` font**: if Task 1's Chinese-write test fails on extract (font not embedded), try `fontname="china-ss"` as fallback before considering system-font detection. PyMuPDF `>=1.27.2.3` supports both.
- **Nested splitter restore**: `QSplitter.restoreState` restores only that splitter's own geometry; nested child splitters are NOT automatically restored by restoring the parent. If Task 6 Step 5 shows the vertical splitter doesn't restore, extend `OCRPreferences` to store both main and right state (two base64 keys) and restore each. This is the one design point to verify empirically.
