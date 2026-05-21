# 填充颜色与透明度控制 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为截图编辑器的矩形和椭圆工具添加独立的填充颜色选择和透明度滑块控制，替代当前硬编码的描边色+固定透明度。

**Architecture:** 从数据层向上构建——先扩展标注项的数据模型（分离 fill_color RGB 和 fill_opacity），再扩展画布属性管理，然后添加 UI 控件，最后在覆盖层连接信号。

**Tech Stack:** PySide6 (QColor, QBrush, QSlider, QToolButton, QColorDialog), pytest + pytest-qt

---

### 涉及文件

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/vibeocr/widgets/editor/annotation_items.py` | 修改 | RectAnnotation、EllipseAnnotation 添加 fill_color/fill_opacity |
| `src/vibeocr/widgets/inline_edit_canvas.py` | 修改 | 画布属性管理：fill_linked、fill_opacity |
| `src/vibeocr/widgets/editor/tool_properties_bar.py` | 修改 | UI 控件：填充色按钮、链接按钮、透明度滑块、新信号 |
| `src/vibeocr/widgets/screen_capture_overlay.py` | 修改 | 信号连接和事件处理 |
| `tests/test_annotation_items.py` | 修改 | 新增 fill_color/fill_opacity 测试 |
| `tests/test_inline_edit_canvas.py` | 修改 | 新增 canvas fill 属性测试 |

---

### Task 1: RectAnnotation / EllipseAnnotation 数据模型扩展

**Files:**
- Modify: `src/vibeocr/widgets/editor/annotation_items.py:69-157`
- Test: `tests/test_annotation_items.py`

- [ ] **Step 1: 编写失败测试**

在 `tests/test_annotation_items.py` 中 `TestRectAnnotationSetters` 和 `TestEllipseAnnotationSetters` 末尾追加：

```python
# === 在 TestRectAnnotationSetters 末尾追加 ===

def test_fill_opacity_default(self, qapp):
    item = RectAnnotation(QRectF(0, 0, 100, 80), fill_enabled=True, fill_color=QColor(0, 0, 255, 50))
    assert item._fill_opacity == 20  # 50/255 ≈ 20%

def test_set_fill_color(self, qapp):
    item = RectAnnotation(QRectF(0, 0, 100, 80), fill_enabled=True, fill_color=QColor(255, 0, 0, 50))
    item.set_fill_color(QColor(0, 128, 255))
    brush_color = item.brush().color()
    assert brush_color.red() == 0
    assert brush_color.green() == 128
    assert brush_color.blue() == 255
    # 透明度不变
    assert brush_color.alpha() == item._computed_fill_color().alpha()

def test_set_fill_opacity(self, qapp):
    item = RectAnnotation(QRectF(0, 0, 100, 80), fill_enabled=True, fill_color=QColor(255, 0, 0, 50))
    item.set_fill_opacity(80)
    assert item._fill_opacity == 80
    brush_color = item.brush().color()
    assert brush_color.alpha() == int(80 * 255 / 100)

def test_fill_opacity_constructor(self, qapp):
    item = RectAnnotation(QRectF(0, 0, 100, 80), fill_enabled=True,
                          fill_color=QColor(0, 128, 0), fill_opacity=60)
    assert item._fill_opacity == 60
    assert item.brush().color().alpha() == int(60 * 255 / 100)

def test_fill_disabled_preserves_state(self, qapp):
    item = RectAnnotation(QRectF(0, 0, 100, 80), fill_enabled=True,
                          fill_color=QColor(0, 128, 0), fill_opacity=60)
    item.set_fill_enabled(False)
    assert item._fill_opacity == 60
    assert item._fill_color == QColor(0, 128, 0)
```

```python
# === 在 TestEllipseAnnotationSetters 末尾追加 ===

def test_set_fill_color(self, qapp):
    item = EllipseAnnotation(QRectF(0, 0, 100, 80), fill_enabled=True, fill_color=QColor(255, 0, 0, 50))
    item.set_fill_color(QColor(0, 128, 255))
    brush_color = item.brush().color()
    assert brush_color.red() == 0
    assert brush_color.green() == 128
    assert brush_color.blue() == 255

def test_set_fill_opacity(self, qapp):
    item = EllipseAnnotation(QRectF(0, 0, 100, 80), fill_enabled=True, fill_color=QColor(255, 0, 0, 50))
    item.set_fill_opacity(80)
    assert item._fill_opacity == 80
    brush_color = item.brush().color()
    assert brush_color.alpha() == int(80 * 255 / 100)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_annotation_items.py -v -k "fill_opacity or fill_color"`
Expected: FAIL (AttributeError: `set_fill_color` / `set_fill_opacity` / `fill_opacity` 不存在)

- [ ] **Step 3: 实现 RectAnnotation 数据模型**

修改 `src/vibeocr/widgets/editor/annotation_items.py`。

替换 `RectAnnotation.__init__`（第 69-95 行）为：

```python
class RectAnnotation(QGraphicsRectItem):
    """矩形标注"""

    def __init__(
        self,
        rect: QRectF,
        pen_color: QColor = QColor(255, 0, 0),
        pen_width: int = 2,
        fill_enabled: bool = False,
        fill_color: QColor | None = None,
        fill_opacity: int | None = None,
    ):
        super().__init__(rect)
        self._pen_color = pen_color
        self._pen_width = pen_width
        self._fill_enabled = fill_enabled
        self._fill_color = QColor(fill_color.red(), fill_color.green(), fill_color.blue()) if fill_color else QColor(pen_color.red(), pen_color.green(), pen_color.blue())
        self._fill_opacity = fill_opacity if fill_opacity is not None else (
            round(fill_color.alpha() * 100 / 255) if fill_color else 20
        )
        self.setPen(QPen(pen_color, pen_width))
        if fill_enabled:
            self.setBrush(QBrush(self._computed_fill_color()))
        else:
            self.setBrush(Qt.BrushStyle.NoBrush)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(10)

    def _computed_fill_color(self) -> QColor:
        return QColor(
            self._fill_color.red(),
            self._fill_color.green(),
            self._fill_color.blue(),
            int(self._fill_opacity * 255 / 100),
        )

    def set_pen_color(self, color: QColor) -> None:
        self._pen_color = color
        self.setPen(QPen(color, self._pen_width))

    def set_pen_width(self, width: int) -> None:
        self._pen_width = width
        self.setPen(QPen(self._pen_color, width))

    def set_fill_enabled(self, enabled: bool, color: QColor | None = None, opacity: int | None = None) -> None:
        self._fill_enabled = enabled
        if color:
            self._fill_color = QColor(color.red(), color.green(), color.blue())
        if opacity is not None:
            self._fill_opacity = opacity
        if enabled:
            self.setBrush(QBrush(self._computed_fill_color()))
        else:
            self.setBrush(Qt.BrushStyle.NoBrush)

    def set_fill_color(self, color: QColor) -> None:
        self._fill_color = QColor(color.red(), color.green(), color.blue())
        if self._fill_enabled:
            self.setBrush(QBrush(self._computed_fill_color()))

    def set_fill_opacity(self, opacity: int) -> None:
        self._fill_opacity = opacity
        if self._fill_enabled:
            self.setBrush(QBrush(self._computed_fill_color()))
```

- [ ] **Step 4: 实现 EllipseAnnotation 数据模型**

替换 `EllipseAnnotation`（第 114-157 行）为：

```python
class EllipseAnnotation(QGraphicsEllipseItem):
    """椭圆标注"""

    def __init__(
        self,
        rect: QRectF,
        pen_color: QColor = QColor(255, 0, 0),
        pen_width: int = 2,
        fill_enabled: bool = False,
        fill_color: QColor | None = None,
        fill_opacity: int | None = None,
    ):
        super().__init__(rect)
        self._pen_color = pen_color
        self._pen_width = pen_width
        self._fill_enabled = fill_enabled
        self._fill_color = QColor(fill_color.red(), fill_color.green(), fill_color.blue()) if fill_color else QColor(pen_color.red(), pen_color.green(), pen_color.blue())
        self._fill_opacity = fill_opacity if fill_opacity is not None else (
            round(fill_color.alpha() * 100 / 255) if fill_color else 20
        )
        self.setPen(QPen(pen_color, pen_width))
        if fill_enabled:
            self.setBrush(QBrush(self._computed_fill_color()))
        else:
            self.setBrush(Qt.BrushStyle.NoBrush)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(10)

    def _computed_fill_color(self) -> QColor:
        return QColor(
            self._fill_color.red(),
            self._fill_color.green(),
            self._fill_color.blue(),
            int(self._fill_opacity * 255 / 100),
        )

    def set_pen_color(self, color: QColor) -> None:
        self._pen_color = color
        self.setPen(QPen(color, self._pen_width))

    def set_pen_width(self, width: int) -> None:
        self._pen_width = width
        self.setPen(QPen(self._pen_color, width))

    def set_fill_enabled(self, enabled: bool, color: QColor | None = None, opacity: int | None = None) -> None:
        self._fill_enabled = enabled
        if color:
            self._fill_color = QColor(color.red(), color.green(), color.blue())
        if opacity is not None:
            self._fill_opacity = opacity
        if enabled:
            self.setBrush(QBrush(self._computed_fill_color()))
        else:
            self.setBrush(Qt.BrushStyle.NoBrush)

    def set_fill_color(self, color: QColor) -> None:
        self._fill_color = QColor(color.red(), color.green(), color.blue())
        if self._fill_enabled:
            self.setBrush(QBrush(self._computed_fill_color()))

    def set_fill_opacity(self, opacity: int) -> None:
        self._fill_opacity = opacity
        if self._fill_enabled:
            self.setBrush(QBrush(self._computed_fill_color()))
```

- [ ] **Step 5: 运行全部 annotation 测试**

Run: `python -m pytest tests/test_annotation_items.py -v`
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/widgets/editor/annotation_items.py tests/test_annotation_items.py
git commit -m "feat(annotation): 扩展 RectAnnotation/EllipseAnnotation 支持独立填充色和透明度"
```

---

### Task 2: InlineEditCanvas 填充属性管理

**Files:**
- Modify: `src/vibeocr/widgets/inline_edit_canvas.py:84-88,203-209,406-422`
- Test: `tests/test_inline_edit_canvas.py`

- [ ] **Step 1: 编写失败测试**

在 `tests/test_inline_edit_canvas.py` 末尾追加：

```python
class TestFillProperties:
    def test_default_fill_linked(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas._fill_linked is True

    def test_default_fill_opacity(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas._fill_opacity == 20

    def test_default_fill_color_follows_pen(self, qapp):
        canvas = InlineEditCanvas()
        assert canvas._fill_color.red() == canvas._pen_color.red()
        assert canvas._fill_color.green() == canvas._pen_color.green()
        assert canvas._fill_color.blue() == canvas._pen_color.blue()

    def test_set_pen_color_syncs_fill_when_linked(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_pen_color(QColor(0, 255, 0))
        assert canvas._fill_color.red() == 0
        assert canvas._fill_color.green() == 255
        assert canvas._fill_color.blue() == 0

    def test_set_pen_color_no_sync_when_unlinked(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_fill_linked(False)
        canvas.set_fill_color(QColor(0, 0, 255))
        canvas.set_pen_color(QColor(0, 255, 0))
        assert canvas._fill_color.red() == 0
        assert canvas._fill_color.green() == 0
        assert canvas._fill_color.blue() == 255

    def test_set_fill_linked_syncs_to_pen_color(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_fill_linked(False)
        canvas.set_fill_color(QColor(0, 0, 255))
        canvas.set_pen_color(QColor(0, 255, 0))
        canvas.set_fill_linked(True)
        assert canvas._fill_color.red() == 0
        assert canvas._fill_color.green() == 255
        assert canvas._fill_color.blue() == 0

    def test_set_fill_opacity(self, qapp):
        canvas = InlineEditCanvas()
        canvas.set_fill_opacity(80)
        assert canvas._fill_opacity == 80
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_inline_edit_canvas.py::TestFillProperties -v`
Expected: FAIL (AttributeError)

- [ ] **Step 3: 修改 InlineEditCanvas**

在 `src/vibeocr/widgets/inline_edit_canvas.py` 中：

**3a.** 修改 `__init__` 中的属性初始化（约第 84-88 行），替换：

```python
        self._pen_color: QColor = QColor(255, 0, 0)
        self._pen_width: int = 2
        self._fill_enabled: bool = False
        self._fill_color: QColor = QColor(255, 0, 0, 50)
```

为：

```python
        self._pen_color: QColor = QColor(255, 0, 0)
        self._pen_width: int = 2
        self._fill_enabled: bool = False
        self._fill_color: QColor = QColor(255, 0, 0)
        self._fill_opacity: int = 20
        self._fill_linked: bool = True
```

**3b.** 修改 `set_pen_color`（约第 203-205 行），替换：

```python
    def set_pen_color(self, color: QColor) -> None:
        self._pen_color = color
        self._fill_color = QColor(color.red(), color.green(), color.blue(), 50)
```

为：

```python
    def set_pen_color(self, color: QColor) -> None:
        self._pen_color = color
        if self._fill_linked:
            self._fill_color = QColor(color.red(), color.green(), color.blue())
```

**3c.** 在 `set_fill_enabled` 之后（约第 211 行后）追加：

```python
    def set_fill_color(self, color: QColor) -> None:
        self._fill_color = QColor(color.red(), color.green(), color.blue())

    def set_fill_opacity(self, opacity: int) -> None:
        self._fill_opacity = opacity

    def set_fill_linked(self, linked: bool) -> None:
        self._fill_linked = linked
        if linked:
            self._fill_color = QColor(self._pen_color.red(), self._pen_color.green(), self._pen_color.blue())
```

**3d.** 修改 `_finish_drawing_at` 中创建 RectAnnotation 和 EllipseAnnotation 的代码（约第 408-422 行），替换：

```python
        if tool == EditTool.RECT:
            item = RectAnnotation(
                rect,
                pen_color=self._pen_color,
                pen_width=self._pen_width,
                fill_enabled=self._fill_enabled,
                fill_color=self._fill_color,
            )
        elif tool == EditTool.ELLIPSE:
            item = EllipseAnnotation(
                rect,
                pen_color=self._pen_color,
                pen_width=self._pen_width,
                fill_enabled=self._fill_enabled,
                fill_color=self._fill_color,
            )
```

为：

```python
        if tool == EditTool.RECT:
            item = RectAnnotation(
                rect,
                pen_color=self._pen_color,
                pen_width=self._pen_width,
                fill_enabled=self._fill_enabled,
                fill_color=self._fill_color,
                fill_opacity=self._fill_opacity,
            )
        elif tool == EditTool.ELLIPSE:
            item = EllipseAnnotation(
                rect,
                pen_color=self._pen_color,
                pen_width=self._pen_width,
                fill_enabled=self._fill_enabled,
                fill_color=self._fill_color,
                fill_opacity=self._fill_opacity,
            )
```

- [ ] **Step 4: 运行测试**

Run: `python -m pytest tests/test_inline_edit_canvas.py -v`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/widgets/inline_edit_canvas.py tests/test_inline_edit_canvas.py
git commit -m "feat(canvas): 添加 fill_opacity/fill_linked 属性和 setter 方法"
```

---

### Task 3: ToolPropertiesBar 图形属性页填充控件

**Files:**
- Modify: `src/vibeocr/widgets/editor/tool_properties_bar.py`

- [ ] **Step 1: 添加新信号和属性**

在 `ToolPropertiesBar` 类中，修改信号声明区（约第 29-37 行），在 `blur_radius_changed` 之后追加：

```python
    fill_color_changed = Signal(QColor)
    fill_opacity_changed = Signal(int)
    fill_linked_changed = Signal(bool)
```

在 `__init__` 中（约第 52 行），在 `self._current_color = QColor(255, 0, 0)` 之后追加：

```python
        self._fill_color = QColor(255, 0, 0)
```

- [ ] **Step 2: 添加辅助方法**

在 `_update_color_buttons` 方法（约第 252-259 行）之后追加：

```python
    def _apply_fill_color_style(self, btn: QPushButton) -> None:
        btn.setStyleSheet(
            f"QPushButton#fillColorPickButton {{ background-color: {self._fill_color.name()}; "
            f"border: 1px solid #666; border-radius: 3px; }}"
        )

    def _update_fill_color_buttons(self) -> None:
        for btn in getattr(self, "_fill_color_btns", []):
            self._apply_fill_color_style(btn)

    def _create_fill_color_button(self) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("fillColorPickButton")
        btn.setFixedSize(24, 24)
        self._apply_fill_color_style(btn)
        btn.clicked.connect(self._on_fill_color_pick)
        return btn

    def _on_fill_color_pick(self) -> None:
        color = QColorDialog.getColor(self._fill_color, self, "选择填充颜色")
        if color.isValid():
            self._fill_color = QColor(color.red(), color.green(), color.blue())
            self._update_fill_color_buttons()
            self.fill_color_changed.emit(self._fill_color)

    def _set_fill_sub_controls_visible(self, fill_cb: QCheckBox, visible: bool) -> None:
        attr_map = {
            "id(_fill_cb)": ("_fill_color_btn", "_fill_link_btn", "_fill_opacity_slider", "_fill_opacity_label"),
            "id(_common_fill_cb)": ("_common_fill_color_btn", "_common_fill_link_btn", "_common_fill_opacity_slider", "_common_fill_opacity_label"),
        }
        key = id(fill_cb)
        if key not in attr_map:
            return
        for attr in attr_map[key]:
            widget = getattr(self, attr, None)
            if widget:
                widget.setVisible(visible)
```

实际上上述用 `id()` 的方案不可靠，改用布尔参数：

替换上述 `_set_fill_sub_controls_visible`：

```python
    def _set_fill_sub_controls_visible(self, is_shape_page: bool, visible: bool) -> None:
        if is_shape_page:
            for w in (self._fill_color_btn, self._fill_link_btn,
                      self._fill_opacity_slider, self._fill_opacity_label):
                w.setVisible(visible)
        else:
            for w in (self._common_fill_color_btn, self._common_fill_link_btn,
                      self._common_fill_opacity_slider, self._common_fill_opacity_label):
                w.setVisible(visible)
```

- [ ] **Step 3: 修改 `_create_shape_page`**

替换 `_create_shape_page` 方法（约第 92-115 行）为：

```python
    def _create_shape_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        # 颜色
        layout.addWidget(QLabel("颜色"))
        self._shape_color_btn = self._create_color_button()
        layout.addWidget(self._shape_color_btn)

        # 线宽
        layout.addWidget(QLabel("线宽"))
        self._line_width_spin = QSpinBox()
        self._line_width_spin.setRange(1, 10)
        self._line_width_spin.setValue(2)
        layout.addWidget(self._line_width_spin)

        # 填充
        self._fill_cb = QCheckBox("填充")
        layout.addWidget(self._fill_cb)

        # 填充色按钮
        self._fill_color_btn = self._create_fill_color_button()
        layout.addWidget(self._fill_color_btn)

        # 链接按钮
        self._fill_link_btn = QToolButton()
        self._fill_link_btn.setText("🔗")
        self._fill_link_btn.setCheckable(True)
        self._fill_link_btn.setChecked(True)
        self._fill_link_btn.setFixedSize(24, 24)
        self._fill_link_btn.setToolTip("链接：填充色跟随描边色 / 独立填充色")
        self._fill_link_btn.setStyleSheet(
            "QToolButton { font-size: 14px; }"
            "QToolButton:checked { background-color: #0078d4; color: white; }"
        )
        layout.addWidget(self._fill_link_btn)

        # 透明度
        self._fill_opacity_label_title = QLabel("透明度")
        layout.addWidget(self._fill_opacity_label_title)
        self._fill_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._fill_opacity_slider.setRange(0, 100)
        self._fill_opacity_slider.setValue(20)
        self._fill_opacity_slider.setFixedWidth(80)
        layout.addWidget(self._fill_opacity_slider)
        self._fill_opacity_label = QLabel("20%")
        self._fill_opacity_label.setFixedWidth(30)
        layout.addWidget(self._fill_opacity_label)

        # 记录填充子控件列表（用于 show/hide）
        self._fill_color_btns = [self._fill_color_btn]

        # 初始隐藏填充子控件
        self._set_fill_sub_controls_visible(is_shape_page=True, visible=False)

        return page
```

- [ ] **Step 4: 修改 `_create_common_page`**

替换 `_create_common_page` 方法（约第 204-224 行）为：

```python
    def _create_common_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel("颜色"))
        self._common_color_btn = self._create_color_button()
        layout.addWidget(self._common_color_btn)

        layout.addWidget(QLabel("线宽"))
        self._common_line_width_spin = QSpinBox()
        self._common_line_width_spin.setRange(1, 10)
        self._common_line_width_spin.setValue(2)
        layout.addWidget(self._common_line_width_spin)

        self._common_fill_cb = QCheckBox("填充")
        layout.addWidget(self._common_fill_cb)

        # 填充色按钮
        self._common_fill_color_btn = self._create_fill_color_button()
        layout.addWidget(self._common_fill_color_btn)

        # 链接按钮
        self._common_fill_link_btn = QToolButton()
        self._common_fill_link_btn.setText("🔗")
        self._common_fill_link_btn.setCheckable(True)
        self._common_fill_link_btn.setChecked(True)
        self._common_fill_link_btn.setFixedSize(24, 24)
        self._common_fill_link_btn.setToolTip("链接：填充色跟随描边色 / 独立填充色")
        self._common_fill_link_btn.setStyleSheet(
            "QToolButton { font-size: 14px; }"
            "QToolButton:checked { background-color: #0078d4; color: white; }"
        )
        layout.addWidget(self._common_fill_link_btn)

        # 透明度
        self._common_fill_opacity_label_title = QLabel("透明度")
        layout.addWidget(self._common_fill_opacity_label_title)
        self._common_fill_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._common_fill_opacity_slider.setRange(0, 100)
        self._common_fill_opacity_slider.setValue(20)
        self._common_fill_opacity_slider.setFixedWidth(80)
        layout.addWidget(self._common_fill_opacity_slider)
        self._common_fill_opacity_label = QLabel("20%")
        self._common_fill_opacity_label.setFixedWidth(30)
        layout.addWidget(self._common_fill_opacity_label)

        # 记录所有填充色按钮
        if hasattr(self, "_fill_color_btns"):
            self._fill_color_btns.append(self._common_fill_color_btn)
        else:
            self._fill_color_btns = [self._common_fill_color_btn]

        # 初始隐藏
        self._set_fill_sub_controls_visible(is_shape_page=False, visible=False)

        return page
```

- [ ] **Step 5: 修改 `_connect_signals`**

替换 `_connect_signals` 方法（约第 226-235 行）为：

```python
    def _connect_signals(self) -> None:
        self._line_width_spin.valueChanged.connect(self.line_width_changed.emit)
        self._fill_cb.toggled.connect(self._on_fill_toggled_shape)
        self._common_fill_cb.toggled.connect(self._on_fill_toggled_common)
        self._fill_link_btn.toggled.connect(self._on_fill_link_toggled_shape)
        self._common_fill_link_btn.toggled.connect(self._on_fill_link_toggled_common)
        self._fill_opacity_slider.valueChanged.connect(self._on_fill_opacity_changed_shape)
        self._common_fill_opacity_slider.valueChanged.connect(self._on_fill_opacity_changed_common)
        self._font_combo.currentFontChanged.connect(self.font_changed.emit)
        self._font_size_spin.valueChanged.connect(self.font_size_changed.emit)
        self._bold_btn.toggled.connect(self.bold_changed.emit)
        self._italic_btn.toggled.connect(self.italic_changed.emit)
        self._mosaic_slider.valueChanged.connect(self._on_mosaic_changed)
        self._blur_slider.valueChanged.connect(self._on_blur_changed)

    def _on_fill_toggled_shape(self, checked: bool) -> None:
        self._set_fill_sub_controls_visible(is_shape_page=True, visible=checked)
        self._update_fill_color_btn_state(self._fill_link_btn, self._fill_color_btn)
        self.fill_enabled_changed.emit(checked)

    def _on_fill_toggled_common(self, checked: bool) -> None:
        self._set_fill_sub_controls_visible(is_shape_page=False, visible=checked)
        self._update_fill_color_btn_state(self._common_fill_link_btn, self._common_fill_color_btn)
        self.fill_enabled_changed.emit(checked)

    def _on_fill_link_toggled_shape(self, checked: bool) -> None:
        if checked:
            self._fill_color = QColor(self._current_color.red(), self._current_color.green(), self._current_color.blue())
            self._update_fill_color_buttons()
        self._update_fill_color_btn_state(self._fill_link_btn, self._fill_color_btn)
        self.fill_linked_changed.emit(checked)

    def _on_fill_link_toggled_common(self, checked: bool) -> None:
        if checked:
            self._fill_color = QColor(self._current_color.red(), self._current_color.green(), self._current_color.blue())
            self._update_fill_color_buttons()
        self._update_fill_color_btn_state(self._common_fill_link_btn, self._common_fill_color_btn)
        self.fill_linked_changed.emit(checked)

    def _on_fill_opacity_changed_shape(self, value: int) -> None:
        self._fill_opacity_label.setText(f"{value}%")
        self.fill_opacity_changed.emit(value)

    def _on_fill_opacity_changed_common(self, value: int) -> None:
        self._common_fill_opacity_label.setText(f"{value}%")
        self.fill_opacity_changed.emit(value)

    def _update_fill_color_btn_state(self, link_btn: QToolButton, color_btn: QPushButton) -> None:
        color_btn.setEnabled(not link_btn.isChecked())
```

- [ ] **Step 6: 修改 `_on_color_pick` 以同步填充色**

替换 `_on_color_pick` 方法（约第 237-243 行）为：

```python
    def _on_color_pick(self) -> None:
        color = QColorDialog.getColor(self._current_color, self, "选择颜色")
        if color.isValid():
            self._current_color = color
            self._update_color_buttons()
            self.color_changed.emit(color)
            if self._fill_link_btn.isChecked() or self._common_fill_link_btn.isChecked():
                self._fill_color = QColor(color.red(), color.green(), color.blue())
                self._update_fill_color_buttons()
```

- [ ] **Step 7: 修改 `update_for_selection` 和 `_sync_common_page`**

替换 `update_for_selection`（约第 283-312 行）为：

```python
    def update_for_selection(self, item) -> None:
        from vibeocr.widgets.editor.annotation_items import (
            ArrowAnnotation,
            BlurItem,
            EllipseAnnotation,
            MosaicItem,
            RectAnnotation,
            TextAnnotation,
        )

        if isinstance(item, (RectAnnotation, EllipseAnnotation)):
            self._sync_common_page(item)
            self._stack.setCurrentIndex(self._COMMON_PAGE)
        elif isinstance(item, ArrowAnnotation):
            self._sync_common_page(item)
            self._hide_common_fill_controls()
            self._stack.setCurrentIndex(self._COMMON_PAGE)
        elif isinstance(item, TextAnnotation):
            self._sync_text_page(item)
            self._stack.setCurrentIndex(self._TEXT_PAGE)
        elif isinstance(item, MosaicItem):
            self._mosaic_slider.setValue(item._strength)
            self._stack.setCurrentIndex(self._MOSAIC_PAGE)
        elif isinstance(item, BlurItem):
            self._blur_slider.setValue(item._radius)
            self._stack.setCurrentIndex(self._BLUR_PAGE)
        else:
            self.clear_selection()
```

替换 `_sync_common_page`（约第 320-327 行）为：

```python
    def _sync_common_page(self, item) -> None:
        self._common_line_width_spin.blockSignals(True)
        self._common_line_width_spin.setValue(item._pen_width)
        self._common_line_width_spin.blockSignals(False)
        self._current_color = item._pen_color
        self._update_color_buttons()

        # 同步填充属性
        fill_enabled = getattr(item, "_fill_enabled", False)
        fill_color = getattr(item, "_fill_color", QColor(item._pen_color.red(), item._pen_color.green(), item._pen_color.blue()))
        fill_opacity = getattr(item, "_fill_opacity", 20)

        # 判断链接状态
        is_linked = (fill_color.red() == item._pen_color.red()
                     and fill_color.green() == item._pen_color.green()
                     and fill_color.blue() == item._pen_color.blue())

        self._common_fill_cb.blockSignals(True)
        self._common_fill_cb.setChecked(fill_enabled)
        self._common_fill_cb.blockSignals(False)

        self._common_fill_link_btn.blockSignals(True)
        self._common_fill_link_btn.setChecked(is_linked)
        self._common_fill_link_btn.blockSignals(False)

        self._fill_color = QColor(fill_color.red(), fill_color.green(), fill_color.blue())
        self._update_fill_color_buttons()

        self._common_fill_opacity_slider.blockSignals(True)
        self._common_fill_opacity_slider.setValue(fill_opacity)
        self._common_fill_opacity_slider.blockSignals(False)
        self._common_fill_opacity_label.setText(f"{fill_opacity}%")

        # 显隐填充子控件
        self._set_fill_sub_controls_visible(is_shape_page=False, visible=fill_enabled)
        self._update_fill_color_btn_state(self._common_fill_link_btn, self._common_fill_color_btn)

    def _hide_common_fill_controls(self) -> None:
        self._common_fill_cb.hide()
        self._set_fill_sub_controls_visible(is_shape_page=False, visible=False)
```

- [ ] **Step 8: 修改 `clear_selection`**

替换 `clear_selection`（约第 314-318 行）为：

```python
    def clear_selection(self) -> None:
        self._common_fill_cb.show()
        self.update_for_tool(self._last_tool)
```

- [ ] **Step 9: 提交**

```bash
git add src/vibeocr/widgets/editor/tool_properties_bar.py
git commit -m "feat(toolbar): 添加填充色按钮、链接按钮和透明度滑块控件"
```

---

### Task 4: ScreenCaptureOverlay 信号连接

**Files:**
- Modify: `src/vibeocr/widgets/screen_capture_overlay.py`

- [ ] **Step 1: 修改 `_connect_editing_signals`**

在 `_connect_editing_signals` 方法中（约第 435 行，`props.fill_enabled_changed.connect` 之后）追加：

```python
        props.fill_color_changed.connect(self._on_fill_color_changed)
        props.fill_opacity_changed.connect(self._on_fill_opacity_changed)
        props.fill_linked_changed.connect(self._on_fill_linked_changed)
```

- [ ] **Step 2: 修改 `_on_fill_enabled_changed`**

替换 `_on_fill_enabled_changed`（约第 529-539 行）为：

```python
    def _on_fill_enabled_changed(self, enabled) -> None:
        self._canvas.set_fill_enabled(enabled)
        item = self._canvas.selected_annotation
        if item and hasattr(item, "set_fill_enabled"):
            item.set_fill_enabled(enabled, self._canvas._fill_color, self._canvas._fill_opacity)
```

- [ ] **Step 3: 添加新的信号处理方法**

在 `_on_fill_enabled_changed` 之后追加：

```python
    def _on_fill_color_changed(self, color) -> None:
        self._canvas.set_fill_color(color)
        item = self._canvas.selected_annotation
        if item and hasattr(item, "set_fill_color"):
            item.set_fill_color(color)

    def _on_fill_opacity_changed(self, opacity) -> None:
        self._canvas.set_fill_opacity(opacity)
        item = self._canvas.selected_annotation
        if item and hasattr(item, "set_fill_opacity"):
            item.set_fill_opacity(opacity)

    def _on_fill_linked_changed(self, linked) -> None:
        self._canvas.set_fill_linked(linked)
        if linked:
            item = self._canvas.selected_annotation
            if item and hasattr(item, "set_fill_color"):
                item.set_fill_color(self._canvas._pen_color)
```

- [ ] **Step 4: 修改 `_on_color_changed` 以同步填充**

替换 `_on_color_changed`（约第 515-521 行）为：

```python
    def _on_color_changed(self, color) -> None:
        self._canvas.set_pen_color(color)
        item = self._canvas.selected_annotation
        if item and hasattr(item, "set_pen_color"):
            item.set_pen_color(color)
        elif isinstance(item, TextAnnotation):
            item.set_text_color(color)
        if self._canvas._fill_linked:
            if item and hasattr(item, "set_fill_color"):
                item.set_fill_color(color)
```

- [ ] **Step 5: 运行全部测试确认无回归**

Run: `python -m pytest tests/test_annotation_items.py tests/test_inline_edit_canvas.py -v`
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/widgets/screen_capture_overlay.py
git commit -m "feat(overlay): 连接填充色、透明度和链接信号处理"
```

---

### Task 5: 手动验证

- [ ] **Step 1: 启动应用**

Run: `python -m vibeocr`（或项目启动命令）

- [ ] **Step 2: 测试填充基本流程**

1. 截图 → 选择矩形工具 → 勾选"填充" → 确认出现填充色按钮、链接按钮、透明度滑块
2. 链接激活时绘制矩形 → 填充色为描边色 + 20% 透明度
3. 调整透明度滑块到 80% → 绘制新矩形 → 填充透明度明显更高
4. 点击链接按钮关闭 → 点击填充色按钮选择蓝色 → 绘制矩形 → 填充色为蓝色

- [ ] **Step 3: 测试选中同步**

1. 绘制一个填充矩形 → 选中它 → 通用属性页正确回显填充色、透明度、链接状态
2. 修改透明度 → 选中项实时更新
3. 关闭链接 → 选新填充色 → 选中项实时更新

- [ ] **Step 4: 测试边界情况**

1. 选中箭头标注 → 填充控件正确隐藏
2. 关闭链接后改描边色 → 填充色不变
3. 重新打开链接 → 填充色立即同步为描边色
