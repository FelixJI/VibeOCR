# PDF OCR 独立设置与文字层修复设计

> 日期: 2026-06-12
> 状态: 待审核

## 背景

当前 PDF 文字层添加功能存在三个问题：

1. **无独立配置**：PDF 处理使用默认 `OCROptions()`（通用 OCR 管道），无法在设置中配置，与截图识别共用同一套选项，但 PDF 场景需要不同的默认值和额外参数（DPI、内存控制、字号策略等）。
2. **Bbox 完全错乱**：PDF 页面以 300DPI 渲染后，OCR 预处理（`use_doc_orientation_classify=True` 默认开启）会旋转图像，但 `add_text_layer()` 将归一化 bbox 直接映射到 PDF `page_rect`，未做逆旋转变换，导致文字层坐标完全错位。
3. **无法预览文字层**：`render_mode=3` 写入隐形文字，`PdfPreviewWindow` 的高亮功能使用的是 PDF 内部坐标但渲染时按像素绘制，坐标转换不正确。

## 设计

### 1. 数据模型 — `PdfOcrOptions`

新增 `models/pdf_ocr_options.py`，继承 `OCROptions` 的管道选项，增加 PDF 专用参数。

```python
@dataclass
class PdfOcrOptions:
    """PDF OCR 独立配置"""
    # 管道
    pipeline: OCRPipeline = OCRPipeline.DOCUMENT_PARSING

    # 渲染控制
    render_dpi: int = 300                  # 范围 72-600
    max_pixels: int = 16_000_000           # 单页像素上限 (4000×4000)

    # 预处理（PDF 默认关闭，避免坐标空间问题）
    use_doc_orientation_classify: bool = False
    use_doc_unwarping: bool = False
    use_textline_orientation: bool = False

    # 文字层参数
    font_size_ratio: float = 0.8           # 字号 = rect.height × ratio
    text_layer_visible: bool = False       # True → render_mode=0, False → render_mode=3
    font_size_retry_count: int = 5
    font_size_shrink_factor: float = 0.75

    # 管道特定选项（与 OCROptions 相同字段）
    backend: str = "hybrid-auto-engine"
    parse_method: str = "auto"
    enable_formula: bool = True
    enable_table: bool = True
    lang_list: list[str] = field(default_factory=list)
    start_page_id: int = 0
    end_page_id: int | None = None
    # ... 其他管道选项
```

**关键默认值差异（对比截图 OCR）：**

| 参数 | 截图 OCR 默认 | PDF OCR 默认 | 原因 |
|---|---|---|---|
| pipeline | OCR | DOCUMENT_PARSING | PDF 需要结构化布局解析 |
| use_doc_orientation_classify | True | False | PDF 渲染器已处理旋转 |
| use_doc_unwarping | True | False | PDF 页面无扭曲 |
| use_textline_orientation | False | False | PDF 页面方向已确定 |

**内存控制逻辑：**
- 渲染前检查 `render_dpi` 对应的像素数是否超过 `max_pixels`
- 超过则自动降低 DPI：`adjusted_dpi = floor(render_dpi * sqrt(max_pixels / actual_pixels))`

**持久化：**
- `OCRPreferences` 新增 `"pdf"` 数据源，与 `"main"` / `"screenshot"` 并列
- `PdfOcrOptions` 提供 `to_dict()` / `from_dict()` 序列化方法
- 保存到 `ocr_preferences.json` 的 `"pdf"` 字段
- `OCRPreferences._CONFIG_VERSION` 升至 3

### 2. 设置页 UI

在 `SettingsPageController` 中新增 **"PDF 选项"** 导航页。

**页面结构：**

```
设置导航
├── ... (现有页面)
├── 截图选项          ← 已有
└── PDF 选项          ← 新增
    └── PdfOptionsWidget（新增组件）
        ├── 管道选择下拉框（锁定为文档类管道）
        ├── 预处理选项卡
        │   ├── 文档方向分类（默认关）
        │   ├── 文档扭曲矫正（默认关）
        │   └── 文本行方向分类（默认关）
        └── 高级选项卡
            ├── PDF 渲染 DPI：QSpinBox (72-600, 默认 300)
            ├── 单页像素上限：QSpinBox (默认 16M)
            ├── 字号比例：QDoubleSpinBox (0.1-1.0, 默认 0.8)
            ├── 字号重试次数：QSpinBox (1-20, 默认 5)
            ├── 缩放因子：QDoubleSpinBox (0.1-1.0, 默认 0.75)
            ├── 文字层可见：QCheckBox（默认关）
            └── (MineRU / PP-StructureV3 管道选项，与截图选项卡复用逻辑)
```

**持久化数据流：**

```
用户在设置页修改 PDF 选项
  → PdfOptionsWidget.options_changed → OCRPreferences.set_pipeline_options("pdf", pipeline, options)
  → 持久化到 ocr_preferences.json

PdfTab._on_add_text_layer()
  → prefs = OCRPreferences.instance()
  → options = prefs.get_pipeline_options("pdf", pipeline)
  → PdfSessionManager.start_ocr(indices, options)
```

**实现要点：**
- 新建 `widgets/pdf_options_widget.py`（参考 `PreprocessOptionsWidget` 结构）
- 管道选择调用 `lock_to_document_parsing()` 复用已有锁定逻辑
- `SettingsPageController._init_pdf_options()` 方法（参考 `_init_screenshot_options()` 实现）

### 3. Bbox 坐标逆变换修复

**问题根因：**

```
PDF 页面 (595×842 pt)
  → render_page_as_array(dpi=300) → 图像 (2480×3508 px)
  → OCR 预处理旋转 90° → 预处理图像 (3508×2480 px)
  → OCR bbox 在 (3508×2480) 坐标系中 (像素)
  → 归一化到 [0, 1000]
  → add_text_layer 映射到 page_rect (595×842)
  → ❌ bbox 错位！旋转后的坐标映射到未旋转的页面
```

**修复：** 在 `PdfService` 中新增逆变换函数：

```python
@staticmethod
def _denormalize_and_unrotate_bbox(
    bbox: tuple[float, float, float, float],  # [0, 1000] 归一化坐标
    preproc_angle: int,                        # 预处理旋转角度 (0/90/180/270)
    page_rect: fitz.Rect,                      # PDF 页面尺寸 (points)
) -> fitz.Rect:
    """将归一化 bbox 逆旋转后映射到 PDF 页面坐标。"""
```

**逆变换逻辑：**

设 `nx = bbox[0]/1000`, `ny = bbox[1]/1000`, `nx2 = bbox[2]/1000`, `ny2 = bbox[3]/1000`

| preproc_angle | 逆变换 (x, y) | 说明 |
|---|---|---|
| 0 | `(nx * page_w, ny * page_h)` | 无旋转，直接映射 |
| 90 | `(ny * page_w, (1 - nx2) * page_h)` | 逆时针90°（原图顺时针90°的逆） |
| 180 | `((1 - nx2) * page_w, (1 - ny2) * page_h)` | 中心对称 |
| 270 | `((1 - ny2) * page_w, nx * page_h)` | 顺时针90°（原图顺时针270°的逆） |

同时计算对应的 `(x2, y2)` 确保得到正确的 `fitz.Rect`。

**`add_text_layer()` 修改：**

```python
def add_text_layer(doc, pdf_document, page_index, ocr_result, pdf_options=None):
    page = doc[page_index]
    page_rect = page.rect
    preproc_angle = getattr(ocr_result, "preproc_angle", 0)
    
    for block in ocr_result.text_blocks:
        if block.bbox is None:
            continue
        # 逆旋转 + 归一化映射
        rect = PdfService._denormalize_and_unrotate_bbox(
            block.bbox, preproc_angle, page_rect
        )
        if rect.is_empty or rect.width < 1 or rect.height < 1:
            continue
        
        # 使用 pdf_options 的字号策略（默认 0.8）
        font_size_ratio = pdf_options.font_size_ratio if pdf_options else 0.8
        fontsize = rect.height * font_size_ratio
        ...
```

**需要同步传递的信息：**
- `PdfOcrWorker` 已经传递完整 `OCRResult`（包含 `preproc_angle`）
- `PdfSessionManager._on_ocr_page_done()` 调用 `add_text_layer()` 时，`ocr_result` 已包含角度信息
- 需要把 `pdf_options` 一路传递到 `add_text_layer()`

### 4. 文字层预览（高亮覆盖）

**两种预览场景：**

| 场景 | 数据源 | bbox 格式 | 触发方式 |
|---|---|---|---|
| 预览已写入的文字层 | `PdfService.detect_text_layers()` | PDF points 坐标 | "预览文字层" 按钮 |
| 预览 OCR 结果（写入前） | `OCRResult.text_blocks` | [0, 1000] 归一化 | OCR 完成后自动可选 |

**坐标转换统一接口：**

```python
@staticmethod
def bbox_to_pixel(
    bbox: tuple[float, float, float, float],
    page_rect: fitz.Rect,
    render_dpi: int,
    source: str = "pdf",  # "pdf" 或 "normalized"
) -> tuple[float, float, float, float]:
    """将 bbox 转换为渲染图像的像素坐标。"""
```

- `"pdf"` 模式：`pixel = coord / 72.0 * render_dpi`
- `"normalized"` 模式：`pixel = (coord / 1000.0 * page_size) / 72.0 * render_dpi`

**`PdfPreviewWindow` 增强：**
- `_PreviewCanvas` 的 `paintEvent()` 使用 `bbox_to_pixel()` 正确转换坐标后绘制
- 每个文字块用不同颜色的半透明矩形（复用现有 8 色方案，alpha=80）
- 矩形边框用实线（alpha=180）
- 鼠标悬停显示文字内容 tooltip
- 窗口标题显示页码和文字块数量

**`pdf_tab.py` 的预览调用修改：**

```python
def _on_preview_text_layer(self):
    session = self._session_mgr.active_session
    page_idx = selected_pages[0]
    render_dpi = 150
    
    with session.doc_lock:
        pixmap = PdfService.render_page(session.doc, page_idx, dpi=render_dpi)
        page_rect = session.doc[page_idx].rect
        text_layers = PdfService.detect_text_layers(session.doc, page_idx)
    
    # 转换 bbox 到像素坐标
    pixel_layers = [
        PdfService.bbox_to_pixel(layer.bbox, page_rect, render_dpi, source="pdf")
        for layer in text_layers
    ]
    
    preview_window.set_page_pixmap(pixmap)
    preview_window._canvas.set_highlight_layers(text_layers, render_dpi)
```

## 涉及文件

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `models/pdf_ocr_options.py` | 新增 | PDF OCR 独立配置数据模型 |
| `services/pdf_service.py` | 修改 | 添加 `_denormalize_and_unrotate_bbox()`、`bbox_to_pixel()`；修改 `add_text_layer()` |
| `widgets/pdf_options_widget.py` | 新增 | PDF 设置页组件 |
| `views/settings_page_controller.py` | 修改 | 添加 `_init_pdf_options()` |
| `utils/ocr_preferences.py` | 修改 | 新增 `"pdf"` 数据源，升级配置版本 |
| `managers/pdf_session_manager.py` | 修改 | `start_ocr()` 接收 `PdfOcrOptions`，传递到 worker 和 `add_text_layer()` |
| `workers/pdf_ocr_worker.py` | 修改 | 接收并使用 `PdfOcrOptions` |
| `views/tabs/pdf_tab.py` | 修改 | 从 `OCRPreferences` 读取 PDF 配置 |
| `views/pdf_preview_window.py` | 修改 | 修复坐标转换，添加 tooltip |
| `models/ocr_options.py` | 修改 | 确保 `PdfOcrOptions` 字段兼容（或独立文件） |

## 测试要点

1. **逆变换正确性**：针对 0°/90°/180°/270° 四种角度，构造已知 bbox 验证逆变换结果
2. **内存控制**：大 DPI + 大页面的情况验证自动降级
3. **设置持久化**：修改 PDF 设置后重启应用，验证配置恢复
4. **预览坐标**：验证已写入文字层的高亮矩形与实际文字位置对齐
5. **边界情况**：空文字层、单文字块、整页文字层
