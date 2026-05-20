# 截图界面窗口识别框选功能设计

## 概述

在 ScreenCaptureOverlay 的 CAPTURING 阶段，鼠标悬停时通过 Win32 API 自动检测窗口和子控件边界并高亮显示，点击即选中该区域进入 EDITING 模式。未检测到窗口时回退到手动拖拽框选。

行为参照 Snipaste、微信截图等成熟截图工具。

## 技术方案

纯 Win32 API 实现：

- `WindowFromPoint` 获取鼠标下的窗口句柄
- `AccessibleObjectFromPoint` 获取子控件边界（IAccessible 树）
- `EnumChildWindows` 作为 IAccessible 失败时的降级方案

## 架构

### 新增类：WindowDetector

纯工具类（非 QWidget），封装 Win32 API 调用。

```
WindowDetector
├── __init__(overlay_hwnd: int)
├── detect_at(pos: QPoint, dpr: float, virtual_offset: QPoint) -> QRect | None
├── _hit_test(physical_pos: tuple[int,int]) -> int | None
├── _get_control_rect(hwnd: int, physical_pos: tuple[int,int]) -> QRect | None
└── _get_window_rect(hwnd: int) -> QRect | None
```

**detect_at 流程：**

1. `_hit_test`：`WindowFromPoint` 获取 HWND，过滤自身窗口和不可见窗口，`GetAncestor(GA_ROOT)` 获取顶层窗口
2. `_get_control_rect`：`AccessibleObjectFromPoint` 遍历 IAccessible 树找最小子控件，获取 `accLocation`；失败时降级 `EnumChildWindows` 找最小子窗口
3. 坐标转换：物理屏幕坐标 → 减去 virtual_offset → 除以 DPR → 逻辑坐标

**性能优化：**

- 缓存上次检测结果（HWND），鼠标在同一控件内不重复调用 API
- 鼠标移动超过 3px 才重新检测

### ScreenCaptureOverlay 改动

CAPTURING 状态新增子状态：

- **HOVER**：鼠标悬停，实时调用 WindowDetector，命中则高亮
- **DRAG**：鼠标按下并拖动，现有手动矩形框选逻辑

状态转换：

```
HOVER + 点击(有检测窗口) → EDITING
HOVER + 按下(无检测窗口) → DRAG
DRAG + 释放(选区有效) → EDITING
HOVER/DRAG + ESC → 取消
```

**新增属性：**

- `_sub_state: str` — "HOVER" | "DRAG"，默认 "HOVER"
- `_detected_rect: QRect | None` — 当前检测到的窗口/控件矩形
- `_window_detector: WindowDetector` — 检测器实例
- `_last_detect_pos: QPoint` — 上次检测的鼠标位置

**事件处理改动：**

| 事件 | 当前行为 | 新行为 |
|------|---------|--------|
| mouseMoveEvent | 直接更新选区和放大镜 | HOVER: 检测+高亮+放大镜; DRAG: 现有逻辑 |
| mousePressEvent | 开始拖拽选区 | HOVER+有检测: 选中进入EDITING; HOVER+无检测: 切换DRAG |
| mouseReleaseEvent | 完成选区 | DRAG: 现有逻辑 |
| paintEvent | 绘制选区+放大镜 | 新增: 绘制 `_detected_rect` 的蓝色半透明高亮 |

**高亮视觉效果：**

- 蓝色边框 (0, 120, 215)，2px
- 蓝色半透明填充 (0, 120, 215, 40)
- 与现有选区边框颜色一致，风格统一

## 边界情况处理

1. **自身窗口过滤**：`GetAncestor(GA_ROOT)` 后与 overlay HWND 比较，匹配则跳过
2. **高 DPI / 多显示器**：使用目标窗口所在显示器的 DPI 做坐标转换
3. **UWP / 自绘窗口**：IAccessible 失败时降级到 `EnumChildWindows`；无子窗口则返回顶层窗口矩形
4. **隐藏/最小化窗口**：`IsWindowVisible` 过滤不可见窗口
5. **矩形超出屏幕**：检测结果 clip 到 `virtual_geometry`
6. **右键/滚轮**：右键取消、滚轮切换放大镜倍数，行为不变，HOVER 和 DRAG 均生效

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `src/vibeocr/widgets/window_detector.py` | 新增 WindowDetector 类 |
| `src/vibeocr/widgets/screen_capture_overlay.py` | CAPTURING 模式集成窗口检测 |

## 不在范围内

- 跨平台支持（仅 Windows）
- 像素边缘检测降级方案
- 窗口/控件检测的自动化测试
