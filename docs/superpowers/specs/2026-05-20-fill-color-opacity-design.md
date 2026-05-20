# 截图工具填充颜色与透明度控制

## 背景

截图编辑器的矩形和椭圆工具支持填充效果，但填充颜色硬编码为描边色 + alpha 50（约 20% 不透明度），用户无法控制填充颜色和透明度。

## 设计方案

### UI 布局

**图形属性页（`_SHAPE_PAGE`）** 布局变更为：

```
颜色 [色块] 线宽 [数值] [✓填充] [填充色块] [🔗] [透明度滑块]
```

- 填充色按钮、链接按钮、透明度滑块仅在"填充"勾选后显示
- 链接按钮默认激活（同步模式），点击关闭（独立模式）
- 链接激活时，填充色按钮灰显，显示当前同步的颜色但不可点击
- 透明度滑块范围 0~100，默认值 20

**通用属性页（`_COMMON_PAGE`，选中已有标注时）** 同样布局。选中 ArrowAnnotation 时隐藏填充相关控件。

### 数据模型

#### RectAnnotation / EllipseAnnotation

- 新增 `_fill_opacity: int`（0~100），默认 20
- `set_fill_enabled(enabled, color, opacity=None)` 扩展，向后兼容
- 新增 `set_fill_color(color: QColor)`
- 新增 `set_fill_opacity(opacity: int)`，内部计算 `QColor(r, g, b, int(opacity * 255 / 100))`

#### InlineEditCanvas

- `_fill_color` 存储 RGB 值（不含 alpha），默认跟随 `_pen_color`
- 新增 `_fill_opacity: int`，默认 20
- 新增 `_fill_linked: bool`，默认 True
- `set_pen_color` 仅在 `_fill_linked` 为 True 时同步填充色
- 新增 `set_fill_color` / `set_fill_opacity` / `set_fill_linked`

#### ToolPropertiesBar

- 新增信号：`fill_color_changed(QColor)`、`fill_opacity_changed(int)`、`fill_linked_changed(bool)`
- 填充勾选时显示/隐藏填充色按钮、链接按钮、透明度滑块

#### ScreenCaptureOverlay

- `_on_fill_enabled_changed` 移除硬编码 alpha 50，改为读取 canvas 属性
- 新增 `fill_color_changed`、`fill_opacity_changed`、`fill_linked_changed` 信号处理

### 交互流程

**创建新标注：**

1. 选择矩形/椭圆工具 → 图形属性页，填充控件隐藏
2. 勾选"填充" → 展开填充色按钮、链接按钮（激活）、透明度滑块（20%）
3. 链接激活时改描边色 → 填充色自动跟随
4. 关闭链接 → 填充色按钮可点击，独立选色
5. 绘制完成 → 使用 `_fill_color` + `_fill_opacity` 创建标注

**选中已有标注：**

1. 点击矩形/椭圆 → 通用属性页回显属性
2. 判断链接：`fill_color == pen_color` 则链接激活，否则关闭
3. 修改透明度/填充色 → 实时更新选中项

### 边界情况

- 切换工具时填充状态不重置
- 关闭链接后改描边色，填充色不变
- 重新打开链接时，填充色立即同步为当前描边色

### 涉及文件

- `src/vibeocr/widgets/editor/tool_properties_bar.py` — UI 控件和信号
- `src/vibeocr/widgets/editor/annotation_items.py` — RectAnnotation、EllipseAnnotation 扩展
- `src/vibeocr/widgets/inline_edit_canvas.py` — 画布属性管理
- `src/vibeocr/widgets/screen_capture_overlay.py` — 信号连接
