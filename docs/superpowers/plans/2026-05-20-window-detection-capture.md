# 截图界面窗口识别框选功能 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在截图 CAPTURING 阶段，鼠标悬停时通过 Win32 API 自动检测窗口/子控件并高亮，点击选中；未检测到时回退手动拖拽。

**Architecture:** 新增 WindowDetector 工具类封装 Win32 API（WindowFromPoint、AccessibleObjectFromPoint、EnumChildWindows），ScreenCaptureOverlay 新增 HOVER/DRAG 子状态集成检测逻辑。

**Tech Stack:** PySide6, ctypes (Win32 API), Python 3.13

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `src/vibeocr/widgets/window_detector.py` | WindowDetector 类：Win32 API 封装，窗口/控件边界检测 |
| `src/vibeocr/widgets/screen_capture_overlay.py` | ScreenCaptureOverlay 改动：HOVER/DRAG 子状态、集成检测 |
| `tests/test_window_detector.py` | WindowDetector 单元测试（mock Win32 API） |
| `tests/test_screen_capture_overlay.py` | 扩展现有测试：HOVER/DRAG 子状态、检测集成 |

---

### Task 1: WindowDetector 基础框架 + _hit_test

**Files:**
- Create: `src/vibeocr/widgets/window_detector.py`
- Create: `tests/test_window_detector.py`

- [ ] **Step 1: 写失败测试 — WindowDetector 初始化和 _hit_test**

创建 `tests/test_window_detector.py`：

```python
"""Tests for WindowDetector."""
import sys

import pytest
from PySide6.QtCore import QPoint, QRect

from vibeocr.widgets.window_detector import WindowDetector


@pytest.fixture
def detector(qapp):
    overlay_hwnd = 12345
    return WindowDetector(overlay_hwnd)


class TestWindowDetectorInit:
    def test_stores_overlay_hwnd(self, detector):
        assert detector._overlay_hwnd == 12345

    def test_initial_cache_is_none(self, detector):
        assert detector._cached_hwnd is None
        assert detector._cached_rect is None


class TestHitTest:
    def test_returns_none_when_no_window(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(window_from_point_result=0),
        )
        result = detector._hit_test((100, 200))
        assert result is None

    def test_filters_overlay_hwnd(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(window_from_point_result=12345, ancestor_result=12345),
        )
        result = detector._hit_test((100, 200))
        assert result is None

    def test_returns_root_hwnd(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(window_from_point_result=999, ancestor_result=888, is_visible=True),
        )
        result = detector._hit_test((100, 200))
        assert result == 888


class _MockWin32:
    def __init__(self, **kwargs):
        self._kwargs = kwargs

    def WindowFromPoint(self, point):
        return self._kwargs.get("window_from_point_result", 0)

    def GetAncestor(self, hwnd, flags):
        return self._kwargs.get("ancestor_result", hwnd)

    def IsWindowVisible(self, hwnd):
        return self._kwargs.get("is_visible", False)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_window_detector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vibeocr.widgets.window_detector'`

- [ ] **Step 3: 实现 WindowDetector 基础框架**

创建 `src/vibeocr/widgets/window_detector.py`：

```python
"""WindowDetector — 通过 Win32 API 检测鼠标下的窗口和子控件边界。

仅 Windows 平台可用。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
from typing import NamedTuple

from PySide6.QtCore import QPoint, QRect

if sys.platform != "win32":
    raise ImportError("WindowDetector is only available on Windows")

user32 = ctypes.windll.user32


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


GA_ROOT = 2


class _Win32Bindings:
    def WindowFromPoint(self, point: _POINT) -> int:
        return user32.WindowFromPoint(point)

    def GetAncestor(self, hwnd: int, flags: int) -> int:
        return user32.GetAncestor(hwnd, flags)

    def IsWindowVisible(self, hwnd: int) -> bool:
        return bool(user32.IsWindowVisible(hwnd))


_win = _Win32Bindings()


class WindowDetector:
    def __init__(self, overlay_hwnd: int) -> None:
        self._overlay_hwnd = overlay_hwnd
        self._cached_hwnd: int | None = None
        self._cached_rect: QRect | None = None

    def detect_at(
        self,
        pos: QPoint,
        dpr: float,
        virtual_offset: QPoint,
    ) -> QRect | None:
        physical_x = int(pos.x() * dpr) + int(virtual_offset.x() * dpr)
        physical_y = int(pos.y() * dpr) + int(virtual_offset.y() * dpr)
        hwnd = self._hit_test((physical_x, physical_y))
        if hwnd is None:
            self._cached_hwnd = None
            self._cached_rect = None
            return None

        rect = self._get_control_rect(hwnd, (physical_x, physical_y))
        if rect is None:
            rect = self._get_window_rect(hwnd)
        if rect is None:
            return None

        logical = QRect(
            int((rect.x() - virtual_offset.x()) / dpr),
            int((rect.y() - virtual_offset.y()) / dpr),
            int(rect.width() / dpr),
            int(rect.height() / dpr),
        )
        self._cached_hwnd = hwnd
        self._cached_rect = logical
        return logical

    def _hit_test(self, physical_pos: tuple[int, int]) -> int | None:
        point = _POINT(physical_pos[0], physical_pos[1])
        hwnd = _win.WindowFromPoint(point)
        if hwnd == 0:
            return None

        root = _win.GetAncestor(hwnd, GA_ROOT)
        if root == 0:
            root = hwnd

        if root == self._overlay_hwnd:
            return None

        if not _win.IsWindowVisible(root):
            return None

        return root

    def _get_control_rect(
        self, hwnd: int, physical_pos: tuple[int, int]
    ) -> QRect | None:
        raise NotImplementedError

    def _get_window_rect(self, hwnd: int) -> QRect | None:
        raise NotImplementedError
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_window_detector.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/widgets/window_detector.py tests/test_window_detector.py
git commit -m "feat: WindowDetector 基础框架 — _hit_test 窗口检测"
```

---

### Task 2: WindowDetector._get_window_rect

**Files:**
- Modify: `src/vibeocr/widgets/window_detector.py`
- Modify: `tests/test_window_detector.py`

- [ ] **Step 1: 写失败测试 — _get_window_rect**

在 `tests/test_window_detector.py` 新增：

```python
class TestGetWindowRect:
    def test_returns_rect_for_valid_hwnd(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(get_window_rect_result=ctypes.wintypes.RECT(100, 200, 500, 400)),
        )
        result = detector._get_window_rect(888)
        assert result == QRect(100, 200, 400, 200)

    def test_returns_none_for_invalid_hwnd(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(get_window_rect_result=None),
        )
        result = detector._get_window_rect(0)
        assert result is None
```

在 `_MockWin32` 中新增方法：

```python
def GetWindowRect(self, hwnd):
    result = self._kwargs.get("get_window_rect_result")
    if result is None:
        return False
    return result
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_window_detector.py::TestGetWindowRect -v`
Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: 实现 _get_window_rect**

替换 `window_detector.py` 中的 `_get_window_rect` 方法：

```python
def _get_window_rect(self, hwnd: int) -> QRect | None:
    rect = ctypes.wintypes.RECT()
    result = user32.GetWindowRect(hwnd, ctypes.byref(rect))
    if not result:
        return None
    return QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_window_detector.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/widgets/window_detector.py tests/test_window_detector.py
git commit -m "feat: WindowDetector._get_window_rect 实现"
```

---

### Task 3: WindowDetector._get_control_rect（IAccessible + EnumChildWindows 降级）

**Files:**
- Modify: `src/vibeocr/widgets/window_detector.py`
- Modify: `tests/test_window_detector.py`

- [ ] **Step 1: 写失败测试 — _get_control_rect**

在 `tests/test_window_detector.py` 新增：

```python
class TestGetControlRect:
    def test_returns_smallest_child_rect(self, detector, monkeypatch):
        children = [
            ctypes.wintypes.RECT(100, 200, 500, 400),
            ctypes.wintypes.RECT(150, 220, 300, 350),
            ctypes.wintypes.RECT(160, 230, 280, 340),
        ]
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(
                accessible_result=None,
                enum_children=children,
            ),
        )
        result = detector._get_control_rect(888, (200, 250))
        assert result == QRect(160, 230, 120, 110)

    def test_returns_none_when_no_children(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(accessible_result=None, enum_children=[]),
        )
        result = detector._get_control_rect(888, (200, 250))
        assert result is None

    def test_uses_accessible_when_available(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(accessible_result=ctypes.wintypes.RECT(110, 210, 290, 330)),
        )
        result = detector._get_control_rect(888, (200, 250))
        assert result == QRect(110, 210, 180, 120)
```

在 `_MockWin32` 中新增方法：

```python
def AccessibleObjectFromPoint(self, x, y):
    result = self._kwargs.get("accessible_result")
    if result is None:
        return -1, None, None
    return 0, None, result

def EnumChildWindows(self, parent, callback, lparam):
    for rect in self._kwargs.get("enum_children", []):
        callback(rect)
    return True

def GetWindowRect(self, hwnd):
    result = self._kwargs.get("get_window_rect_result")
    if result is None:
        return False
    return result
```

注意：需要更新 `_MockWin32.GetWindowRect` 使其也处理 `enum_children` 的调用场景。重新整理 `_MockWin32`：

```python
class _MockWin32:
    def __init__(self, **kwargs):
        self._kwargs = kwargs

    def WindowFromPoint(self, point):
        return self._kwargs.get("window_from_point_result", 0)

    def GetAncestor(self, hwnd, flags):
        return self._kwargs.get("ancestor_result", hwnd)

    def IsWindowVisible(self, hwnd):
        return self._kwargs.get("is_visible", False)

    def GetWindowRect(self, hwnd):
        result = self._kwargs.get("get_window_rect_result")
        if result is None:
            return False
        return result

    def AccessibleObjectFromPoint(self, x, y):
        result = self._kwargs.get("accessible_result")
        if result is None:
            return -1, None, None
        return 0, None, result

    def EnumChildWindows(self, parent, callback, lparam):
        for rect in self._kwargs.get("enum_children", []):
            callback(rect)
        return True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_window_detector.py::TestGetControlRect -v`
Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: 实现 _get_control_rect**

替换 `window_detector.py` 中的 `_get_control_rect` 方法：

```python
import oleacc
import comtypes

def _get_control_rect(
    self, hwnd: int, physical_pos: tuple[int, int]
) -> QRect | None:
    rect = self._try_accessible(physical_pos)
    if rect is not None:
        return rect
    return self._try_enum_children(hwnd, physical_pos)

def _try_accessible(self, physical_pos: tuple[int, int]) -> QRect | None:
    try:
        hr, accessible, child_id = oleacc.AccessibleObjectFromPoint(
            physical_pos[0], physical_pos[1]
        )
        if hr != 0 or accessible is None:
            return None
        location = accessible.accLocation(0)
        if location is None:
            return None
        left, top, width, height = location
        if width <= 0 or height <= 0:
            return None
        return QRect(int(left), int(top), int(width), int(height))
    except Exception:
        return None

def _try_enum_children(
    self, hwnd: int, physical_pos: tuple[int, int]
) -> QRect | None:
    children: list[ctypes.wintypes.RECT] = []
    result = user32.EnumChildWindows(
        hwnd,
        ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)(
            lambda h, l: children.append(self._get_window_rect(h)) or True
            if self._get_window_rect(h) is not None else True
        ),
        0,
    )
    if not children:
        return None

    px, py = physical_pos
    smallest: QRect | None = None
    for rect in children:
        qrect = rect if isinstance(rect, QRect) else QRect(
            rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        )
        if qrect.contains(QPoint(px, py)):
            if smallest is None or (qrect.width() * qrect.height()) < (smallest.width() * smallest.height()):
                smallest = qrect
    return smallest
```

注意：上面的 `_try_enum_children` 用回调收集子窗口矩形的方式需要更稳定的 ctypes 回调写法。实际实现改用稳定的方式：

```python
def _try_enum_children(
    self, hwnd: int, physical_pos: tuple[int, int]
) -> QRect | None:
    children_rects: list[QRect] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def enum_callback(child_hwnd: int, _lparam: int) -> bool:
        rect = ctypes.wintypes.RECT()
        if user32.GetWindowRect(child_hwnd, ctypes.byref(rect)):
            children_rects.append(
                QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
            )
        return True

    user32.EnumChildWindows(hwnd, enum_callback, 0)

    if not children_rects:
        return None

    px, py = physical_pos
    smallest: QRect | None = None
    for qrect in children_rects:
        if qrect.contains(QPoint(px, py)):
            if smallest is None or (qrect.width() * qrect.height()) < (smallest.width() * smallest.height()):
                smallest = qrect
    return smallest
```

同时移除 `_get_control_rect` 中的 `import oleacc` 和 `import comtypes`，改为文件顶部条件导入：

在 `window_detector.py` 顶部导入区域添加：

```python
try:
    import oleacc
except ImportError:
    oleacc = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_window_detector.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/widgets/window_detector.py tests/test_window_detector.py
git commit -m "feat: WindowDetector._get_control_rect — IAccessible + EnumChildWindows 降级"
```

---

### Task 4: WindowDetector.detect_at 缓存优化 + 坐标转换测试

**Files:**
- Modify: `src/vibeocr/widgets/window_detector.py`
- Modify: `tests/test_window_detector.py`

- [ ] **Step 1: 写失败测试 — detect_at 缓存和坐标转换**

在 `tests/test_window_detector.py` 新增：

```python
class TestDetectAt:
    def test_returns_logical_rect_with_dpr_and_offset(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(
                window_from_point_result=888,
                ancestor_result=888,
                is_visible=True,
                accessible_result=ctypes.wintypes.RECT(200, 400, 600, 800),
            ),
        )
        pos = QPoint(50, 100)
        result = detector.detect_at(pos, dpr=2.0, virtual_offset=QPoint(0, 0))
        assert result is not None
        assert result.x() == 100
        assert result.y() == 200
        assert result.width() == 200
        assert result.height() == 200

    def test_returns_none_when_no_window(self, detector, monkeypatch):
        monkeypatch.setattr(
            "vibeocr.widgets.window_detector._win",
            _MockWin32(window_from_point_result=0),
        )
        result = detector.detect_at(QPoint(50, 50), dpr=1.0, virtual_offset=QPoint(0, 0))
        assert result is None
```

- [ ] **Step 2: 运行测试确认通过（detect_at 已在 Task 1 实现）**

Run: `python -m pytest tests/test_window_detector.py::TestDetectAt -v`
Expected: PASS（验证已有实现正确性）

- [ ] **Step 3: 添加缓存命中测试**

```python
class TestDetectAtCache:
    def test_caches_result(self, detector, monkeypatch):
        mock = _MockWin32(
            window_from_point_result=888,
            ancestor_result=888,
            is_visible=True,
            accessible_result=ctypes.wintypes.RECT(100, 200, 500, 400),
        )
        monkeypatch.setattr("vibeocr.widgets.window_detector._win", mock)
        pos = QPoint(50, 50)
        r1 = detector.detect_at(pos, 1.0, QPoint(0, 0))
        assert detector._cached_hwnd == 888
        assert detector._cached_rect == r1
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_window_detector.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_window_detector.py
git commit -m "test: WindowDetector.detect_at 坐标转换和缓存测试"
```

---

### Task 5: ScreenCaptureOverlay 集成 — HOVER/DRAG 子状态 + 新增属性

**Files:**
- Modify: `src/vibeocr/widgets/screen_capture_overlay.py`
- Modify: `tests/test_screen_capture_overlay.py`

- [ ] **Step 1: 写失败测试 — 子状态初始化和检测属性**

在 `tests/test_screen_capture_overlay.py` 新增：

```python
class TestSubState:
    def test_initial_sub_state_is_hover(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert overlay._sub_state == "HOVER"

    def test_initial_detected_rect_is_none(self, qapp):
        overlay = ScreenCaptureOverlay()
        assert overlay._detected_rect is None

    def test_reset_capturing_resets_sub_state(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._sub_state = "DRAG"
        overlay._detected_rect = QRect(10, 10, 100, 100)
        overlay._reset_capturing()
        assert overlay._sub_state == "HOVER"
        assert overlay._detected_rect is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_screen_capture_overlay.py::TestSubState -v`
Expected: FAIL — `AttributeError: no attribute '_sub_state'`

- [ ] **Step 3: 在 ScreenCaptureOverlay.__init__ 添加新属性**

在 `screen_capture_overlay.py` 的 `__init__` 方法中，`# 放大镜相关` 注释之前，添加：

```python
        # HOVER/DRAG 子状态
        self._sub_state: str = "HOVER"
        self._detected_rect: QRect | None = None
        self._window_detector: WindowDetector | None = None
        self._last_detect_pos: QPoint = QPoint()
```

在文件顶部导入区域添加：

```python
from vibeocr.widgets.window_detector import WindowDetector
```

- [ ] **Step 4: 在 _reset_capturing 中重置新状态**

在 `_reset_capturing` 方法末尾（`self._state = "CAPTURING"` 之前）添加：

```python
        self._sub_state = "HOVER"
        self._detected_rect = None
        self._last_detect_pos = QPoint()
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_screen_capture_overlay.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/widgets/screen_capture_overlay.py tests/test_screen_capture_overlay.py
git commit -m "feat: ScreenCaptureOverlay 新增 HOVER/DRAG 子状态属性"
```

---

### Task 6: ScreenCaptureOverlay.start_capture 初始化 WindowDetector

**Files:**
- Modify: `src/vibeocr/widgets/screen_capture_overlay.py`
- Modify: `tests/test_screen_capture_overlay.py`

- [ ] **Step 1: 写失败测试**

```python
class TestStartCaptureInit:
    def test_creates_window_detector_with_overlay_hwnd(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._virtual_geometry = QRect(0, 0, 100, 100)
        overlay._device_pixel_ratio = 1.0
        overlay.show()
        hwnd = int(overlay.winId())
        overlay.start_capture()
        assert overlay._window_detector is not None
        assert overlay._window_detector._overlay_hwnd == hwnd
        overlay.hide()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_screen_capture_overlay.py::TestStartCaptureInit -v`
Expected: FAIL — `_window_detector is None`

- [ ] **Step 3: 在 start_capture 方法中初始化 WindowDetector**

在 `start_capture` 方法中，`self.show()` 之前添加：

```python
        hwnd = int(self.winId())
        self._window_detector = WindowDetector(hwnd)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_screen_capture_overlay.py::TestStartCaptureInit -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/widgets/screen_capture_overlay.py tests/test_screen_capture_overlay.py
git commit -m "feat: start_capture 初始化 WindowDetector"
```

---

### Task 7: ScreenCaptureOverlay.mouseMoveEvent — HOVER 检测

**Files:**
- Modify: `src/vibeocr/widgets/screen_capture_overlay.py`
- Modify: `tests/test_screen_capture_overlay.py`

- [ ] **Step 1: 写失败测试**

```python
from unittest.mock import MagicMock


class TestMouseMoveHoverDetect:
    def test_hover_calls_detector_and_sets_detected_rect(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        overlay._device_pixel_ratio = 1.0

        detector = MagicMock()
        detector.detect_at.return_value = QRect(100, 100, 400, 300)
        overlay._window_detector = detector

        event = _make_mouse_event(QPoint(200, 200), Qt.MouseButton.NoButton)
        overlay.mouseMoveEvent(event)

        detector.detect_at.assert_called_once_with(
            QPoint(200, 200), 1.0, overlay._virtual_geometry.topLeft()
        )
        assert overlay._detected_rect == QRect(100, 100, 400, 300)

    def test_hover_sets_detected_rect_none_when_no_detection(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        overlay._device_pixel_ratio = 1.0

        detector = MagicMock()
        detector.detect_at.return_value = None
        overlay._window_detector = detector

        event = _make_mouse_event(QPoint(200, 200), Qt.MouseButton.NoButton)
        overlay.mouseMoveEvent(event)

        assert overlay._detected_rect is None

    def test_drag_substate_uses_existing_logic(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "DRAG"
        overlay._start_pos = QPoint(10, 10)
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        overlay._device_pixel_ratio = 1.0

        detector = MagicMock()
        overlay._window_detector = detector

        event = _make_mouse_event(QPoint(200, 200), Qt.MouseButton.NoButton)
        overlay.mouseMoveEvent(event)

        assert overlay._selection_rect == QRect(10, 10, 190, 190)
        detector.detect_at.assert_not_called()


def _make_mouse_event(pos: QPoint, button: Qt.MouseButton) -> MagicMock:
    event = MagicMock()
    event.pos.return_value = pos
    event.button.return_value = button
    return event
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_screen_capture_overlay.py::TestMouseMoveHoverDetect -v`
Expected: FAIL — `detector.detect_at` 未被调用（当前 mouseMoveEvent 不区分子状态）

- [ ] **Step 3: 重写 mouseMoveEvent 支持子状态**

替换 `screen_capture_overlay.py` 中的 `mouseMoveEvent`：

```python
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """CAPTURING: 鼠标移动 — HOVER 检测或 DRAG 更新选区"""
        if self._state != "CAPTURING":
            return
        self._current_mouse_pos = event.pos()

        if self._sub_state == "DRAG":
            if self._start_pos:
                self._end_pos = event.pos()
                self._selection_rect = QRect(self._start_pos, self._end_pos).normalized()
            self.update()
            return

        # HOVER: 窗口检测
        if self._window_detector:
            delta = event.pos() - self._last_detect_pos
            if delta.x() * delta.x() + delta.y() * delta.y() >= 9:
                self._detected_rect = self._window_detector.detect_at(
                    event.pos(),
                    self._device_pixel_ratio,
                    self._virtual_geometry.topLeft(),
                )
                self._last_detect_pos = event.pos()
        self.update()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_screen_capture_overlay.py::TestMouseMoveHoverDetect -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/widgets/screen_capture_overlay.py tests/test_screen_capture_overlay.py
git commit -m "feat: mouseMoveEvent HOVER 子状态窗口检测"
```

---

### Task 8: ScreenCaptureOverlay.mousePressEvent — HOVER 点击选中 / DRAG 切换

**Files:**
- Modify: `src/vibeocr/widgets/screen_capture_overlay.py`
- Modify: `tests/test_screen_capture_overlay.py`

- [ ] **Step 1: 写失败测试**

```python
class TestMousePressSubState:
    def test_hover_with_detected_rect_selects_and_enters_editing(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"
        overlay._screen_pixmap = QPixmap(1920, 1080)
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        overlay._device_pixel_ratio = 1.0
        overlay._detected_rect = QRect(100, 100, 400, 300)

        event = _make_mouse_event(QPoint(200, 200), Qt.MouseButton.LeftButton)
        overlay.mousePressEvent(event)

        assert overlay._selection_rect == QRect(100, 100, 400, 300)
        assert overlay._state == "EDITING"

    def test_hover_without_detected_rect_switches_to_drag(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"
        overlay._detected_rect = None

        event = _make_mouse_event(QPoint(200, 200), Qt.MouseButton.LeftButton)
        overlay.mousePressEvent(event)

        assert overlay._sub_state == "DRAG"
        assert overlay._start_pos == QPoint(200, 200)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_screen_capture_overlay.py::TestMousePressSubState -v`
Expected: FAIL — 当前逻辑不区分子状态，不检查 `_detected_rect`

- [ ] **Step 3: 重写 mousePressEvent 支持子状态**

替换 `screen_capture_overlay.py` 中的 `mousePressEvent`：

```python
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """CAPTURING: HOVER 点击选中窗口 / DRAG 开始拖拽"""
        if self._state != "CAPTURING":
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._sub_state == "HOVER" and self._detected_rect is not None:
            # 检测到窗口，直接选中
            self._selection_rect = self._detected_rect
            self.releaseMouse()
            dpr = self._device_pixel_ratio
            sel = self._selection_rect
            physical_rect = QRect(
                int(sel.x() * dpr),
                int(sel.y() * dpr),
                int(sel.width() * dpr),
                int(sel.height() * dpr),
            )
            captured = self._screen_pixmap.copy(physical_rect)
            self._captured_pixmap = captured
            self._enter_editing()
            return

        # 无检测窗口或 DRAG 模式：切换到 DRAG
        self._sub_state = "DRAG"
        self._start_pos = event.pos()
        self._selection_rect = QRect(self._start_pos, self._start_pos)
        self.update()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_screen_capture_overlay.py::TestMousePressSubState -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/widgets/screen_capture_overlay.py tests/test_screen_capture_overlay.py
git commit -m "feat: mousePressEvent HOVER 点击选中 / DRAG 切换"
```

---

### Task 9: ScreenCaptureOverlay.paintEvent — 绘制检测高亮

**Files:**
- Modify: `src/vibeocr/widgets/screen_capture_overlay.py`
- Modify: `tests/test_screen_capture_overlay.py`

- [ ] **Step 1: 写失败测试 — 检测高亮绘制**

```python
class TestPaintDetectionHighlight:
    def test_detected_rect_drawn_in_capturing_hover(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "HOVER"
        overlay._screen_pixmap = QPixmap(1920, 1080)
        overlay._detected_rect = QRect(100, 100, 400, 300)
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        overlay._device_pixel_ratio = 1.0
        overlay.resize(1920, 1080)
        # paintEvent 不应抛异常
        overlay.repaint()

    def test_no_detected_rect_highlight_in_drag(self, qapp):
        overlay = ScreenCaptureOverlay()
        overlay._state = "CAPTURING"
        overlay._sub_state = "DRAG"
        overlay._screen_pixmap = QPixmap(1920, 1080)
        overlay._detected_rect = QRect(100, 100, 400, 300)
        overlay._virtual_geometry = QRect(0, 0, 1920, 1080)
        overlay._device_pixel_ratio = 1.0
        overlay.resize(1920, 1080)
        overlay.repaint()
```

- [ ] **Step 2: 运行测试确认通过**

Run: `python -m pytest tests/test_screen_capture_overlay.py::TestPaintDetectionHighlight -v`
Expected: PASS（paintEvent 不崩溃即可）

- [ ] **Step 3: 在 paintEvent 中添加检测高亮绘制**

在 `screen_capture_overlay.py` 的 `paintEvent` 中，`# --- 以下仅 CAPTURING 模式 ---` 之后，`# 4. 绘制选区边框和尺寸` 之前，插入：

```python
        # 3.5 HOVER 模式绘制检测高亮
        if self._sub_state == "HOVER" and self._detected_rect:
            painter.fillRect(self._detected_rect, QColor(0, 120, 215, 40))
            pen = QPen(QColor(0, 120, 215), 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._detected_rect)
```

- [ ] **Step 4: 运行全部测试确认无回归**

Run: `python -m pytest tests/test_screen_capture_overlay.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/widgets/screen_capture_overlay.py tests/test_screen_capture_overlay.py
git commit -m "feat: paintEvent 绘制窗口检测高亮"
```

---

### Task 10: 手动验证 + 清理

**Files:** 无新增

- [ ] **Step 1: 运行全部测试**

Run: `python -m pytest tests/ -v`
Expected: 全部 PASS

- [ ] **Step 2: 手动验证**

启动应用，触发截图功能，验证：
1. 鼠标悬停在窗口上时，窗口边界被蓝色高亮
2. 悬停在按钮/文本框等子控件上时，子控件边界被高亮
3. 点击高亮区域直接进入 EDITING 模式
4. 在桌面空白区域拖拽仍可手动框选
5. 检测高亮不遮挡放大镜和像素信息
6. ESC 取消正常工作
7. 滚轮切换放大镜倍数正常工作

- [ ] **Step 3: 最终提交（如有修复）**

```bash
git add -A
git commit -m "fix: 手动验证后的修复"
```
