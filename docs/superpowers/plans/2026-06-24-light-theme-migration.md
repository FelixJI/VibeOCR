# 浅色主题统一 + 关于页卡片化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立唯一的浅色设计 token 模块 `ui/theme.py`，全局 QSS 统一所有控件外观，关于页卡片化重写，删除 4 套旧样式源并迁移全部内联 `setStyleSheet`。

**Architecture:** 新建 `src/vibeocr/ui/theme.py`（token + QSS 生成器，纯 Python）。`main.py` 在创建 `QApplication` 后加载全局 QSS。关于页重写为 3 张卡片（品牌/详细信息/更新日志）。删除 `core/styles.py`、`core/editor_styles.py`、`core/inline_styles.py` 及 `constants.py` 的旧色名，所有调用方 import 一次清干净。编辑器从暗色统一改为浅色。

**Tech Stack:** PySide6 / Qt Style Sheets (QSS)、Python f-string 生成 QSS、pytest、ruff、mypy

**Spec:** `docs/superpowers/specs/2026-06-23-light-theme-and-about-page-design.md`

---

## 文件结构总览

### 新建
| 文件 | 职责 |
|------|------|
| `src/vibeocr/ui/theme.py` | 唯一 token 源（Colors/Spacing/Radius/Typography/Shadow/Layout）+ QSS 生成函数 |
| `src/vibeocr/ui/__init__.py` | 将 `ui/` 变为包（目前 `ui/` 只有 `main_window.ui` 和生成的 `ui_main_window.py`，不是包） |
| `tests/ui/__init__.py` | 测试包 |
| `tests/ui/test_theme.py` | theme token 与 QSS 生成函数测试 |

### 删除
| 文件 | 原因 |
|------|------|
| `src/vibeocr/core/styles.py` | `AppStyles` 零消费者，死代码 |
| `src/vibeocr/core/editor_styles.py` | 暗色主题，迁入 theme |
| `src/vibeocr/core/inline_styles.py` | 浅色毛玻璃主题，迁入 theme |
| `tests/core/test_styles.py` | 测已删的 `AppStyles` |
| `tests/core/test_inline_styles.py` | 测已删的 `InlineStyles` |

### 修改
| 文件 | 改动 |
|------|------|
| `src/vibeocr/core/constants.py` | 删 `COLOR_*` / `WindowsColors`；`Constants.Style` 引用 theme |
| `src/vibeocr/core/__init__.py` | 删对 `COLOR_*`/`WindowsColors`/`AppStyles` 的 import 与 `__all__` |
| `src/vibeocr/main.py` | 加 `app.setStyleSheet(theme.global_qss())` |
| `src/vibeocr/views/tabs/about_tab.py` | 卡片化重写（核心交付） |
| 15 个内联样式文件 | 迁移 `setStyleSheet` 到 token（见下方分类） |

### 内联样式文件迁移分类（15 个，A 类删 / B 类改引用 / C 类换工厂方法）

**A 类（删 setStyleSheet，吃全局默认，6 个）：**
`batch_recognition_tab.py`、`backend_options_widget.py`、`preprocess_options_widget.py`、`update_service.py`、`screen_capture_overlay.py`、`inline_edit_canvas.py`

**A+（关于页卡片化重写，1 个）：**
`about_tab.py`

**B 类（局部样式改用 token，4 个）：**
`chat_widget.py`、`preview_widget.py`、`clipboard_controller.py`、`qrcode_tab.py`

**C 类（删 EditorStyles/InlineStyles import，换 theme，5 个）：**
`edit_toolbar.py`、`tool_properties_bar.py`、`inline_toolbar.py`、`recognition_panel.py`、`inline_recognition_panel.py`

---

## Task 1: 创建 theme token 模块

**Files:**
- Create: `src/vibeocr/ui/__init__.py`
- Create: `src/vibeocr/ui/theme.py`
- Create: `tests/ui/__init__.py`
- Create: `tests/ui/test_theme.py`

- [ ] **Step 1: 创建 ui 包**

创建 `src/vibeocr/ui/__init__.py`：

```python
"""UI 资源与主题层"""
```

创建 `tests/ui/__init__.py`（空文件）。

- [ ] **Step 2: 写 theme token 测试**

创建 `tests/ui/test_theme.py`：

```python
# tests/ui/test_theme.py
"""theme 模块 token 与 QSS 生成函数测试"""

from vibeocr.ui import theme


class TestColors:
    def test_all_colors_are_hex_or_rgba(self):
        for name in ("bg", "surface", "surface_alt", "text", "text_muted",
                     "text_subtle", "border", "border_strong", "accent",
                     "accent_hover", "accent_soft", "success", "danger"):
            val = getattr(theme.Colors, name)
            assert val.startswith("#"), f"{name}={val} 应为十六进制"

    def test_transparent_overlays(self):
        assert theme.Colors.overlay.startswith("rgba")
        assert theme.Colors.hover_bg.startswith("#")
        assert theme.Colors.pressed_bg.startswith("#")


class TestSpacingScale:
    def test_scale_is_4_multiples(self):
        assert theme.Spacing.xs == 4
        assert theme.Spacing.sm == 8
        assert theme.Spacing.md == 12
        assert theme.Spacing.lg == 16
        assert theme.Spacing.xl == 24
        assert theme.Spacing.xxl == 32


class TestLayoutSizes:
    def test_toolbar_and_panel_dims(self):
        assert theme.Layout.toolbar_height == 48
        assert theme.Layout.panel_width == 280
        assert theme.Layout.panel_min_width == 180

    def test_shadow(self):
        assert theme.Layout.shadow_blur == 12
        assert theme.Layout.shadow_color.startswith("rgba")


class TestGlobalQss:
    def test_global_qss_returns_str(self):
        qss = theme.global_qss()
        assert isinstance(qss, str)
        assert len(qss) > 0

    def test_global_qss_covers_core_widgets(self):
        qss = theme.global_qss()
        for selector in ("QWidget", "QPushButton", "QLineEdit", "QGroupBox",
                         "QTabBar::tab", "QProgressBar"):
            assert selector in qss, f"全局 QSS 缺少 {selector}"

    def test_global_qss_uses_token_colors(self):
        qss = theme.global_qss()
        assert theme.Colors.accent in qss
        assert theme.Colors.bg in qss


class TestCardQss:
    def test_card_qss(self):
        qss = theme.card_qss()
        assert "border-radius" in qss
        assert theme.Colors.border in qss


class TestButtonQss:
    def test_primary_button(self):
        qss = theme.button_qss("primary")
        assert theme.Colors.accent in qss
        assert "QPushButton" in qss

    def test_default_button(self):
        qss = theme.button_qss("default")
        # default 返回空串（由全局 QSS 接管），仅校验类型
        assert isinstance(qss, str)

    def test_invalid_variant_raises(self):
        import pytest
        with pytest.raises(ValueError):
            theme.button_qss("nonexistent")


class TestToolbarButtonQss:
    def test_toolbar_button(self):
        qss = theme.toolbar_button_qss()
        assert "QToolButton" in qss
        assert ":hover" in qss
        assert ":checked" in qss


class TestPanelQss:
    def test_panel_qss(self):
        qss = theme.panel_qss()
        assert isinstance(qss, str)
        assert len(qss) > 0
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/ui/test_theme.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'vibeocr.ui.theme'`）

- [ ] **Step 4: 实现 theme.py（token + QSS 生成器）**

创建 `src/vibeocr/ui/theme.py`：

```python
# src/vibeocr/ui/theme.py
"""唯一设计 token 源 + QSS 生成器（浅色主题）。

所有颜色、间距、圆角、字号、布局尺寸在此集中定义，QSS 通过 f-string 引用
token 生成，确保全应用配色一致。
"""

from __future__ import annotations


class Colors:
    """语义色 token（浅色单一套）"""
    # 背景层
    bg          = "#f3f4f6"
    surface     = "#ffffff"
    surface_alt = "#f9fafb"

    # 文字
    text        = "#1f2937"
    text_muted  = "#6b7280"
    text_subtle = "#9ca3af"

    # 边框
    border        = "#e5e7eb"
    border_strong = "#d1d5db"

    # 强调
    accent        = "#0078d4"
    accent_hover  = "#106ebe"
    accent_soft   = "#e3f2fd"

    # 语义
    success       = "#107c10"
    success_hover = "#0b6a0b"
    warning       = "#f7630c"
    danger        = "#c83232"
    danger_hover  = "#d6550a"

    # 透明叠加
    overlay    = "rgba(0,0,0,0.30)"
    hover_bg   = "#e8e8e8"
    pressed_bg = "#dcdcdc"


class Spacing:
    """间距 scale（4 的倍数）"""
    xs, sm, md, lg, xl, xxl = 4, 8, 12, 16, 24, 32


class Radius:
    sm, md, lg = 4, 6, 8


class Typography:
    title   = 24
    h1      = 16
    body    = 14
    small   = 12
    caption = 11
    weight_bold   = 700
    weight_medium = 500


class Shadow:
    blur, offset_y, color = 12, 2, "rgba(0,0,0,0.08)"


class Layout:
    """布局尺寸 token（承接原 EditorStyles/InlineStyles 的尺寸常量）"""
    toolbar_height = 48
    panel_width = 280
    panel_min_width = 180
    shadow_blur = 12
    shadow_offset_y = 2
    shadow_color = "rgba(0,0,0,0.15)"


def global_qss() -> str:
    """全局基础样式（控件级），由 main.py 加载一次。"""
    c, s, r, t = Colors, Spacing, Radius, Typography
    return f"""
    QWidget        {{ background: {c.bg}; color: {c.text}; font-size: {t.body}px; }}
    QToolTip       {{ background: {c.text}; color: {c.surface}; border: none;
                     padding: {s.xs}px; border-radius: {r.sm}px; }}

    QPushButton    {{ background: {c.surface}; color: {c.text};
                     border: 1px solid {c.border}; border-radius: {r.md}px;
                     padding: 6px 14px; }}
    QPushButton:hover    {{ background: {c.hover_bg}; border-color: {c.border_strong}; }}
    QPushButton:pressed  {{ background: {c.pressed_bg}; }}
    QPushButton:disabled {{ color: {c.text_subtle}; background: {c.surface_alt};
                            border-color: {c.border}; }}

    QLineEdit, QSpinBox, QFontComboBox, QComboBox {{
        background: {c.surface}; color: {c.text};
        border: 1px solid {c.border}; border-radius: {r.sm}px; padding: 4px 8px;
    }}
    QLineEdit:focus, QSpinBox:focus, QFontComboBox:focus, QComboBox:focus {{
        border-color: {c.accent};
    }}
    QComboBox QAbstractItemView {{ background: {c.surface};
        selection-background-color: {c.accent_soft}; selection-color: {c.text}; }}

    QGroupBox {{ background: {c.surface}; border: 1px solid {c.border};
                 border-radius: {r.md}px; margin-top: {s.md}px;
                 padding-top: {s.sm}px; }}
    QGroupBox::title {{ color: {c.text_muted}; subcontrol-origin: margin;
                        left: {s.sm}px; padding: 0 {s.xs}px; }}

    QListWidget, QScrollArea {{ background: {c.surface}; border: 1px solid {c.border}; }}
    QListWidget::item:selected {{ background: {c.accent_soft}; color: {c.text}; }}
    QListWidget::item:hover    {{ background: {c.surface_alt}; }}

    QTabWidget::pane   {{ border: 1px solid {c.border}; top: -1px; }}
    QTabBar::tab       {{ padding: 8px {s.lg}px; border: 1px solid {c.border};
                          border-bottom: none; background: {c.surface_alt};
                          border-top-left-radius: {r.sm}px;
                          border-top-right-radius: {r.sm}px; }}
    QTabBar::tab:selected {{ background: {c.surface};
                              border-bottom: 2px solid {c.accent}; }}

    QProgressBar       {{ background: {c.surface_alt}; border: 1px solid {c.border};
                          border-radius: {r.sm}px; text-align: center;
                          color: {c.text}; height: 20px; }}
    QProgressBar::chunk {{ background: {c.accent}; border-radius: {r.sm}px; }}

    QCheckBox, QRadioButton {{ color: {c.text}; spacing: {s.xs}px; }}

    QScrollBar:vertical {{ background: {c.surface}; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {c.border_strong}; border-radius: {r.sm}px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {c.text_subtle}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar:horizontal {{ background: {c.surface}; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{
        background: {c.border_strong}; border-radius: {r.sm}px; min-width: 24px; }}
    """


def card_qss() -> str:
    """卡片容器样式（关于页 / 设置页用）。"""
    c, r, s = Colors, Radius, Spacing
    return f"""
    QFrame#card {{
        background: {c.surface};
        border: 1px solid {c.border};
        border-radius: {r.lg}px;
    }}
    """


def button_qss(variant: str = "default") -> str:
    """生成按钮样式。

    Args:
        variant: "default" | "primary" | "danger"
    """
    c, r = Colors, Radius
    if variant == "primary":
        return f"""
        QPushButton {{
            background: {c.accent}; color: white;
            border: none; border-radius: {r.md}px; padding: 8px 20px;
            font-weight: {Typography.weight_medium};
        }}
        QPushButton:hover {{ background: {c.accent_hover}; }}
        QPushButton:pressed {{ background: {c.accent_hover}; }}
        QPushButton:disabled {{ background: {c.text_subtle}; color: white; }}
        """
    if variant == "danger":
        return f"""
        QPushButton {{
            background: {c.danger}; color: white;
            border: none; border-radius: {r.md}px; padding: 8px 20px;
        }}
        QPushButton:hover {{ background: {c.danger_hover}; }}
        """
    if variant == "default":
        return ""  # 由全局 QSS 接管
    raise ValueError(f"未知按钮 variant: {variant}")


def toolbar_button_qss() -> str:
    """QToolButton 统一样式（编辑器 + 内联浮窗共用，浅色）。"""
    c, r = Colors, Radius
    return f"""
    QToolButton {{
        background: transparent; color: {c.text};
        border: none; border-radius: {r.sm}px; padding: 4px 8px;
    }}
    QToolButton:hover    {{ background: {c.hover_bg}; }}
    QToolButton:pressed  {{ background: {c.pressed_bg}; }}
    QToolButton:checked  {{ background: {c.accent}; color: white; }}
    QToolButton:checked:hover {{ background: {c.accent_hover}; }}
    QToolButton:disabled {{ color: {c.text_subtle}; }}
    """


def panel_qss(object_name: str = "recognitionPanel") -> str:
    """右侧识别面板样式（原 EditorStyles.panel_style，现为浅色）。

    Args:
        object_name: 面板 widget 的 objectName，用于 QSS 选择器作用域。
    """
    c, r, s = Colors, Radius, Spacing
    return f"""
    QWidget#{object_name} {{
        background: {c.surface};
        border-left: 1px solid {c.border};
    }}
    QWidget#{object_name} QLabel#panelTitle {{
        color: {c.text}; font-size: {Typography.h1}px;
        font-weight: {Typography.weight_bold}; padding: {s.sm}px;
    }}
    """
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/ui/test_theme.py -v`
Expected: PASS（全部测试通过）

- [ ] **Step 6: 静态检查**

Run: `uv run ruff check src/vibeocr/ui/theme.py tests/ui/test_theme.py`
Expected: 无错误

- [ ] **Step 7: 提交**

```bash
git add src/vibeocr/ui/__init__.py src/vibeocr/ui/theme.py tests/ui/__init__.py tests/ui/test_theme.py
git commit -m "feat(theme): 新建浅色 token 模块 ui/theme.py + QSS 生成器"
```

---

## Task 2: main.py 加载全局 QSS

**Files:**
- Modify: `src/vibeocr/main.py`（`launch_application()` 内，`_setup_app_icon(app)` 之后）

- [ ] **Step 1: 在 launch_application 中加载全局 QSS**

读取 `src/vibeocr/main.py`，定位 `launch_application()` 函数。当前 `app.setApplicationVersion(__version__)` 之后、`_setup_app_icon(app)` 之后是初始化 ConfigManager 的代码。

在 `_setup_app_icon(app)` 之后插入全局 QSS 加载。找到这一行：

```python
    # 设置应用图标（必须在主窗口创建之前，窗口才能继承图标）
    _setup_app_icon(app)
```

在其后添加：

```python
    # 应用全局浅色主题 QSS（必须在窗口创建前，控件才能继承样式）
    from vibeocr.ui import theme

    app.setStyleSheet(theme.global_qss())
```

- [ ] **Step 2: 启动冒烟测试**

Run: `uv run python -c "from vibeocr.main import launch_application; from vibeocr.ui import theme; print('QSS length:', len(theme.global_qss())); print('OK')"`
Expected: 打印 QSS 长度 > 0 和 OK，无异常。

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/main.py
git commit -m "feat(theme): main.py 加载全局浅色 QSS"
```

---

## Task 3: 关于页卡片化重写（核心交付）

**Files:**
- Modify: `src/vibeocr/views/tabs/about_tab.py`（整体重写 `_setup_ui`）

- [ ] **Step 1: 重写 about_tab.py**

将 `src/vibeocr/views/tabs/about_tab.py` 的 `_setup_ui` 与 helper 方法替换为卡片化布局。保留模块级常量 `_APP_NAME`/`_DESCRIPTION`/`_AUTHOR`/`_COPYRIGHT`/`_GITHUB_URL`/`_TECH_STACK`、`_create_logo_label`（448c456 已实现）、`_on_check_update` 不变。

完整替换 import 区与 class 主体为：

```python
# src/vibeocr/views/tabs/about_tab.py
"""关于标签页 — 展示应用元信息（卡片化布局）"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from vibeocr import __version__, env_manager
from vibeocr.ui import theme

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_APP_NAME = "VibeOCR"
_DESCRIPTION = (
    "一款基于 PaddleOCR 的截图文字识别工具，支持表格识别、公式识别、文档解析等功能。"
)
_AUTHOR = "Felix Ji"
_COPYRIGHT = "© 2025 Felix Ji. All rights reserved."
_GITHUB_URL = "https://github.com/felixji/vibeocr"
_TECH_STACK = [
    ("PaddlePaddle / PaddleX", "OCR 引擎"),
    ("MinerU", "文档解析"),
    ("PySide6", "UI 框架"),
]


class AboutTab(QWidget):
    """关于标签页，展示应用元信息。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        container.setMaximumWidth(720)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(theme.Spacing.xxl, theme.Spacing.xl,
                                  theme.Spacing.xxl, theme.Spacing.xl)
        layout.setSpacing(theme.Spacing.lg)

        # 品牌卡片
        layout.addWidget(self._create_brand_card())
        # 详细信息卡片
        layout.addWidget(self._create_info_card())
        # 更新日志卡片
        layout.addWidget(self._create_changelog_card())

        # 检查更新按钮
        update_btn = QPushButton("检查更新")
        update_btn.setFixedWidth(160)
        update_btn.setStyleSheet(theme.button_qss("primary"))
        update_btn.clicked.connect(self._on_check_update)
        layout.addWidget(update_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()

        scroll.setWidget(container)

        # 居中 container
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch()
        outer.addWidget(scroll, stretch=1)
        outer.addStretch()

    def _create_card(self) -> QFrame:
        """创建一张卡片容器（QFrame + card_qss）。"""
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(theme.card_qss())
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            theme.Spacing.lg, theme.Spacing.lg,
            theme.Spacing.lg, theme.Spacing.lg,
        )
        card_layout.setSpacing(theme.Spacing.sm)
        return card, card_layout

    def _create_brand_card(self) -> QFrame:
        """品牌卡片：图标 + 应用名 + 版本徽标 + 简介。"""
        card, card_layout = self._create_card()

        logo = self._create_logo_label(96)
        if logo is not None:
            card_layout.addWidget(logo, alignment=Qt.AlignmentFlag.AlignCenter)

        name_label = QLabel(_APP_NAME)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = name_label.font()
        f.setPointSize(theme.Typography.title)
        f.setBold(True)
        name_label.setFont(f)
        card_layout.addWidget(name_label)

        # 版本药丸徽标
        version_label = QLabel(f" v{__version__} ")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet(
            f"background: {theme.Colors.accent_soft}; color: {theme.Colors.accent};"
            f" border-radius: {theme.Radius.md}px; padding: 2px {theme.Spacing.sm}px;"
            f" font-size: {theme.Typography.body}px;"
        )
        card_layout.addWidget(version_label, alignment=Qt.AlignmentFlag.AlignCenter)

        card_layout.addSpacing(theme.Spacing.sm)
        desc_label = QLabel(_DESCRIPTION)
        desc_label.setWordWrap(True)
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
        card_layout.addWidget(desc_label)
        return card

    def _create_info_card(self) -> QFrame:
        """详细信息卡片：键值对。"""
        from PySide6.QtWidgets import QFormLayout

        card, card_layout = self._create_card()

        title = QLabel("详细信息")
        title.setStyleSheet(
            f"font-size: {theme.Typography.h1}px;"
            f" font-weight: {theme.Typography.weight_bold};"
            f" color: {theme.Colors.text};"
        )
        card_layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(theme.Spacing.sm)
        label_style = f"color: {theme.Colors.text_muted};"

        def make_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(label_style)
            return lbl

        tech = " · ".join(name for name, _ in _TECH_STACK)
        link = QLabel(f'<a href="{_GITHUB_URL}" style="color:{theme.Colors.accent};">'
                      f"{_GITHUB_URL}</a>")
        link.setOpenExternalLinks(True)

        form.addRow(make_label("作者"), QLabel(_AUTHOR))
        form.addRow(make_label("版权"), QLabel(_COPYRIGHT))
        form.addRow(make_label("技术栈"), QLabel(tech))
        form.addRow(make_label("项目"), link)
        card_layout.addLayout(form)
        return card

    def _create_changelog_card(self) -> QFrame:
        """更新日志卡片。"""
        card, card_layout = self._create_card()

        title = QLabel("更新日志")
        title.setStyleSheet(
            f"font-size: {theme.Typography.h1}px;"
            f" font-weight: {theme.Typography.weight_bold};"
            f" color: {theme.Colors.text};"
        )
        card_layout.addWidget(title)

        self._changelog_browser = QTextBrowser()
        self._changelog_browser.setOpenExternalLinks(True)
        self._changelog_browser.setMaximumHeight(320)
        self._changelog_browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        self._changelog_browser.setStyleSheet("background: transparent;")

        changelog_path: Path = env_manager.get_project_root() / "CHANGELOG.md"
        if changelog_path.exists():
            try:
                raw = changelog_path.read_text(encoding="utf-8")
                self._changelog_browser.setMarkdown(raw)
            except Exception:
                logger.exception("读取 CHANGELOG.md 失败")
                self._changelog_browser.setMarkdown("暂无更新日志")
        else:
            self._changelog_browser.setMarkdown("暂无更新日志")
        card_layout.addWidget(self._changelog_browser)
        return card

    @staticmethod
    def _create_logo_label(size: int = 128) -> QLabel | None:
        """创建关于页 Logo 标签（448c456 已实现，复用）。

        优先读取多分辨率 app_icon.ico；缺失时返回 None（不破坏布局）。
        """
        icon_path = env_manager.get_project_root() / "resources" / "app_icon.ico"
        if not icon_path.exists():
            logger.warning(f"应用图标不存在: {icon_path}")
            return None

        pixmap = QPixmap(str(icon_path))
        if pixmap.isNull():
            logger.warning(f"应用图标加载失败: {icon_path}")
            return None

        label = QLabel()
        label.setPixmap(
            pixmap.scaled(
                QSize(size, size),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        return label

    def _on_check_update(self) -> None:
        """手动检查更新"""
        import asyncio

        try:
            from vibeocr.services.update_service import UpdateService

            app_dir = env_manager.get_project_root()
            service = UpdateService(app_dir)
            _update_task = asyncio.ensure_future(service.check_and_prompt(self))  # noqa: RUF006
        except Exception as e:
            logger.exception(f"检查更新失败: {e}")
```

- [ ] **Step 2: 静态检查**

Run: `uv run ruff check src/vibeocr/views/tabs/about_tab.py`
Expected: 无错误

- [ ] **Step 3: 运行现有测试**

Run: `uv run pytest tests/ -k "about or tab" -v`
Expected: PASS（about_tab 无现有测试也不报错；如有，确认通过）

- [ ] **Step 4: 提交**

```bash
git add src/vibeocr/views/tabs/about_tab.py
git commit -m "feat(about): 关于页卡片化重写——品牌/信息/日志三卡片 + token 配色"
```

---

## Task 4: 清理旧样式源（constants + core/__init__）

**Files:**
- Modify: `src/vibeocr/core/constants.py`（删 `COLOR_*`、`WindowsColors`、相关导出）
- Modify: `src/vibeocr/core/__init__.py`（删对 `COLOR_*`/`WindowsColors`/`AppStyles` 的 import 和 `__all__`）
- Delete: `src/vibeocr/core/styles.py`
- Delete: `src/vibeocr/core/editor_styles.py`
- Delete: `src/vibeocr/core/inline_styles.py`
- Delete: `tests/core/test_styles.py`
- Delete: `tests/core/test_inline_styles.py`

> **⚠️ 执行顺序很重要**：Task 5-9 先迁移完所有调用方（C 类引用 EditorStyles/InlineStyles），本 Task 才能安全删除源文件。但 C 类迁移依赖 theme（已完成）。实际执行时，本 Task 的"删除"步骤应放在 Task 5-9（C 类迁移）**之后**。
>
> 为降低风险，本 Task 拆为两阶段：**先改 constants.py/core/__init__.py 的非删除性修改（不影响运行）**，**删除源文件放最后**（见 Step 7-8，且需在 Task 9 之后回填）。

- [ ] **Step 1: 确认没有外部直接引用 core 包导出的旧色名**

Run: `uv run python -c "import ast, pathlib; files=list(pathlib.Path('src').rglob('*.py')); print('扫描', len(files), '文件')"`
然后搜索：

Run（用 Grep 工具）: pattern `from vibeocr.core import.*COLOR_|from vibeocr.core import.*WindowsColors|from vibeocr\.core import.*AppStyles`，path `src/vibeocr`
Expected: 仅 `core/__init__.py` 自身（即 re-export 定义点），无外部消费者。若有外部消费者，需先在对应文件迁移。

- [ ] **Step 2: 修改 constants.py — 删除旧色名**

读取 `src/vibeocr/core/constants.py`，删除以下内容：

1. 第 104-112 行的 `COLOR_*` 常量块：
```python
# 颜色常量（向后兼容 - Material Design）
COLOR_PRIMARY = "#2196F3"
COLOR_SUCCESS = "#4CAF50"
COLOR_WARNING = "#FF9800"
COLOR_ERROR = "#F44336"
COLOR_TEXT = "#212121"
COLOR_BORDER = "#E0E0E0"
COLOR_BACKGROUND = "#FFFFFF"
COLOR_HOVER = "#F5F5F5"
```

2. 第 101-102 行引用它们的兼容导出：
```python
DEFAULT_SPACING = Constants.Style.SPACING_MEDIUM
DEFAULT_MARGIN = Constants.Style.PADDING_MEDIUM
```
**保留**这两行（它们不引用 COLOR_*，仍有效）。

3. 第 118-146 行整个 `WindowsColors` 类。

- [ ] **Step 3: 修改 core/__init__.py — 删旧色名/WindowsColors/AppStyles 的 import 与 __all__**

读取 `src/vibeocr/core/__init__.py`，从 constants import 块中删除：`COLOR_BACKGROUND, COLOR_BORDER, COLOR_ERROR, COLOR_HOVER, COLOR_PRIMARY, COLOR_SUCCESS, COLOR_TEXT, COLOR_WARNING, WindowsColors`（保留 `DEFAULT_*`/`Constants`/`FileType` 等）。

删除 `from vibeocr.core.styles import AppStyles` 整行。

从 `__all__` 中删除：`"COLOR_BACKGROUND"`, `"COLOR_BORDER"`, `"COLOR_ERROR"`, `"COLOR_HOVER"`, `"COLOR_PRIMARY"`, `"COLOR_SUCCESS"`, `"COLOR_TEXT"`, `"COLOR_WARNING"`, `"WindowsColors"`, `"AppStyles"`。

- [ ] **Step 4: 验证 constants/core 修改不破坏导入**

Run: `uv run python -c "from vibeocr.core import Constants, OCRPipeline; print('core import OK')"`
Expected: 打印 `core import OK`，无 ImportError。

- [ ] **Step 5: 提交 constants + __init__ 修改（删除旧色名定义）**

```bash
git add src/vibeocr/core/constants.py src/vibeocr/core/__init__.py
git commit -m "refactor(constants): 删除 COLOR_*/WindowsColors/AppStyles 旧色名定义"
```

- [ ] **Step 6: ⚠️ 此步骤暂停——先执行 Task 5-9 完成 C 类迁移**

> 删除 `editor_styles.py`/`inline_styles.py`/`styles.py` 及其测试，必须在所有调用方迁移完毕后执行。跳到 Task 5-9，完成后回到 **Step 7**。

- [ ] **Step 7: 删除旧样式源文件及测试（在 Task 9 完成后执行）**

```bash
git rm src/vibeocr/core/styles.py
git rm src/vibeocr/core/editor_styles.py
git rm src/vibeocr/core/inline_styles.py
git rm tests/core/test_styles.py
git rm tests/core/test_inline_styles.py
```

- [ ] **Step 8: 全量导入验证**

Run: `uv run python -c "import vibeocr.core; print('all core imports OK')"`
Expected: 打印 OK，确认没有遗漏的 import 指向已删除模块。

- [ ] **Step 9: 提交删除**

```bash
git add -A
git commit -m "refactor(styles): 删除 editor_styles/inline_styles/styles 旧样式模块及测试"
```

---

## Task 5: C 类迁移 — recognition_panel.py

**Files:**
- Modify: `src/vibeocr/widgets/recognition_panel.py`

- [ ] **Step 1: 替换 import 与样式引用**

读取 `src/vibeocr/widgets/recognition_panel.py`。当前第 14 行 `from vibeocr.core.editor_styles import EditorStyles`。

替换 import 行为：
```python
from vibeocr.ui import theme
```

第 25 行 `self.setFixedWidth(EditorStyles.PANEL_WIDTH)` → `self.setFixedWidth(theme.Layout.panel_width)`

第 26 行 `self.setStyleSheet(EditorStyles.panel_style())` → `self.setStyleSheet(theme.panel_qss())`

第 43 行内联滚动区样式保留不变（已是透明，全局 QSS 会覆盖外观）。

- [ ] **Step 2: 运行测试**

Run: `uv run pytest tests/widgets/test_inline_recognition_panel.py tests/widgets/test_backend_options_widget.py -v`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/widgets/recognition_panel.py
git commit -m "refactor(recognition_panel): 迁移到 theme token（暗→浅色）"
```

---

## Task 6: C 类迁移 — edit_toolbar.py

**Files:**
- Modify: `src/vibeocr/widgets/editor/edit_toolbar.py`

- [ ] **Step 1: 替换 import 与所有 EditorStyles 引用**

读取 `src/vibeocr/widgets/editor/edit_toolbar.py`。当前第 16 行 `from vibeocr.core.editor_styles import EditorStyles`。

替换 import 行为：
```python
from vibeocr.ui import theme
```

逐行替换：
- 第 35 行 `self.setFixedHeight(EditorStyles.TOOLBAR_HEIGHT)` → `self.setFixedHeight(theme.Layout.toolbar_height)`
- 第 36 行 `self.setStyleSheet(EditorStyles.toolbar_style())` → 删除（全局 QSS + `#editorToolbar` 由 panel_qss 接管，或保留空。此处编辑器工具栏用 toolbar_button_qss 即可）。实际改为：
  ```python
  self.setStyleSheet(f"QWidget#editorToolbar {{ background: {theme.Colors.surface}; border-top: 1px solid {theme.Colors.border}; }}")
  ```
- 第 50 行 `tool_style = EditorStyles.tool_button_style()` → `tool_style = theme.toolbar_button_qss()`
- 第 87 行 `action_style = EditorStyles.action_button_style()` → `action_style = theme.toolbar_button_qss()`
- 第 112 行 `EditorStyles.confirm_button_style()` → `theme.button_qss("primary")`
- 第 116 行 `EditorStyles.cancel_button_style()` → `theme.button_qss("default")`（由全局接管，传空串或用默认）实际改为 `""`（继承全局 QPushButton 样式）
- 第 123 行 `f"color: {EditorStyles.SEPARATOR_COLOR};"` → `f"color: {theme.Colors.border};"`

- [ ] **Step 2: 运行测试**

Run: `uv run pytest tests/widgets/ -k "toolbar or editor" -v`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/widgets/editor/edit_toolbar.py
git commit -m "refactor(edit_toolbar): 迁移到 theme token（暗→浅色）"
```

---

## Task 7: C 类迁移 — tool_properties_bar.py

**Files:**
- Modify: `src/vibeocr/widgets/editor/tool_properties_bar.py`

- [ ] **Step 1: 替换 import 与 EditorStyles 引用**

读取 `src/vibeocr/widgets/editor/tool_properties_bar.py`。当前第 22 行 `from vibeocr.core.editor_styles import EditorStyles`。

替换 import 行为：
```python
from vibeocr.ui import theme
```

第 53 行 `self.setStyleSheet(EditorStyles.properties_bar_style())` → 替换为内联浅色属性条样式：
```python
self.setStyleSheet(
    f"QWidget#propertiesBar {{ background: transparent; }}"
    f" QLabel {{ color: {theme.Colors.text}; font-size: {theme.Typography.caption}px; }}"
)
```

> 注：`tool_properties_bar.py` 内部可能还有第 77/78 行附近的 `#666`/`#0078d4` 内联色（见 B 类清单提到的 checked 高亮），逐个替换：
- `#666` → `theme.Colors.text_muted`
- `#0078d4` → `theme.Colors.accent`
- `white` → `theme.Colors.surface`

用 Grep 确认：Run（Grep 工具）pattern `setStyleSheet|#666|#0078d4|white` path `src/vibeocr/widgets/editor/tool_properties_bar.py` output_mode `content -n`，逐行替换。

- [ ] **Step 2: 运行测试**

Run: `uv run pytest tests/widgets/ -k "editor or toolbar or properties" -v`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/widgets/editor/tool_properties_bar.py
git commit -m "refactor(tool_properties_bar): 迁移到 theme token（暗→浅色）"
```

---

## Task 8: C 类迁移 — inline_toolbar.py + inline_recognition_panel.py

**Files:**
- Modify: `src/vibeocr/widgets/inline_toolbar.py`
- Modify: `src/vibeocr/widgets/inline_recognition_panel.py`

- [ ] **Step 1: 迁移 inline_toolbar.py**

读取 `src/vibeocr/widgets/inline_toolbar.py`。当前第 19 行 `from vibeocr.core.inline_styles import InlineStyles`。

替换 import 行为：
```python
from vibeocr.ui import theme
```

逐行替换：
- 第 70 行 `background-color: {InlineStyles.PANEL_BG};` → `background-color: {theme.Colors.surface};`（注意这是在已有 f-string 内，需把整个 f-string 的变量源改对）
- 第 82 行 `self._top_bar.setFixedHeight(InlineStyles.TOOLBAR_HEIGHT)` → `theme.Layout.toolbar_height`
- 第 83 行 `InlineStyles.panel_style()` → 内联浅色：`f"QWidget {{ background: {theme.Colors.surface}; border: 1px solid {theme.Colors.border}; border-radius: {theme.Radius.lg}px; }}"`
- 第 93 行 `InlineStyles.tool_button_style()` → `theme.toolbar_button_qss()`
- 第 111 行 `InlineStyles.action_button_style()` → `theme.toolbar_button_qss()`
- 第 130 行 `InlineStyles.cancel_button_style()` → 内联：`f"QToolButton {{ background: transparent; color: {theme.Colors.text}; border: none; border-radius: {theme.Radius.sm}px; padding: 4px 6px; }} QToolButton:hover {{ background: {theme.Colors.danger_hover}; color: {theme.Colors.danger}; }}"`
- 第 140 行 `InlineStyles.properties_panel_style()` → 内联浅色属性面板（参照原方法结构，颜色全部用 theme token）
- 第 164 行 `f"color: {InlineStyles.SEPARATOR_COLOR};"` → `f"color: {theme.Colors.overlay};"`

> **⚠️ 关键**：`inline_toolbar.py` 的 `EdgeToolbar`/`InlineToolbar` 设了 `WA_TranslucentBackground`（见 spec 风险项 + commit `0f6e93d`）。迁移样式时**必须保留** `self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)`。用 Grep 确认该行存在，不可删除。

- [ ] **Step 2: 迁移 inline_recognition_panel.py**

读取 `src/vibeocr/widgets/inline_recognition_panel.py`。当前第 6 行 `from vibeocr.core.inline_styles import InlineStyles`。

替换 import 行为：
```python
from vibeocr.ui import theme
```

- 第 77 行 `InlineStyles.panel_style()` → 内联浅色：`f"QWidget {{ background: {theme.Colors.surface}; border: 1px solid {theme.Colors.border}; border-radius: {theme.Radius.lg}px; }}"`
- 第 79 行 `InlineStyles.recognition_button_style()` → 内联：`f"QPushButton {{ background: transparent; color: {theme.Colors.text}; border: none; border-radius: {theme.Radius.sm}px; padding: 6px; text-align: left; }} QPushButton:hover {{ background: {theme.Colors.hover_bg}; }} QPushButton:checked {{ background: {theme.Colors.accent}; color: white; }}"`

- [ ] **Step 3: 运行测试**

Run: `uv run pytest tests/widgets/test_inline_toolbar.py tests/widgets/test_inline_recognition_panel.py tests/widgets/test_edge_toolbar.py -v`
Expected: PASS（尤其 `test_edge_toolbar.py` 覆盖 WA_StyledBackground）

- [ ] **Step 4: 提交**

```bash
git add src/vibeocr/widgets/inline_toolbar.py src/vibeocr/widgets/inline_recognition_panel.py
git commit -m "refactor(inline): inline_toolbar/recognition_panel 迁移到 theme token"
```

---

## Task 9: C 类迁移 — screen_capture_overlay.py（尺寸常量）

**Files:**
- Modify: `src/vibeocr/widgets/screen_capture_overlay.py`

- [ ] **Step 1: 替换 InlineStyles 尺寸常量引用**

读取 `src/vibeocr/widgets/screen_capture_overlay.py`。当前第 32 行 `from vibeocr.core.inline_styles import InlineStyles`。

替换 import 行为：
```python
from vibeocr.ui import theme
```

逐行替换（这些是布局尺寸，非样式方法）：
- 第 697 行 `toolbar_h = InlineStyles.TOOLBAR_HEIGHT` → `toolbar_h = theme.Layout.toolbar_height`
- 第 750 行 `effect.setBlurRadius(InlineStyles.SHADOW_BLUR)` → `effect.setBlurRadius(theme.Layout.shadow_blur)`
- 第 751 行 `effect.setOffset(InlineStyles.SHADOW_OFFSET)` → `effect.setOffset(theme.Layout.shadow_offset_y)`
- 第 752 行 `effect.setColor(QColor(InlineStyles.SHADOW_COLOR))` → `effect.setColor(QColor(theme.Layout.shadow_color))`

> 第 32 行 import 可能还引入了 `InlineStyles.SELECTION_BORDER`（`#0078d4`）用于选区绘制。若有，替换为 `theme.Colors.accent`。用 Grep 确认：Run（Grep 工具）pattern `InlineStyles\.` path 该文件，确保无残留引用。

- [ ] **Step 2: 运行测试**

Run: `uv run pytest tests/widgets/test_screen_capture_overlay.py -v`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/widgets/screen_capture_overlay.py
git commit -m "refactor(screen_capture): 迁移 InlineStyles 尺寸常量到 theme.Layout"
```

- [ ] **Step 4: 回到 Task 4 的 Step 7，删除旧样式源文件**

此时所有 EditorStyles/InlineStyles 调用方已迁移完毕。执行 Task 4 Step 7-9。

- [ ] **Step 5: 全局确认无残留 import**

Run（Grep 工具）pattern `EditorStyles|InlineStyles|AppStyles|from vibeocr\.core\.styles|from vibeocr\.core\.editor_styles|from vibeocr\.core\.inline_styles` path `src`
Expected: 无匹配（全部迁移完毕）

---

## Task 10: B 类迁移 — chat_widget / preview_widget / clipboard_controller / qrcode_tab

**Files:**
- Modify: `src/vibeocr/widgets/chat_widget.py`
- Modify: `src/vibeocr/widgets/preview_widget.py`
- Modify: `src/vibeocr/views/clipboard_controller.py`
- Modify: `src/vibeocr/views/tabs/qrcode_tab.py`

- [ ] **Step 1: 迁移 chat_widget.py**

读取 `src/vibeocr/widgets/chat_widget.py`，在各 `setStyleSheet` 处替换：
- 第 38 行 `role_label.setStyleSheet("font-size: 11px; color: #666;")` → `f"font-size: {theme.Typography.caption}px; color: {theme.Colors.text_muted};"`（顶部加 `from vibeocr.ui import theme`）
- 第 54-60 行用户气泡：`#E3F2FD` → `theme.Colors.accent_soft`
- 第 62-68 行 AI 气泡：`#F5F5F5` → `theme.Colors.surface_alt`
- 第 100-101 行滚动区：`background: white` → `background: {theme.Colors.surface}`

- [ ] **Step 2: 迁移 preview_widget.py**

读取 `src/vibeocr/widgets/preview_widget.py`，替换内联色：
- `#f0f0f0` → `theme.Colors.surface_alt`
- `#ccc` → `theme.Colors.border`
- `#ff9800` → `theme.Colors.warning`
- `#ddd` → `theme.Colors.border`
- `#fff` → `theme.Colors.surface`

顶部加 `from vibeocr.ui import theme`。

- [ ] **Step 3: 迁移 clipboard_controller.py**

读取 `src/vibeocr/views/clipboard_controller.py`，第 42 行的 toast 大段 QSS：
- `#333333` → `theme.Colors.text`
- `white` → `theme.Colors.surface`

顶部加 `from vibeocr.ui import theme`。

- [ ] **Step 4: 迁移 qrcode_tab.py**

读取 `src/vibeocr/views/tabs/qrcode_tab.py`，替换内联色：
- 第 170 行类型标签：`#e0e0e0` → `theme.Colors.hover_bg`，`#444` → `theme.Colors.text`
- 第 256 行预览区：`#f5f5f5` → `theme.Colors.surface_alt`，`#ddd` → `theme.Colors.border`
- 第 377/382 行拾色按钮 `_color_btn_style(color)`：保持 f-string 动态色，但固定部分引用 token
- 第 450 行 hint：`#888` → `theme.Colors.text_muted`
- 第 468 行结果计数：`#888` → `theme.Colors.text_muted`
- `#1976D2`/`#f44336` 等业务色按需映射到 `theme.Colors.accent`/`theme.Colors.danger`

顶部加 `from vibeocr.ui import theme`。

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/widgets/test_chat_widget.py tests/widgets/test_preview_widget.py tests/views/ -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/widgets/chat_widget.py src/vibeocr/widgets/preview_widget.py src/vibeocr/views/clipboard_controller.py src/vibeocr/views/tabs/qrcode_tab.py
git commit -m "refactor(widgets): B 类内联样式迁移到 theme token"
```

---

## Task 11: A 类清理 — 删除零星 setStyleSheet

**Files:**
- Modify: `src/vibeocr/views/batch_recognition_tab.py`
- Modify: `src/vibeocr/widgets/backend_options_widget.py`
- Modify: `src/vibeocr/widgets/preprocess_options_widget.py`
- Modify: `src/vibeocr/services/update_service.py`
- Modify: `src/vibeocr/widgets/screen_capture_overlay.py`（transparent 那行）
- Modify: `src/vibeocr/widgets/inline_edit_canvas.py`

- [ ] **Step 1: 删除各文件的零星 setStyleSheet**

这些文件的内联样式会被全局 QSS 覆盖，直接删除：

- `batch_recognition_tab.py`：删除第 202 行 `self._progress_label.setStyleSheet("color: #3b82f6; font-weight: bold;")`（全局 QSS 无蓝色文字，改为 token：`f"color: {theme.Colors.accent}; font-weight: bold;"`）和第 223/238 行的 `font-weight: bold; color: #555;`（→ `f"font-weight: bold; color: {theme.Colors.text_muted};"`）
- `backend_options_widget.py`：第 78 行 `"color: #666;"` → `f"color: {theme.Colors.text_muted};"`（或删除，全局已处理；保留 token 化更明确）
- `preprocess_options_widget.py`：第 63 行 `self._pipeline_lock_label.setStyleSheet("color: #888; font-size: 11px;")` 和第 88 行 `self._source_hint_label.setStyleSheet("color: #888; font-size: 11px;")` → 均改为 `f"color: {theme.Colors.text_muted}; font-size: {theme.Typography.caption}px;"`
- `update_service.py`：第 335/340/356 行的 `font-size`/`font-weight`/`color: gray` → token 化或删除（`gray` → `theme.Colors.text_muted`）
- `screen_capture_overlay.py`：transparent 那行保留（功能性透明，非配色）
- `inline_edit_canvas.py`：transparent 那行保留

每个文件顶部按需加 `from vibeocr.ui import theme`。

- [ ] **Step 2: 运行相关测试**

Run: `uv run pytest tests/ -k "batch or backend or preprocess or update or canvas" -v`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/views/batch_recognition_tab.py src/vibeocr/widgets/backend_options_widget.py src/vibeocr/widgets/preprocess_options_widget.py src/vibeocr/services/update_service.py src/vibeocr/widgets/inline_edit_canvas.py
git commit -m "refactor(cleanup): A 类零星内联样式迁移到 theme token"
```

---

## Task 12: 全量验证与收尾

**Files:**
- 无修改（验证任务）

- [ ] **Step 1: 全局确认无残留旧色值**

Run（Grep 工具）：pattern `#0078d4|#2196F3|#4CAF50|#1a1a1a|#2d2d2d|#f5f5f5|#e0e0e0|#d0d0d0` path `src/vibeocr`（排除 theme.py 自身）
Expected: 应无匹配，或仅剩业务必需的动态色（如二维码 `#000000`/`#FFFFFF`）。

如有残留的硬编码颜色，确认是否业务必需。非必需的替换为 token。

- [ ] **Step 2: 全局确认无残留旧样式 import**

Run（Grep 工具）：pattern `editor_styles|inline_styles|from vibeocr\.core\.styles|WindowsColors|COLOR_PRIMARY|COLOR_SUCCESS|COLOR_WARNING|COLOR_ERROR|COLOR_TEXT|COLOR_BORDER|COLOR_BACKGROUND|COLOR_HOVER` path `src`
Expected: 无匹配。

- [ ] **Step 3: 运行完整测试套件**

Run: `uv run pytest tests/ -v`
Expected: 全部 PASS（或仅有与本次无关的 pre-existing 失败，需确认）

- [ ] **Step 4: 运行静态检查**

Run: `uv run ruff check src/vibeocr/ui/theme.py src/vibeocr/views/tabs/about_tab.py src/vibeocr/widgets src/vibeocr/views`
Expected: 无错误

Run: `uv run mypy src/vibeocr/ui/theme.py src/vibeocr/views/tabs/about_tab.py`
Expected: 无错误（或仅 pre-existing 警告）

- [ ] **Step 5: 启动冒烟测试**

Run: `uv run python -m vibeocr.main`
Expected: 应用正常启动，无 QSS 解析警告（stderr 无 Qt stylesheet warnings），关于页显示三张卡片、图标、版本徽标。

- [ ] **Step 6: 逐 tab 目检（人工）**

启动后切换每个 tab，确认：
- 单次识别：预览/结果区正常
- 批量识别：进度标签、文件列表正常
- 二维码：预览区、拾色按钮正常
- PDF 处理：正常
- 设置：分组框、导航列表正常
- 关于：三卡片、图标 96×96、版本药丸徽标、检查更新按钮为 primary 蓝

- [ ] **Step 7: 编辑器目检（人工）**

触发截图（Ctrl+S）→ 进入编辑器，确认：
- 底部工具栏浅色、按钮文字深色可读
- 属性条浅色
- 右侧识别面板浅色（原暗色 `#2d2d2d` → 白底）
- 确认识别按钮为蓝色 primary

- [ ] **Step 8: 最终提交（如有收尾改动）**

```bash
git add -A
git commit -m "chore(theme): 浅色主题统一迁移收尾验证"
```
（若无改动则跳过）

---

## 自审记录

（计划编写者填写：对照 spec 检查覆盖率、占位符、类型一致性）

**Spec 覆盖检查：**
- ✅ 3.1 架构（新建 theme.py + ui/__init__.py，删 4 源，改 main/constants/__init__）→ Task 1, 2, 4
- ✅ 3.2 token 设计（Colors/Spacing/Radius/Typography/Shadow/Layout）→ Task 1
- ✅ 3.3 关于页卡片化（品牌/信息/日志 3 卡 + 徽标 + FormLayout）→ Task 3
- ✅ 3.4 全局 QSS + A/B/C 三类迁移 → Task 2, 10, 11（A/B）, 5-9（C）
- ✅ 4 迁移顺序（地基→全局→关于页→清源→A→B→C）→ Task 序号对应，注意 Task 4 拆两阶段
- ✅ 5 风险控制（WA_StyledBackground → Task 8 Step 1 注明；import grep → Task 4/9/12）→ 各 Task 验证步骤
- ✅ 6 验证（冒烟/tab 目检/编辑器目检/pytest/ruff/mypy）→ Task 12

**类型一致性检查：**
- `theme.button_qss("primary"|"default"|"danger")` — Task 1 定义，Task 3/6 调用 ✓
- `theme.panel_qss(object_name=)` — Task 1 定义，Task 5 调用 `theme.panel_qss()` ✓
- `theme.toolbar_button_qss()` — Task 1 定义，Task 6/8 调用 ✓
- `theme.card_qss()` — Task 1 定义，Task 3 调用 ✓
- `theme.Layout.toolbar_height/panel_width/panel_min_width/shadow_*` — Task 1 定义，Task 5/8/9 调用 ✓
