# 二维码识别功能与标签页重构 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有"二维码生成"标签页扩展为统一的"二维码"标签页，内含「生成」「识别」两个子标签页。识别子页支持粘贴/拖入/选择图片解码二维码与条形码，结果以列表展示，URL 可点击调用系统浏览器打开。

**Architecture:** 新增纯 Python 的 `QrcodeDecodeService`（基于 `pyzbar`）封装解码逻辑。重构 `QrcodeTab`：外层保留 `QSplitter`（左共享预览 + 右控制区），右侧改为嵌套 `QTabWidget`（项目首个先例），原生成面板整体搬入「生成」子页，新增「识别」子页。左侧预览 `QLabel` 升级为支持拖入图片的 `DropLabel` 子类，在识别子页激活时启用拖入与 Ctrl+V 粘贴图片。

**Tech Stack:** PySide6, pyzbar, Pillow, pytest-qt

**Spec:** `docs/superpowers/specs/2026-06-15-qrcode-decode-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/vibeocr/services/qrcode_decode_service.py` | **Create** | `DecodedItem` 数据类 + `QrcodeDecodeService` 解码服务（pyzbar 封装、URL 判定、大图保护） |
| `src/vibeocr/views/tabs/qrcode_tab.py` | **Modify** | 重构为嵌套子标签页：`DropLabel`、`DecodeResultWidget`、生成/识别子页、子页切换状态管理、解码/粘贴/拖入/打开 URL slot |
| `src/vibeocr/views/main_window.py:288` | **Modify** | 顶层 tab 标题 `"二维码生成"` → `"二维码"` |
| `pyproject.toml` | **Modify** | 新增 `pyzbar>=0.1.9` 依赖；mypy overrides 加 `pyzbar` 相关模块 |
| `tests/services/test_qrcode_decode_service.py` | **Create** | 服务层往返/多码/非URL/空图/文件/字节/大图测试 |
| `tests/views/tabs/test_qrcode_tab.py` | **Modify** | 扩展识别子页结构与行为测试；确保现有生成测试兼容 |

**实现顺序原则：** 自底向上（先服务层 → 再 UI 组件 → 再整合 → 最后依赖与标题）。Task 1 先加依赖确保后续测试能 import pyzbar。

---

### Task 1: 新增 pyzbar 依赖

**Files:**
- Modify: `pyproject.toml`（依赖区行 6-28；mypy overrides 行 128-145）

- [ ] **Step 1: 在 `pyproject.toml` dependencies 列表加 pyzbar**

在 `pyproject.toml` 的 `[project] dependencies` 数组里，紧接 `"opencv-contrib-python>=4.10.0.84",` 这一行（行 27）之后加一行：

```toml
    "opencv-contrib-python>=4.10.0.84",
    "pyzbar>=0.1.9",
```

- [ ] **Step 2: 在 mypy overrides 模块列表加 pyzbar 相关模块**

在 `pyproject.toml` 的 `[[tool.mypy.overrides]] module = [...]` 列表里，紧跟 `"qrcode",`（约行 137）之后加三行：

```toml
    "qrcode",
    "pyzbar",
    "pyzbar.pyzbar",
    "pyzbar.symbols",
    "qrcode.image.svg",
```

- [ ] **Step 3: 安装依赖并验证可导入**

Run:
```bash
uv sync
```

然后验证：
```bash
.venv\Scripts\python.exe -c "from pyzbar.pyzbar import decode; print('pyzbar OK')"
```
Expected: 输出 `pyzbar OK`，无 ImportError。

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add pyzbar dependency for QR/barcode decoding"
```

---

### Task 2: QrcodeDecodeService 服务层（TDD）

**Files:**
- Create: `src/vibeocr/services/qrcode_decode_service.py`
- Create: `tests/services/test_qrcode_decode_service.py`

- [ ] **Step 1: 写失败测试 — 基础结构与往返解码**

创建 `tests/services/test_qrcode_decode_service.py`：

```python
"""QrcodeDecodeService 单元测试"""

import pytest

pytest.importorskip("pyzbar")  # pyzbar 缺失时整个文件跳过

from PIL import Image  # noqa: E402

from vibeocr.services.qrcode_decode_service import (  # noqa: E402
    DecodedItem,
    QrcodeDecodeService,
)
from vibeocr.services.qrcode_service import QrcodeService  # noqa: E402


@pytest.fixture
def decode_service():
    return QrcodeDecodeService()


@pytest.fixture
def gen_service():
    return QrcodeService()


def _make_qr_image(text: str, gen_service) -> Image.Image:
    opts = gen_service.default_options()
    opts["format"] = "qr"
    return gen_service.generate(text, opts)


class TestDecodeServiceStructure:
    def test_default_options_returns_dict(self, decode_service):
        opts = decode_service.default_options()
        assert isinstance(opts, dict)

    def test_decode_returns_list(self, decode_service, gen_service):
        img = _make_qr_image("Hello", gen_service)
        results = decode_service.decode(img)
        assert isinstance(results, list)

    def test_decoded_item_fields(self):
        item = DecodedItem(data="x", type="QRCODE", is_url=False)
        assert item.data == "x"
        assert item.type == "QRCODE"
        assert item.is_url is False


class TestDecodeRoundtrip:
    def test_decode_url_qr(self, decode_service, gen_service):
        url = "https://example.com"
        img = _make_qr_image(url, gen_service)
        results = decode_service.decode(img)
        assert len(results) == 1
        assert results[0].data == url
        assert results[0].is_url is True

    def test_decode_non_url_text(self, decode_service, gen_service):
        text = "Hello 世界"
        img = _make_qr_image(text, gen_service)
        results = decode_service.decode(img)
        assert len(results) == 1
        assert results[0].data == text
        assert results[0].is_url is False

    def test_decode_type_is_qrcode(self, decode_service, gen_service):
        img = _make_qr_image("test", gen_service)
        results = decode_service.decode(img)
        assert results[0].type.upper() == "Qrcode".upper() or "QR" in results[0].type.upper()
```

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
.venv\Scripts\python.exe -m pytest tests/services/test_qrcode_decode_service.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'vibeocr.services.qrcode_decode_service'`。

- [ ] **Step 3: 实现 QrcodeDecodeService 最小版本**

创建 `src/vibeocr/services/qrcode_decode_service.py`：

```python
"""二维码/条形码识别（解码）服务"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from PIL import Image

logger = logging.getLogger(__name__)

_MAX_DECODE_DIM = 4096  # 超过此尺寸先缩放，防止 pyzbar OOM/超时


@dataclass
class DecodedItem:
    """单条解码结果。"""

    data: str
    type: str
    is_url: bool


def _is_http_url(value: str) -> bool:
    """严格判定 http/https URL，拒绝 javascript:/file: 等其他 scheme。"""
    if not value.startswith(("http://", "https://")):
        return False
    try:
        parsed = urlparse(value)
    except (ValueError, TypeError):
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


class QrcodeDecodeService:
    """二维码和条形码识别服务（基于 pyzbar）。"""

    def default_options(self) -> dict:
        return {
            "max_decode_dim": _MAX_DECODE_DIM,
        }

    def decode(self, image: Image.Image) -> list[DecodedItem]:
        from pyzbar.pyzbar import decode as _zbar_decode

        # 大图保护：任一边超过上限先等比缩放（在副本上操作，不改原图）
        max_dim = max(image.size)
        opts_max = _MAX_DECODE_DIM
        if max_dim > opts_max:
            working = image.copy()
            working.thumbnail((opts_max, opts_max))
        else:
            working = image

        # pyzbar 优先用灰度
        if working.mode != "L":
            gray = working.convert("L")
        else:
            gray = working

        raw_results = _zbar_decode(gray)
        items: list[DecodedItem] = []
        for r in raw_results:
            try:
                data = r.data.decode("utf-8", errors="replace")
            except Exception:
                continue
            if not data.strip():
                continue
            items.append(
                DecodedItem(data=data, type=r.type, is_url=_is_http_url(data))
            )
        return items

    def decode_bytes(self, data: bytes) -> list[DecodedItem]:
        import io

        img = Image.open(io.BytesIO(data))
        return self.decode(img)

    def decode_file(self, path: str) -> list[DecodedItem]:
        img = Image.open(path)
        return self.decode(img)
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
.venv\Scripts\python.exe -m pytest tests/services/test_qrcode_decode_service.py -v
```
Expected: PASS — 全部测试通过。

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/services/qrcode_decode_service.py tests/services/test_qrcode_decode_service.py
git commit -m "feat: add QrcodeDecodeService for QR/barcode decoding via pyzbar"
```

---

### Task 3: 补全服务层测试（多码、空图、文件、字节、大图）

**Files:**
- Modify: `tests/services/test_qrcode_decode_service.py`

- [ ] **Step 1: 追加测试用例到现有测试文件**

在 `tests/services/test_qrcode_decode_service.py` 末尾追加：

```python
class TestDecodeEdgeCases:
    def test_decode_blank_image_returns_empty(self, decode_service):
        blank = Image.new("RGB", (100, 100), "white")
        assert decode_service.decode(blank) == []

    def test_decode_multiple_codes(self, decode_service, gen_service):
        img1 = _make_qr_image("first", gen_service)
        img2 = _make_qr_image("second", gen_service)
        w = img1.width + img2.width + 20
        h = max(img1.height, img2.height)
        canvas = Image.new("RGB", (w, h), "white")
        canvas.paste(img1, (0, 0))
        canvas.paste(img2, (img1.width + 20, 0))
        results = decode_service.decode(canvas)
        datas = {r.data for r in results}
        assert "first" in datas
        assert "second" in datas
        assert len(results) >= 2

    def test_decode_file(self, decode_service, gen_service, tmp_path):
        img = _make_qr_image("file-test", gen_service)
        path = tmp_path / "qr.png"
        img.save(str(path))
        results = decode_service.decode_file(str(path))
        assert len(results) == 1
        assert results[0].data == "file-test"

    def test_decode_bytes(self, decode_service, gen_service):
        import io

        img = _make_qr_image("bytes-test", gen_service)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        results = decode_service.decode_bytes(buf.getvalue())
        assert len(results) == 1
        assert results[0].data == "bytes-test"


class TestDecodeLargeImage:
    def test_huge_image_does_not_crash(self, decode_service, gen_service):
        """构造一张含小二维码的大图，验证大图保护路径不抛异常。"""
        qr = _make_qr_image("big-img-test", gen_service).resize((100, 100))
        # 粘贴到远大于 4096 的白画布上
        canvas = Image.new("RGB", (5000, 5000), "white")
        canvas.paste(qr, (0, 0))
        results = decode_service.decode(canvas)
        datas = {r.data for r in results}
        assert "big-img-test" in datas


class TestUrlDetection:
    def test_http_url_detected(self, decode_service, gen_service):
        img = _make_qr_image("http://foo.bar/baz", gen_service)
        results = decode_service.decode(img)
        assert results[0].is_url is True

    def test_javascript_scheme_not_url(self):
        from vibeocr.services.qrcode_decode_service import _is_http_url

        assert _is_http_url("javascript:alert(1)") is False

    def test_file_scheme_not_url(self):
        from vibeocr.services.qrcode_decode_service import _is_http_url

        assert _is_http_url("file:///etc/passwd") is False

    def test_plain_text_not_url(self):
        from vibeocr.services.qrcode_decode_service import _is_http_url

        assert _is_http_url("just some text") is False
```

- [ ] **Step 2: 运行测试验证通过**

Run:
```bash
.venv\Scripts\python.exe -m pytest tests/services/test_qrcode_decode_service.py -v
```
Expected: PASS — 所有新增与原有测试通过。

> **注意：** `test_decode_multiple_codes` 把两个二维码左右排列。pyzbar 通常能识别画面中多个独立二维码。若偶发只识别到 1 个（与生成尺寸、border 重叠有关），把 canvas 间距从 20 增大到 40，或把两张图改为上下排列。断言用的是 `len(results) >= 2`，已留余量。

- [ ] **Step 3: Commit**

```bash
git add tests/services/test_qrcode_decode_service.py
git commit -m "test: cover multi-code, blank, file/bytes, large-image, URL edge cases"
```

---

### Task 4: 重构 qrcode_tab.py — 嵌套子标签页骨架（保留生成功能）

本任务把 `QrcodeTab` 改为嵌套子标签页结构，**生成功能完整保留**，识别子页先留占位（下一任务填充）。先跑通结构改造并确保现有生成测试不破坏。

**Files:**
- Modify: `src/vibeocr/views/tabs/qrcode_tab.py`

- [ ] **Step 1: 先跑现有测试建立基线**

Run:
```bash
.venv\Scripts\python.exe -m pytest tests/views/tabs/test_qrcode_tab.py -v
```
Expected: PASS — 全部现有测试通过（这是基线，重构后必须仍通过）。

- [ ] **Step 2: 重构 `_setup_ui` 为「外层 splitter + 左预览 + 右 sub_tabs」**

把 `qrcode_tab.py` 的 `_setup_ui` 方法（行 92-248）整体替换为下面这版。关键变化：
1. `_preview_label` 改用 `DropLabel`（下个 step 定义）。
2. 右侧从直接放 `scroll`（QScrollArea）改为放进一个 `QTabWidget`（`_sub_tabs`）作为「生成」子页；`scroll` 本身不变。
3. 预览操作栏（`_btn_save`/`_btn_copy`）保留在左侧，但包进 `_gen_action_bar_widget` 以便子页切换时控制显隐。

替换后的 `_setup_ui`：

```python
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self._splitter = QSplitter()

        # ── 左侧：预览区（生成与识别共享） ──
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self._preview_label = DropLabel("输入内容后自动生成预览")
        self._preview_label.setObjectName("previewLabel")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumSize(200, 200)
        self._preview_label.setStyleSheet(
            "QLabel { background-color: #f5f5f5; border: 1px solid #ddd; border-radius: 4px; }"
        )
        self._preview_label.setAcceptDrops(False)  # 仅识别子页激活时开启
        self._preview_label.imageDropped.connect(self._on_image_input)
        left_layout.addWidget(self._preview_label, stretch=1)

        # 生成操作栏（保存/复制）—— 子页切换时显隐
        self._gen_action_bar_widget = QWidget()
        gen_action_bar = QHBoxLayout(self._gen_action_bar_widget)
        gen_action_bar.setContentsMargins(0, 0, 0, 0)
        gen_action_bar.setSpacing(6)

        self._btn_save = QPushButton("保存")
        self._btn_save.setObjectName("btnSave")
        self._btn_save.setFixedHeight(28)
        self._btn_copy = QPushButton("复制到剪贴板")
        self._btn_copy.setObjectName("btnCopy")
        self._btn_copy.setFixedHeight(28)

        gen_action_bar.addWidget(self._btn_save)
        gen_action_bar.addWidget(self._btn_copy)
        gen_action_bar.addStretch()
        left_layout.addWidget(self._gen_action_bar_widget)

        # 识别操作栏（粘贴/选择/识别/清空）—— 子页切换时显隐，初始隐藏
        self._decode_action_bar_widget = QWidget()
        dec_action_bar = QHBoxLayout(self._decode_action_bar_widget)
        dec_action_bar.setContentsMargins(0, 0, 0, 0)
        dec_action_bar.setSpacing(6)

        self._btn_paste_img = QPushButton("粘贴图片")
        self._btn_paste_img.setObjectName("btnPasteImg")
        self._btn_paste_img.setFixedHeight(28)
        self._btn_select_img = QPushButton("选择图片...")
        self._btn_select_img.setObjectName("btnSelectImg")
        self._btn_select_img.setFixedHeight(28)
        dec_action_bar.addWidget(self._btn_paste_img)
        dec_action_bar.addWidget(self._btn_select_img)
        dec_action_bar.addStretch()
        self._btn_decode = QPushButton("🔍 识别")
        self._btn_decode.setObjectName("btnDecode")
        self._btn_decode.setFixedHeight(28)
        self._btn_decode.setEnabled(False)  # 无图时禁用
        dec_action_bar.addWidget(self._btn_decode)
        self._btn_clear = QPushButton("清空")
        self._btn_clear.setObjectName("btnClear")
        self._btn_clear.setFixedHeight(28)
        dec_action_bar.addWidget(self._btn_clear)
        self._decode_action_bar_widget.setVisible(False)
        left_layout.addWidget(self._decode_action_bar_widget)

        self._splitter.addWidget(left_panel)

        # ── 右侧：嵌套子标签页 ──
        self._sub_tabs = QTabWidget()
        self._sub_tabs.setObjectName("subTabs")

        # 「生成」子页 = 原 QScrollArea 包裹的参数面板
        self._sub_tabs.addTab(self._build_generate_panel(), "生成")

        # 「识别」子页（下一任务填充内容，先占位）
        self._sub_tabs.addTab(self._build_decode_panel(), "识别")

        self._splitter.addWidget(self._sub_tabs)
        self._splitter.setSizes([500, 300])

        layout.addWidget(self._splitter, stretch=1)
```

- [ ] **Step 3: 把原参数面板构建逻辑抽成 `_build_generate_panel`**

原 `_setup_ui` 中行 132-244（从 `scroll = QScrollArea()` 到 `scroll.setWidget(params_widget)` 之间的全部参数面板构建代码）整体移入新方法 `_build_generate_panel(self) -> QScrollArea`。即把这段代码：

```python
    def _build_generate_panel(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumWidth(260)
        scroll.setMaximumWidth(360)

        params_widget = QWidget()
        params_layout = QVBoxLayout(params_widget)
        params_layout.setContentsMargins(8, 4, 8, 4)
        params_layout.setSpacing(8)

        # ── 1. 输入内容 ──
        params_layout.addWidget(self._create_section_label("输入内容"))

        self._format_combo = QComboBox()
        for name, _ in FORMAT_ITEMS:
            self._format_combo.addItem(name)
        params_layout.addWidget(self._format_combo)

        self._text_input = QPlainTextEdit()
        self._text_input.setPlaceholderText("输入要编码的内容...")
        self._text_input.setMaximumHeight(80)
        params_layout.addWidget(self._text_input)

        self._btn_paste = QPushButton("从剪贴板粘贴")
        self._btn_paste.setFixedHeight(26)
        params_layout.addWidget(self._btn_paste)

        # ── 2. 尺寸与纠错 ──
        params_layout.addWidget(self._create_section_label("尺寸与纠错"))

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("尺寸:"))
        self._size_spin = self._create_spin_box(100, 2000, 600)
        size_row.addWidget(self._size_spin)
        size_row.addStretch()
        params_layout.addLayout(size_row)

        ec_row = QHBoxLayout()
        self._ec_label = QLabel("纠错等级:")
        ec_row.addWidget(self._ec_label)
        self._ec_group = _create_button_group(self)
        for btn in self._ec_group.buttons():
            ec_row.addWidget(btn)
        params_layout.addLayout(ec_row)

        # ── 3. 颜色设置 ──
        params_layout.addWidget(self._create_section_label("颜色设置"))

        color_row = QHBoxLayout()
        self._fg_btn = QPushButton("前景色")
        self._fg_color = "#000000"
        self._fg_btn.setStyleSheet(self._color_btn_style(self._fg_color))
        color_row.addWidget(self._fg_btn)

        self._bg_btn = QPushButton("背景色")
        self._bg_color = "#FFFFFF"
        self._bg_btn.setStyleSheet(self._color_btn_style(self._bg_color))
        color_row.addWidget(self._bg_btn)

        self._invert_check = QCheckBox("反色")
        color_row.addWidget(self._invert_check)
        params_layout.addLayout(color_row)

        # ── 4. Logo 嵌入（仅二维码）──
        params_layout.addWidget(self._create_section_label("Logo 嵌入"))

        logo_row = QHBoxLayout()
        self._logo_check = QCheckBox("启用")
        logo_row.addWidget(self._logo_check)
        self._logo_select_btn = QPushButton("选择图片")
        self._logo_select_btn.setEnabled(False)
        logo_row.addWidget(self._logo_select_btn)
        params_layout.addLayout(logo_row)

        logo_size_row = QHBoxLayout()
        logo_size_row.addWidget(QLabel("Logo 大小比例:"))
        self._logo_ratio_spin = self._create_spin_box(5, 50, 20)
        self._logo_ratio_spin.setSuffix("%")
        logo_size_row.addWidget(self._logo_ratio_spin)
        logo_size_row.addStretch()
        params_layout.addLayout(logo_size_row)
        self._logo_section_widgets = [
            self._logo_check,
            self._logo_select_btn,
            self._logo_ratio_spin,
        ]

        # ── 5. 文字说明 ──
        params_layout.addWidget(self._create_section_label("文字说明"))

        self._label_text_input = QLineEdit()
        self._label_text_input.setPlaceholderText("自定义说明文字（留空使用原始内容）")
        params_layout.addWidget(self._label_text_input)

        label_pos_row = QHBoxLayout()
        label_pos_row.addWidget(QLabel("位置:"))
        self._label_pos_combo = QComboBox()
        self._label_pos_combo.addItems(["下方", "上方", "无"])
        label_pos_row.addWidget(self._label_pos_combo)
        label_pos_row.addStretch()
        params_layout.addLayout(label_pos_row)

        label_font_row = QHBoxLayout()
        label_font_row.addWidget(QLabel("字体大小:"))
        self._label_font_spin = self._create_spin_box(8, 48, 12)
        label_font_row.addWidget(self._label_font_spin)
        label_font_row.addStretch()
        params_layout.addLayout(label_font_row)

        params_layout.addStretch()

        scroll.setWidget(params_widget)
        return scroll
```

> **注意：** 这段代码与原 `_setup_ui` 中的对应部分**逐字相同**，只是被包进一个方法并 `return scroll`。不要改动任何控件创建逻辑，确保生成行为零变化。

- [ ] **Step 4: 新增 `_build_decode_panel` 占位方法**

在 `_build_generate_panel` 之后加一个占位方法（下一任务再填充真实内容）：

```python
    def _build_decode_panel(self) -> QWidget:
        """构建「识别」子页。Task 5 填充真实内容。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        layout.addWidget(QLabel("（识别面板占位 — 待实现）"))
        layout.addStretch()
        return panel
```

- [ ] **Step 5: 新增 `DropLabel` helper 类**

在 `qrcode_tab.py` 模块级（`_scale_pixmap_for_label` 函数之后、`class QrcodeTab` 之前）加：

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
            event.acceptProposedAction()
        else:
            event.ignore()
```

- [ ] **Step 6: 更新 import — 加 Signal、QTabWidget、QListWidget**

把文件顶部 import 区改为（新增 `QTabWidget`、`QListWidget`；`QtCore` 加 `Signal`）：

```python
import logging
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vibeocr.services.qrcode_decode_service import QrcodeDecodeService
from vibeocr.services.qrcode_service import QrcodeService
```

- [ ] **Step 7: 更新 `__init__` — 加 decode service、状态字段、Ctrl+V shortcut**

替换 `__init__`（行 78-90）为：

```python
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = QrcodeService()
        self._decode_service = QrcodeDecodeService()
        self._current_image: Image.Image | None = None
        self._logo_path: str | None = None

        # 子页预览状态（切换时保存/恢复）
        self._gen_preview_pixmap: QPixmap | None = None
        self._decode_pending_pixmap: QPixmap | None = None
        self._decode_results: list = []  # list[DecodedItem]，由 _on_decode 填充

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(300)
        self._debounce_timer.timeout.connect(self._refresh_preview)

        self._setup_ui()
        self._connect_signals()
        self._on_sub_tab_changed(0)  # 初始：生成子页
```

- [ ] **Step 8: 新增 `_on_sub_tab_changed` 与子页切换信号连接**

在 `_connect_signals` 末尾（`self._btn_copy.clicked.connect(self._on_copy)` 之后）加一行：

```python
        self._sub_tabs.currentChanged.connect(self._on_sub_tab_changed)
```

然后在 `_connect_signals` 方法之后新增 slot 方法：

```python
    def _on_sub_tab_changed(self, index: int) -> None:
        """切换生成/识别子页时，保存/恢复预览状态并切换操作栏与拖入支持。"""
        is_decode = index == 1

        if is_decode:
            # 离开生成页：保存当前预览
            pm = self._preview_label.pixmap()
            self._gen_preview_pixmap = pm if (pm and not pm.isNull()) else None
            # 恢复识别页预览
            if self._decode_pending_pixmap is not None:
                self._preview_label.setPixmap(
                    _scale_pixmap_for_label(
                        self._decode_pending_pixmap, self._preview_label
                    )
                )
            else:
                self._preview_label.clear()
                self._preview_label.setText("粘贴、拖入或选择图片以识别")
        else:
            # 离开识别页：保存待识别图
            pm = self._preview_label.pixmap()
            if pm and not pm.isNull() and self._decode_pending_pixmap is not None:
                # _decode_pending_pixmap 保持原值（不被缩放显示覆盖）
                pass
            # 恢复生成页预览
            if self._current_image is not None:
                pixmap = _pil_to_qpixmap(self._current_image)
                self._preview_label.setPixmap(
                    _scale_pixmap_for_label(pixmap, self._preview_label)
                )
            else:
                self._preview_label.clear()
                self._preview_label.setText("输入内容后自动生成预览")

        self._gen_action_bar_widget.setVisible(not is_decode)
        self._decode_action_bar_widget.setVisible(is_decode)
        self._preview_label.setAcceptDrops(is_decode)
```

> **注意：** `Ctrl+V` 快捷键留到 Task 6（识别子页行为）再加，避免本步引入未测试的快捷键。

- [ ] **Step 9: 运行现有 UI 测试验证不破坏**

Run:
```bash
.venv\Scripts\python.exe -m pytest tests/views/tabs/test_qrcode_tab.py -v
```
Expected: PASS — 全部现有测试通过。`findChild` 深度搜索能找到嵌套在 `_generate_panel` 内的 `_btn_save`/`_btn_copy`/`_format_combo` 等。

> **若失败排查：** 现有测试 `test_tab_has_save_button` 等用 `findChild(QPushButton, "btnSave")`，会递归搜索整个 widget 树，应仍能找到。若 `findChild(QComboBox)` 因识别子页也含 QComboBox 而返回错对象，需把该测试改为 `findChild(QComboBox, "<objectName>")`——但本步的识别子页是占位，不含 QComboBox，不会冲突。

- [ ] **Step 10: 新增子页结构测试并运行**

在 `tests/views/tabs/test_qrcode_tab.py` 的 `TestQrcodeTabStructure` 类末尾追加：

```python
    def test_tab_has_sub_tabs(self, qrcode_tab):
        from PySide6.QtWidgets import QTabWidget

        sub = qrcode_tab.findChild(QTabWidget, "subTabs")
        assert sub is not None
        assert sub.count() == 2
        assert sub.tabText(0) == "生成"
        assert sub.tabText(1) == "识别"

    def test_tab_has_decode_button(self, qrcode_tab):
        btn = qrcode_tab.findChild(QPushButton, "btnDecode")
        assert btn is not None
        assert not btn.isEnabled()  # 无图时禁用

    def test_tab_has_decode_service(self, qrcode_tab):
        from vibeocr.services.qrcode_decode_service import QrcodeDecodeService

        assert isinstance(qrcode_tab._decode_service, QrcodeDecodeService)
```

Run:
```bash
.venv\Scripts\python.exe -m pytest tests/views/tabs/test_qrcode_tab.py -v
```
Expected: PASS。

- [ ] **Step 11: Commit**

```bash
git add src/vibeocr/views/tabs/qrcode_tab.py tests/views/tabs/test_qrcode_tab.py
git commit -m "refactor: restructure QrcodeTab into nested generate/decode sub-tabs"
```

---

### Task 5: 识别子页 UI 内容（说明、结果列表、DecodeResultWidget）

**Files:**
- Modify: `src/vibeocr/views/tabs/qrcode_tab.py`

- [ ] **Step 1: 实现 `_build_decode_panel` 真实内容**

把 Task 4 Step 4 的占位 `_build_decode_panel` 整体替换为：

```python
    def _build_decode_panel(self) -> QWidget:
        """构建「识别」子页。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        hint = QLabel(
            "支持粘贴图片 (Ctrl+V)、拖入图片到左侧预览区、\n或点击下方选择文件"
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)

        # 识别结果区
        layout.addWidget(self._create_section_label("识别结果"))

        self._decode_result_list = QListWidget()
        self._decode_result_list.setObjectName("decodeResultList")
        layout.addWidget(self._decode_result_list, stretch=1)

        # 底部操作
        bottom_row = QHBoxLayout()
        self._btn_copy_all = QPushButton("复制全部")
        self._btn_copy_all.setObjectName("btnCopyAll")
        self._btn_copy_all.setFixedHeight(26)
        bottom_row.addWidget(self._btn_copy_all)
        bottom_row.addStretch()
        self._result_count_label = QLabel("识别到 0 条结果")
        self._result_count_label.setStyleSheet("color: #888;")
        bottom_row.addWidget(self._result_count_label)
        layout.addLayout(bottom_row)

        return panel
```

- [ ] **Step 2: 新增 `DecodeResultWidget` 模块级类**

在 `qrcode_tab.py` 模块级（`DropLabel` 类之后、`class QrcodeTab` 之前）加：

```python
class DecodeResultWidget(QWidget):
    """单条识别结果展示：序号 + 类型标签 + 内容/链接 + 操作按钮。"""

    open_url_requested = Signal(str)
    copy_requested = Signal(str)

    def __init__(self, index: int, data: str, type_label: str, is_url: bool, parent=None):
        super().__init__(parent)
        self._data = data

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(8)

        idx_label = QLabel(f"{index}.")
        idx_label.setFixedWidth(20)
        row.addWidget(idx_label)

        type_tag = QLabel(type_label)
        type_tag.setStyleSheet(
            "QLabel { background-color: #e0e0e0; color: #444;"
            " border-radius: 6px; padding: 1px 6px; font-size: 11px; }"
        )
        row.addWidget(type_tag)

        content_label = QLabel()
        content_label.setWordWrap(True)
        if is_url:
            # 显示成可点击链接，但不在内部打开（交由信号）
            display = data if len(data) <= 80 else data[:77] + "..."
            content_label.setText(
                f"<a href='{data}' style='color:#1976D2; text-decoration: underline;'>"
                f"{display}</a>"
            )
            content_label.setOpenExternalLinks(False)
            content_label.linkActivated.connect(self._on_link)

            open_btn = QPushButton("🔗打开")
            open_btn.setFixedHeight(22)
            open_btn.clicked.connect(lambda: self.open_url_requested.emit(self._data))
            row.addWidget(open_btn)
        else:
            display = data if len(data) <= 80 else data[:77] + "..."
            content_label.setText(display)
            content_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
        row.addWidget(content_label, stretch=1)

        copy_btn = QPushButton("📋复制")
        copy_btn.setFixedHeight(22)
        copy_btn.clicked.connect(lambda: self.copy_requested.emit(self._data))
        row.addWidget(copy_btn)

    def _on_link(self, url: str) -> None:
        self.open_url_requested.emit(url)
```

> **安全说明：** URL 直接放进 `<a href='...'>` 富文本。若 URL 含单引号会破坏 HTML。在 `_on_decode` 中构造 widget 前，对 `data` 做转义（见 Task 6 Step 2 的 `_escape_for_richtext`）。本步先用未转义版本让结构测试通过，Task 6 补转义。

- [ ] **Step 3: 运行现有 UI 测试确保不破坏**

Run:
```bash
.venv\Scripts\python.exe -m pytest tests/views/tabs/test_qrcode_tab.py -v
```
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add src/vibeocr/views/tabs/qrcode_tab.py
git commit -m "feat: build decode sub-panel UI with result list and DecodeResultWidget"
```

---

### Task 6: 识别行为 — 解码、粘贴、拖入、选择、打开 URL（TDD）

**Files:**
- Modify: `src/vibeocr/views/tabs/qrcode_tab.py`
- Modify: `tests/views/tabs/test_qrcode_tab.py`

- [ ] **Step 1: 写失败测试 — 识别行为**

在 `tests/views/tabs/test_qrcode_tab.py` 末尾新增测试类：

```python
class TestQrcodeDecodeBehavior:
    def test_switch_to_decode_enables_drops(self, qrcode_tab):
        qrcode_tab.show()
        qrcode_tab._sub_tabs.setCurrentIndex(1)
        assert qrcode_tab._preview_label.acceptDrops() is True

    def test_switch_to_generate_disables_drops(self, qrcode_tab):
        qrcode_tab.show()
        qrcode_tab._sub_tabs.setCurrentIndex(1)
        qrcode_tab._sub_tabs.setCurrentIndex(0)
        assert qrcode_tab._preview_label.acceptDrops() is False

    def test_image_input_enables_decode_btn(self, qrcode_tab):
        from PySide6.QtGui import QPixmap

        pm = QPixmap(10, 10)
        pm.fill()
        qrcode_tab._on_image_input(pm)
        assert qrcode_tab._btn_decode.isEnabled()

    def test_clear_disables_decode_btn(self, qrcode_tab):
        from PySide6.QtGui import QPixmap

        pm = QPixmap(10, 10)
        pm.fill()
        qrcode_tab._on_image_input(pm)
        qrcode_tab._on_clear_decode()
        assert not qrcode_tab._btn_decode.isEnabled()

    def test_decode_qr_shows_result(self, qrcode_tab, qtbot):
        from vibeocr.services.qrcode_service import QrcodeService

        gen = QrcodeService()
        opts = gen.default_options()
        opts["format"] = "qr"
        pil_img = gen.generate("https://decode-test.example", opts)

        from vibeocr.views.tabs.qrcode_tab import _pil_to_qpixmap

        pm = _pil_to_qpixmap(pil_img)
        qrcode_tab._on_image_input(pm)
        qtbot.waitUntil(lambda: qrcode_tab._btn_decode.isEnabled())
        qrcode_tab._btn_decode.click()
        # 同步解码，结果立即可用
        assert qrcode_tab._decode_result_list.count() == 1
        assert "1" in qrcode_tab._result_count_label.text()

    def test_open_url_calls_desktop_services(self, qrcode_tab, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            "vibeocr.views.tabs.qrcode_tab.QDesktopServices.openUrl",
            lambda url: recorded.append(url.toString()),
        )
        qrcode_tab._on_open_url("https://example.com/x")
        assert recorded == ["https://example.com/x"]

    def test_copy_all_joins_results(self, qrcode_tab, qtbot):
        from PySide6.QtGui import QGuiApplication

        # 手动塞两条结果到 _decode_results 以测复制逻辑
        from vibeocr.services.qrcode_decode_service import DecodedItem

        qrcode_tab._decode_results = [
            DecodedItem("a", "QRCODE", False),
            DecodedItem("b", "QRCODE", False),
        ]
        qrcode_tab._on_copy_all()
        assert QGuiApplication.clipboard().text() == "a\nb"

    def test_blank_image_shows_zero_hint(self, qrcode_tab, qtbot):
        from PIL import Image

        from vibeocr.views.tabs.qrcode_tab import _pil_to_qpixmap

        blank = Image.new("RGB", (100, 100), "white")
        pm = _pil_to_qpixmap(blank)
        qrcode_tab._on_image_input(pm)
        qrcode_tab._btn_decode.click()
        assert qrcode_tab._decode_result_list.count() == 0
        assert "0" in qrcode_tab._result_count_label.text()
```

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
.venv\Scripts\python.exe -m pytest tests/views/tabs/test_qrcode_tab.py::TestQrcodeDecodeBehavior -v
```
Expected: FAIL — 多个 AttributeError（`_on_image_input`、`_on_clear_decode`、`_on_open_url`、`_on_copy_all` 等方法未定义）。

- [ ] **Step 3: 实现识别相关 slot 与 helper**

在 `qrcode_tab.py` 的 `QrcodeTab` 类中（`_on_copy` 方法之后、`resizeEvent` 之前）新增以下方法。首先更新 import 区，在 `QtGui` import 加 `QDesktopServices`、`QGuiApplication`、`QKeySequence`、`QShortcut`：

```python
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtCore import QUrl, Qt, QTimer, Signal
```

> 注意：把 `QUrl` 加进 `QtCore` import；`QShortcut` 在 PySide6 6.11+ 已移到 `QtGui`。

在 `__init__` 末尾（`self._on_sub_tab_changed(0)` 之前）加 Ctrl+V 快捷键：

```python
        self._decode_paste_shortcut = QShortcut(QKeySequence.Paste, self)
        self._decode_paste_shortcut.setEnabled(False)
        self._decode_paste_shortcut.activated.connect(self._on_paste_image)
```

在 `_on_sub_tab_changed` 中切换快捷键启用状态：在 `self._preview_label.setAcceptDrops(is_decode)` 那行之后加：

```python
        self._decode_paste_shortcut.setEnabled(is_decode)
```

然后新增以下 slot 方法（放在 `_on_copy` 之后）：

```python
    # ── 识别子页 slots ──

    def _on_image_input(self, pixmap: QPixmap) -> None:
        """统一的图片输入入口（粘贴/拖入/选择文件）。"""
        if pixmap.isNull():
            return
        # 归一化 devicePixelRatio
        if pixmap.devicePixelRatio() != 1.0:
            pixmap = QPixmap(pixmap)
            pixmap.setDevicePixelRatio(1.0)
        self._decode_pending_pixmap = pixmap
        self._preview_label.setPixmap(
            _scale_pixmap_for_label(pixmap, self._preview_label)
        )
        self._btn_decode.setEnabled(True)
        # 清空上次结果
        self._decode_result_list.clear()
        self._decode_results = []
        self._result_count_label.setText("识别到 0 条结果")

    def _on_paste_image(self) -> None:
        clipboard = QGuiApplication.clipboard()
        pm = clipboard.pixmap()
        if not pm.isNull():
            self._on_image_input(pm)

    def _on_select_image(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp *.jp2)"
            ";;所有文件 (*)",
        )
        if path:
            pm = QPixmap(path)
            if not pm.isNull():
                self._on_image_input(pm)

    def _on_clear_decode(self) -> None:
        self._decode_pending_pixmap = None
        self._decode_results = []
        self._decode_result_list.clear()
        self._btn_decode.setEnabled(False)
        self._result_count_label.setText("识别到 0 条结果")
        self._preview_label.clear()
        self._preview_label.setText("粘贴、拖入或选择图片以识别")

    def _on_decode(self) -> None:
        if self._decode_pending_pixmap is None:
            return
        self._btn_decode.setEnabled(False)
        self._btn_decode.setText("识别中...")
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()
        try:
            pil_img = _qpixmap_to_pil(self._decode_pending_pixmap)
            results = self._decode_service.decode(pil_img)
        except Exception as e:
            logger.error(f"识别失败: {e}", exc_info=True)
            self._decode_result_list.clear()
            item = QListWidgetItem()
            err_label = QLabel(f"<span style='color:#f44336;'>识别失败：{e}</span>")
            self._decode_result_list.addItem(item)
            self._decode_result_list.setItemWidget(item, err_label)
            item.setSizeHint(err_label.sizeHint())
            self._decode_results = []
            self._result_count_label.setText("识别到 0 条结果")
            self._btn_decode.setText("🔍 识别")
            self._btn_decode.setEnabled(True)
            return

        self._decode_results = results
        self._decode_result_list.clear()
        if not results:
            hint = QLabel("<span style='color:#888;'>未识别到二维码/条形码，请尝试更清晰的图片</span>")
            item = QListWidgetItem()
            self._decode_result_list.addItem(item)
            self._decode_result_list.setItemWidget(item, hint)
            item.setSizeHint(hint.sizeHint())
        else:
            for idx, r in enumerate(results, start=1):
                widget = DecodeResultWidget(
                    index=idx,
                    data=r.data,
                    type_label=_decode_type_label(r.type),
                    is_url=r.is_url,
                )
                widget.open_url_requested.connect(self._on_open_url)
                widget.copy_requested.connect(self._on_copy_single)
                item = QListWidgetItem()
                self._decode_result_list.addItem(item)
                self._decode_result_list.setItemWidget(item, widget)
                item.setSizeHint(widget.sizeHint())

        self._result_count_label.setText(f"识别到 {len(results)} 条结果")
        self._btn_decode.setText("🔍 识别")
        self._btn_decode.setEnabled(True)

    def _on_open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _on_copy_single(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    def _on_copy_all(self) -> None:
        texts = [item.data for item in self._decode_results]
        QGuiApplication.clipboard().setText("\n".join(texts))
```

- [ ] **Step 4: 新增模块级 helper `_qpixmap_to_pil` 和 `_decode_type_label`**

在 `_scale_pixmap_for_label` 之后加：

```python
def _qpixmap_to_pil(pixmap: QPixmap) -> Image.Image:
    """QPixmap → PIL.Image（RGB）。用 PNG 中转，不引入新依赖。"""
    from io import BytesIO

    from PySide6.QtCore import QBuffer

    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.ReadWrite)
    pixmap.save(buffer, "PNG")
    buffer.seek(0)
    img = Image.open(BytesIO(bytes(buffer.data())))
    buffer.close()
    return img.convert("RGB")


def _decode_type_label(type_str: str) -> str:
    """把 pyzbar 的 type 字符串转成更友好的中文标签。"""
    t = type_str.upper()
    if "QR" in t:
        return "二维码"
    return f"条形码·{type_str}"


def _escape_for_richtext(text: str) -> str:
    """转义用于富文本属性值的字符（防止单引号/HTML 破坏）。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&#39;")
        .replace('"', "&quot;")
    )
```

- [ ] **Step 5: 在 `_on_decode` 中使用转义后的 URL 构造 widget**

把 Step 3 中 `_on_decode` 里创建 widget 的循环改为（仅改 `data=r.data` 这一行附近）：

```python
                display_data = r.data
                safe_data = _escape_for_richtext(r.data)
                widget = DecodeResultWidget(
                    index=idx,
                    data=display_data,
                    type_label=_decode_type_label(r.type),
                    is_url=r.is_url,
                    safe_data=safe_data,
                )
```

并相应改 `DecodeResultWidget.__init__` 签名加 `safe_data: str | None = None`，在构造富文本链接时用 `safe_data if safe_data is not None else data`：

```python
    def __init__(
        self,
        index: int,
        data: str,
        type_label: str,
        is_url: bool,
        safe_data: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._data = data
        href_value = safe_data if safe_data is not None else data
        # ... 后续富文本使用 href_value，显示文本用 data 截断
```

把 Task 5 Step 2 中 `f"<a href='{data}' ...>"` 的 `data` 改为 `href_value`。

- [ ] **Step 6: 连接识别子页的按钮信号**

在 `_connect_signals` 中（`self._sub_tabs.currentChanged.connect(self._on_sub_tab_changed)` 之后）加：

```python
        self._btn_paste_img.clicked.connect(self._on_paste_image)
        self._btn_select_img.clicked.connect(self._on_select_image)
        self._btn_decode.clicked.connect(self._on_decode)
        self._btn_clear.clicked.connect(self._on_clear_decode)
        self._btn_copy_all.clicked.connect(self._on_copy_all)
```

- [ ] **Step 7: 运行全部 qrcode_tab 测试**

Run:
```bash
.venv\Scripts\python.exe -m pytest tests/views/tabs/test_qrcode_tab.py -v
```
Expected: PASS — 生成与识别测试全部通过。

> **若 `test_decode_qr_shows_result` 失败：** 检查 `QApplication.processEvents()` 是否让同步解码完成。pyzbar 的 `_zbar_decode` 是同步阻塞调用，`_on_decode` 在 `click()` 返回前应已完成。若仍未出结果，把测试改为 `qtbot.waitUntil(lambda: qrcode_tab._decode_result_list.count() == 1, timeout=2000)`。

- [ ] **Step 8: Commit**

```bash
git add src/vibeocr/views/tabs/qrcode_tab.py tests/views/tabs/test_qrcode_tab.py
git commit -m "feat: implement QR decode behavior — paste/drop/select/recognize/open-url"
```

---

### Task 7: 顶层 tab 标题改为「二维码」

**Files:**
- Modify: `src/vibeocr/views/main_window.py:288`

- [ ] **Step 1: 修改标题字面量**

在 `src/vibeocr/views/main_window.py` 的 `_init_qrcode_tab` 方法中（约行 280-290），把：

```python
        self._ui.tabWidget.insertTab(
            self._ui.tabWidget.indexOf(self._ui.tabSettings),
            self._qrcode_tab,
            "二维码生成",
        )
```

改为：

```python
        self._ui.tabWidget.insertTab(
            self._ui.tabWidget.indexOf(self._ui.tabSettings),
            self._qrcode_tab,
            "二维码",
        )
```

- [ ] **Step 2: 运行 main_window 相关测试（如有）+ 全量 qrcode 测试**

Run:
```bash
.venv\Scripts\python.exe -m pytest tests/views/ tests/services/test_qrcode_decode_service.py -v -k "qr or main_window"
```
Expected: PASS。

- [ ] **Step 3: Commit**

```bash
git add src/vibeocr/views/main_window.py
git commit -m "feat: rename top tab title from \"二维码生成\" to \"二维码\""
```

---

### Task 8: 拖入图片的集成测试

**Files:**
- Modify: `tests/views/tabs/test_qrcode_tab.py`

- [ ] **Step 1: 写拖入图片的模拟测试**

在 `tests/views/tabs/test_qrcode_tab.py` 的 `TestQrcodeDecodeBehavior` 类末尾追加：

```python
    def test_drop_label_emits_image_dropped(self, qrcode_tab, qtbot):
        """验证 DropLabel 信号能触发 _on_image_input。"""
        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QPixmap

        qrcode_tab.show()
        qrcode_tab._sub_tabs.setCurrentIndex(1)

        pm = QPixmap(20, 20)
        pm.fill()

        received = []
        qrcode_tab._preview_label.imageDropped.connect(received.append)
        qrcode_tab._preview_label.imageDropped.emit(pm)
        assert len(received) == 1
        # 信号连接应触发 _on_image_input（通过 btnDecode 启用间接验证）
        assert qrcode_tab._btn_decode.isEnabled()

    def test_generate_subtab_ignores_drops(self, qrcode_tab):
        """生成子页激活时，预览区不接受拖入。"""
        qrcode_tab.show()
        qrcode_tab._sub_tabs.setCurrentIndex(0)
        assert qrcode_tab._preview_label.acceptDrops() is False
```

- [ ] **Step 2: 运行测试**

Run:
```bash
.venv\Scripts\python.exe -m pytest tests/views/tabs/test_qrcode_tab.py -v
```
Expected: PASS。

- [ ] **Step 3: Commit**

```bash
git add tests/views/tabs/test_qrcode_tab.py
git commit -m "test: cover image-drop signal and generate-subtab drop rejection"
```

---

### Task 9: PyInstaller 打包验证与最终全量验证

**Files:**
- 可能 Modify: PyInstaller `.spec` 文件（若 pyzbar DLL 未被自动收集）

- [ ] **Step 1: 运行全量测试套件**

Run:
```bash
.venv\Scripts\python.exe -m pytest tests/ -v -k "qr"
```
Expected: PASS — 所有 QR 相关（service + tab）测试通过。

- [ ] **Step 2: 检查 pyzbar 在 PyInstaller spec 中的收集**

查看 `.spec` 文件（项目根目录）：

Run:
```bash
dir /b *.spec
```

打开主 spec 文件，检查 `datas`/`binaries`/`hiddenimports` 是否需要加 pyzbar。在 spec 的 `Analysis(...)` 里，若有 `hiddenimports=[...]`，追加：

```python
    hiddenimports=[
        # ... 现有项 ...
        "pyzbar",
        "pyzbar.pyzbar",
    ],
```

并在 `binaries`（若存在）里加 pyzbar 的 native DLL 收集。最稳妥的做法是在 spec 顶部加：

```python
from PyInstaller.utils.hooks import collect_dynamic_libs

pyzbar_binaries = collect_dynamic_libs("pyzbar")
```

然后把 `pyzbar_binaries` 加入 `Analysis(binaries=[...] + pyzbar_binaries)`。

> **若没有现成 spec 或不确定：** 用 PyInstaller 的 `--collect-all` 试打包验证：
> ```bash
> .venv\Scripts\python.exe -m PyInstaller --collect-all pyzbar --noconfirm <entrypoint>
> ```
> 验证打包后的 exe 能 import pyzbar。此项若环境受限可标记为「后续验证」，不阻塞功能合并。

- [ ] **Step 3: 运行 ruff 检查新增代码风格**

Run:
```bash
.venv\Scripts\python.exe -m ruff check src/vibeocr/services/qrcode_decode_service.py src/vibeocr/views/tabs/qrcode_tab.py
```
Expected: 无错误（有 warning 视情况修）。

- [ ] **Step 4: 手动冒烟测试（最终验收）**

启动应用：

```bash
.venv\Scripts\python.exe -m vibeocr
```

手动验证清单：
1. 顶层 tab 标题为「二维码」（不再是「二维码生成」）。
2. 进入「二维码」tab，右侧有「生成」「识别」两个子标签页。
3. 「生成」子页：输入内容 → 左侧自动生成二维码预览（原功能正常）。
4. 切到「识别」子页：左侧显示占位文字「粘贴、拖入或选择图片以识别」。
5. 用截图工具截一张含二维码的图 → 在「识别」子页按 Ctrl+V → 左侧显示该图 → 「🔍识别」按钮启用。
6. 点「🔍识别」→ 结果列表显示一条，URL 项有「🔗打开」按钮且链接可点击。
7. 点「🔗打开」→ 系统默认浏览器打开该 URL。
8. 把一张二维码图片文件拖到左侧预览区 → 自动显示 → 可识别。
9. 点「选择图片...」→ 选一张二维码 PNG → 显示并识别。
10. 切回「生成」子页 → 预览恢复为之前生成的二维码；切回「识别」→ 预览恢复为之前待识别图。
11. 点「清空」→ 预览与结果清空，「🔍识别」按钮禁用。
12. 点「复制全部」→ 剪贴板含所有结果文本。

- [ ] **Step 5: Commit（若有 spec 改动）**

```bash
git add *.spec
git commit -m "build: ensure pyzbar native libs collected by PyInstaller"
```

---

## Self-Review 清单（计划完成后执行）

**Spec 覆盖：**

| Spec 要求 | 对应 Task |
|---|---|
| 标签页改名「二维码」 | Task 7 |
| 嵌套子标签页（生成/识别） | Task 4 |
| 左侧预览支持粘贴图片 | Task 6（`_on_paste_image` + Ctrl+V shortcut） |
| 左侧预览支持拖入图片 | Task 4（DropLabel）+ Task 6（`_on_image_input`） |
| 选择文件加载图片 | Task 6（`_on_select_image`） |
| 手动「识别」按钮触发解码 | Task 6（`_on_decode`） |
| 结果列表展示 | Task 5（`_build_decode_panel` + `DecodeResultWidget`） |
| URL 可点击调用系统浏览器 | Task 6（`_on_open_url` via `QDesktopServices`） |
| QrcodeDecodeService | Task 2 |
| pyzbar 依赖 | Task 1 |
| 大图保护 | Task 2（`decode` 内 thumbnail） |
| URL 安全校验 | Task 2（`_is_http_url`）+ Task 3 测试 |
| 服务层测试 | Task 2 + Task 3 |
| UI 测试 | Task 4 + Task 6 + Task 8 |

**Placeholder 扫描：** 无 TBD/TODO；所有代码块完整。

**类型/方法名一致性：** `_on_image_input`、`_on_clear_decode`、`_on_open_url`、`_on_copy_all`、`_on_copy_single`、`_on_decode`、`_on_paste_image`、`_on_select_image`、`_on_sub_tab_changed`、`_build_generate_panel`、`_build_decode_panel`、`_qpixmap_to_pil`、`_decode_type_label`、`_escape_for_richtext`、`DropLabel`、`DecodeResultWidget`、`DecodedItem`、`QrcodeDecodeService` — 各 Task 间命名一致。属性 `_decode_pending_pixmap`、`_decode_results`、`_gen_preview_pixmap`、`_decode_paste_shortcut` 一致。

---

## 执行选择

计划已完成并保存在 `docs/superpowers/plans/2026-06-15-qrcode-decode.md`。两种执行方式：

**1. Subagent-Driven（推荐）** — 每个 Task 派一个全新 subagent 执行，Task 间我做两阶段审查，迭代快、上下文干净。

**2. Inline Execution** — 在当前会话内用 executing-plans 批量执行，带检查点审查。

**选哪种？**
