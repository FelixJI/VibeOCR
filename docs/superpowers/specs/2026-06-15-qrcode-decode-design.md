# 二维码识别功能与标签页重构设计

> 日期: 2026-06-15
> 状态: 待审核

## 背景

当前应用有"二维码生成"标签页（`src/vibeocr/views/tabs/qrcode_tab.py`，顶层标题 `"二维码生成"`，见 `main_window.py:288`），仅支持生成二维码与条形码，**无任何识别/解码能力**。

用户需求：

1. 标签页名称改为 **"二维码"**。
2. 在原二维码生成界面加入**二维码识别功能**。
3. 左侧预览区域支持**粘贴或拖入图片**。
4. 右侧做成**两个小标签页**：一个生成、一个识别。
5. 识别结果如果是链接，支持**点击调用系统浏览器打开**。

### 现状关键发现

- 现有 `QrcodeTab` 是单层 `QSplitter`（左预览 QLabel + 右控制面板），**不继承 `BaseOcrTab`**，自带 `QrcodeService` 实例和 300ms debounce 刷新机制。
- 项目里**无嵌套 `QTabWidget` 先例**（所有 tab 都是顶层 `main_window.tabWidget` 的直接子页）。
- 项目里**无"粘贴图片""拖入图片数据"的先例**：`single_recognition_tab._on_paste` 用 `clipboard.pixmap()` 但只用于 OCR；`batch_file_list_widget` 的拖入只处理文件 URL（`hasUrls()`），不处理 `hasImage()`。`qrcode_tab._on_paste_from_clipboard` 只粘贴文本。
- 解码库 **`pyzbar` 未安装、未声明**；`cv2.QRCodeDetector` 可用但仅支持二维码。
- 项目无"打开外部 URL"先例；标准做法是 `QDesktopServices.openUrl(QUrl(...))`。
- 测试用 `pytest-qt`（`qt_api = "pyside6"`），`qtbot.addWidget` + `findChild` by objectName 模式。

## 设计

### 1. 整体架构

把原 `QrcodeTab` 重构为统一"二维码"标签页，内嵌一个 `QTabWidget`（**项目首个嵌套子标签页先例**），左侧预览区**单一 QLabel 共享**，右侧两个子页：**生成**（原功能完整保留）和 **识别**（新增）。

```
顶层 tabWidget (main_window.py)
└─ [二维码] tab  ← 标题由 "二维码生成" 改为 "二维码"
   └─ QrcodeTab (重构后)
      └─ QSplitter(水平) [500, 300]
         ├─ 左:共享预览面板 QWidget
         │   ├─ _preview_label (DropLabel 子类, objectName="previewLabel")
         │   └─ 操作栏(随子标签页切换显示不同按钮组)
         └─ 右:_sub_tabs (QTabWidget, objectName="subTabs")
            ├─ [生成] _generate_panel (原 qrcode_tab 右侧控制面板整体搬入)
            └─ [识别] _decode_panel (新增)
```

**关键设计决策：**

- **左侧预览区共享**：单一 `QLabel`，切换子标签页时通过 `_on_sub_tab_changed(index)` 保存当前子页预览状态、恢复另一个子页预览。生成子页显示二维码；识别子页显示待识别图片。
- **操作栏按钮两套并存，按子页可见性切换**：生成页显示"保存/复制到剪贴板"；识别页显示"粘贴图片/选择图片/识别/清空"。
- **拖入和 Ctrl+V 仅在识别子页激活时生效**，避免与生成子页（也用 Ctrl+V 粘贴文本）冲突。

### 2. 新增服务：QrcodeDecodeService

**文件：** `src/vibeocr/services/qrcode_decode_service.py`（新建）

遵循 `QrcodeService` 风格：纯 Python 类、无 Qt 依赖、懒加载重库、让异常上抛。

```python
from dataclasses import dataclass

@dataclass
class DecodedItem:
    data: str          # 解码内容（utf-8 解码，errors="replace"）
    type: str          # "QRCODE" | pyzbar 的 type 字符串（如 "EAN13", "CODE128"）
    is_url: bool       # 是否为 http/https URL

class QrcodeDecodeService:
    def default_options(self) -> dict: ...        # 选项契约（预留，目前空或仅含元信息）
    def decode(self, image: PIL.Image.Image) -> list[DecodedItem]: ...
    def decode_bytes(self, data: bytes) -> list[DecodedItem]: ...
    def decode_file(self, path: str) -> list[DecodedItem]: ...
```

**实现要点：**

- `decode`：PIL 图转灰度（`image.convert("L")`，若已是 L 跳过），调用 `from pyzbar.pyzbar import decode as _zbar_decode; results = _zbar_decode(img)`，把每个 `pyzbar.Decoded` 映射成 `DecodedItem`：`data=d.data.decode("utf-8", errors="replace")`、`type=d.type`、`is_url=_is_http_url(data)`。
- `_is_http_url(value)`：`value.startswith(("http://","https://")) and urllib.parse.urlparse(value).scheme in ("http","https") and bool(urllib.parse.urlparse(value).netloc)`。严格 scheme 校验避免 `javascript:`、`file:` 等被当链接（安全考虑）。
- **大图保护**：解码前若任一边 > 4096px，先 `image.thumbnail((4096, 4096))`（生成副本，不改原图），防止 pyzbar OOM/超时。
- **空结果不抛异常**：返回空 list，UI 层提示"未识别到"。
- **过滤空内容**：`data.strip() == ""` 的结果跳过。
- 异常（`ImportError` 等）上抛，UI 层 try/except 显示红字错误。
- `decode_bytes`：`from io import BytesIO; img = Image.open(BytesIO(data))` → `decode(img)`。
- `decode_file`：`img = Image.open(path)` → `decode(img)`。

### 3. UI：识别子页（_decode_panel）

右侧子标签页内的 `QWidget`，QVBoxLayout，边距 8/4/8/4，间距 8。

```
┌─ 识别子页（_decode_panel）─────────────────────────┐
│ 输入说明（小字灰）:                                 │
│  支持粘贴图片(Ctrl+V)、拖入图片到左侧预览区、      │
│  或点击下方选择文件                                 │
│                                                    │
│ [粘贴图片][选择图片...]            [🔍识别][清空] │
│                                                    │
│ 识别结果                                           │
│ ┌──────────────────────────────────────────────┐  │
│ │ ① [二维码] https://example.com  🔗打开 📋复制│  │ ← URL:可点击
│ │ ② [条形码] 6901234567890         📋复制      │  │ ← 非 URL:纯文本
│ └──────────────────────────────────────────────┘  │
│                              [复制全部]  识别到 N条│
└────────────────────────────────────────────────────┘
```

**控件清单：**

| 控件 | objectName | 类型 | 说明 |
|---|---|---|---|
| 输入说明 | — | QLabel | 灰色小字说明文字 |
| 粘贴图片 | `btnPasteImg` | QPushButton(h=28) | 从剪贴板粘贴图片 |
| 选择图片 | `btnSelectImg` | QPushButton(h=28) | 文件对话框选择图片 |
| 识别 | `btnDecode` | QPushButton(h=28) | **默认禁用**，有图后启用 |
| 清空 | `btnClear` | QPushButton(h=28) | 清空预览与结果 |
| 识别结果列表 | `decodeResultList` | QListWidget | 每条一个 item，自定义 widget 展示 |
| 复制全部 | `btnCopyAll` | QPushButton(h=26) | 复制所有结果到剪贴板 |
| 结果计数 | — | QLabel | 显示"识别到 N 条结果" |

### 4. 自定义 widget：DecodeResultWidget

用于 `decodeResultList` 的列表项展示 widget（每行一条结果）：

```python
class DecodeResultWidget(QWidget):
    """单条识别结果展示：序号 + 类型标签 + 内容/链接 + 打开/复制按钮"""
    open_url_requested = Signal(str)
    copy_requested = Signal(str)
```

- 布局：`QHBoxLayout`，从左到右：序号 QLabel(固定宽)、类型标签 QLabel(灰底圆角小标签)、内容 QLabel(stretch=1)、操作按钮。
- **URL 项**：内容 QLabel 用富文本 `<a href="URL" style="color:#1976D2;">显示文本</a>`，`setOpenExternalLinks(False)`，连接 `linkActivated` → `open_url_requested`。额外一个"🔗打开"按钮也触发 `open_url_requested`。
- **非 URL 项**：内容 QLabel 显示纯文本，用 `setTextInteractionFlags(Qt.TextSelectableByMouse)` 允许选中复制；无"打开"按钮，仅"📋复制"。
- "📋复制"按钮 → `copy_requested`。

### 5. 左侧共享预览区：粘贴/拖入图片（新功能）

**新增模块内 helper 类 `DropLabel(QLabel)`：**

```python
class DropLabel(QLabel):
    """支持拖入图片数据的 QLabel。"""
    imageDropped = Signal(QPixmap)

    def dragEnterEvent(self, event):
        if event.mimeData().hasImage():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        pm = QPixmap(event.mimeData().imageData())
        if not pm.isNull():
            self.imageDropped.emit(pm)
```

`_preview_label` 改为 `DropLabel` 实例（替换原 QLabel），并保留 objectName="previewLabel" 与所有现有样式。

**预览区行为：**

- `setAcceptDrops(True/False)` 由 `_on_sub_tab_changed` 切换：识别子页 True，生成子页 False。
- `imageDropped` 信号 → `_on_image_input(pixmap)`。
- **Ctrl+V 快捷键**：`QShortcut(QKeySequence.Paste, self)`，通过 `_decode_paste_shortcut` 引用持有，`setEnabled(True/False)` 由 `_on_sub_tab_changed` 切换（避免与生成子页文本粘贴冲突）。

**统一图片输入入口 `_on_image_input(pixmap: QPixmap)`：**

1. 归一化 devicePixelRatio 为 1.0（参考 `single_recognition_tab._on_paste:165`）。
2. 用模块现有 `_scale_pixmap_for_label(pixmap, label)` 缩放显示到 `_preview_label`。
3. 保存原始 QPixmap 到 `self._decode_pending_pixmap`。
4. 启用 `btnDecode`，清空 `decodeResultList` 与结果计数。

**三种输入途径都汇入 `_on_image_input`：**

- **粘贴图片按钮 / Ctrl+V**：`QGuiApplication.clipboard().pixmap()` → `_on_image_input`（仿 `single_recognition_tab._on_paste:159`）。
- **拖入**：`DropLabel.imageDropped` → `_on_image_input`。
- **选择文件**：`QFileDialog.getOpenFileName`（用 `from vibeocr.utils.mime_types import FILE_FILTER_ALL`）→ `QPixmap(path)` → `_on_image_input`。

### 6. 子标签页切换的状态管理

`QrcodeTab` 新增状态字段：

- `self._gen_preview_pixmap: QPixmap | None` — 生成子页当前预览二维码。
- `self._decode_pending_pixmap: QPixmap | None` — 识别子页待识别图。
- `self._decode_paste_shortcut: QShortcut` — Ctrl+V 快捷键引用。

**`_on_sub_tab_changed(index)`：**

- `index == 0`（生成）：
  - 保存识别页 `self._decode_pending_pixmap`。
  - 恢复生成页预览：`_gen_preview_pixmap` 非空则显示，否则触发 `_refresh_preview()`。
  - 显示生成操作栏按钮，隐藏识别操作栏按钮。
  - `_preview_label.setAcceptDrops(False)`；`_decode_paste_shortcut.setEnabled(False)`。
- `index == 1`（识别）：
  - 保存生成页预览到 `_gen_preview_pixmap`。
  - 恢复识别页预览：`_decode_pending_pixmap` 非空则显示，否则显示占位文本"粘贴、拖入或选择图片以识别"。
  - 显示识别操作栏按钮，隐藏生成操作栏按钮。
  - `_preview_label.setAcceptDrops(True)`；`_decode_paste_shortcut.setEnabled(True)`。

构造函数末尾默认选中生成子页（index 0），并触发一次 `_on_sub_tab_changed(0)` 确保初始状态正确。

### 7. 识别按钮点击处理 `_on_decode()`

1. `if self._decode_pending_pixmap is None: return`（防御）。
2. 禁用 `btnDecode`，显示加载提示（"识别中..."）。
3. `QApplication.processEvents()` 让 UI 更新。
4. `pil_img = _qpixmap_to_pil(self._decode_pending_pixmap)`（新增模块内 helper `_qpixmap_to_pil(pixmap) -> PIL.Image`：用 `QBuffer` 保存为 PNG 字节流再 `Image.open`，强制 RGB 模式。不引入新依赖）。
5. `try: results = self._decode_service.decode(pil_img) except Exception as e: ...`。
6. 异常：结果区显示红字错误 + 日志。
7. 空结果：结果区显示灰字"未识别到二维码/条形码，请尝试更清晰的图片"，计数"识别到 0 条"。
8. 有结果：清空 `decodeResultList`，对每条 `DecodedItem` 创建 `QListWidgetItem` + `DecodeResultWidget`，连接 `open_url_requested → _on_open_url`、`copy_requested → _on_copy_single`。计数"识别到 N 条"。
9. 重新启用 `btnDecode`。

**打开 URL：**

```python
def _on_open_url(self, url: str) -> None:
    from PySide6.QtGui import QDesktopServices, QUrl
    QDesktopServices.openUrl(QUrl(url))
```

**复制单条/全部：**

```python
def _on_copy_single(self, text: str) -> None:
    QGuiApplication.clipboard().setText(text)

def _on_copy_all(self) -> None:
    texts = [item.data for item in self._decode_results]  # 缓存的 list[DecodedItem]
    QGuiApplication.clipboard().setText("\n".join(texts))
```

### 8. 错误处理与边界

| 场景 | 处理 |
|---|---|
| `pyzbar` 导入失败 | `decode` 抛 `ImportError`；UI 显示红字"解码库未安装，请检查 pyzbar 依赖" |
| 图片损坏/格式不支持 | 异常上抛；UI 显示红字错误信息 + 日志 |
| 空结果 | 灰字提示"未识别到二维码/条形码"；计数"0 条" |
| 超大图（任一边 > 4096px） | service 内 `thumbnail` 缩放后再解码（不改变预览） |
| `data.strip() == ""` 的结果 | service 内跳过不返回 |
| URL 安全校验 | 严格 scheme=http/https 且 netloc 非空；其他 scheme 按纯文本不可点击 |
| 生成子页激活时拖入 | `setAcceptDrops(False)`，事件被忽略 |
| 无图点识别 | `btnDecode` 默认禁用，无图不可点 |

### 9. 现有生成功能完整保留

重构原则：**生成逻辑零行为变更**。具体：

- 原 `QrcodeTab` 的所有控件（`_format_combo`、`_text_input`、`_size_spin`、EC 单选、颜色按钮、Logo 嵌入、文字说明）整体搬入 `_generate_panel`（一个 QWidget 容器），保持原有父级关系与信号连接。
- `_setup_ui` 改为：先建外层 splitter + 预览面板 + sub_tabs，再把原"右侧控制面板"的构建代码封装为 `_build_generate_panel() -> QWidget`，返回值 `addTab` 到 `_sub_tabs`。
- `_connect_signals` 中原有信号连接保留；新增识别子页与子页切换的信号连接。
- `_refresh_preview`、`_on_format_changed`、`_on_logo_*`、`_on_pick_*_color`、`_on_paste_from_clipboard`、`_on_save`、`_on_copy` 等方法**保持不变**，仅其操作的目标预览 label 和操作栏按钮仍是同一组控件（搬进了 `_generate_panel` 但引用不变）。
- debounce 机制（300ms QTimer）保留。

### 10. main_window.py 改动

**唯一改动：** `main_window.py:288` 的字符串字面量 `"二维码生成"` → `"二维码"`。

布局持久化（`_restore_layout`/`_save_layout` 的 `"qrcode_tab"` splitter key）**不变**——splitter 仍是 `QrcodeTab._splitter`，嵌套 sub_tabs 不持久化（子页选中状态使用默认即可，YAGNI）。

## 涉及文件

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/vibeocr/views/main_window.py:288` | 修改 | 标题 `"二维码生成"` → `"二维码"` |
| `src/vibeocr/views/tabs/qrcode_tab.py` | 重构 | 嵌套 `QTabWidget`；生成逻辑搬入 `_generate_panel`；新增 `_decode_panel` + `DropLabel` + `DecodeResultWidget` + 解码 slot + 子页切换；新增 `_qpixmap_to_pil` helper |
| `src/vibeocr/services/qrcode_decode_service.py` | **新增** | `DecodedItem` + `QrcodeDecodeService` + `_is_http_url` |
| `pyproject.toml` | 修改 | 依赖区加 `pyzbar>=0.1.9`；`[tool.mypy.overrides]` 加 `"pyzbar"`、`"pyzbar.pyzbar"`、`"pyzbar.symbols"` |
| `tests/services/test_qrcode_decode_service.py` | **新增** | 服务层往返/多码/非URL/空图/文件/字节测试 |
| `tests/views/tabs/test_qrcode_tab.py` | 扩展 | 识别子页结构+行为测试；调整现有结构测试（findChild 深度搜索仍可用） |

**预估规模：** `qrcode_tab.py` 从 461 → 约 750 行；新增 service 约 90 行；测试约 +250 行。

## 测试要点

### 服务层 `tests/services/test_qrcode_decode_service.py`（新增）

- **fixture**：`decode_service` 返回 `QrcodeDecodeService()`。
- 文件头 `pytest.importorskip("pyzbar")`，pyzbar 缺失时跳过不阻断 CI。
- **往返测试**：`QrcodeService.generate("https://example.com", opts)` 生成 → `decode` → 断言 1 条、`data=="https://example.com"`、`is_url is True`。
- **非 URL 测试**：生成纯文本（如 `"Hello 世界"`）二维码 → 断言 `is_url is False`、`data` 相等。
- **多码测试**：两个二维码图横向拼接（`Image.new` + `paste`）→ 断言 2 条结果。
- **空图测试**：`Image.new("RGB", (100,100), "white")` → 空 list。
- **decode_file**：写临时 PNG → `decode_file` 断言一致。
- **decode_bytes**：`BytesIO` 序列化 → `decode_bytes` 断言一致。
- **大图保护**：构造 5000×5000 白图（先在二维码上画一个小的码区域）→ 不抛异常。

### UI 层 `tests/views/tabs/test_qrcode_tab.py`（扩展）

- 复用现有 `qrcode_tab(qtbot)` fixture。
- **结构断言**：`findChild(QTabWidget, "subTabs")` 存在且 `count()==2`；`findChild(QPushButton,"btnDecode")` 存在且默认 `isEnabled()==False`；`findChild(QListWidget,"decodeResultList")` 存在；`findChild(QPushButton,"btnPasteImg")`、`"btnSelectImg"`、`"btnClear"` 存在。
- **现有结构测试兼容性**：`_splitter`、`previewLabel`、`_btn_save`、`_btn_copy`、`_format_combo` 等通过 `findChild` 深度搜索仍能找到（因嵌套在 `_generate_panel` 内）。验证现有 `TestQrcodeTabStructure` / `TestQrcodeTabBehavior` 全部通过；若因父级变化失败，调整断言方式（优先用 `findChild` 而非直接 `tab.layout()`）。
- **子页切换**：`_sub_tabs.setCurrentIndex(1)` → `_preview_label.acceptDrops() is True`；`setCurrentIndex(0)` → `is False`。
- **识别行为**：用 service 生成二维码 → `_pil_to_qpixmap` → 调用 `tab._on_image_input(pm)` → `_btn_decode.isEnabled()` → `qtbot.mouseClick(_btn_decode)` → `qtbot.waitUntil(lambda: "1" in _result_count_label.text())` → 断言 `decodeResultList.count()==1`。
- **URL 打开**：`monkeypatch.setattr("vibeocr.views.tabs.qrcode_tab.QDesktopServices.openUrl", recorder)`（或用 `monkeypatch.setattr` 替换 `_on_open_url`）→ 触发 → 断言以正确 QUrl 调用。
- **标签页标题**：在 `tests/views/test_main_window.py`（若存在则扩展，否则新增轻量用例）断言顶层 tab 文本为"二维码"。

## 依赖与风险

- **新增依赖** `pyzbar>=0.1.9`：Windows wheel 自带 `libzbar-0.dll`，无需额外系统包；Linux/macOS 需系统 `libzbar0`（项目目标平台以 Windows 为主，风险可控）。
- **嵌套 QTabWidget**：项目首个先例，但 Qt 原生支持、无技术风险；需确认样式（外层 QSS 不会破坏内层 tab 外观）——若内层 tab 样式异常，加 `setStyleSheet` 限定作用域。
- **打包（PyInstaller）**：`pyzbar` 的 native DLL 需被 PyInstaller 收集。检查现有 `.spec` 文件是否需要显式 `binaries` 条目（`--collect-all pyzbar` 或 `hiddenimports=["pyzbar.pyzbar"]`）。**作为实现阶段的一项验证任务。**
