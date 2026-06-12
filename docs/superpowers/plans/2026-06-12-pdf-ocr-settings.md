# PDF OCR 独立设置与文字层修复 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 PDF 处理添加独立的 OCR 设置页面，修复 bbox 坐标逆变换，修复文字层预览高亮。

**Architecture:** 新增 `PdfGlobalSettings` 数据模型存储 PDF 专用参数（DPI/内存/字号），复用 `OCRPreferences` 的 `"pdf"` 数据源持久化管道选项。在 `PdfService` 中新增逆旋转映射函数修复坐标错乱。预览窗口增加正确的坐标转换。

**Tech Stack:** PySide6, PyMuPDF (fitz), dataclasses, pytest

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/vibeocr/models/pdf_ocr_options.py` | **Create** | PDF 专用全局设置数据模型 + 序列化 |
| `src/vibeocr/services/pdf_service.py` | **Modify** | 新增 `_denormalize_and_unrotate_bbox()`、`bbox_to_pixel()`；修改 `add_text_layer()` |
| `src/vibeocr/utils/ocr_preferences.py` | **Modify** | 新增 `"pdf"` 数据源、`_pdf_settings` 字段、配置版本升至 3 |
| `src/vibeocr/managers/pdf_session_manager.py` | **Modify** | `start_ocr()` 接收 `PdfGlobalSettings`，传递到 worker 和 `add_text_layer()` |
| `src/vibeocr/workers/pdf_ocr_worker.py` | **Modify** | 接收 `OCROptions`（从偏好读取） |
| `src/vibeocr/widgets/pdf_options_widget.py` | **Create** | PDF 设置页组件（管道选项 + 全局设置） |
| `src/vibeocr/views/settings_page_controller.py` | **Modify** | 新增 `_init_pdf_options()` |
| `src/vibeocr/views/tabs/pdf_tab.py` | **Modify** | 从 `OCRPreferences` 读取 PDF 配置 |
| `src/vibeocr/views/pdf_preview_window.py` | **Modify** | 修复坐标转换，添加 tooltip |
| `tests/services/test_pdf_service_bbox.py` | **Create** | 逆变换坐标测试 |
| `tests/models/test_pdf_ocr_options.py` | **Create** | PdfGlobalSettings 序列化测试 |

---

### Task 1: PdfGlobalSettings 数据模型

**Files:**
- Create: `src/vibeocr/models/pdf_ocr_options.py`
- Create: `tests/models/test_pdf_ocr_options.py`

- [ ] **Step 1: Write failing tests for PdfGlobalSettings**

```python
# tests/models/test_pdf_ocr_options.py
"""Tests for PdfGlobalSettings data model."""

from vibeocr.models.pdf_ocr_options import PdfGlobalSettings


class TestPdfGlobalSettingsDefaults:
    def test_default_values(self):
        s = PdfGlobalSettings()
        assert s.render_dpi == 300
        assert s.max_pixels == 16_000_000
        assert s.font_size_ratio == 0.8
        assert s.text_layer_visible is False
        assert s.font_size_retry_count == 5
        assert s.font_size_shrink_factor == 0.75

    def test_custom_values(self):
        s = PdfGlobalSettings(render_dpi=150, max_pixels=8_000_000, font_size_ratio=0.6)
        assert s.render_dpi == 150
        assert s.max_pixels == 8_000_000
        assert s.font_size_ratio == 0.6


class TestPdfGlobalSettingsSerialization:
    def test_to_dict_roundtrip(self):
        s = PdfGlobalSettings(render_dpi=200, font_size_ratio=0.7)
        d = s.to_dict()
        assert d["render_dpi"] == 200
        assert d["font_size_ratio"] == 0.7

        s2 = PdfGlobalSettings.from_dict(d)
        assert s2.render_dpi == 200
        assert s2.font_size_ratio == 0.7

    def test_from_dict_missing_fields_use_defaults(self):
        s = PdfGlobalSettings.from_dict({"render_dpi": 150})
        assert s.render_dpi == 150
        assert s.max_pixels == 16_000_000
        assert s.font_size_retry_count == 5

    def test_from_dict_empty(self):
        s = PdfGlobalSettings.from_dict({})
        assert s == PdfGlobalSettings()

    def test_adjust_dpi_no_change_when_within_limit(self):
        s = PdfGlobalSettings(render_dpi=300, max_pixels=16_000_000)
        # A4 at 300dpi = 2480*3508 = ~8.7M pixels, well within 16M
        adjusted = s.adjust_dpi(612, 792)
        assert adjusted == 300

    def test_adjust_dpi_reduces_when_exceeds_limit(self):
        s = PdfGlobalSettings(render_dpi=600, max_pixels=4_000_000)
        # A4 at 600dpi = 4960*7016 = ~34.8M, way over 4M
        adjusted = s.adjust_dpi(612, 792)
        assert adjusted < 600
        # Verify the adjusted DPI stays within limit
        w = int(612 / 72 * adjusted)
        h = int(792 / 72 * adjusted)
        assert w * h <= 4_000_000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/models/test_pdf_ocr_options.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vibeocr.models.pdf_ocr_options'`

- [ ] **Step 3: Implement PdfGlobalSettings**

```python
# src/vibeocr/models/pdf_ocr_options.py
"""PDF OCR 全局设置数据模型

PDF 处理的专用参数（DPI、内存控制、字号策略等），
与截图识别的 OCROptions 完全独立。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class PdfGlobalSettings:
    """PDF OCR 全局设置

    控制 PDF 页面渲染和文字层写入行为。
    不包含管道选项（管道选项复用 OCROptions，通过 OCRPreferences "pdf" 数据源管理）。

    Attributes:
        render_dpi: PDF 页面渲染 DPI（传给 render_page_as_array）。
        max_pixels: 单页最大像素数，超过时自动降低 DPI。
        font_size_ratio: 字号占矩形高度的比例。
        text_layer_visible: True → render_mode=0（可见），False → render_mode=3（隐形）。
        font_size_retry_count: 文字溢出时字号缩放重试次数。
        font_size_shrink_factor: 每次重试的字号缩放因子。
    """

    render_dpi: int = 300
    max_pixels: int = 16_000_000
    font_size_ratio: float = 0.8
    text_layer_visible: bool = False
    font_size_retry_count: int = 5
    font_size_shrink_factor: float = 0.75

    def to_dict(self) -> dict:
        return {
            "render_dpi": self.render_dpi,
            "max_pixels": self.max_pixels,
            "font_size_ratio": self.font_size_ratio,
            "text_layer_visible": self.text_layer_visible,
            "font_size_retry_count": self.font_size_retry_count,
            "font_size_shrink_factor": self.font_size_shrink_factor,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PdfGlobalSettings:
        if not data:
            return cls()
        return cls(
            render_dpi=data.get("render_dpi", 300),
            max_pixels=data.get("max_pixels", 16_000_000),
            font_size_ratio=data.get("font_size_ratio", 0.8),
            text_layer_visible=data.get("text_layer_visible", False),
            font_size_retry_count=data.get("font_size_retry_count", 5),
            font_size_shrink_factor=data.get("font_size_shrink_factor", 0.75),
        )

    def adjust_dpi(self, page_width: float, page_height: float) -> int:
        """根据像素上限自动调整 DPI。

        Args:
            page_width: PDF 页面宽度（points）。
            page_height: PDF 页面高度（points）。

        Returns:
            调整后的 DPI（不超过 render_dpi，不超过 max_pixels 限制）。
        """
        target_dpi = self.render_dpi
        pixel_w = page_width / 72.0 * target_dpi
        pixel_h = page_height / 72.0 * target_dpi
        total_pixels = pixel_w * pixel_h
        if total_pixels <= self.max_pixels:
            return target_dpi
        # 计算满足 max_pixels 的最大 DPI
        scale = math.sqrt(self.max_pixels / total_pixels)
        adjusted = int(target_dpi * scale)
        # 确保至少 72 DPI
        return max(72, adjusted)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/models/test_pdf_ocr_options.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/models/pdf_ocr_options.py tests/models/test_pdf_ocr_options.py
git commit -m "feat: add PdfGlobalSettings data model with DPI adjustment"
```

---

### Task 2: Bbox 坐标逆变换

**Files:**
- Create: `tests/services/test_pdf_service_bbox.py`
- Modify: `src/vibeocr/services/pdf_service.py` (新增 `_denormalize_and_unrotate_bbox`)

- [ ] **Step 1: Write failing tests for inverse bbox transform**

```python
# tests/services/test_pdf_service_bbox.py
"""Tests for PDF bbox coordinate inverse transform."""

import fitz
import pytest

from vibeocr.services.pdf_service import PdfService


# 标准信纸尺寸 page_rect: 612×792 points
_PAGE_RECT = fitz.Rect(0, 0, 612, 792)


class TestDenormalizeAndUnrotateBbox:
    """测试 _denormalize_and_unrotate_bbox 的四种旋转角度。"""

    def test_no_rotation(self):
        """0°：直接映射，无变换。"""
        # bbox 覆盖整个页面 [0, 1000]
        result = PdfService._denormalize_and_unrotate_bbox(
            (0.0, 0.0, 1000.0, 1000.0), 0, _PAGE_RECT
        )
        assert result.is_empty is False
        assert abs(result.x0 - 0) < 1
        assert abs(result.y0 - 0) < 1
        assert abs(result.x1 - 612) < 1
        assert abs(result.y1 - 792) < 1

    def test_no_rotation_partial(self):
        """0°：部分区域 bbox。"""
        result = PdfService._denormalize_and_unrotate_bbox(
            (100.0, 200.0, 500.0, 800.0), 0, _PAGE_RECT
        )
        assert abs(result.x0 - 61.2) < 1  # 100/1000 * 612
        assert abs(result.y0 - 158.4) < 1  # 200/1000 * 792
        assert abs(result.x1 - 306.0) < 1  # 500/1000 * 612
        assert abs(result.y1 - 633.6) < 1  # 800/1000 * 792

    def test_rotation_180_center(self):
        """180°：中心点保持不变。"""
        # bbox 在归一化空间的中心 (500, 500)
        result = PdfService._denormalize_and_unrotate_bbox(
            (450.0, 450.0, 550.0, 550.0), 180, _PAGE_RECT
        )
        cx = (result.x0 + result.x1) / 2
        cy = (result.y0 + result.y1) / 2
        # 页面中心 (306, 396)
        assert abs(cx - 306) < 2
        assert abs(cy - 396) < 2

    def test_rotation_180_corner(self):
        """180°：左上角映射到右下角。"""
        result = PdfService._denormalize_and_unrotate_bbox(
            (0.0, 0.0, 100.0, 100.0), 180, _PAGE_RECT
        )
        # 原始左上 → 旋转后映射到右下区域
        assert result.x0 > 300  # 应在右半部分
        assert result.y0 > 400  # 应在下半部分

    def test_rotation_90_width_height_swap(self):
        """90°：旋转后宽度方向映射到页面高度方向。"""
        # 窄长条 (归一化 x: 0-100, y: 0-900)
        result = PdfService._denormalize_and_unrotate_bbox(
            (0.0, 0.0, 100.0, 900.0), 90, _PAGE_RECT
        )
        # 90° 逆变换：y 方向映射到 x 方向，x 方向映射到 y 方向
        # 横向范围 = 0-900/1000*612 → 约 0-550.8
        # 纵向范围 = (1-100/1000)*792 - (1-0/1000)*792 → 约 712.8-792
        assert result.width > result.height  # 旋转后窄条变横条

    def test_rotation_270_width_height_swap(self):
        """270°：与 90° 方向相反。"""
        result = PdfService._denormalize_and_unrotate_bbox(
            (0.0, 0.0, 100.0, 900.0), 270, _PAGE_RECT
        )
        assert result.width > result.height

    def test_roundtrip_90(self):
        """90° 旋转后完整 bbox 覆盖整个页面。"""
        result = PdfService._denormalize_and_unrotate_bbox(
            (0.0, 0.0, 1000.0, 1000.0), 90, _PAGE_RECT
        )
        assert abs(result.width - 612) < 2
        assert abs(result.height - 792) < 2

    def test_roundtrip_270(self):
        """270° 旋转后完整 bbox 覆盖整个页面。"""
        result = PdfService._denormalize_and_unrotate_bbox(
            (0.0, 0.0, 1000.0, 1000.0), 270, _PAGE_RECT
        )
        assert abs(result.width - 612) < 2
        assert abs(result.height - 792) < 2

    def test_invalid_angle_defaults_to_zero(self):
        """无效角度视为 0°。"""
        result = PdfService._denormalize_and_unrotate_bbox(
            (100.0, 200.0, 500.0, 800.0), 45, _PAGE_RECT
        )
        expected = PdfService._denormalize_and_unrotate_bbox(
            (100.0, 200.0, 500.0, 800.0), 0, _PAGE_RECT
        )
        assert abs(result.x0 - expected.x0) < 1
        assert abs(result.y0 - expected.y0) < 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/services/test_pdf_service_bbox.py -v`
Expected: FAIL — `AttributeError: type object 'PdfService' has no attribute '_denormalize_and_unrotate_bbox'`

- [ ] **Step 3: Implement inverse bbox transform**

在 `src/vibeocr/services/pdf_service.py` 的 `# ---- helpers ----` 部分之前添加：

```python
    # ---- bbox coordinate transforms --------------------------------

    @staticmethod
    def _denormalize_and_unrotate_bbox(
        bbox: tuple[float, float, float, float],
        preproc_angle: int,
        page_rect: fitz.Rect,
    ) -> fitz.Rect:
        """将 [0, 1000] 归一化 bbox 逆旋转后映射到 PDF 页面坐标。

        当 OCR 预处理旋转了图像（preproc_angle），bbox 坐标在旋转后的空间中。
        此方法执行逆变换，将坐标映射回原始页面坐标。

        Args:
            bbox: 归一化坐标 (x0, y0, x1, y1)，范围 [0, 1000]。
            preproc_angle: 预处理旋转角度 (0, 90, 180, 270)。
            page_rect: PDF 页面矩形 (points)。

        Returns:
            映射后的 fitz.Rect。
        """
        nx0, ny0, nx1, ny1 = bbox[0] / 1000, bbox[1] / 1000, bbox[2] / 1000, bbox[3] / 1000
        pw, ph = page_rect.width, page_rect.height

        if preproc_angle == 90:
            # 逆时针 90°: y→x, (1-x)→y
            x0 = ny0 * pw
            y0 = (1 - nx1) * ph
            x1 = ny1 * pw
            y1 = (1 - nx0) * ph
        elif preproc_angle == 180:
            # 中心对称
            x0 = (1 - nx1) * pw
            y0 = (1 - ny1) * ph
            x1 = (1 - nx0) * pw
            y1 = (1 - ny0) * ph
        elif preproc_angle == 270:
            # 顺时针 90° (= 逆时针 270°): (1-y)→x, x→y
            x0 = (1 - ny1) * pw
            y0 = nx0 * ph
            x1 = (1 - ny0) * pw
            y1 = nx1 * ph
        else:
            # 0° 或未知角度：直接映射
            x0 = nx0 * pw
            y0 = ny0 * ph
            x1 = nx1 * pw
            y1 = ny1 * ph

        return fitz.Rect(x0, y0, x1, y1)

    @staticmethod
    def bbox_to_pixel(
        bbox: tuple[float, float, float, float],
        page_rect: fitz.Rect,
        render_dpi: int,
        source: str = "pdf",
    ) -> tuple[float, float, float, float]:
        """将 bbox 转换为渲染图像的像素坐标。

        Args:
            bbox: 输入 bbox。
            page_rect: PDF 页面矩形 (points)。
            render_dpi: 渲染 DPI。
            source: "pdf" 表示 bbox 是 PDF points 坐标，
                    "normalized" 表示 [0, 1000] 归一化坐标。

        Returns:
            像素坐标 (x0, y0, x1, y1)。
        """
        if source == "normalized":
            # 先转为 PDF points
            x0 = bbox[0] / 1000 * page_rect.width
            y0 = bbox[1] / 1000 * page_rect.height
            x1 = bbox[2] / 1000 * page_rect.width
            y1 = bbox[3] / 1000 * page_rect.height
        else:
            x0, y0, x1, y1 = bbox

        # PDF points → pixels: coord / 72 * dpi
        scale = render_dpi / 72.0
        return (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/services/test_pdf_service_bbox.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/services/pdf_service.py tests/services/test_pdf_service_bbox.py
git commit -m "feat: add bbox inverse rotation transform and pixel conversion"
```

---

### Task 3: 修改 add_text_layer 使用逆变换和 PdfGlobalSettings

**Files:**
- Modify: `src/vibeocr/services/pdf_service.py` (修改 `add_text_layer` 方法)
- Modify: `tests/services/test_pdf_service.py` (更新现有测试 + 新增带旋转测试)

- [ ] **Step 1: Write failing test for add_text_layer with rotation**

在 `tests/services/test_pdf_service.py` 的 `TestPdfServiceTextLayer` 中添加：

```python
    def test_add_text_layer_with_90_rotation(self, tmp_path):
        """90° 预处理旋转后 bbox 仍然映射到正确位置。"""
        import numpy as np

        from vibeocr.models.ocr_result import OCRResult, TextBlock

        path = tmp_path / "scan_rotated.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        # 模拟 OCR 检测到图像旋转 90°，返回旋转后空间中的 bbox
        result = OCRResult(
            raw_text="Hello",
            text_blocks=[
                TextBlock(
                    text="Hello",
                    score=0.99,
                    bbox=(400.0, 100.0, 600.0, 200.0),  # [0, 1000] 归一化
                    page_idx=0,
                ),
            ],
            preproc_angle=90,
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result)
        assert pdf_doc.pages[0].has_text_layer is True

        # 验证文字层出现在页面上（而非页面外）
        layers = PdfService.detect_text_layers(doc, 0)
        assert len(layers) > 0
        # 文字层应该完全在页面内
        page_rect = doc[0].rect
        for layer in layers:
            layer_rect = fitz.Rect(layer.bbox)
            assert layer_rect.x0 >= -1  # 允许 1pt 误差
            assert layer_rect.y0 >= -1
            assert layer_rect.x1 <= page_rect.width + 1
            assert layer_rect.y1 <= page_rect.height + 1
        doc.close()

    def test_add_text_layer_uses_global_settings(self, tmp_path):
        """PdfGlobalSettings 控制字号和重试参数。"""
        import numpy as np

        from vibeocr.models.ocr_options import OCROptions
        from vibeocr.models.ocr_result import OCRResult, TextBlock
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        path = tmp_path / "scan_settings.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        doc, pdf_doc = PdfService.open_doc(str(path))
        settings = PdfGlobalSettings(font_size_ratio=0.5, font_size_retry_count=2)
        result = OCRResult(
            raw_text="Test",
            text_blocks=[
                TextBlock(
                    text="Test",
                    score=0.99,
                    bbox=(100.0, 100.0, 500.0, 200.0),
                    page_idx=0,
                ),
            ],
        )
        PdfService.add_text_layer(doc, pdf_doc, 0, result, pdf_settings=settings)
        assert pdf_doc.pages[0].has_text_layer is True
        doc.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/services/test_pdf_service.py::TestPdfServiceTextLayer::test_add_text_layer_with_90_rotation -v`
Expected: FAIL — `add_text_layer` 不接受 `pdf_settings` 参数

- [ ] **Step 3: Modify add_text_layer**

将 `src/vibeocr/services/pdf_service.py` 中的 `add_text_layer` 替换为：

```python
    @staticmethod
    def add_text_layer(
        doc: fitz.Document,
        pdf_document: PdfDocument,
        page_index: int,
        ocr_result: object,
        pdf_settings: object | None = None,
    ) -> None:
        """将 OCR 结果作为隐形文字层写入 PDF 页面。

        Args:
            doc: fitz.Document 实例。
            pdf_document: PdfDocument 状态对象。
            page_index: 页码索引。
            ocr_result: OCRResult 实例。
            pdf_settings: PdfGlobalSettings 实例（None 则使用默认值）。
        """
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        settings = pdf_settings if pdf_settings is not None else PdfGlobalSettings()

        page = doc[page_index]
        page_rect = page.rect
        preproc_angle = getattr(ocr_result, "preproc_angle", 0)

        text_blocks = getattr(ocr_result, "text_blocks", [])
        for block in text_blocks:
            if block.text is None or not block.text.strip():
                continue
            bbox = block.bbox
            if bbox is None:
                continue

            # 逆旋转 + 归一化到 PDF 页面坐标
            rect = PdfService._denormalize_and_unrotate_bbox(
                bbox, preproc_angle, page_rect
            )
            if rect.is_empty or rect.width < 1 or rect.height < 1:
                continue

            fontsize = rect.height * settings.font_size_ratio
            if fontsize < 1:
                continue

            render_mode = 0 if settings.text_layer_visible else 3

            for _ in range(settings.font_size_retry_count):
                rc = page.insert_textbox(
                    rect,
                    block.text,
                    fontsize=fontsize,
                    color=(0, 0, 0),
                    render_mode=render_mode,
                )
                if rc >= 0:
                    break
                fontsize *= settings.font_size_shrink_factor
                if fontsize < 1:
                    break

        pdf_document.is_modified = True
        PdfService.update_page_info(doc, pdf_document, page_index)
```

- [ ] **Step 4: Update existing test to pass pdf_settings=None (backward compatible)**

现有的 `test_add_text_layer_from_ocr_result` 不传 `pdf_settings`，应兼容默认值。无需修改，但需确认通过。

Run: `python -m pytest tests/services/test_pdf_service.py::TestPdfServiceTextLayer -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/services/pdf_service.py tests/services/test_pdf_service.py
git commit -m "feat: add_text_layer uses inverse rotation and PdfGlobalSettings"
```

---

### Task 4: PdfOcrWorker + PdfSessionManager 传递配置

**Files:**
- Modify: `src/vibeocr/workers/pdf_ocr_worker.py`
- Modify: `src/vibeocr/managers/pdf_session_manager.py`

- [ ] **Step 1: Modify PdfOcrWorker to use adjusted DPI**

修改 `src/vibeocr/workers/pdf_ocr_worker.py` 的 `run` 方法：

在文件顶部 TYPE_CHECKING 块中添加导入：
```python
if TYPE_CHECKING:
    import numpy as np

    from vibeocr.models.ocr_options import OCROptions
    from vibeocr.models.pdf_ocr_options import PdfGlobalSettings
    from vibeocr.services.ocr_service_base import OCRServiceBase
```

`__init__` 方法签名不变（已接收 `ocr_options`）。

`run` 方法中修改 `options` 使用逻辑：
```python
    def run(self) -> None:
        from vibeocr.models.ocr_options import OCROptions

        success = 0
        fail = 0
        total = len(self._pages)
        options = self._ocr_options if self._ocr_options is not None else OCROptions()

        for i, (page_index, image) in enumerate(self._pages):
            if self._cancelled:
                break
            self.progress.emit(i + 1, total)
            try:
                result = self._ocr_service.recognize(image, options)
                self.page_done.emit(page_index, result)
                success += 1
            except Exception as e:
                logger.error("PdfOcrWorker OCR failed (page %d): %s", page_index, e)
                self.page_done.emit(page_index, None)
                fail += 1

        self.all_done.emit(self._session_id, success, fail)
```

（此步骤代码实际上不变，Worker 已经正确接收和使用 `ocr_options`。）

- [ ] **Step 2: Modify PdfSessionManager to pass settings through**

修改 `src/vibeocr/managers/pdf_session_manager.py`:

1. 在 `start_ocr` 中接收 `PdfGlobalSettings`，用它调整 DPI
2. 在 `_on_ocr_page_done` 中传递 `pdf_settings` 给 `add_text_layer`

在 `start_ocr` 方法中，修改渲染逻辑：

```python
    def start_ocr(
        self,
        page_indices: list[int],
        ocr_options: OCROptions | None = None,
        pdf_settings: PdfGlobalSettings | None = None,
    ) -> None:
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        if pdf_settings is None:
            pdf_settings = PdfGlobalSettings()

        session = self.active_session
        if session is None or self._ocr_service is None:
            return

        self._cancel_ocr_worker()

        pages: list[tuple[int, np.ndarray]] = []
        for page_idx in page_indices:
            with session.doc_lock:
                page = session.doc[page_idx]
                adjusted_dpi = pdf_settings.adjust_dpi(page.rect.width, page.rect.height)
                img_array = PdfService.render_page_as_array(session.doc, page_idx, dpi=adjusted_dpi)
            if img_array.size > 0:
                pages.append((page_idx, img_array))

        if not pages:
            return

        self._pdf_settings = pdf_settings

        self._ocr_worker = PdfOcrWorker(
            session_id=session.file_path,
            pages=pages,
            ocr_service=self._ocr_service,
            ocr_options=ocr_options,
        )
        self._ocr_worker.page_done.connect(self._on_ocr_page_done)
        self._ocr_worker.progress.connect(self._on_ocr_progress)
        self._ocr_worker.all_done.connect(self._on_ocr_all_done)
        self._ocr_worker.start()
```

在 `__init__` 中添加：
```python
        self._pdf_settings: PdfGlobalSettings | None = None
```

修改 `_on_ocr_page_done`：
```python
    def _on_ocr_page_done(self, page_index: int, result) -> None:
        worker = self._ocr_worker
        if worker is None:
            return
        session = self._sessions.get(worker.session_id)
        if session is None:
            return
        if result is not None:
            with session.doc_lock:
                PdfService.add_text_layer(
                    session.doc, session.pdf_document, page_index, result,
                    pdf_settings=self._pdf_settings,
                )
        self.ocr_page_done.emit(session.file_path, page_index, result)
```

- [ ] **Step 3: Commit**

```bash
git add src/vibeocr/workers/pdf_ocr_worker.py src/vibeocr/managers/pdf_session_manager.py
git commit -m "feat: PdfSessionManager passes PdfGlobalSettings to add_text_layer"
```

---

### Task 5: OCRPreferences 新增 "pdf" 数据源

**Files:**
- Modify: `src/vibeocr/utils/ocr_preferences.py`

- [ ] **Step 1: Modify OCRPreferences to support PDF settings**

在 `src/vibeocr/utils/ocr_preferences.py` 中：

1. 将 `_CONFIG_VERSION` 改为 `3`
2. 在 `__init__` 中初始化 `"pdf"` 数据源和 `_pdf_settings`
3. 在 `_load` 中处理 version 3 的 pdf 字段
4. 在 `save` 中保存 pdf 字段
5. 新增 `get_pdf_settings` / `set_pdf_settings` 方法

```python
_CONFIG_VERSION = 3
```

在 `__init__` 中添加：
```python
        self._per_pipeline: dict[str, dict[str, OCROptions]] = {
            "main": {},
            "screenshot": {},
            "pdf": {},
        }
        self._pdf_settings: dict = {}  # PdfGlobalSettings raw dict
```

在 `_load` 的 version 2+ 分支中，添加加载 `"pdf"`：
```python
            for source in ("main", "screenshot", "pdf"):
                source_data = data.get(source, {})
                for pipeline_name, opts_dict in source_data.items():
                    self._per_pipeline.setdefault(source, {})[pipeline_name] = (
                        OCROptions.from_dict(opts_dict)
                    )
```

在 `_load` 末尾添加：
```python
        pdf_settings_data = data.get("pdf_settings")
        if pdf_settings_data and isinstance(pdf_settings_data, dict):
            self._pdf_settings = pdf_settings_data
```

在 `save` 中添加：
```python
        save_data = {
            "version": _CONFIG_VERSION,
            "last_main_pipeline": self._last_main_pipeline.value,
            "main": {
                k: v.to_dict() for k, v in self._per_pipeline.get("main", {}).items()
            },
            "screenshot": {
                k: v.to_dict()
                for k, v in self._per_pipeline.get("screenshot", {}).items()
            },
            "pdf": {
                k: v.to_dict()
                for k, v in self._per_pipeline.get("pdf", {}).items()
            },
            "pdf_settings": self._pdf_settings,
            "batch_options": self._batch_options.to_dict(),
        }
```

新增方法：
```python
    def get_pdf_settings(self) -> "PdfGlobalSettings":
        """获取 PDF 全局设置。"""
        from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

        return PdfGlobalSettings.from_dict(self._pdf_settings)

    def set_pdf_settings(self, settings: "PdfGlobalSettings") -> None:
        """保存 PDF 全局设置。"""
        self._pdf_settings = settings.to_dict()
        self.save()
```

- [ ] **Step 2: Commit**

```bash
git add src/vibeocr/utils/ocr_preferences.py
git commit -m "feat: OCRPreferences adds 'pdf' source and PdfGlobalSettings persistence"
```

---

### Task 6: PdfOptionsWidget 组件

**Files:**
- Create: `src/vibeocr/widgets/pdf_options_widget.py`

- [ ] **Step 1: Create PDF options widget**

```python
# src/vibeocr/widgets/pdf_options_widget.py
"""PDF OCR 选项组件

包含管道选择（锁定为文档类）、管道选项和 PDF 全局设置。
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vibeocr.core.pipelines import OCRPipeline
from vibeocr.models.ocr_options import OCROptions
from vibeocr.models.pdf_ocr_options import PdfGlobalSettings
from vibeocr.widgets.preprocess_options_widget import PreprocessOptionsWidget


class PdfOptionsWidget(QWidget):
    """PDF OCR 选项组件。

    组合了 PreprocessOptionsWidget（管道选项，锁定为文档类管道）
    和 PDF 全局设置（DPI、内存、字号等）。
    """

    settings_changed = Signal(object)  # PdfGlobalSettings

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._current_settings = PdfGlobalSettings()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 管道选项（复用 PreprocessOptionsWidget，初始化后锁定管道）
        self._pipeline_options = PreprocessOptionsWidget()
        self._pipeline_options.lock_to_document_parsing("PDF 处理")
        layout.addWidget(self._pipeline_options)

        # PDF 全局设置
        settings_group = QGroupBox("PDF 渲染设置")
        settings_layout = QVBoxLayout(settings_group)

        # DPI
        dpi_layout = QHBoxLayout()
        dpi_layout.addWidget(QLabel("渲染 DPI:"))
        self._dpi_spin = QSpinBox()
        self._dpi_spin.setRange(72, 600)
        self._dpi_spin.setValue(300)
        self._dpi_spin.setToolTip("PDF 页面渲染分辨率，越高越清晰但内存占用越大")
        dpi_layout.addWidget(self._dpi_spin)
        dpi_layout.addStretch()
        settings_layout.addLayout(dpi_layout)

        # 最大像素
        pixels_layout = QHBoxLayout()
        pixels_layout.addWidget(QLabel("单页像素上限:"))
        self._max_pixels_spin = QSpinBox()
        self._max_pixels_spin.setRange(1_000_000, 100_000_000)
        self._max_pixels_spin.setValue(16_000_000)
        self._max_pixels_spin.setSingleStep(1_000_000)
        self._max_pixels_spin.setToolTip("超过此限制时自动降低渲染 DPI")
        pixels_layout.addWidget(self._max_pixels_spin)
        pixels_layout.addStretch()
        settings_layout.addLayout(pixels_layout)

        # 字号比例
        font_layout = QHBoxLayout()
        font_layout.addWidget(QLabel("字号比例:"))
        self._font_ratio_spin = QDoubleSpinBox()
        self._font_ratio_spin.setRange(0.1, 1.0)
        self._font_ratio_spin.setSingleStep(0.05)
        self._font_ratio_spin.setValue(0.8)
        self._font_ratio_spin.setToolTip("字号 = 文字块高度 × 此比例")
        font_layout.addWidget(self._font_ratio_spin)
        font_layout.addStretch()
        settings_layout.addLayout(font_layout)

        # 重试次数
        retry_layout = QHBoxLayout()
        retry_layout.addWidget(QLabel("字号重试次数:"))
        self._retry_spin = QSpinBox()
        self._retry_spin.setRange(1, 20)
        self._retry_spin.setValue(5)
        self._retry_spin.setToolTip("文字溢出时缩小字号重试的最大次数")
        retry_layout.addWidget(self._retry_spin)
        retry_layout.addStretch()
        settings_layout.addLayout(retry_layout)

        # 缩放因子
        shrink_layout = QHBoxLayout()
        shrink_layout.addWidget(QLabel("缩放因子:"))
        self._shrink_spin = QDoubleSpinBox()
        self._shrink_spin.setRange(0.1, 1.0)
        self._shrink_spin.setSingleStep(0.05)
        self._shrink_spin.setValue(0.75)
        self._shrink_spin.setToolTip("每次重试字号乘以此因子")
        shrink_layout.addWidget(self._shrink_spin)
        shrink_layout.addStretch()
        settings_layout.addLayout(shrink_layout)

        # 文字层可见
        self._visible_cb = QCheckBox("文字层可见（调试用）")
        self._visible_cb.setToolTip("启用后写入可见文字，方便调试 bbox 位置")
        self._visible_cb.setChecked(False)
        settings_layout.addWidget(self._visible_cb)

        layout.addWidget(settings_group)
        layout.addStretch()

        # 连接信号
        self._dpi_spin.valueChanged.connect(self._on_settings_changed)
        self._max_pixels_spin.valueChanged.connect(self._on_settings_changed)
        self._font_ratio_spin.valueChanged.connect(self._on_settings_changed)
        self._retry_spin.valueChanged.connect(self._on_settings_changed)
        self._shrink_spin.valueChanged.connect(self._on_settings_changed)
        self._visible_cb.toggled.connect(self._on_settings_changed)

    def _on_settings_changed(self):
        settings = self.get_settings()
        self._current_settings = settings
        self.settings_changed.emit(settings)

    @property
    def pipeline_options(self) -> PreprocessOptionsWidget:
        """获取底层管道选项组件。"""
        return self._pipeline_options

    def get_settings(self) -> PdfGlobalSettings:
        return PdfGlobalSettings(
            render_dpi=self._dpi_spin.value(),
            max_pixels=self._max_pixels_spin.value(),
            font_size_ratio=self._font_ratio_spin.value(),
            text_layer_visible=self._visible_cb.isChecked(),
            font_size_retry_count=self._retry_spin.value(),
            font_size_shrink_factor=self._shrink_spin.value(),
        )

    def set_settings(self, settings: PdfGlobalSettings):
        """设置全局参数（不触发信号）。"""
        self._current_settings = settings
        widgets = [
            self._dpi_spin,
            self._max_pixels_spin,
            self._font_ratio_spin,
            self._retry_spin,
            self._shrink_spin,
            self._visible_cb,
        ]
        for w in widgets:
            w.blockSignals(True)

        self._dpi_spin.setValue(settings.render_dpi)
        self._max_pixels_spin.setValue(settings.max_pixels)
        self._font_ratio_spin.setValue(settings.font_size_ratio)
        self._retry_spin.setValue(settings.font_size_retry_count)
        self._shrink_spin.setValue(settings.font_size_shrink_factor)
        self._visible_cb.setChecked(settings.text_layer_visible)

        for w in widgets:
            w.blockSignals(False)
```

- [ ] **Step 2: Commit**

```bash
git add src/vibeocr/widgets/pdf_options_widget.py
git commit -m "feat: add PdfOptionsWidget with pipeline options and global settings"
```

---

### Task 7: Settings 页面集成 PDF 选项

**Files:**
- Modify: `src/vibeocr/views/settings_page_controller.py`

- [ ] **Step 1: Add _init_pdf_options to SettingsPageController**

在 `src/vibeocr/views/settings_page_controller.py` 的 `connect_signals` 方法末尾调用：

```python
        self._init_pdf_options(nav_list, stacked)
```

在 `_init_screenshot_options` 方法之后添加新方法：

```python
    def _init_pdf_options(
        self, nav_list: QListWidget | None, stacked: QStackedWidget | None
    ) -> None:
        """初始化 PDF 选项页面。"""
        if not nav_list or not stacked:
            return

        from vibeocr.utils.ocr_preferences import OCRPreferences
        from vibeocr.widgets.pdf_options_widget import PdfOptionsWidget

        nav_list.addItem("PDF 选项")

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(16, 16, 16, 16)
        page_layout.setSpacing(12)

        self._pdf_options = PdfOptionsWidget()
        page_layout.addWidget(self._pdf_options)

        spacer = QSpacerItem(
            20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        page_layout.addItem(spacer)
        stacked.addWidget(page)

        # 恢复保存的设置
        try:
            prefs = OCRPreferences.instance()
            # 管道选项
            default_pipeline = self._pdf_options.pipeline_options.get_current_pipeline()
            self._pdf_options.pipeline_options.set_options(
                prefs.get_pipeline_options("pdf", default_pipeline)
            )
            # 全局设置
            self._pdf_options.set_settings(prefs.get_pdf_settings())
        except RuntimeError:
            pass

        # 连接管道选项信号
        self._pdf_switching = False
        self._pdf_options.pipeline_options.pipeline_switching.connect(
            self._on_pdf_pipeline_switching
        )
        self._pdf_options.pipeline_options.pipeline_switched.connect(
            self._on_pdf_pipeline_switched
        )
        self._pdf_options.pipeline_options.options_changed.connect(
            self._on_pdf_option_changed
        )

        # 连接全局设置信号
        self._pdf_options.settings_changed.connect(self._on_pdf_settings_changed)

    def _on_pdf_pipeline_switching(self, old_pipeline, options) -> None:
        self._pdf_switching = True
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_pipeline_options(
                "pdf", old_pipeline, options
            )
        except RuntimeError:
            pass

    def _on_pdf_pipeline_switched(self, new_pipeline) -> None:
        try:
            from vibeocr.utils.ocr_preference import OCRPreferences

            loaded = OCRPreferences.instance().get_pipeline_options(
                "pdf", new_pipeline
            )
            self._pdf_options.pipeline_options.set_options(loaded)
        except RuntimeError:
            pass
        self._pdf_switching = False

    def _on_pdf_option_changed(self, options) -> None:
        if self._pdf_switching:
            return
        try:
            from vibeocr.utils.ocr_preference import OCRPreferences

            OCRPreferences.instance().set_pipeline_options(
                "pdf", options.pipeline, options
            )
        except RuntimeError:
            pass

    def _on_pdf_settings_changed(self, settings) -> None:
        try:
            from vibeocr.utils.ocr_preference import OCRPreferences

            OCRPreferences.instance().set_pdf_settings(settings)
        except RuntimeError:
            pass
```

**注意：** 上面的 `_on_pdf_pipeline_switched` 和 `_on_pdf_option_changed` 中用了 `ocr_preference`（无 s），需要修正为 `ocr_preferences`：

```python
    def _on_pdf_pipeline_switched(self, new_pipeline) -> None:
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            loaded = OCRPreferences.instance().get_pipeline_options(
                "pdf", new_pipeline
            )
            self._pdf_options.pipeline_options.set_options(loaded)
        except RuntimeError:
            pass
        self._pdf_switching = False

    def _on_pdf_option_changed(self, options) -> None:
        if self._pdf_switching:
            return
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_pipeline_options(
                "pdf", options.pipeline, options
            )
        except RuntimeError:
            pass

    def _on_pdf_settings_changed(self, settings) -> None:
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            OCRPreferences.instance().set_pdf_settings(settings)
        except RuntimeError:
            pass
```

- [ ] **Step 2: Commit**

```bash
git add src/vibeocr/views/settings_page_controller.py
git commit -m "feat: add PDF options page to settings"
```

---

### Task 8: PdfTab 集成 — 从偏好读取配置

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py`

- [ ] **Step 1: Modify PdfTab._on_add_text_layer to read preferences**

修改 `src/vibeocr/views/tabs/pdf_tab.py` 中的 `_on_add_text_layer` 方法：

```python
    def _on_add_text_layer(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return

        indices = self._get_selected_page_indices()
        if not indices:
            indices = list(range(session.pdf_document.page_count))

        if not self._session_mgr.is_ocr_ready:
            QMessageBox.warning(
                self,
                "OCR 服务未就绪",
                "OCR 服务尚未初始化，请等待服务启动完成。",
            )
            return

        # 从偏好读取 PDF 配置
        from vibeocr.utils.ocr_preferences import OCRPreferences

        try:
            prefs = OCRPreferences.instance()
            pdf_settings = prefs.get_pdf_settings()
            ocr_options = prefs.get_pipeline_options(
                "pdf", self._session_mgr.active_session.pdf_document.pages[0].page_index
                if session.pdf_document.pages
                else None
            )
        except RuntimeError:
            from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

            pdf_settings = PdfGlobalSettings()
            ocr_options = None

        reply = QMessageBox.question(
            self,
            "添加文字层",
            f"将对 {len(indices)} 页执行 OCR 并添加隐形文字层。\n"
            "建议先另存为备份。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._progress_bar.setRange(0, len(indices))
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._btn_cancel.setVisible(True)
        self._set_file_buttons_enabled(False)
        self._btn_open.setEnabled(False)
        self._btn_add_file.setEnabled(False)

        # 读取当前 PDF 管道的选项
        try:
            prefs = OCRPreferences.instance()
            # 获取最后一次使用的 pdf 管道
            pdf_per_pipeline = prefs._per_pipeline.get("pdf", {})
            if pdf_per_pipeline:
                last_pipeline_name = list(pdf_per_pipeline.keys())[-1]
                from vibeocr.core.pipelines import OCRPipeline

                last_pipeline = OCRPipeline(last_pipeline_name)
                ocr_options = prefs.get_pipeline_options("pdf", last_pipeline)
            else:
                ocr_options = None
        except Exception:
            ocr_options = None

        self._session_mgr.start_ocr(indices, ocr_options=ocr_options, pdf_settings=pdf_settings)
```

- [ ] **Step 2: Commit**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat: PdfTab reads PDF OCR options from preferences"
```

---

### Task 9: 预览窗口坐标修复 + Tooltip

**Files:**
- Modify: `src/vibeocr/views/pdf_preview_window.py`
- Modify: `src/vibeocr/views/tabs/pdf_tab.py` (预览调用)

- [ ] **Step 1: Fix PreviewCanvas coordinate conversion**

修改 `src/vibeocr/views/pdf_preview_window.py` 中的 `_PreviewCanvas`：

```python
class _PreviewCanvas(QWidget):
    """可缩放/平移的画布。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._scale = 1.0
        self._highlight_layers: list = []
        self._render_dpi: int = 150
        self._page_rect: fitz.Rect | None = None
        self._source: str = "pdf"  # "pdf" or "normalized"
        self.setMouseTracking(True)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._scale = 1.0
        self._update_size()
        self.update()

    def set_highlight_layers(
        self,
        layers: list,
        render_dpi: int = 150,
        page_rect: fitz.Rect | None = None,
        source: str = "pdf",
    ) -> None:
        self._highlight_layers = layers
        self._render_dpi = render_dpi
        self._page_rect = page_rect
        self._source = source
        self.update()

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

        if not self._highlight_layers or self._page_rect is None:
            painter.end()
            return

        from PySide6.QtGui import QColor, QPen

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

        from vibeocr.services.pdf_service import PdfService

        for layer in self._highlight_layers:
            bbox = layer.bbox
            color_idx = layer.color_id % len(colors)
            r, g, b, a = colors[color_idx]

            # 使用 PdfService.bbox_to_pixel 转换坐标
            pixel_bbox = PdfService.bbox_to_pixel(
                bbox, self._page_rect, self._render_dpi, source=self._source
            )
            x0, y0, x1, y1 = pixel_bbox

            painter.setBrush(QColor(r, g, b, a))
            painter.setPen(QPen(QColor(r, g, b, 180), 1))
            painter.drawRect(
                int(x0),
                int(y0),
                int(x1 - x0),
                int(y1 - y0),
            )
        painter.end()

    def mouseMoveEvent(self, event) -> None:
        """鼠标悬停时显示文字块内容 tooltip。"""
        if not self._highlight_layers or self._page_rect is None or self._pixmap is None:
            self.setToolTip("")
            return

        from vibeocr.services.pdf_service import PdfService

        mx = event.position().x() / self._scale
        my = event.position().y() / self._scale

        for layer in self._highlight_layers:
            pixel_bbox = PdfService.bbox_to_pixel(
                layer.bbox, self._page_rect, self._render_dpi, source=self._source
            )
            x0, y0, x1, y1 = pixel_bbox
            if x0 <= mx <= x1 and y0 <= my <= y1:
                self.setToolTip(f"{layer.text_preview}")
                return
        self.setToolTip("")

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
```

- [ ] **Step 2: Fix PdfTab._on_preview_text_layer to pass page_rect and render_dpi**

修改 `src/vibeocr/views/tabs/pdf_tab.py` 中的 `_on_preview_text_layer`：

```python
    def _on_preview_text_layer(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        indices = self._get_selected_page_indices()
        if not indices:
            QMessageBox.information(self, "预览文字层", "请先选择页面。")
            return
        page_idx = indices[0]
        page_info = session.pdf_document.get_page(page_idx)
        if page_info is None or not page_info.text_layers:
            QMessageBox.information(self, "预览文字层", "选中页面无文字层。")
            return

        render_dpi = 150
        with session.doc_lock:
            pixmap = PdfService.render_page(session.doc, page_idx, dpi=render_dpi)
            page_rect = session.doc[page_idx].rect
        if self._preview_window is None:
            self._preview_window = PdfPreviewWindow()
        assert self._preview_window is not None
        self._preview_window.setWindowTitle(
            f"文字层预览 — 第{page_idx + 1}页 ({len(page_info.text_layers)}个文字块)"
        )
        self._preview_window._canvas.set_pixmap(pixmap)
        self._preview_window._canvas.set_highlight_layers(
            page_info.text_layers,
            render_dpi=render_dpi,
            page_rect=page_rect,
            source="pdf",
        )
        self._preview_window.show()
        self._preview_window.raise_()
```

- [ ] **Step 3: Commit**

```bash
git add src/vibeocr/views/pdf_preview_window.py src/vibeocr/views/tabs/pdf_tab.py
git commit -m "fix: correct text layer preview coordinates and add hover tooltips"
```

---

### Task 10: 端到端验证

**Files:**
- 无新增文件，手动验证

- [ ] **Step 1: Run all existing tests**

Run: `python -m pytest tests/services/test_pdf_service.py tests/services/test_pdf_service_bbox.py tests/models/test_pdf_ocr_options.py -v`
Expected: all PASS

- [ ] **Step 2: Manual smoke test checklist**

1. 启动应用 → 设置页面 → 确认出现"PDF 选项"导航项
2. PDF 选项页中确认管道锁定为文档类（MineRU / PaddleOCR-VL）
3. 修改 DPI、字号比例等参数 → 关闭应用 → 重新打开 → 确认设置已恢复
4. 打开一个扫描 PDF → 选择页面 → 点击"添加文字层" → 确认 OCR 执行
5. 添加完成后点击"预览文字层" → 确认高亮矩形与页面内容对齐
6. 鼠标悬停高亮区域 → 确认显示文字内容 tooltip

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: address issues found during end-to-end verification"
```
