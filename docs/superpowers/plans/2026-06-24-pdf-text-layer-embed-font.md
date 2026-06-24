# PDF 文字层字体内嵌修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 PDF 文字层在所有主流阅读器（Adobe/Foxit/浏览器/macOS Preview/pdftotext）可搜索/复制，通过嵌入 fontTools 子集化字体 + 自动生成 ToUnicode CMap 实现。

**Architecture:** 新增 `CjkFontResolver`（跨平台探测系统 CJK 字体 + fontTools 按字符集子集化，字符集缓存）。修改 `PdfService._write_blocks_to_page`：写入前收集本页字符 → 解析子集字体 → 传给 `insert_textbox(fontfile=...)`。PyMuPDF 嵌入子集字体时自动生成 FontFile2 + ToUnicode。探测失败优雅回退 china-s（当前行为）。

**Tech Stack:** fontTools（子集化）、PyMuPDF（字体嵌入）、pytest（TDD）

**Spec:** `docs/superpowers/specs/2026-06-24-pdf-text-layer-embed-font-design.md`

---

## File Structure

- **Create:** `src/vibeocr/utils/cjk_font_resolver.py` — 跨平台字体探测 + 子集化
- **Create:** `tests/utils/test_cjk_font_resolver.py` — resolver 单元测试
- **Modify:** `pyproject.toml` — 添加 `fonttools` 显式依赖（当前仅为传递依赖）
- **Modify:** `src/vibeocr/services/pdf_service.py` — `_write_blocks_to_page` 接入 resolver + 模块级单例
- **Extend:** `tests/integration/test_pdf_text_layer_e2e.py` — 跨阅读器视角断言（ToUnicode/FontFile/体积）

---

## Task 1: 添加 fonttools 显式依赖

**Files:**
- Modify: `pyproject.toml:6-30`（dependencies 数组）

fonttools 当前仅作为 paddleocr/pillow 的传递依赖存在，但我们直接 `from fontTools import subset`，必须显式声明，避免传递依赖被移除时崩溃。

- [ ] **Step 1: 添加 fonttools 到 dependencies**

在 `pyproject.toml` 的 `dependencies` 数组中，`"pymupdf>=1.27.2.3",` 行之后添加：

```toml
    "pymupdf>=1.27.2.3",
    # PDF 文字层字体内嵌：fontTools 按字符集子集化系统 CJK 字体，
    # 嵌入后 PyMuPDF 自动生成 ToUnicode CMap，使文字层在所有阅读器可搜索。
    "fonttools>=4.61.1",
```

- [ ] **Step 2: 验证依赖可解析**

Run: `uv lock`
Expected: 成功生成 lock 文件（fonttools 已是传递依赖，应秒级完成）

- [ ] **Step 3: 验证导入**

Run: `python -c "from fontTools import subset; from fontTools.ttLib import TTFont; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): add fonttools for PDF text layer font subsetting"
```

---

## Task 2: CjkFontResolver — 字体探测（TDD）

**Files:**
- Create: `tests/utils/test_cjk_font_resolver.py`
- Create: `src/vibeocr/utils/cjk_font_resolver.py`

先写探测逻辑（不含子集化），用 TDD 驱动。

- [ ] **Step 1: 写失败测试 — 探测返回存在的字体路径**

创建 `tests/utils/test_cjk_font_resolver.py`：

```python
"""CjkFontResolver 单元测试：系统 CJK 字体探测 + 子集化。"""

from __future__ import annotations

import pytest

from vibeocr.utils.cjk_font_resolver import CjkFontResolver


class TestFindSystemFont:
    """系统字体探测。"""

    def test_returns_path_when_font_exists(self, monkeypatch, tmp_path):
        """候选字体存在时返回其路径。"""
        fake_font = tmp_path / "fake.ttf"
        fake_font.write_bytes(b"fake")  # 存在即可
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: [str(fake_font)]
        )
        assert resolver._find_system_font() == str(fake_font)

    def test_returns_none_when_no_font(self, monkeypatch):
        """所有候选都不存在时返回 None（优雅降级）。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: ["/nonexistent/font.ttf"]
        )
        assert resolver._find_system_font() is None

    def test_returns_first_existing(self, monkeypatch, tmp_path):
        """多个候选时返回第一个存在的。"""
        exists = tmp_path / "second.ttf"
        exists.write_bytes(b"x")
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver,
            "_get_candidates",
            lambda: ["/nonexistent/x.ttf", str(exists)],
        )
        assert resolver._find_system_font() == str(exists)

    def test_find_result_cached(self, monkeypatch, tmp_path):
        """探测结果缓存：第二次调用不再扫描文件系统。"""
        fake_font = tmp_path / "cached.ttf"
        fake_font.write_bytes(b"x")
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: [str(fake_font)]
        )
        first = resolver._find_system_font()
        # 删除文件后再次调用，仍应返回缓存路径（证明不重复扫描）
        fake_font.unlink()
        second = resolver._find_system_font()
        assert first == second == str(fake_font)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/utils/test_cjk_font_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vibeocr.utils.cjk_font_resolver'`

- [ ] **Step 3: 实现 CjkFontResolver 骨架（探测部分）**

创建 `src/vibeocr/utils/cjk_font_resolver.py`：

```python
"""系统 CJK 字体探测 + fontTools 子集化。

为 PDF 文字层提供可嵌入的子集字体：按本页实际用到的字符做子集化，
生成临时小字体文件，PyMuPDF 嵌入后自动生成 ToUnicode CMap，
使文字层在所有主流阅读器可搜索/复制（不依赖阅读器自带 Adobe GB1 CMap）。

跨平台探测系统 CJK 字体，无需随包分发字体。探测失败时返回 None，
调用方回退 china-s（当前行为）。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class CjkFontResolver:
    """系统 CJK 字体探测 + fontTools 子集化。

    进程级单例：通过模块级 `_CJK_RESOLVER` 实例访问，避免重复探测。
    子集字体按字符集 hash 缓存到临时目录，相同字符集复用。
    """

    # 跨平台候选优先级（复用 qrcode_service._load_font 的模式）
    _WIN_CANDIDATES = [
        "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",  # 黑体
        "C:/Windows/Fonts/simsun.ttc",  # 宋体
        "C:/Windows/Fonts/Deng.ttf",  # 等线
    ]
    _MAC_CANDIDATES = [
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Songti.ttc",
    ]
    _LINUX_CANDIDATES = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
    ]

    def __init__(self) -> None:
        self._system_font: str | None = None  # 探测缓存（None 表示已探测且无）
        self._probed: bool = False  # 是否已探测过
        self._subset_cache: dict[frozenset[str], str] = {}  # 字符集 → 子集路径

    @property
    def _candidates(self) -> list[str]:
        """按平台返回候选字体路径列表。"""
        if sys.platform == "win32":
            return self._WIN_CANDIDATES
        if sys.platform == "darwin":
            return self._MAC_CANDIDATES
        return self._LINUX_CANDIDATES

    def _get_candidates(self) -> list[str]:
        """按平台返回候选字体路径列表（可被测试 monkeypatch 覆盖）。

        注意：用方法而非 property，因为 property 无 setter 无法被
        monkeypatch.setattr 覆盖。_find_system_font 调用此方法。
        """
        return self._candidates

    def _find_system_font(self) -> str | None:
        """探测首个存在的系统 CJK 字体（结果缓存）。"""
        if self._probed:
            return self._system_font
        for path in self._get_candidates():
            if Path(path).is_file():
                self._system_font = path
                break
        self._probed = True
        if self._system_font is None:
            logger.warning(
                "[CjkFontResolver] 未找到系统 CJK 字体，文字层将回退 china-s"
            )
        return self._system_font
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/utils/test_cjk_font_resolver.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/utils/cjk_font_resolver.py tests/utils/test_cjk_font_resolver.py
git commit -m "feat(cjk-font): system CJK font detection with caching"
```

---

## Task 3: CjkFontResolver — 子集化（TDD）

**Files:**
- Modify: `src/vibeocr/utils/cjk_font_resolver.py`（添加 `_subset` + `resolve` + `cleanup`）
- Modify: `tests/utils/test_cjk_font_resolver.py`（添加子集化测试）

子集化测试需要真实字体文件。用系统字体（CI 在 Windows），mock 探测路径指向真实系统字体。

- [ ] **Step 1: 写失败测试 — 子集化 + resolve + 缓存 + cleanup**

在 `tests/utils/test_cjk_font_resolver.py` 末尾追加：

```python
class TestSubsetAndResolve:
    """子集化与 resolve 主流程。"""

    @pytest.fixture
    def real_font(self):
        """获取真实系统 CJK 字体路径（Windows 测试环境）。"""
        import glob
        import os

        win = os.environ.get("WINDIR", r"C:\Windows")
        for pat in [f"{win}\\Fonts\\simhei.ttf", f"{win}\\Fonts\\msyh.ttc"]:
            for f in glob.glob(pat):
                return f
        pytest.skip("无系统 CJK 字体，跳过子集化测试")

    def test_resolve_returns_subset_path(self, monkeypatch, real_font):
        """resolve 返回子集字体文件路径（文件存在且非空）。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: [real_font]
        )
        path = resolver.resolve("签收联测试中文")
        assert path is not None
        from pathlib import Path

        assert Path(path).is_file()
        assert Path(path).stat().st_size > 0
        resolver.cleanup()

    def test_subset_much_smaller_than_original(self, monkeypatch, real_font):
        """子集字体远小于原字体（验证 fontTools 真做了子集化）。"""
        import os

        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: [real_font]
        )
        path = resolver.resolve("签收联测试")
        assert path is not None
        orig_size = os.path.getsize(real_font)
        sub_size = os.path.getsize(path)
        # 子集应比原字体小至少 10 倍（实测通常小 1000+ 倍）
        assert sub_size < orig_size / 10, f"子集未缩小: orig={orig_size} sub={sub_size}"
        resolver.cleanup()

    def test_subset_cache_reuses_same_charset(self, monkeypatch, real_font):
        """相同字符集返回相同子集路径（缓存复用）。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: [real_font]
        )
        p1 = resolver.resolve("签收联")
        p2 = resolver.resolve("签收联")
        assert p1 == p2
        resolver.cleanup()

    def test_subset_different_chars_different_path(self, monkeypatch, real_font):
        """不同字符集返回不同子集路径。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: [real_font]
        )
        p1 = resolver.resolve("签收联")
        p2 = resolver.resolve("发货单")
        assert p1 != p2
        resolver.cleanup()

    def test_resolve_none_for_empty_chars(self, monkeypatch, real_font):
        """空字符集返回 None。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: [real_font]
        )
        assert resolver.resolve("") is None

    def test_resolve_none_when_no_system_font(self, monkeypatch):
        """无系统字体时返回 None。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: ["/nonexistent.ttf"]
        )
        assert resolver.resolve("签收联") is None

    def test_cleanup_removes_temp_files(self, monkeypatch, real_font):
        """cleanup 删除临时子集文件。"""
        from pathlib import Path

        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: [real_font]
        )
        path = resolver.resolve("签收联")
        assert path is not None and Path(path).is_file()
        resolver.cleanup()
        assert not Path(path).is_file()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/utils/test_cjk_font_resolver.py::TestSubsetAndResolve -v`
Expected: FAIL — `AttributeError: 'CjkFontResolver' object has no attribute 'resolve'`（或 `cleanup`）

- [ ] **Step 3: 实现 _subset + resolve + cleanup**

在 `src/vibeocr/utils/cjk_font_resolver.py` 的 `CjkFontResolver` 类中，`_find_system_font` 方法之后追加：

```python
    def resolve(self, chars: str) -> str | None:
        """返回覆盖 chars 的子集字体路径；探测失败或空字符返回 None。

        Args:
            chars: 本页文字层需要的所有字符。
        Returns:
            子集字体临时文件路径，或 None（调用方回退 china-s）。
        """
        if not chars:
            return None
        sys_font = self._find_system_font()
        if sys_font is None:
            return None
        key = frozenset(chars)
        if key not in self._subset_cache:
            try:
                self._subset_cache[key] = self._subset(sys_font, chars)
            except Exception as e:
                logger.warning(
                    "[CjkFontResolver] 子集化失败，回退内置字体: %s", e
                )
                return None
        return self._subset_cache[key]

    @staticmethod
    def _subset(orig_path: str, chars: str) -> str:
        """fontTools 子集化到临时文件，返回路径。

        .ttc（字体集合）需 fontNumber=0 取第一个 face；.ttf 直接打开。
        populate(text=...) 自动闭包 notdef 等必需字形。
        """
        import os
        import tempfile

        from fontTools import subset
        from fontTools.ttLib import TTFont

        is_ttc = orig_path.lower().endswith(".ttc")
        font = TTFont(orig_path, fontNumber=0) if is_ttc else TTFont(orig_path)
        sub = subset.Subsetter()
        sub.populate(text=chars)
        sub.subset(font)
        fd, path = tempfile.mkstemp(suffix=".ttf", prefix="vibeocr_subset_")
        os.close(fd)
        font.save(path)
        return path

    def cleanup(self) -> None:
        """删除所有缓存的子集临时文件（进程退出或 session 关闭时调用）。"""
        for path in self._subset_cache.values():
            try:
                from pathlib import Path

                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        self._subset_cache.clear()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/utils/test_cjk_font_resolver.py -v`
Expected: 11 passed（4 探测 + 7 子集化）

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/utils/cjk_font_resolver.py tests/utils/test_cjk_font_resolver.py
git commit -m "feat(cjk-font): fontTools subsetting + resolve/cleanup with charset cache"
```

---

## Task 4: 模块级单例 + atexit 清理钩子

**Files:**
- Modify: `src/vibeocr/utils/cjk_font_resolver.py`（模块底部添加单例 + atexit）

模块级单例供 `pdf_service.py` 导入；atexit 确保临时子集文件在进程退出时清理。

- [ ] **Step 1: 写失败测试 — 单例与 atexit 注册**

在 `tests/utils/test_cjk_font_resolver.py` 末尾追加：

```python
class TestModuleSingleton:
    """模块级单例与清理钩子。"""

    def test_module_singleton_exists(self):
        """模块导出 _CJK_RESOLVER 单例。"""
        from vibeocr.utils import cjk_font_resolver

        assert cjk_font_resolver._CJK_RESOLVER is not None
        assert isinstance(cjk_font_resolver._CJK_RESOLVER, CjkFontResolver)

    def test_singleton_is_same_instance(self):
        """多次导入拿到同一实例。"""
        from vibeocr.utils.cjk_font_resolver import _CJK_RESOLVER as r1
        from vibeocr.utils.cjk_font_resolver import _CJK_RESOLVER as r2

        assert r1 is r2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/utils/test_cjk_font_resolver.py::TestModuleSingleton -v`
Expected: FAIL — `AttributeError: module has no attribute '_CJK_RESOLVER'`

- [ ] **Step 3: 添加模块级单例 + atexit**

在 `src/vibeocr/utils/cjk_font_resolver.py` 文件**最末尾**追加：

```python
import atexit

# 模块级单例：pdf_service.py 通过此实例访问，避免重复探测与子集化。
_CJK_RESOLVER = CjkFontResolver()


def _cleanup_on_exit() -> None:
    """进程退出时清理临时子集字体文件。"""
    _CJK_RESOLVER.cleanup()


atexit.register(_cleanup_on_exit)
```

注意：`import atexit` 放文件末尾是为了让 `CjkFontResolver` 类先定义（避免循环），实际 atexit 是标准库无副作用。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/utils/test_cjk_font_resolver.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/utils/cjk_font_resolver.py tests/utils/test_cjk_font_resolver.py
git commit -m "feat(cjk-font): module singleton + atexit cleanup hook"
```

---

## Task 5: PdfService._write_blocks_to_page 接入 resolver

**Files:**
- Modify: `src/vibeocr/services/pdf_service.py`（顶部导入 + `_write_blocks_to_page` 改 fontname/fontfile）

核心改动：写入前收集字符 → 解析子集字体 → 传 `fontfile` 给 `insert_textbox`/`insert_text`。探测失败时 `font_path=None`，PyMuPDF 回退内置字体（china-s 行为不变）。

- [ ] **Step 1: 添加模块级导入**

在 `src/vibeocr/services/pdf_service.py` 顶部导入区（`from vibeocr.models.pdf_document import ...` 之后）添加：

```python
from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo, TextLayerInfo
from vibeocr.utils.cjk_font_resolver import _CJK_RESOLVER
```

- [ ] **Step 2: 修改 _write_blocks_to_page — 收集字符并解析子集字体**

定位 `_write_blocks_to_page` 方法（`pdf_service.py:374` 附近的 `page = doc[page_index]`）。

把这段：

```python
        page = doc[page_index]
        page_rect = page.rect

        written = 0
        skipped = 0
        for block in text_blocks:
```

替换为：

```python
        page = doc[page_index]
        page_rect = page.rect

        # 收集本页所有字符，解析子集字体（探测失败则 None，回退 china-s）。
        # 子集字体嵌入后 PyMuPDF 自动生成 ToUnicode CMap，使文字层在所有
        # 主流阅读器可搜索/复制（china-s 依赖阅读器自带 Adobe GB1 CMap，脆弱）。
        all_chars = "".join(b.text for b in text_blocks if b.text)
        font_path = _CJK_RESOLVER.resolve(all_chars)
        fontname = "F0" if font_path is not None else "china-s"

        written = 0
        skipped = 0
        for block in text_blocks:
```

- [ ] **Step 3: 修改 insert_textbox 调用 — 传 fontname + fontfile**

定位 `insert_textbox` 调用（`pdf_service.py:417` 附近）。把：

```python
                rc = page.insert_textbox(
                    rect,
                    block.text,
                    fontsize=fontsize,
                    fontname="china-s",
                    color=(0, 0, 0),
                    render_mode=render_mode,
                )
```

替换为：

```python
                rc = page.insert_textbox(
                    rect,
                    block.text,
                    fontsize=fontsize,
                    fontname=fontname,
                    fontfile=font_path,
                    color=(0, 0, 0),
                    render_mode=render_mode,
                )
```

- [ ] **Step 4: 修改 insert_text 兜底调用 — 传 fontname + fontfile**

定位 `insert_text` 兜底调用（`pdf_service.py:441` 附近）。把：

```python
                    page.insert_text(
                        baseline,
                        block.text,
                        fontsize=last_fontsize,
                        fontname="china-s",
                        color=(0, 0, 0),
                        render_mode=render_mode,
                    )
```

替换为：

```python
                    page.insert_text(
                        baseline,
                        block.text,
                        fontsize=last_fontsize,
                        fontname=fontname,
                        fontfile=font_path,
                        color=(0, 0, 0),
                        render_mode=render_mode,
                    )
```

- [ ] **Step 5: 运行现有 pdf_service 测试确认无回归**

Run: `python -m pytest tests/services/test_pdf_service.py -v`
Expected: 全部 passed（现有测试用 fitz 自验证，行为不变）

- [ ] **Step 6: Commit**

```bash
git add src/vibeocr/services/pdf_service.py
git commit -m "feat(pdf-service): embed subset CJK font in text layer for cross-reader search"
```

---

## Task 6: 跨阅读器视角集成测试（关键回归防护）

**Files:**
- Modify: `tests/integration/test_pdf_text_layer_e2e.py`（追加跨阅读器断言）

现有 e2e 测试用 `fitz.get_text()` 验证，**掩盖了问题**（fitz 能读自己的输出）。新增断言检查 PDF 原始字节含 ToUnicode/FontFile，这是外部阅读器可搜索的真正前提。

- [ ] **Step 1: 写失败测试 — 文字层含 ToUnicode + FontFile**

在 `tests/integration/test_pdf_text_layer_e2e.py` 末尾追加：

```python
class TestCrossReaderSearchability:
    """跨阅读器可搜索性：文字层必须含嵌入字体 + ToUnicode CMap。

    fitz.get_text() 能读自己的输出（掩盖问题），但外部阅读器（浏览器/
    macOS Preview/pdftotext）依赖 ToUnicode 反向映射。本类用原始字节断言。
    """

    def test_saved_pdf_has_tounicode_cmap(self, tmp_path):
        """保存后的 PDF 必须含 ToUnicode CMap（外部搜索的前提）。"""
        path = _make_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))
        result = OCRResult(
            raw_text="签收联测试",
            text_blocks=[
                TextBlock(text="签收联测试", score=0.95, bbox=(50, 50, 400, 120)),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        PdfService.save(doc, pdf_doc)
        doc.close()

        raw = path.read_bytes()
        assert b"ToUnicode" in raw, "PDF 缺少 ToUnicode CMap，外部阅读器无法搜索"

    def test_saved_pdf_has_embedded_font(self, tmp_path):
        """保存后的 PDF 必须含嵌入字体（FontFile），字形数据随文件走。"""
        path = _make_scanned_pdf(tmp_path / "scan.pdf")
        doc, pdf_doc = PdfService.open_doc(str(path))
        result = OCRResult(
            raw_text="签收联测试",
            text_blocks=[
                TextBlock(text="签收联测试", score=0.95, bbox=(50, 50, 400, 120)),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        PdfService.save(doc, pdf_doc)
        doc.close()

        raw = path.read_bytes()
        assert b"FontFile" in raw, "PDF 缺少嵌入字体，字形未随文件保存"

    def test_volume_increase_acceptable(self, tmp_path):
        """子集化字体嵌入后体积增量可忽略（< 100KB）。"""
        base_path = tmp_path / "base.pdf"
        _make_scanned_pdf(base_path)
        base_size = base_path.stat().st_size

        path = tmp_path / "scan.pdf"
        _make_scanned_pdf(path)
        doc, pdf_doc = PdfService.open_doc(str(path))
        result = OCRResult(
            raw_text="签收联测试中文文字层发货单",
            text_blocks=[
                TextBlock(
                    text="签收联测试中文文字层发货单",
                    score=0.95,
                    bbox=(50, 50, 500, 120),
                ),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        PdfService.save(doc, pdf_doc)
        doc.close()

        increase = path.stat().st_size - base_size
        # 子集字体增量应远小于整字体（整字体 3.5MB+）；放宽到 100KB 容错
        assert increase < 100_000, f"体积增量过大: {increase} bytes（疑似嵌整字体）"

    def test_fallback_when_no_system_font(self, tmp_path, monkeypatch):
        """无系统字体时回退 china-s，文字层仍可被 fitz 提取（不阻断流程）。"""
        from vibeocr.services.pdf_service import _CJK_RESOLVER

        # 强制 resolver 探测失败
        monkeypatch.setattr(
            _CJK_RESOLVER, "_get_candidates", lambda: ["/nonexistent.ttf"]
        )
        _CJK_RESOLVER._probed = False  # 重置缓存
        _CJK_RESOLVER._system_font = None

        try:
            path = _make_scanned_pdf(tmp_path / "scan.pdf")
            doc, pdf_doc = PdfService.open_doc(str(path))
            result = OCRResult(
                raw_text="签收联",
                text_blocks=[
                    TextBlock(text="签收联", score=0.95, bbox=(50, 50, 200, 120)),
                ],
            )
            PdfService.add_text_layer(doc, pdf_doc, 0, result)
            PdfService.save(doc, pdf_doc)
            doc.close()

            verify = fitz.open(str(path))
            assert "签收联" in verify[0].get_text()
            verify.close()
        finally:
            # 恢复 resolver 状态，避免污染后续测试
            _CJK_RESOLVER._probed = False
            _CJK_RESOLVER._system_font = None
```

- [ ] **Step 2: 运行新测试确认通过（Task 5 已实现，应直接通过）**

Run: `python -m pytest tests/integration/test_pdf_text_layer_e2e.py::TestCrossReaderSearchability -v`
Expected: 4 passed

- [ ] **Step 3: 运行全部 e2e 测试确认无回归**

Run: `python -m pytest tests/integration/test_pdf_text_layer_e2e.py -v`
Expected: 8 passed（4 原有 + 4 新增）

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_pdf_text_layer_e2e.py
git commit -m "test(pdf): cross-reader searchability assertions (ToUnicode/FontFile/volume)"
```

---

## Task 7: 全量验证 + 清理

**Files:** 无（验证步骤）

- [ ] **Step 1: 运行全部相关测试**

Run: `python -m pytest tests/utils/test_cjk_font_resolver.py tests/services/test_pdf_service.py tests/integration/test_pdf_text_layer_e2e.py -v`
Expected: 全部 passed

- [ ] **Step 2: 静态检查**

Run: `ruff check src/vibeocr/utils/cjk_font_resolver.py src/vibeocr/services/pdf_service.py`
Expected: 无错误

Run: `pyright src/vibeocr/utils/cjk_font_resolver.py`
Expected: 无错误

- [ ] **Step 3: 手动验证生成 PDF 的可搜索性（关键）**

Run（生成测试 PDF）:
```bash
PYTHONPATH=src python -c "
import fitz, numpy as np
from vibeocr.models.ocr_result import OCRResult, TextBlock
from vibeocr.services.pdf_service import PdfService

# 生成扫描件
doc = fitz.open()
page = doc.new_page(width=612, height=792)
img = np.ones((792, 612, 3), dtype=np.uint8) * 240
cs = fitz.Colorspace(fitz.CS_RGB)
pm = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
page.insert_image(fitz.Rect(0,0,612,792), pixmap=pm)
doc.save('./_verify.pdf'); doc.close()

doc, pd = PdfService.open_doc('./_verify.pdf')
PdfService.add_text_layer(doc, pd, 0, OCRResult(raw_text='签收联测试', text_blocks=[TextBlock(text='签收联测试', score=0.95, bbox=(50,50,400,120))]))
PdfService.save(doc, pd); doc.close()

raw = open('./_verify.pdf','rb').read()
print('ToUnicode:', '有' if b'ToUnicode' in raw else '缺')
print('FontFile:', '有' if b'FontFile' in raw else '缺')
print('get_text:', fitz.open('./_verify.pdf')[0].get_text().strip())
"
```
Expected: `ToUnicode: 有`，`FontFile: 有`，`get_text: 签收联测试`

打开 `./_verify.pdf` 在 Chrome / Edge / Adobe Reader 验证 Ctrl+F 搜索"签收联"成功。

- [ ] **Step 4: 清理验证文件**

Run: `rm -f ./_verify.pdf`

- [ ] **Step 5: 最终 commit（如有未提交改动）**

```bash
git status
# 若有改动:
git add -A && git commit -m "chore: final verification cleanup"
```

---

## Self-Review 结果

**Spec 覆盖检查：**
- ✅ CjkFontResolver 探测（Task 2）
- ✅ fontTools 子集化（Task 3）
- ✅ 字符集缓存（Task 3）
- ✅ cleanup 清理（Task 3）
- ✅ 模块级单例 + atexit（Task 4）
- ✅ _write_blocks_to_page 接入（Task 5，含 insert_textbox + insert_text 两处）
- ✅ fonttools 显式依赖（Task 1）
- ✅ 跨阅读器视角测试（Task 6：ToUnicode/FontFile/体积/回退）
- ✅ 优雅回退 china-s（Task 3 resolve + Task 6 回退测试）

**Placeholder 扫描：** 无 TBD/TODO，所有代码块完整。

**类型/方法一致性：** `resolve(chars) -> str | None`、`cleanup() -> None`、`_CJK_RESOLVER` 单例名在各 Task 间一致。`_get_candidates()` 方法（非 property，因 property 无 setter 无法被 monkeypatch 覆盖）供测试注入候选字体路径。

**风险已缓解：** .ttc 的 fontNumber=0（Task 3 _subset）、子集化异常回退（Task 3 resolve try/except）、测试后恢复 resolver 状态（Task 6 finally）。
