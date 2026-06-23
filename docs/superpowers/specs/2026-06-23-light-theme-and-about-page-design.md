# 浅色主题统一 + 关于页卡片化设计

> 日期: 2026-06-23
> 状态: 设计中

## 1. 问题

应用当前没有任何全局主题/QSS。`main.py:launch_application()` 只调用
`QApplication(sys.argv)`，跑的是 Qt 默认 Windows 原生控件风格——这是界面
"显丑"的根本来源。样式零散硬编码在 17 个文件、共 57 处 `setStyleSheet`
调用里，颜色随手写（`#3b82f6`、`#555`、`#888`、`#0078d4`、`#e0e0e0`、
`#f5f5f5`……），没有统一的设计 token。

现状存在**两套并行主题**：

| 模块 | 风格 | 用途 |
|------|------|------|
| `core/editor_styles.py` | **暗色**（`#1a1a1a`/`#2d2d2d`/`#404040`） | 截图编辑器工具栏 / 右侧识别面板 |
| `core/inline_styles.py` | **浅色毛玻璃**（`#f5f5f5`/`#d0d0d0`） | 内联识别浮窗工具栏 |
| 其余 15 个文件 | 零散字面色值 | 各写各的 |

`core/constants.py` 底部还残留一套 Material 色 `COLOR_*` 和 `WindowsColors`
类，与上述两套并存，实际无人系统化引用。

**关于页**（`about_tab.py`）是本次重点交付的"可见效果"入口。现状为 6 个
`QGroupBox` 竖排（简介/技术栈/作者/版权/项目链接/更新日志），每个框都有
原生粗边框和标题，又丑又割裂；纯文字堆叠，没有用上 `resources/app_icon.ico`。

## 2. 目标

1. 建立**唯一**的浅色 token 源（`ui/theme.py`），收敛 4 处色源。
2. 全局 QSS 由 `main.py` 加载一次，统一所有控件的默认外观。
3. **关于页**卡片化重写，作为本次核心交付。
4. 其余 16 个文件的内联 `setStyleSheet` 迁移到 theme token。
5. 删除旧色源：`editor_styles.py`、`inline_styles.py`、`constants.py` 的
   `COLOR_*`/`WindowsColors`。连 import 一次清干净，**不留别名**。
6. 编辑器从暗色统一改为浅色。

**不做（YAGNI，明确排除）：**

- 不做深色主题 / 主题切换 UI（本次仅浅色单套）
- 不重排其余 tab 的布局结构（仅样式 token 迁移，关于页除外）
- 不引入新依赖（不装 `qfluentwidgets` 等）
- 不改 `ui_main_window.py`（Qt Designer 生成代码，靠全局 QSS 覆盖）
- 不新增视觉回归测试

## 3. 设计

### 3.1 架构

**新增**
- `src/vibeocr/ui/theme.py` — 唯一 token 源 + QSS 模板生成器（纯 Python，
  不引入 `.qss` 文件，便于打包、便于动态颜色如二维码拾色器引用 token）
- `src/vibeocr/ui/__init__.py`（将 `ui/` 变为包）

**删除**
- `src/vibeocr/core/editor_styles.py`
- `src/vibeocr/core/inline_styles.py`

**改动**
- `src/vibeocr/core/constants.py` — 删除 `COLOR_*` 旧 Material 色、
  `WindowsColors` 类；`Constants.Style` 的 spacing token 改为引用 theme
- `src/vibeocr/main.py` — `QApplication` 创建后
  `app.setStyleSheet(theme.global_qss())`，加载 `resources/app_icon.ico`
  为 app/窗口图标
- `src/vibeocr/views/tabs/about_tab.py` — 卡片化重写（本次重点交付）
- 其余 15 个文件的 `setStyleSheet` 迁移到 theme token

**纯 Python theme.py（不引入 .qss 文件）的决策依据**：二维码 tab 的前景/
背景色拾取按钮需要动态颜色（`_color_btn_style(color)`），用 f-string 引用
`theme.Colors.accent` 比读静态 `.qss` 灵活；打包成 exe 时少一类资源文件。
代价：全局 QSS 是一个大字符串，但结构化分段 + token 引用仍可读。

### 3.2 Token 设计（`ui/theme.py`）

把 4 处色源统一收敛成一套**语义化浅色 token**：

```python
# src/vibeocr/ui/theme.py
from __future__ import annotations


class Colors:
    """语义色 token（浅色单一套）"""
    # 背景层
    bg          = "#f3f4f6"   # 应用底色（原 #f0f0f0 / #f5f5f5 收敛）
    surface     = "#ffffff"   # 卡片/面板表面
    surface_alt = "#f9fafb"   # 次级面板（嵌套分组、表头）

    # 文字
    text        = "#1f2937"   # 主文字（原 #333/#212121 收敛）
    text_muted  = "#6b7280"   # 次文字（原 #666/#555/#888/#999 收敛）
    text_subtle = "#9ca3af"   # 占位/禁用（原 #aaa/#ccc）

    # 边框
    border        = "#e5e7eb"   # 常规边框（原 #ddd/#ccc/#d0d0d0 收敛）
    border_strong = "#d1d5db"   # 输入框/聚焦态边框

    # 强调（统一用 Windows 蓝，向后兼容）
    accent        = "#0078d4"   # 原 WindowsColors.PRIMARY
    accent_hover  = "#106ebe"   # 原 PRIMARY_HOVER
    accent_soft   = "#e3f2fd"   # 选中行/链接淡底（原 chat 用户气泡）

    # 语义
    success       = "#107c10"   # 原 SUCCESS
    success_hover = "#0b6a0b"
    warning       = "#f7630c"   # 原 ACCENT
    danger        = "#c83232"   # 原 cancel 红字
    danger_hover  = "#d6550a"

    # 透明叠加
    overlay    = "rgba(0,0,0,0.30)"  # inline 分隔线
    hover_bg   = "#e8e8e8"           # inline 按钮悬停（原 #e8e8e8）
    pressed_bg = "#dcdcdc"


class Spacing:
    """间距 scale（替代散乱的 9/16/20）"""
    xs, sm, md, lg, xl, xxl = 4, 8, 12, 16, 24, 32


class Radius:
    sm, md, lg = 4, 6, 8


class Typography:
    title   = 24  # 关于页应用名
    h1      = 16  # 卡片标题
    body    = 14
    small   = 12  # 工具栏
    caption = 11  # 属性条
    weight_bold   = 700
    weight_medium = 500


class Shadow:
    blur, offset_y, color = 12, 2, "rgba(0,0,0,0.08)"


def global_qss() -> str:               # 全局基础样式（控件级），见 3.4
    ...
def card_qss() -> str:                 # 关于页/设置卡片
    ...
def button_qss(variant: str) -> str:   # variant ∈ {"default","primary","danger"}
    ...
def toolbar_button_qss() -> str:       # QToolButton（统一原 editor/inline 两套）
    ...
def panel_qss() -> str:                # 编辑器右侧识别面板（现为浅色）
    ...
```

**收敛对照**（核心收益）：

| 旧（散落） | 新 token |
|---|---|
| `#f0f0f0` / `#f5f5f5` / `#e0e0e0` | `bg` / `hover_bg` |
| `#ddd` / `#ccc` / `#d0d0d0` / `#c0c0c0` | `border` / `border_strong` |
| `#333` / `#555` / `#666` / `#888` / `#999` | `text` / `text_muted` / `text_subtle` |
| `#0078d4`（editor+inline+tool_properties 各写） | `accent` |
| `WindowsColors.PRIMARY` / `COLOR_PRIMARY` | `accent` |

**不保留任何别名**：`constants.py` 的 `COLOR_*`/`WindowsColors` 直接删除，
所有调用方 import 一次迁移到 `theme`。

### 3.3 关于页卡片化布局（核心交付）

`about_tab.py` 重写为单一纵向滚动卡片流，全部引用 theme token：

```
┌───────────── 关于（QScrollArea，无边框，居中，最大宽 720px）──────────────┐
│                                                                       │
│   ┌─ 品牌卡片 ─────────────────────────────────────────────┐         │
│   │              [图标 80×80]   ← resources/app_icon.ico    │         │
│   │                                                        │         │
│   │                 VibeOCR           (24pt bold, text)     │         │
│   │                  v1.2.3          (accent 色药丸徽标)    │         │
│   │   ─────────────────────────────────────────────────    │         │
│   │   基于 PaddleOCR 的截图文字识别工具，支持表格、公式、    │         │
│   │   文档解析等功能。            (body, text_muted, 居中)  │         │
│   └────────────────────────────────────────────────────────┘         │
│                                                                       │
│   ┌─ 详细信息卡片 ─────────────────────────────────────────┐         │
│   │  作者      Felix Ji                                    │         │
│   │  版权      © 2025 Felix Ji. All rights reserved.       │         │
│   │  技术栈    PaddlePaddle/PaddleX · MinerU · PySide6     │         │
│   │  项目      github.com/felixji/vibeocr  (accent 下划线) │         │
│   │  ── 键值对 QFormLayout，label=text_muted，value=text ──│         │
│   └────────────────────────────────────────────────────────┘         │
│                                                                       │
│   ┌─ 更新日志卡片 ─────────────────────────────────────────┐         │
│   │  更新日志                          (h1 bold, 标题行)    │         │
│   │  ## v1.2.3                                           │         │
│   │  - 修复 PDF…          (QTextBrowser，透明背景无框，    │         │
│   │                       高度自适应上限 320px)            │         │
│   └────────────────────────────────────────────────────────┘         │
│                                                                       │
│                    [  检查更新  ]   (primary 按钮居中)                 │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

**关键改动点**（对照现状的 6 个丑点）：

| 现状丑点 | 重写方案 |
|---|---|
| 无图标，纯文字堆叠 | 品牌卡顶部居中 `app_icon.ico` 缩放 80×80 |
| 6 个 `QGroupBox` 竖排，双层粗边框 | 3 张卡片，`card_qss()`：`surface` 底、`border` 1px、`radius.lg`、`Spacing.lg` 内边距 |
| 版本号灰字 `color: gray` | 改成 `accent` 色药丸徽标（圆角小药丸 `accent_soft` 底 + `accent` 字） |
| 作者/版权/技术栈/链接拆成 4 个框 | 合并成 1 张"详细信息"卡，`QFormLayout` 键值对，label 用 `text_muted` |
| 更新日志 `QTextBrowser` 写死 `maxHeight=300` 嵌在 GroupBox 双层边框 | 独立卡片，`setMaximumHeight(320)`，背景透明融入卡片 |
| 按钮孤零零底部居中、无层级 | `button_qss("primary")`：`accent` 实心填充、白字、`radius.md`、固定宽 ~140px |

**居中策略**：`QScrollArea` 内 `container` 用 `QHBoxLayout` 包一层，左右
`addStretch()`，`container.setMaximumWidth(720)`，保证宽屏下卡片不拉满、
视觉居中。

**图标加载**：`resources/app_icon.ico` 经 `env_manager.get_project_root()`
定位；打包环境（`sys.frozen`）走 `sys._MEIPASS` 兜底，复用 env_manager
已有资源定位逻辑，不新造轮子。

**数据不变**：`_APP_NAME`/`_DESCRIPTION`/`_AUTHOR`/`_COPYRIGHT`/
`_GITHUB_URL`/`_TECH_STACK`/`__version__`/CHANGELOG 读取逻辑全部保留，
只改呈现。

### 3.4 全局 QSS 与其余 15 个文件迁移

**`global_qss()` 内容**（`main.py` 加载一次，作用于所有控件默认外观）：

```python
def global_qss() -> str:
    c, s, r, t = Colors, Spacing, Radius, Typography
    return f"""
    /* —— 基础 —— */
    QWidget        {{ background: {c.bg}; color: {c.text}; font-size: {t.body}px; }}
    QToolTip       {{ background: {c.text}; color: {c.surface}; border: none; padding: {s.xs}px; }}

    /* —— 按钮 —— */
    QPushButton    {{ background: {c.surface}; color: {c.text};
                     border: 1px solid {c.border}; border-radius: {r.md}px;
                     padding: 6px 14px; }}
    QPushButton:hover    {{ background: {c.hover_bg}; border-color: {c.border_strong}; }}
    QPushButton:pressed  {{ background: {c.pressed_bg}; }}
    QPushButton:disabled {{ color: {c.text_subtle}; background: {c.surface_alt}; }}

    /* —— 输入控件 —— */
    QLineEdit, QSpinBox, QFontComboBox, QComboBox {{
        background: {c.surface}; color: {c.text};
        border: 1px solid {c.border}; border-radius: {r.sm}px; padding: 4px 8px;
    }}
    *:focus  {{ border-color: {c.accent}; }}
    QComboBox QAbstractItemView {{ background: {c.surface};
                                    selection-background-color: {c.accent_soft}; }}

    /* —— 容器 —— */
    QGroupBox {{ background: {c.surface}; border: 1px solid {c.border};
                 border-radius: {r.md}px; margin-top: {s.md}px; padding-top: {s.sm}px; }}
    QGroupBox::title {{ color: {c.text_muted}; subcontrol-origin: margin; left: {s.sm}px; }}

    /* —— 列表 / 滚动 —— */
    QListWidget, QScrollArea {{ background: {c.surface}; border: 1px solid {c.border}; }}
    QListWidget::item:selected {{ background: {c.accent_soft}; color: {c.text}; }}

    /* —— Tab —— */
    QTabWidget::pane   {{ border: 1px solid {c.border}; }}
    QTabBar::tab       {{ padding: 8px {s.lg}px; border: 1px solid {c.border};
                          background: {c.surface_alt}; }}
    QTabBar::tab:selected {{ background: {c.surface}; border-bottom: 2px solid {c.accent}; }}

    /* —— 进度 / 滚动条 —— */
    QProgressBar       {{ background: {c.surface_alt}; border: 1px solid {c.border};
                          border-radius: {r.sm}px; text-align: center; }}
    QProgressBar::chunk {{ background: {c.accent}; border-radius: {r.sm}px; }}
    /* QScrollBar:* 简洁细条，accent_soft handle */
    """
```

全局 QSS 一旦生效，许多零散内联可**直接删除**——因为控件默认就吃全局样式。
这是"迁移"的主要工作量分布。

**15 个文件的迁移分类与处理**：

| 类别 | 文件 | 处理 |
|---|---|---|
| **A. 删除即可**（吃全局默认） | `batch_recognition_tab`、`backend_options_widget`、`preprocess_options_widget`、`update_service`、`screen_capture_overlay`、`inline_edit_canvas` | 删掉零星 `setStyleSheet`，靠全局 QSS |
| **A+. 卡片化重写**（核心交付） | `about_tab` | 见 3.3，独立卡片化重写，引用 `card_qss`/`button_qss` |
| **B. 改引用 theme**（语义明确） | `chat_widget`（气泡→`accent_soft`/`surface_alt`）、`preview_widget`（空态→`surface_alt`+`border`虚线）、`clipboard_controller`（toast→`text`底+白字+`Shadow`）、`qrcode_tab`（拾色按钮 `_color_btn_style`→f-string 引 `accent`，类型标签→`surface_alt`） | 局部样式改用 token |
| **C. 工厂方法迁移**（原 editor/inline） | `edit_toolbar`、`tool_properties_bar`、`inline_toolbar`、`recognition_panel`、`inline_recognition_panel` | 删除对 `EditorStyles`/`InlineStyles` 的 import，改用 `theme.toolbar_button_qss()`/`panel_qss()` 等。**编辑器从暗色变浅色** |

**编辑器变浅色的连带影响**（C 类，需留意）：

- `recognition_panel.py` 的 `panel_style()` 原是 `#2d2d2d` 黑底 →
  改 `theme.panel_qss()`：`surface` 白底、`border`、左侧分隔。
- `edit_toolbar.py` / `tool_properties_bar.py` 的按钮文字色从
  `#e0e0e0`（暗底白字）→ `text`（浅底深字）。
- `inline_toolbar.py` 已是浅色，迁移成本低，主要是统一到 token。

**打包资源**：`app_icon.ico` 等已有 `env_manager` 资源定位，本次不新增
资源文件。

## 4. 迁移顺序

先建地基，再自底向上，每步可独立验证：

1. **建地基**：新建 `ui/theme.py`（token + `global_qss` +
   `card_qss`/`button_qss`/`toolbar_button_qss`/`panel_qss`）+
   `ui/__init__.py`
2. **加载全局**：`main.py` 接入 `app.setStyleSheet(theme.global_qss())` +
   设置 `app_icon.ico`
3. **关于页重写**：`about_tab.py` 卡片化（核心交付，先让"可见效果"落地）
4. **清旧色源**：`constants.py` 删 `COLOR_*`/`WindowsColors`；删
   `editor_styles.py`/`inline_styles.py`
5. **A 类**（删即可）：6 个文件删零星 `setStyleSheet`
6. **B 类**（改引用）：4 个文件改用 token
7. **C 类**（编辑器迁移）：5 个文件换工厂方法，暗→浅

## 5. 风险控制

| 风险 | 控制 |
|------|------|
| 删 `WindowsColors`/`COLOR_*`/`EditorStyles`/`InlineStyles` 后漏改 import 导致 ImportError | 每删一个符号前先全局 grep 其引用，确认全部迁移完毕再删；用 `ruff`/`mypy` 兜底 |
| 编辑器暗→浅色后，某些暗底设计的细节（选区描边、阴影）在浅底下不好看 | 实现后实际跑一次截图编辑器人工验证；选区边框保留 `accent` 实色 |
| 全局 QSS 影响所有控件，可能与个别内联样式冲突（QSS 叠加优先级） | 遵循"内联仅在必要时覆盖全局"；迁移完成后 grep 确认剩余 `setStyleSheet` 都是有意为之的局部覆盖 |
| `app_icon.ico` 在打包/开发环境路径差异 | 复用 `env_manager` 现有资源定位，不新造 |

## 6. 验证

本次无自动化视觉测试，以人工 + 现有测试为准：

1. **启动冒烟**：`uv run python -m vibeocr.main`，确认应用能正常启动、
   无 QSS 解析报错（QSS 语法错 Qt 会在 stderr 打警告）。
2. **逐 tab 目检**：单次识别、批量识别、二维码、PDF 处理、设置、关于——
   每个 tab 切一遍，确认无错位、无残留暗色、控件可交互。
3. **编辑器目检**：触发截图→进编辑器，确认工具栏/属性条/识别面板是浅色、
   按钮可点。
4. **现有单元测试**：`uv run pytest`（尤其 OCR/tab/widget 相关），确认迁移
   没破坏业务逻辑（样式改动不应影响逻辑，但 import 路径变更可能波及）。
5. **静态检查**：`uv run ruff check` + `uv run mypy`，清理未用 import 和
   类型问题。
