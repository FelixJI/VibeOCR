# PDF 处理标签页实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 VibeOCR 中新增"PDF 处理"标签页，支持 PDF 文字层增删和页面操作（旋转、插入、删除、排序）。

**Architecture:** 新增 PdfService（PyMuPDF 封装）+ PdfDocument 数据模型 + PdfTab UI + PdfPreviewWindow 独立预览窗口。服务层与 UI 层分离，PdfTab 通过 PdfService 操作 PDF，不直接接触 PyMuPDF。OCR 部分复用现有 OCRService。

**Tech Stack:** PySide6, PyMuPDF, PaddleX (existing OCRService), numpy, Pillow

**Design spec:** `docs/superpowers/specs/2026-05-21-pdf-processing-tab-design.md`

---

## File Structure

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `src/vibeocr/models/pdf_document.py` | PDF 文档数据模型（TextLayerInfo, PdfPageInfo, PdfDocument） |
| Create | `src/vibeocr/services/pdf_service.py` | PDF 操作服务（PyMuPDF 封装） |
| Create | `src/vibeocr/views/tabs/pdf_tab.py` | PDF 标签页 UI |
| Create | `src/vibeocr/views/pdf_preview_window.py` | 独立预览窗口 |
| Create | `tests/test_pdf_document.py` | 数据模型测试 |
| Create | `tests/test_pdf_service.py` | 服务层测试 |
| Modify | `src/vibeocr/views/main_window.py:173-184` | 添加 PDF 标签页初始化 |
| Modify | `pyproject.toml:7-35` | 添加 pymupdf 依赖 |

---

### Task 1: 添加 pymupdf 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加依赖到 pyproject.toml**

在 `dependencies` 列表中添加 `pymupdf`：

```toml
# 在 pyproject.toml 的 dependencies 中，"qrcode[pil]>=8.2" 之前添加：
    "pymupdf>=1.25.0",
```

- [ ] **Step 2: 安装依赖**

Run: `uv sync`
Expected: pymupdf 安装成功

- [ ] **Step 3: 验证导入**

Run: `python -c "import fitz; print(fitz.__version__)"`
Expected: 打印版本号

- [ ] **Step 4: 提交**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: 添加 pymupdf 依赖"
```

---

### Task 2: 数据模型 pdf_document.py

**Files:**
- Create: `src/vibeocr/models/pdf_document.py`
- Create: `tests/test_pdf_document.py`

- [ ] **Step 1: 写数据模型测试**

创建 `tests/test_pdf_document.py`：

```python
"""Tests for PDF document data models."""

from PySide6.QtGui import QPixmap

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo, TextLayerInfo


class TestTextLayerInfo:
    def test_create(self):
        layer = TextLayerInfo(
            index=0,
            text_preview="Hello World",
            char_count=11,
            bbox=(10.0, 20.0, 200.0, 40.0),
            color_id=0,
        )
        assert layer.index == 0
        assert layer.text_preview == "Hello World"
        assert layer.char_count == 11
        assert layer.bbox == (10.0, 20.0, 200.0, 40.0)
        assert layer.color_id == 0


class TestPdfPageInfo:
    def test_defaults(self):
        info = PdfPageInfo(page_index=0)
        assert info.page_index == 0
        assert info.rotation == 0
        assert info.has_text_layer is False
        assert info.text_layers == []
        assert info.is_scanned is False
        assert info.thumbnail is None

    def test_with_text_layers(self):
        layer = TextLayerInfo(
            index=0, text_preview="abc", char_count=3,
            bbox=(0, 0, 100, 20), color_id=0,
        )
        info = PdfPageInfo(page_index=1, has_text_layer=True, text_layers=[layer])
        assert info.has_text_layer is True
        assert len(info.text_layers) == 1


class TestPdfDocument:
    def test_defaults(self):
        doc = PdfDocument()
        assert doc.file_path is None
        assert doc.pages == []
        assert doc.is_modified is False
        assert doc.render_dpi == 300
        assert doc.thumbnail_dpi == 96

    def test_get_page(self):
        p0 = PdfPageInfo(page_index=0)
        p1 = PdfPageInfo(page_index=1)
        doc = PdfDocument(pages=[p0, p1])
        assert doc.get_page(0) is p0
        assert doc.get_page(1) is p1
        assert doc.get_page(2) is None

    def test_page_count(self):
        doc = PdfDocument(pages=[PdfPageInfo(page_index=i) for i in range(5)])
        assert doc.page_count == 5
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_pdf_document.py -v`
Expected: FAIL — ModuleNotFoundError: No module named 'vibeocr.models.pdf_document'

- [ ] **Step 3: 写数据模型实现**

创建 `src/vibeocr/models/pdf_document.py`：

```python
"""PDF 文档数据模型"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QPixmap


@dataclass
class TextLayerInfo:
    """单个文字层信息"""

    index: int
    text_preview: str
    char_count: int
    bbox: tuple[float, float, float, float]
    color_id: int


@dataclass
class PdfPageInfo:
    """单页状态"""

    page_index: int
    rotation: int = 0
    has_text_layer: bool = False
    text_layers: list[TextLayerInfo] = field(default_factory=list)
    is_scanned: bool = False
    thumbnail: QPixmap | None = None


@dataclass
class PdfDocument:
    """PDF 文档状态"""

    file_path: str | None = None
    pages: list[PdfPageInfo] = field(default_factory=list)
    is_modified: bool = False
    render_dpi: int = 300
    thumbnail_dpi: int = 96

    def get_page(self, index: int) -> PdfPageInfo | None:
        if 0 <= index < len(self.pages):
            return self.pages[index]
        return None

    @property
    def page_count(self) -> int:
        return len(self.pages)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_pdf_document.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/models/pdf_document.py tests/test_pdf_document.py
git commit -m "feat: 添加 PDF 文档数据模型"
```

---

### Task 3: PdfService — 打开、保存、渲染缩略图

**Files:**
- Create: `src/vibeocr/services/pdf_service.py`
- Create: `tests/test_pdf_service.py`

- [ ] **Step 1: 写服务层基础测试**

创建 `tests/test_pdf_service.py`：

```python
"""Tests for PDF service."""

import fitz
import pytest
from pathlib import Path


def _create_test_pdf(path: Path, num_pages: int = 3) -> Path:
    """创建用于测试的 PDF 文件。"""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Page {i + 1}", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def test_pdf(tmp_path):
    return _create_test_pdf(tmp_path / "test.pdf", num_pages=3)


@pytest.fixture
def pdf_service():
    from vibeocr.services.pdf_service import PdfService
    return PdfService()


class TestPdfServiceOpen:
    def test_open_pdf(self, pdf_service, test_pdf):
        doc = pdf_service.open(str(test_pdf))
        assert doc.file_path == str(test_pdf)
        assert doc.page_count == 3
        pdf_service.close()

    def test_open_nonexistent_raises(self, pdf_service):
        with pytest.raises(FileNotFoundError):
            pdf_service.open("/nonexistent/file.pdf")
        pdf_service.close()

    def test_open_encrypt_raises(self, pdf_service, tmp_path):
        import fitz
        src = fitz.open()
        src.new_page(width=612, height=792)
        path = str(tmp_path / "encrypted.pdf")
        src.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner")
        src.close()
        with pytest.raises(RuntimeError, match="加密"):
            pdf_service.open(path)
        pdf_service.close()


class TestPdfServiceSave:
    def test_save(self, pdf_service, test_pdf, tmp_path):
        pdf_service.open(str(test_pdf))
        pdf_service.rotate_pages([0], 90)
        save_path = str(tmp_path / "saved.pdf")
        pdf_service.save(save_path)
        pdf_service.close()

        verify = fitz.open(save_path)
        assert verify[0].rotation == 90
        verify.close()

    def test_save_creates_backup(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        pdf_service.rotate_pages([0], 90)
        pdf_service.save()
        pdf_service.close()

        assert Path(str(test_pdf) + ".bak").exists() is False
        verify = fitz.open(str(test_pdf))
        assert verify[0].rotation == 90
        verify.close()


class TestPdfServiceRender:
    def test_render_thumbnail(self, pdf_service, test_pdf, qapp):
        pdf_service.open(str(test_pdf))
        pixmap = pdf_service.render_page(0, dpi=96)
        assert pixmap is not None
        assert not pixmap.isNull()
        pdf_service.close()

    def test_render_page_for_ocr(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        img_array = pdf_service.render_page_as_array(0, dpi=300)
        assert img_array is not None
        assert img_array.shape[0] > 0
        assert img_array.shape[2] == 3  # RGB
        pdf_service.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_pdf_service.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 写 PdfService 基础实现**

创建 `src/vibeocr/services/pdf_service.py`：

```python
"""PDF 操作服务

基于 PyMuPDF (fitz) 封装 PDF 操作，包括打开/保存/渲染/旋转/插入/删除/文字层操作。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import fitz
import numpy as np
from PySide6.QtGui import QImage, QPixmap

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo, TextLayerInfo

logger = logging.getLogger(__name__)


class PdfService:
    """PDF 操作服务"""

    def __init__(self) -> None:
        self._doc: fitz.Document | None = None
        self._pdf_document: PdfDocument | None = None

    @property
    def document(self) -> PdfDocument | None:
        return self._pdf_document

    def is_open(self) -> bool:
        return self._doc is not None

    def open(self, file_path: str) -> PdfDocument:
        """打开 PDF 文件。"""
        if not Path(file_path).exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        self._doc = fitz.open(file_path)

        if self._doc.is_encrypted:
            self._doc.close()
            self._doc = None
            raise RuntimeError("不支持加密 PDF 文件")

        self._pdf_document = PdfDocument(file_path=file_path)
        self._build_page_infos()
        return self._pdf_document

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self._pdf_document = None

    def _build_page_infos(self) -> None:
        if self._doc is None or self._pdf_document is None:
            return
        pages = []
        for i in range(self._doc.page_count):
            page = self._doc[i]
            text_layers = self._detect_text_layers(i)
            pages.append(PdfPageInfo(
                page_index=i,
                rotation=page.rotation,
                has_text_layer=len(text_layers) > 0,
                text_layers=text_layers,
                is_scanned=len(text_layers) == 0 and self._is_page_scanned(i),
            ))
        self._pdf_document.pages = pages

    def _is_page_scanned(self, page_index: int) -> bool:
        """判断页面是否为扫描件（有大面积图片覆盖）。"""
        if self._doc is None:
            return False
        page = self._doc[page_index]
        images = page.get_images(full=True)
        if not images:
            return False
        page_rect = page.rect
        for img_info in images:
            xref = img_info[0]
            rects = page.get_image_rects(xref)
            for rect in rects:
                coverage = (rect.width * rect.height) / (page_rect.width * page_rect.height)
                if coverage > 0.5:
                    return True
        return False

    def save(self, path: str | None = None) -> None:
        """保存 PDF。如果 path 为 None 则覆盖原文件（先备份）。"""
        if self._doc is None or self._pdf_document is None:
            return

        save_path = path or self._pdf_document.file_path
        if save_path is None:
            return

        if path is None:
            backup_path = save_path + ".bak"
            shutil.copy2(save_path, backup_path)
            try:
                self._doc.save(save_path, incremental=True, encryption=0)
                Path(backup_path).unlink(missing_ok=True)
            except Exception:
                shutil.copy2(backup_path, save_path)
                Path(backup_path).unlink(missing_ok=True)
                raise
        else:
            self._doc.save(save_path, deflate=True)

        self._pdf_document.is_modified = False

    def render_page(self, page_index: int, dpi: int = 96) -> QPixmap:
        """将页面渲染为 QPixmap。"""
        if self._doc is None:
            return QPixmap()
        page = self._doc[page_index]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=mat)
        qimage = QImage(
            pixmap.samples,
            pixmap.width,
            pixmap.height,
            pixmap.stride,
            QImage.Format.Format_RGB888,
        )
        return QPixmap.fromImage(qimage.copy())

    def render_page_as_array(self, page_index: int, dpi: int = 300) -> np.ndarray:
        """将页面渲染为 numpy 数组（RGB），用于 OCR。"""
        if self._doc is None:
            return np.array([])
        page = self._doc[page_index]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=mat)
        return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, 3
        )

    def _detect_text_layers(self, page_index: int) -> list[TextLayerInfo]:
        """检测页面中的文字层。"""
        if self._doc is None:
            return []
        page = self._doc[page_index]
        blocks = page.get_text("dict")["blocks"]

        layers: list[TextLayerInfo] = []
        layer_index = 0
        for block in blocks:
            if block["type"] != 0:
                continue
            lines = block.get("lines", [])
            if not lines:
                continue
            text_parts = []
            for line in lines:
                for span in line.get("spans", []):
                    text_parts.append(span.get("text", ""))
            full_text = "".join(text_parts).strip()
            if not full_text:
                continue
            bbox = block["bbox"]
            layers.append(TextLayerInfo(
                index=layer_index,
                text_preview=full_text[:30],
                char_count=len(full_text),
                bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
                color_id=layer_index % 8,
            ))
            layer_index += 1
        return layers
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_pdf_service.py -v`
Expected: 所有测试通过

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/services/pdf_service.py tests/test_pdf_service.py
git commit -m "feat: 添加 PdfService 基础功能（打开/保存/渲染/文字层检测）"
```

---

### Task 4: PdfService — 页面操作（旋转、插入、删除、排序）

**Files:**
- Modify: `src/vibeocr/services/pdf_service.py`
- Modify: `tests/test_pdf_service.py`

- [ ] **Step 1: 写页面操作测试**

在 `tests/test_pdf_service.py` 末尾追加：

```python
class TestPdfServiceRotate:
    def test_rotate_single_page(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        pdf_service.rotate_pages([0], 90)
        assert pdf_service.document.pages[0].rotation == 90
        assert pdf_service.document.is_modified is True
        pdf_service.close()

    def test_rotate_all_pages(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        pdf_service.rotate_all_pages(90)
        for page in pdf_service.document.pages:
            assert page.rotation == 90
        pdf_service.close()


class TestPdfServiceDelete:
    def test_delete_page(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        assert pdf_service.document.page_count == 3
        pdf_service.delete_pages([1])
        assert pdf_service.document.page_count == 2
        assert pdf_service.document.pages[0].page_index == 0
        assert pdf_service.document.pages[1].page_index == 2
        pdf_service.close()


class TestPdfServiceInsert:
    def test_insert_blank_page(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        pdf_service.insert_blank_page(after_index=0)
        assert pdf_service.document.page_count == 4
        assert pdf_service.document.pages[1].rotation == 0
        pdf_service.close()

    def test_insert_from_another_pdf(self, pdf_service, test_pdf, tmp_path):
        other_pdf = _create_test_pdf(tmp_path / "other.pdf", num_pages=2)
        pdf_service.open(str(test_pdf))
        pdf_service.insert_pages_from(str(other_pdf), after_index=0)
        assert pdf_service.document.page_count == 5
        pdf_service.close()


class TestPdfServiceMove:
    def test_move_page(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        pdf_service.move_page(0, 2)
        assert pdf_service.document.pages[2].page_index == 0
        pdf_service.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_pdf_service.py::TestPdfServiceRotate -v`
Expected: FAIL — AttributeError

- [ ] **Step 3: 实现页面操作方法**

在 `src/vibeocr/services/pdf_service.py` 的 `PdfService` 类中追加以下方法（在 `_detect_text_layers` 之后）：

```python
    def rotate_pages(self, page_indices: list[int], angle: int) -> None:
        """旋转指定页面。"""
        if self._doc is None or self._pdf_document is None:
            return
        for idx in page_indices:
            if 0 <= idx < self._doc.page_count:
                page = self._doc[idx]
                page.set_rotation((page.rotation + angle) % 360)
                self._pdf_document.pages[idx].rotation = page.rotation
        self._pdf_document.is_modified = True
        self._invalidate_thumbnails(page_indices)

    def rotate_all_pages(self, angle: int) -> None:
        if self._doc is None or self._pdf_document is None:
            return
        self.rotate_pages(list(range(self._doc.page_count)), angle)

    def delete_pages(self, page_indices: list[int]) -> None:
        """删除指定页面（按索引降序删除）。"""
        if self._doc is None or self._pdf_document is None:
            return
        for idx in sorted(page_indices, reverse=True):
            if 0 <= idx < self._doc.page_count:
                self._doc.delete_page(idx)
        self._pdf_document.is_modified = True
        self._build_page_infos()

    def insert_blank_page(self, after_index: int, width: float = 612, height: float = 792) -> None:
        """在指定页面后插入空白页。"""
        if self._doc is None or self._pdf_document is None:
            return
        insert_at = after_index + 1
        self._doc.new_page(pno=insert_at, width=width, height=height)
        self._pdf_document.is_modified = True
        self._build_page_infos()

    def insert_pages_from(self, source_path: str, after_index: int) -> None:
        """从另一个 PDF 插入所有页面到指定位置之后。"""
        if self._doc is None or self._pdf_document is None:
            return
        src = fitz.open(source_path)
        insert_at = after_index + 1
        self._doc.insert_pdf(src, start_at=insert_at)
        src.close()
        self._pdf_document.is_modified = True
        self._build_page_infos()

    def move_page(self, from_index: int, to_index: int) -> None:
        """移动页面位置。"""
        if self._doc is None or self._pdf_document is None:
            return
        if from_index == to_index:
            return
        self._doc.move_page(from_index, to_index)
        self._pdf_document.is_modified = True
        self._build_page_infos()

    def _invalidate_thumbnails(self, page_indices: list[int]) -> None:
        """清除指定页面的缩略图缓存。"""
        if self._pdf_document is None:
            return
        for idx in page_indices:
            if 0 <= idx < len(self._pdf_document.pages):
                self._pdf_document.pages[idx].thumbnail = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_pdf_service.py -v`
Expected: 所有测试通过

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/services/pdf_service.py tests/test_pdf_service.py
git commit -m "feat: 添加 PdfService 页面操作（旋转/删除/插入/移动）"
```

---

### Task 5: PdfService — 添加文字层

**Files:**
- Modify: `src/vibeocr/services/pdf_service.py`
- Modify: `tests/test_pdf_service.py`

- [ ] **Step 1: 写添加文字层测试**

在 `tests/test_pdf_service.py` 末尾追加：

```python
class TestPdfServiceAddTextLayer:
    def test_add_text_layer_from_ocr_result(self, pdf_service, tmp_path):
        """测试从 OCR 结果添加文字层到扫描页。"""
        import fitz as fitz_mod
        from vibeocr.models.ocr_result import OCRResult, TextBlock

        # 创建一个无文字的 PDF（模拟扫描件）
        path = tmp_path / "scan.pdf"
        doc = fitz_mod.open()
        page = doc.new_page(width=612, height=792)
        # 插入一个大图覆盖页面（模拟扫描件）
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        rect = fitz_mod.Rect(0, 0, 612, 792)
        page.insert_image(rect, pixmap=fitz_mod.Pixmap(img.tobytes(), 612, 792, 3))
        doc.save(str(path))
        doc.close()

        pdf_service.open(str(path))
        assert pdf_service.document.pages[0].is_scanned is True
        assert pdf_service.document.pages[0].has_text_layer is False

        # 构造 OCR 结果
        result = OCRResult(
            raw_text="Hello World",
            text_blocks=[
                TextBlock(
                    text="Hello World",
                    score=0.99,
                    bbox=(50.0, 50.0, 300.0, 100.0),
                    page_idx=0,
                ),
            ],
        )

        pdf_service.add_text_layer(0, result)
        assert pdf_service.document.pages[0].has_text_layer is True
        assert pdf_service.document.is_modified is True
        pdf_service.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_pdf_service.py::TestPdfServiceAddTextLayer -v`
Expected: FAIL — AttributeError: 'PdfService' object has no attribute 'add_text_layer'

- [ ] **Step 3: 实现 add_text_layer 方法**

在 `src/vibeocr/services/pdf_service.py` 的 `PdfService` 类中追加：

```python
    def add_text_layer(self, page_index: int, ocr_result: object) -> None:
        """将 OCR 结果作为隐形文字层写入页面。"""
        if self._doc is None or self._pdf_document is None:
            return

        page = self._doc[page_index]
        page_rect = page.rect
        dpi = self._pdf_document.render_dpi
        scale = 72.0 / dpi

        text_blocks = getattr(ocr_result, "text_blocks", [])
        for block in text_blocks:
            if block.text is None or not block.text.strip():
                continue
            bbox = block.bbox
            if bbox is None:
                continue
            # bbox 归一化 [0,1000] → PDF 坐标
            x0 = bbox[0] / 1000.0 * page_rect.width
            y0 = bbox[1] / 1000.0 * page_rect.height
            x1 = bbox[2] / 1000.0 * page_rect.width
            y1 = bbox[3] / 1000.0 * page_rect.height

            rect = fitz.Rect(x0, y0, x1, y1)
            if rect.is_empty or rect.width < 1 or rect.height < 1:
                continue

            fontsize = rect.height * 0.8
            if fontsize < 1:
                continue

            page.insert_textbox(
                rect,
                block.text,
                fontsize=fontsize,
                color=(0, 0, 0),
                render_mode=3,  # 不可见但可选中/搜索
            )

        self._pdf_document.is_modified = True
        self._update_page_info(page_index)
```

同时添加 `_update_page_info` 辅助方法：

```python
    def _update_page_info(self, page_index: int) -> None:
        """更新指定页面的状态信息。"""
        if self._doc is None or self._pdf_document is None:
            return
        if page_index >= len(self._pdf_document.pages):
            return
        text_layers = self._detect_text_layers(page_index)
        page = self._doc[page_index]
        info = self._pdf_document.pages[page_index]
        info.rotation = page.rotation
        info.has_text_layer = len(text_layers) > 0
        info.text_layers = text_layers
        info.is_scanned = not text_layers and self._is_page_scanned(page_index)
        info.thumbnail = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_pdf_service.py::TestPdfServiceAddTextLayer -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/services/pdf_service.py tests/test_pdf_service.py
git commit -m "feat: 添加 PdfService 文字层写入功能"
```

---

### Task 6: PdfService — 删除文字层

**Files:**
- Modify: `src/vibeocr/services/pdf_service.py`
- Modify: `tests/test_pdf_service.py`

- [ ] **Step 1: 写删除文字层测试**

在 `tests/test_pdf_service.py` 末尾追加：

```python
class TestPdfServiceDeleteTextLayer:
    def test_delete_text_layer(self, pdf_service, test_pdf):
        pdf_service.open(str(test_pdf))
        assert pdf_service.document.pages[0].has_text_layer is True
        pdf_service.delete_text_layers(0)
        assert pdf_service.document.pages[0].has_text_layer is False
        assert pdf_service.document.is_modified is True
        pdf_service.close()

    def test_delete_text_layer_preserves_images(self, pdf_service, tmp_path):
        import fitz as fitz_mod

        path = tmp_path / "mixed.pdf"
        doc = fitz_mod.open()
        page = doc.new_page(width=612, height=792)
        # 添加文字
        page.insert_text((72, 72), "Some text", fontsize=12)
        # 添加图片
        img = np.ones((100, 100, 3), dtype=np.uint8) * 128
        page.insert_image(fitz_mod.Rect(72, 200, 172, 300), pixmap=fitz_mod.Pixmap(img.tobytes(), 100, 100, 3))
        doc.save(str(path))
        doc.close()

        pdf_service.open(str(path))
        assert pdf_service.document.pages[0].has_text_layer is True
        pdf_service.delete_text_layers(0)
        assert pdf_service.document.pages[0].has_text_layer is False

        # 验证图片仍在
        page = pdf_service._doc[0]
        assert len(page.get_images(full=True)) == 1
        pdf_service.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_pdf_service.py::TestPdfServiceDeleteTextLayer -v`
Expected: FAIL — AttributeError

- [ ] **Step 3: 实现 delete_text_layers 方法**

在 `src/vibeocr/services/pdf_service.py` 的 `PdfService` 类中追加：

```python
    def delete_text_layers(self, page_index: int) -> None:
        """删除指定页面的所有文字层。"""
        if self._doc is None or self._pdf_document is None:
            return
        page = self._doc[page_index]
        # 获取页面的文本块 xref 列表
        blocks = page.get_text("dict")["blocks"]
        text_xrefs = set()
        for block in blocks:
            if block["type"] != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    xref = span.get("xref", 0)
                    if xref > 0:
                        text_xrefs.add(xref)

        if not text_xrefs:
            return

        # 使用 redaction 方式清除文本
        for block in blocks:
            if block["type"] != 0:
                continue
            rect = fitz.Rect(block["bbox"])
            page.add_redact_annot(rect, fill=None)

        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        self._pdf_document.is_modified = True
        self._update_page_info(page_index)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_pdf_service.py::TestPdfServiceDeleteTextLayer -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/services/pdf_service.py tests/test_pdf_service.py
git commit -m "feat: 添加 PdfService 删除文字层功能"
```

---

### Task 7: PdfPreviewWindow — 独立预览窗口

**Files:**
- Create: `src/vibeocr/views/pdf_preview_window.py`

此任务是纯 UI 组件，不使用 TDD，但会提供手动测试步骤。

- [ ] **Step 1: 创建 PdfPreviewWindow**

创建 `src/vibeocr/views/pdf_preview_window.py`：

```python
"""PDF 页面独立预览窗口

双击缩略图时弹出，支持缩放/平移浏览。
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)


class _PreviewCanvas(QWidget):
    """可缩放/平移的画布。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._scale = 1.0

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._scale = 1.0
        self._update_size()
        self.update()

    def set_highlight_layers(self, layers: list) -> None:
        # TODO: 叠加半透明高亮层，用于删除文字层预览
        pass

    def _update_size(self) -> None:
        if self._pixmap is None:
            return
        w = int(self._pixmap.width() * self._scale)
        h = int(self._pixmap.height() * self._scale)
        self.setFixedSize(w, h)

    def paintEvent(self, event) -> None:
        if self._pixmap is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(self._scale, self._scale)
        painter.drawPixmap(0, 0, self._pixmap)
        painter.end()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._pixmap is None:
            return
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 0.9
        new_scale = self._scale * factor
        if 0.2 <= new_scale <= 5.0:
            self._scale = new_scale
            self._update_size()
            self.update()


class PdfPreviewWindow(QWidget):
    """PDF 页面预览窗口。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PDF 页面预览")
        self.resize(800, 1000)

        self._canvas = _PreviewCanvas()

        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    def set_page_pixmap(self, pixmap: QPixmap) -> None:
        self._canvas.set_pixmap(pixmap)
```

- [ ] **Step 2: 手动验证**

在 Python REPL 中：
```python
from PySide6.QtWidgets import QApplication
import sys
app = QApplication(sys.argv)
from vibeocr.views.pdf_preview_window import PdfPreviewWindow
from PySide6.QtGui import QPixmap
w = PdfPreviewWindow()
w.set_page_pixmap(QPixmap(400, 600))
w.show()
# 应显示一个 800x1000 窗口，包含白色 400x600 图片
# 滚轮可缩放
```

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/views/pdf_preview_window.py
git commit -m "feat: 添加 PDF 独立预览窗口"
```

---

### Task 8: PdfTab — 基础 UI 布局

**Files:**
- Create: `src/vibeocr/views/tabs/pdf_tab.py`

此任务是纯 UI 布局，不使用 TDD。

- [ ] **Step 1: 创建 PdfTab 基础结构**

创建 `src/vibeocr/views/tabs/pdf_tab.py`：

```python
"""PDF 处理标签页"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo
from vibeocr.services.pdf_service import PdfService
from vibeocr.views.pdf_preview_window import PdfPreviewWindow

logger = logging.getLogger(__name__)

_THUMBNAIL_SIZE = 160


class PdfTab(QWidget):
    """PDF 处理标签页"""

    ocr_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = PdfService()
        self._preview_window: PdfPreviewWindow | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._setup_toolbar(main_splitter)

        # 左侧：缩略图列表
        left_panel = self._create_thumbnail_panel()
        main_splitter.addWidget(left_panel)

        # 右侧：操作面板
        right_panel = self._create_operation_panel()
        main_splitter.addWidget(right_panel)

        main_splitter.setSizes([200, 600])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(main_splitter)

    def _setup_toolbar(self, parent: QWidget) -> None:
        pass  # 工具栏按钮放在右侧面板顶部

    def _create_thumbnail_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self._thumbnail_list = QListWidget()
        self._thumbnail_list.setFixedWidth(200)
        self._thumbnail_list.setIconSize(QPixmap(_THUMBNAIL_SIZE, _THUMBNAIL_SIZE).size())
        self._thumbnail_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )
        self._thumbnail_list.setDragDropMode(
            QListWidget.DragDropMode.InternalMove
        )
        self._thumbnail_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._thumbnail_list.customContextMenuRequested.connect(
            self._on_thumbnail_context_menu
        )
        self._thumbnail_list.itemDoubleClicked.connect(
            self._on_thumbnail_double_clicked
        )
        self._thumbnail_list.model().rowsMoved.connect(
            self._on_pages_reordered
        )

        layout.addWidget(self._thumbnail_list)
        return panel

    def _create_operation_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        # 文件操作按钮
        file_layout = QHBoxLayout()
        self._btn_open = QPushButton("打开")
        self._btn_open.clicked.connect(self._on_open_file)
        self._btn_save = QPushButton("保存")
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save.setEnabled(False)
        self._btn_save_as = QPushButton("另存为")
        self._btn_save_as.clicked.connect(self._on_save_as)
        self._btn_save_as.setEnabled(False)
        file_layout.addWidget(self._btn_open)
        file_layout.addWidget(self._btn_save)
        file_layout.addWidget(self._btn_save_as)
        file_layout.addStretch()
        layout.addLayout(file_layout)

        # 页面操作组
        page_group = QGroupBox("页面操作")
        page_layout = QHBoxLayout(page_group)
        self._btn_rotate_cw = QPushButton("顺时针90°")
        self._btn_rotate_cw.clicked.connect(lambda: self._on_rotate(90))
        self._btn_rotate_ccw = QPushButton("逆时针90°")
        self._btn_rotate_ccw.clicked.connect(lambda: self._on_rotate(-90))
        self._btn_rotate_all = QPushButton("旋转全部")
        self._btn_rotate_all.clicked.connect(self._on_rotate_all)
        self._btn_delete = QPushButton("删除选中页")
        self._btn_delete.clicked.connect(self._on_delete_pages)
        self._btn_insert = QPushButton("在选中页后插入")
        self._btn_insert.clicked.connect(self._on_insert_page)
        page_layout.addWidget(self._btn_rotate_cw)
        page_layout.addWidget(self._btn_rotate_ccw)
        page_layout.addWidget(self._btn_rotate_all)
        page_layout.addWidget(self._btn_delete)
        page_layout.addWidget(self._btn_insert)
        layout.addWidget(page_group)

        # 文字层操作组
        text_group = QGroupBox("文字层操作")
        text_layout = QVBoxLayout(text_group)
        text_btn_layout = QHBoxLayout()
        self._btn_add_text_layer = QPushButton("添加文字层")
        self._btn_add_text_layer.clicked.connect(self._on_add_text_layer)
        self._btn_del_text_layer = QPushButton("删除文字层")
        self._btn_del_text_layer.clicked.connect(self._on_delete_text_layer)
        self._btn_preview_text_layer = QPushButton("预览文字层")
        self._btn_preview_text_layer.clicked.connect(self._on_preview_text_layer)
        text_btn_layout.addWidget(self._btn_add_text_layer)
        text_btn_layout.addWidget(self._btn_del_text_layer)
        text_btn_layout.addWidget(self._btn_preview_text_layer)
        text_layout.addLayout(text_btn_layout)

        self._layer_status_label = QLabel("未打开文件")
        text_layout.addWidget(self._layer_status_label)
        layout.addWidget(text_group)

        # 进度区域
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.setVisible(False)
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self._progress_bar)
        progress_layout.addWidget(self._btn_cancel)
        layout.addLayout(progress_layout)

        # 状态标签
        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        layout.addStretch()

        # 初始状态：禁用所有操作按钮
        self._set_file_buttons_enabled(False)

        return panel

    def _set_file_buttons_enabled(self, enabled: bool) -> None:
        for btn in (
            self._btn_save, self._btn_save_as,
            self._btn_rotate_cw, self._btn_rotate_ccw,
            self._btn_rotate_all, self._btn_delete,
            self._btn_insert, self._btn_add_text_layer,
            self._btn_del_text_layer, self._btn_preview_text_layer,
        ):
            btn.setEnabled(enabled)
```

- [ ] **Step 2: 手动验证**

在 Python REPL 中：
```python
from PySide6.QtWidgets import QApplication
import sys
app = QApplication(sys.argv)
from vibeocr.views.tabs.pdf_tab import PdfTab
tab = PdfTab()
tab.show()
# 应显示左侧缩略图区域 + 右侧操作面板
```

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat: 添加 PDF 标签页基础 UI 布局"
```

---

### Task 9: PdfTab — 文件操作和缩略图显示

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py`

- [ ] **Step 1: 在 PdfTab 类中追加文件操作和缩略图方法**

在 `src/vibeocr/views/tabs/pdf_tab.py` 的 `PdfTab` 类末尾追加：

```python
    def _on_open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 PDF 文件", "", "PDF 文件 (*.pdf)"
        )
        if not path:
            return
        if self._service.is_open():
            self._confirm_close()
        try:
            doc = self._service.open(path)
        except (FileNotFoundError, RuntimeError) as e:
            QMessageBox.warning(self, "打开失败", str(e))
            return
        self._refresh_thumbnails()
        self._set_file_buttons_enabled(True)
        self._update_status()
        self._update_layer_status()

    def _refresh_thumbnails(self) -> None:
        """刷新缩略图列表。"""
        doc = self._service.document
        if doc is None:
            return
        self._thumbnail_list.clear()
        for page_info in doc.pages:
            pixmap = self._service.render_page(
                page_info.page_index, dpi=doc.thumbnail_dpi
            )
            scaled = pixmap.scaled(
                _THUMBNAIL_SIZE, _THUMBNAIL_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            item = QListWidgetItem(
                QIcon(scaled), f"第 {page_info.page_index + 1} 页"
            )
            item.setData(Qt.ItemDataRole.UserRole, page_info.page_index)
            self._thumbnail_list.addItem(item)

    def _update_status(self) -> None:
        doc = self._service.document
        if doc is None:
            self._status_label.setText("")
            return
        name = Path(doc.file_path).name if doc.file_path else ""
        modified = " (未保存)" if doc.is_modified else ""
        self._status_label.setText(
            f"{name} | {doc.page_count} 页{modified}"
        )
        self._btn_save.setEnabled(doc.is_modified)

    def _update_layer_status(self) -> None:
        doc = self._service.document
        if doc is None:
            self._layer_status_label.setText("未打开文件")
            return
        lines = []
        for p in doc.pages:
            if p.has_text_layer:
                lines.append(f"第{p.page_index + 1}页: {len(p.text_layers)}层文字层")
            else:
                status = "扫描件" if p.is_scanned else "无文字层"
                lines.append(f"第{p.page_index + 1}页: {status}")
        self._layer_status_label.setText("\n".join(lines))

    def _get_selected_page_indices(self) -> list[int]:
        indices = []
        for item in self._thumbnail_list.selectedItems():
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is not None:
                indices.append(idx)
        return sorted(set(indices))

    def _confirm_close(self) -> bool:
        doc = self._service.document
        if doc and doc.is_modified:
            reply = QMessageBox.question(
                self, "未保存的修改",
                "当前文件有未保存的修改，是否保存？",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Save:
                self._on_save()
            elif reply == QMessageBox.StandardButton.Cancel:
                return False
        self._service.close()
        return True

    def _on_save(self) -> None:
        try:
            self._service.save()
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self._update_status()

    def _on_save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", "", "PDF 文件 (*.pdf)"
        )
        if not path:
            return
        try:
            self._service.save(path)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self._update_status()
```

注意：文件开头需要添加 `from PySide6.QtGui import QIcon` 的导入（已有 `QPixmap`，追加 `QIcon`）。

修改 `src/vibeocr/views/tabs/pdf_tab.py` 顶部的 import 行：

```python
from PySide6.QtGui import QAction, QIcon, QPixmap
```

- [ ] **Step 2: 手动验证**

运行 VibeOCR 主窗口或独立显示 PdfTab，点击"打开"选择一个 PDF 文件。
Expected: 左侧显示缩略图列表，右侧显示文件名和页数。

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat: 添加 PdfTab 文件操作和缩略图显示"
```

---

### Task 10: PdfTab — 页面操作和右键菜单

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py`

- [ ] **Step 1: 在 PdfTab 类中追加页面操作和右键菜单方法**

在 `PdfTab` 类末尾追加：

```python
    def _on_thumbnail_context_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("顺时针旋转90°", lambda: self._on_rotate(90))
        menu.addAction("逆时针旋转90°", lambda: self._on_rotate(-90))
        menu.addSeparator()
        menu.addAction("删除页面", self._on_delete_pages)
        menu.addAction("在此页后插入", self._on_insert_page)
        menu.addSeparator()
        menu.addAction("预览", lambda: self._open_preview_for_selected())

        menu.exec(self._thumbnail_list.mapToGlobal(pos))

    def _on_thumbnail_double_clicked(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None:
            return
        self._open_preview(idx)

    def _on_pages_reordered(self) -> None:
        """拖拽排序后同步 PdfService。"""
        for new_row in range(self._thumbnail_list.count()):
            item = self._thumbnail_list.item(new_row)
            old_idx = item.data(Qt.ItemDataRole.UserRole)
            if old_idx is not None and old_idx != new_row:
                self._service.move_page(old_idx, new_row)
                break
        self._refresh_thumbnails()

    def _on_rotate(self, angle: int) -> None:
        indices = self._get_selected_page_indices()
        if not indices:
            return
        self._service.rotate_pages(indices, angle)
        self._refresh_thumbnails()
        self._update_status()

    def _on_rotate_all(self) -> None:
        reply = QMessageBox.question(
            self, "旋转全部页面",
            "确定旋转全部页面 90°？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._service.rotate_all_pages(90)
        self._refresh_thumbnails()
        self._update_status()

    def _on_delete_pages(self) -> None:
        indices = self._get_selected_page_indices()
        if not indices:
            return
        reply = QMessageBox.question(
            self, "删除页面",
            f"确定删除选中的 {len(indices)} 页？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._service.delete_pages(indices)
        self._refresh_thumbnails()
        self._update_status()
        self._update_layer_status()

    def _on_insert_page(self) -> None:
        indices = self._get_selected_page_indices()
        after_index = indices[0] if indices else 0

        path, _ = QFileDialog.getOpenFileName(
            self, "选择要插入的 PDF", "", "PDF 文件 (*.pdf)"
        )
        if path:
            try:
                self._service.insert_pages_from(path, after_index)
            except Exception as e:
                QMessageBox.warning(self, "插入失败", str(e))
                return
        else:
            self._service.insert_blank_page(after_index)
        self._refresh_thumbnails()
        self._update_status()
        self._update_layer_status()

    def _open_preview_for_selected(self) -> None:
        indices = self._get_selected_page_indices()
        if indices:
            self._open_preview(indices[0])

    def _open_preview(self, page_index: int) -> None:
        doc = self._service.document
        if doc is None:
            return
        pixmap = self._service.render_page(page_index, dpi=150)
        if self._preview_window is None:
            self._preview_window = PdfPreviewWindow()
        self._preview_window.set_page_pixmap(pixmap)
        self._preview_window.show()
        self._preview_window.raise_()
```

- [ ] **Step 2: 手动验证**

打开一个多页 PDF：
- 选中一页右键 → 旋转 → 缩略图应更新旋转
- 选中一页右键 → 删除 → 确认后缩略图减少
- 双击缩略图 → 弹出预览窗口
- 拖拽缩略图 → 页面顺序改变

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat: 添加 PdfTab 页面操作和右键菜单"
```

---

### Task 11: PdfTab — 添加文字层 UI（含进度条和取消）

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py`

- [ ] **Step 1: 在 PdfTab 类中追加添加文字层方法**

在 `PdfTab` 类末尾追加：

```python
    def _on_add_text_layer(self) -> None:
        doc = self._service.document
        if doc is None:
            return

        indices = self._get_selected_page_indices()
        if not indices:
            indices = list(range(doc.page_count))

        # 检查 OCR 服务
        if not hasattr(self, "_ocr_service") or self._ocr_service is None:
            QMessageBox.warning(
                self, "OCR 服务未就绪",
                "OCR 服务尚未初始化，请等待服务启动完成。",
            )
            return

        reply = QMessageBox.question(
            self, "添加文字层",
            f"将对 {len(indices)} 页执行 OCR 并添加隐形文字层。\n"
            "建议先另存为备份。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_add_text_layer(indices)

    def _run_add_text_layer(self, page_indices: list[int]) -> None:
        self._canceled = False
        self._btn_cancel.clicked.connect(self._on_cancel_ocr)
        self._progress_bar.setRange(0, len(page_indices))
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._btn_cancel.setVisible(True)
        self._set_file_buttons_enabled(False)
        self._btn_open.setEnabled(False)

        total = len(page_indices)
        for i, page_idx in enumerate(page_indices):
            if self._canceled:
                break
            self._progress_bar.setValue(i)
            self._status_label.setText(f"正在识别第 {i + 1}/{total} 页...")

            img_array = self._service.render_page_as_array(page_idx)
            if img_array.size == 0:
                continue
            try:
                from vibeocr.models.ocr_options import OCROptions
                result = self._ocr_service.recognize(img_array, OCROptions())
                self._service.add_text_layer(page_idx, result)
            except Exception as e:
                logger.error("OCR 失败 (页 %d): %s", page_idx, e)
                continue

            # 刷新该页缩略图
            if i % 5 == 0 or i == total - 1:
                self._refresh_thumbnails()

        self._progress_bar.setVisible(False)
        self._btn_cancel.setVisible(False)
        self._set_file_buttons_enabled(True)
        self._btn_open.setEnabled(True)
        self._update_status()
        self._update_layer_status()
        msg = "添加文字层完成" if not self._canceled else "已取消"
        self._status_label.setText(msg)

    def _on_cancel_ocr(self) -> None:
        self._canceled = True

    def set_ocr_service(self, service) -> None:
        """设置 OCR 服务实例（由 MainWindow 调用）。"""
        self._ocr_service = service
```

同时需要在 `__init__` 中初始化：

```python
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = PdfService()
        self._preview_window: PdfPreviewWindow | None = None
        self._ocr_service = None
        self._canceled = False
        self._setup_ui()
```

- [ ] **Step 2: 手动验证**

打开一个无文字层的 PDF，点击"添加文字层"，确认对话框后观察进度条逐页推进。完成后保存并打开保存后的 PDF，验证文字可选中。

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat: 添加 PdfTab 文字层添加功能（含进度条和取消）"
```

---

### Task 12: PdfTab — 删除文字层 UI 和预览模式

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py`
- Modify: `src/vibeocr/views/pdf_preview_window.py`

- [ ] **Step 1: 在 PdfPreviewWindow 中添加文字层高亮功能**

修改 `src/vibeocr/views/pdf_preview_window.py` 中的 `_PreviewCanvas` 类，替换 `set_highlight_layers` 的空实现：

```python
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._scale = 1.0
        self._highlight_layers: list = []
```

替换 `set_highlight_layers` 和 `paintEvent`：

```python
    def set_highlight_layers(self, layers: list) -> None:
        self._highlight_layers = layers
        self.update()

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QColor, QPen
        if self._pixmap is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(self._scale, self._scale)
        painter.drawPixmap(0, 0, self._pixmap)

        colors = [
            (0, 120, 215, 80),
            (0, 180, 80, 80),
            (230, 140, 0, 80),
            (180, 0, 180, 80),
            (0, 180, 180, 80),
            (215, 80, 80, 80),
            (140, 100, 0, 80),
            (80, 80, 215, 80),
        ]
        for layer in self._highlight_layers:
            bbox = layer.bbox
            color_idx = layer.color_id % len(colors)
            r, g, b, a = colors[color_idx]
            painter.setBrush(QColor(r, g, b, a))
            painter.setPen(QPen(QColor(r, g, b, 180), 1))
            painter.drawRect(
                int(bbox[0] * self._scale),
                int(bbox[1] * self._scale),
                int((bbox[2] - bbox[0]) * self._scale),
                int((bbox[3] - bbox[1]) * self._scale),
            )
        painter.end()
```

- [ ] **Step 2: 在 PdfTab 中追加删除和预览文字层方法**

在 `PdfTab` 类末尾追加：

```python
    def _on_preview_text_layer(self) -> None:
        indices = self._get_selected_page_indices()
        if not indices:
            QMessageBox.information(self, "预览文字层", "请先选择页面。")
            return
        page_idx = indices[0]
        doc = self._service.document
        if doc is None:
            return
        page_info = doc.get_page(page_idx)
        if page_info is None or not page_info.text_layers:
            QMessageBox.information(self, "预览文字层", "选中页面无文字层。")
            return

        pixmap = self._service.render_page(page_idx, dpi=150)
        if self._preview_window is None:
            self._preview_window = PdfPreviewWindow()
        self._preview_window.set_page_pixmap(pixmap)
        self._preview_window._canvas.set_highlight_layers(page_info.text_layers)
        self._preview_window.show()
        self._preview_window.raise_()

    def _on_delete_text_layer(self) -> None:
        indices = self._get_selected_page_indices()
        if not indices:
            QMessageBox.information(self, "删除文字层", "请先选择页面。")
            return

        reply = QMessageBox.question(
            self, "删除文字层",
            f"将删除选中 {len(indices)} 页的文字层。\n"
            "建议先另存为备份。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for idx in indices:
            self._service.delete_text_layers(idx)
        self._refresh_thumbnails()
        self._update_status()
        self._update_layer_status()
```

- [ ] **Step 3: 手动验证**

打开一个有文字层的 PDF：
1. 选中一页 → 点击"预览文字层" → 预览窗口应显示彩色高亮覆盖
2. 点击"删除文字层" → 确认后缩略图刷新，文字层状态更新

- [ ] **Step 4: 提交**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py src/vibeocr/views/pdf_preview_window.py
git commit -m "feat: 添加 PdfTab 删除/预览文字层 UI"
```

---

### Task 13: 集成 — 添加 PDF 标签页到 MainWindow

**Files:**
- Modify: `src/vibeocr/views/main_window.py`

- [ ] **Step 1: 在 main_window.py 中添加 PDF 标签页初始化**

在 `src/vibeocr/views/main_window.py` 的 import 区域追加（约第 37 行附近）：

```python
from vibeocr.views.tabs.pdf_tab import PdfTab
```

在 `_setup_ui` 方法中，`_init_qrcode_tab()` 调用之后追加：

```python
        # 添加 PDF 处理标签页
        self._init_pdf_tab()
```

在 `main_window.py` 中新增方法（在 `_init_qrcode_tab` 之后）：

```python
    def _init_pdf_tab(self) -> None:
        """初始化 PDF 处理标签页"""
        self._pdf_tab = PdfTab()
        self._ui.tabWidget.insertTab(
            self._ui.tabWidget.indexOf(self._ui.tabSettings),
            self._pdf_tab,
            "PDF 处理",
        )
        logging.debug("PDF 处理标签页已添加")
```

在 `_on_ocr_service_ready` 方法中（搜索 `set_ocr_service` 调用的位置），追加传递 OCR 服务给 PdfTab：

```python
        if hasattr(self, "_pdf_tab") and self._pdf_tab:
            self._pdf_tab.set_ocr_service(service)
```

在 `_restore_layout` 方法中追加 PDF 标签页布局恢复（如需要）。

在 `_save_layout` 方法中追加 PDF 标签页布局保存（如需要）。

- [ ] **Step 2: 运行现有测试确认无回归**

Run: `pytest tests/test_main_window.py -v`
Expected: 所有测试通过

- [ ] **Step 3: 手动验证**

启动 VibeOCR，检查标签页栏是否出现"PDF 处理"标签页，点击进入后确认 UI 正常显示。

- [ ] **Step 4: 提交**

```bash
git add src/vibeocr/views/main_window.py
git commit -m "feat: 集成 PDF 处理标签页到主窗口"
```

---

### Task 14: 最终验证

- [ ] **Step 1: 运行全部测试**

Run: `pytest tests/ -v --tb=short`
Expected: 所有测试通过

- [ ] **Step 2: 运行类型检查**

Run: `pyright src/vibeocr/models/pdf_document.py src/vibeocr/services/pdf_service.py`
Expected: 无错误

- [ ] **Step 3: 端到端手动测试**

1. 启动 VibeOCR → 点击"PDF 处理"标签页
2. 打开一个多页 PDF → 缩略图显示正确
3. 选中一页 → 右键旋转 → 缩略图更新
4. 双击缩略图 → 预览窗口弹出 → 滚轮缩放正常
5. 点击"添加文字层" → 进度条推进 → 完成后保存
6. 重新打开保存的 PDF → 文字可选中/搜索
7. 选中带文字层的页 → "预览文字层" → 彩色高亮
8. "删除文字层" → 确认 → 文字层消失
9. 保存 → 关闭 → 重新打开验证

- [ ] **Step 4: 最终提交**

```bash
git add -A
git commit -m "feat: 完成 PDF 处理标签页功能"
```
