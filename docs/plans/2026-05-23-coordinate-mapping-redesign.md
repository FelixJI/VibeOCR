# 坐标映射层重构实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 消除 175% DPI 下 bbox 偏移，建立确定性坐标映射层，修复多屏 DPR 不匹配。

**Architecture:** PreviewWidget 的 bbox 渲染不再从已设置的 scaled pixmap 推断尺寸，而是直接从 `_original_pixmap` 和 label 逻辑尺寸计算缩放比例。ScreenCoordinateMapper 新增 `screenshot_dpr` 属性统一多屏截图的 DPR 转换。overlay 更新延迟一帧确保布局稳定。

**Tech Stack:** PySide6 (QTimer, QRect, QRectF, QPointF, QPixmap), Python dataclass

---

### Task 1: ScreenCoordinateMapper 新增 screenshot_dpr

**Files:**
- Modify: `src/vibeocr/widgets/screen_coordinate_mapper.py:18-69`

**Step 1: 修改构造函数，新增 screenshot_dpr 参数**

在 `__init__` 中增加 `screenshot_dpr` 参数，默认取 `max_dpr`：

```python
class ScreenCoordinateMapper:
    def __init__(self, screens: list[ScreenInfo], screenshot_dpr: float | None = None) -> None:
        self._screens = screens
        self._screenshot_dpr = screenshot_dpr if screenshot_dpr is not None else self.max_dpr
        if screens:
            vg = screens[0].geometry
            for s in screens[1:]:
                vg = vg.united(s.geometry)
            self._virtual_geometry = vg
        else:
            self._virtual_geometry = QRect()
```

**Step 2: 新增 screenshot_dpr 属性和 logical_to_screenshot_physical 方法**

在 `logical_rect_to_physical` 方法之后添加：

```python
    @property
    def screenshot_dpr(self) -> float:
        return self._screenshot_dpr

    def logical_to_screenshot_physical(self, rect: QRect) -> QRect:
        """逻辑坐标 → 合并截图的物理像素坐标（统一使用 screenshot_dpr）"""
        dpr = self._screenshot_dpr
        return QRect(
            round(rect.x() * dpr),
            round(rect.y() * dpr),
            round(rect.width() * dpr),
            round(rect.height() * dpr),
        )
```

**Step 3: 提交**

```bash
git add src/vibeocr/widgets/screen_coordinate_mapper.py
git commit -m "feat(mapper): 新增 screenshot_dpr 和 logical_to_screenshot_physical"
```

---

### Task 2: ScreenCaptureOverlay 改用 logical_to_screenshot_physical

**Files:**
- Modify: `src/vibeocr/widgets/screen_capture_overlay.py:118-128`

**Step 1: 修改 _logical_rect_to_physical 方法**

将 `self._mapper.logical_rect_to_physical(rect)` 替换为 `self._mapper.logical_to_screenshot_physical(rect)`：

```python
    def _logical_rect_to_physical(self, rect: QRect) -> QRect:
        """将逻辑坐标矩形转换为物理坐标矩形，优先使用 mapper，否则回退标量 DPR"""
        if self._mapper is not None:
            return self._mapper.logical_to_screenshot_physical(rect)
        dpr = 1.0
        return QRect(
            int(rect.x() * dpr),
            int(rect.y() * dpr),
            int(rect.width() * dpr),
            int(rect.height() * dpr),
        )
```

**Step 2: 提交**

```bash
git add src/vibeocr/widgets/screen_capture_overlay.py
git commit -m "fix(overlay): 截图坐标转换改用 screenshot_dpr"
```

---

### Task 3: InlineEditCanvas update_crop_region DPR 修复

**Files:**
- Modify: `src/vibeocr/widgets/inline_edit_canvas.py:194-202`

**Step 1: 修改 update_crop_region 中的 DPR 转换**

将 `mapper.dpr_at(...)` 替换为 `mapper.screenshot_dpr`：

```python
        # 为 Mosaic/Blur 保留裁剪后的背景（场景坐标系，供像素采样）
        dpr = mapper.screenshot_dpr
        physical_rect = QRect(
            round(new_selection.x() * dpr),
            round(new_selection.y() * dpr),
            round(new_selection.width() * dpr),
            round(new_selection.height() * dpr),
        )
        self._background_pixmap = screen_pixmap.copy(physical_rect)
```

**Step 2: 提交**

```bash
git add src/vibeocr/widgets/inline_edit_canvas.py
git commit -m "fix(canvas): update_crop_region 改用 screenshot_dpr"
```

---

### Task 4: PreviewWidget 新增 _compute_scale_factor

**Files:**
- Modify: `src/vibeocr/widgets/preview_widget.py:649-728`

**Step 1: 新增 QTimer import**

修改第 5 行的 import：

```python
from PySide6.QtCore import Qt, QRectF, QTimer, Signal
```

**Step 2: 用 _compute_scale_factor 替换 _compute_display_rect**

删除 `_compute_display_rect` 方法（649-667 行），替换为：

```python
    def _compute_scale_factor(self) -> tuple[float, float, float, float]:
        """基于 _original_pixmap 和 label 尺寸计算显示区域和偏移

        不依赖已设置的 scaled pixmap，消除时序问题。

        Returns: (disp_w, disp_h, offset_x, offset_y)
        """
        if not self._original_pixmap or self._original_pixmap.isNull():
            return 0, 0, 0, 0
        img_w = self._original_pixmap.width()
        img_h = self._original_pixmap.height()
        label_w = self._image_label.width()
        label_h = self._image_label.height()
        if label_w <= 0 or label_h <= 0 or img_w <= 0 or img_h <= 0:
            return 0, 0, 0, 0
        max_w = label_w - 20
        max_h = label_h - 20
        if max_w <= 0 or max_h <= 0:
            return 0, 0, 0, 0
        scale = min(max_w / img_w, max_h / img_h)
        disp_w = img_w * scale
        disp_h = img_h * scale
        offset_x = (label_w - disp_w) / 2
        offset_y = (label_h - disp_h) / 2
        return disp_w, disp_h, offset_x, offset_y
```

**Step 3: 更新 _update_block_overlay 调用**

在 `_update_block_overlay` 方法（692行起），将：
```python
        disp_w, disp_h, offset_x, offset_y = self._compute_display_rect()
```
替换为：
```python
        disp_w, disp_h, offset_x, offset_y = self._compute_scale_factor()
```

**Step 4: 更新 _update_type_overlay 调用**

在 `_update_type_overlay` 方法（548行），将：
```python
        disp_w, disp_h, offset_x, offset_y = self._compute_display_rect()
```
替换为：
```python
        disp_w, disp_h, offset_x, offset_y = self._compute_scale_factor()
```

**Step 5: 提交**

```bash
git add src/vibeocr/widgets/preview_widget.py
git commit -m "refactor(preview): 用 _compute_scale_factor 替代 _compute_display_rect"
```

---

### Task 5: PreviewWidget 延迟 overlay 更新

**Files:**
- Modify: `src/vibeocr/widgets/preview_widget.py`

**Step 1: 新增 _update_overlay_deferred 方法**

在 `_update_display` 方法之后添加：

```python
    def _update_overlay_deferred(self) -> None:
        """延迟一帧更新 overlay，确保布局已完成"""
        if self._content_list:
            self._update_type_overlay()
        elif self._text_blocks:
            self._update_block_overlay()
        self._overlay.setGeometry(self._scroll_area.viewport().rect())
```

**Step 2: 修改 _update_display，移除内联 overlay 更新**

将 `_update_display` 中末尾的 overlay 调用替换为延迟调用：

```python
    def _update_display(self) -> None:
        if self._pixmap:
            viewport = self._scroll_area.viewport()
            dpr = self.devicePixelRatio()
            max_w = max(viewport.width() - 20, 200)
            max_h = max(viewport.height() - 20, 200)

            scaled = self._pixmap.scaled(
                int(max_w * dpr),
                int(max_h * dpr),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            self._image_label.setPixmap(scaled)
            self._image_label.setStyleSheet(
                "QLabel { background-color: #fff; border: 1px solid #ddd; }"
            )
            QTimer.singleShot(0, self._update_overlay_deferred)
```

**Step 3: 修改 resizeEvent，使用延迟更新**

```python
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._original_pixmap and not self._original_pixmap.isNull():
            self._update_display()
            self._reapply_highlight()
        QTimer.singleShot(0, lambda: self._overlay.setGeometry(self._scroll_area.viewport().rect()))
```

**Step 4: 提交**

```bash
git add src/vibeocr/widgets/preview_widget.py
git commit -m "fix(preview): 延迟 overlay 更新，确保布局稳定后再计算坐标"
```

---

### Task 6: 手动验证

**Step 1: 启动应用**

```bash
cd src && python -m vibeocr
```

**Step 2: 验证截图路径**

1. 点击截图，选择一个包含文字的区域
2. 执行 OCR 识别
3. 检查 bbox 覆盖层是否与文字位置对齐

**Step 3: 验证粘贴路径**

1. 复制一张图片到剪贴板
2. 粘贴到 PreviewWidget
3. 执行 OCR 识别
4. 检查 bbox 覆盖层是否与文字位置对齐

**Step 4: 验证 resize 稳定性**

1. 在有 bbox 显示的状态下调整窗口大小
2. 确认 bbox 保持对齐，不出现跳动或偏移

**Step 5: 最终提交（如有调整）**

```bash
git add -A
git commit -m "fix: 坐标映射层重构完成，修复高 DPI 下 bbox 偏移"
```
