# 坐标映射层重构设计

日期: 2026-05-23

## 背景

在高 DPI（175%）环境下，截图/粘贴后的 OCR bbox 叠加层与文字位置存在偏移。
根本原因：PreviewWidget 的 bbox 渲染依赖推断当前 pixmap 显示尺寸，存在时序风险；
ScreenCoordinateMapper 在多屏不同 DPR 时使用错误的 DPR 进行坐标转换。

## 设计目标

1. 消除 bbox 渲染的时序依赖，用确定性计算替代推断
2. 修复多屏 DPR 不匹配问题
3. 保持 PaddleOCR 和 MinerU 两条管道兼容（均产出 [0, 1000] 归一化 bbox）

## 改动范围

### 1. PreviewWidget 坐标映射重构

**文件**: `src/vibeocr/widgets/preview_widget.py`

**删除** `_compute_display_rect()` 方法（从 label pixmap 推断显示尺寸）。

**新增** `_compute_scale_factor()`:
- 直接从 `_original_pixmap` 物理尺寸和 `_image_label` 当前尺寸计算
- 返回 `(disp_w, disp_h, offset_x, offset_y)`
- 不依赖已设置的 scaled pixmap，消除时序问题

逻辑:
```
img_w, img_h = _original_pixmap 尺寸
label_w, label_h = _image_label 尺寸
max_w, max_h = label 尺寸 - 20px 边距
scale = min(max_w / img_w, max_h / img_h)  # KeepAspectRatio
disp_w = img_w * scale
disp_h = img_h * scale
offset_x = (label_w - disp_w) / 2
offset_y = (label_h - disp_h) / 2
```

**修改** `_update_block_overlay()` 和 `_update_type_overlay()`:
- 调用 `_compute_scale_factor()` 替代 `_compute_display_rect()`

**修改** `_update_display()`:
- 不再内联调用 `_update_block_overlay()`/`_update_type_overlay()`
- 改用 `QTimer.singleShot(0, self._update_overlay_deferred)` 延迟一帧

**修改** `resizeEvent()`:
- 同样使用延迟 overlay 更新

### 2. ScreenCoordinateMapper 多屏 DPR 修复

**文件**: `src/vibeocr/widgets/screen_coordinate_mapper.py`

**新增** `screenshot_dpr` 参数和 `logical_to_screenshot_physical()` 方法:
- `screenshot_dpr` 默认为 `max_dpr`（合并截图的实际 DPR）
- 新方法用于截图场景的逻辑→物理转换，使用统一的 `screenshot_dpr`
- 原有 `logical_rect_to_physical()` 保留，供 window_detector 等逐屏场景使用

### 3. ScreenCaptureOverlay 坐标转换适配

**文件**: `src/vibeocr/widgets/screen_capture_overlay.py`

**修改** `_logical_rect_to_physical()`:
- 调用 `mapper.logical_to_screenshot_physical()` 替代 `mapper.logical_rect_to_physical()`

### 4. InlineEditCanvas DPR 转换适配

**文件**: `src/vibeocr/widgets/inline_edit_canvas.py`

**修改** `update_crop_region()`:
- DPR 转换改用 `mapper.screenshot_dpr` 属性，与合并截图的 DPR 一致

## 不涉及的文件

- OCR 服务层（`ocr_service.py`, `mineru_service.py`）— bbox 归一化逻辑不变
- `ocr_result.py` — `normalize_bbox()` 不变
- `base_tab.py` — `_build_content_list()` 不变
- `clipboard_controller.py` — 不涉及坐标转换

## 验证策略

1. 175% 单屏截图 → OCR → 检查 bbox 对齐
2. 175% 粘贴图片 → OCR → 检查 bbox 对齐
3. 100% 缩放回归测试
4. 窗口 resize 后 bbox 是否保持对齐
5. 多屏不同 DPR（如有条件）
