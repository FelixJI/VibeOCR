# PDF 添加文字层功能修复设计

- 日期:2026-06-19
- 分支:develop
- 状态:已批准,待实现规划

## 1. 背景与问题

PDF 处理标签页的"添加文字层"功能存在四个问题,经代码审查确认:

1. **措辞误导**:`_update_layer_status`(`pdf_tab.py:342`)输出"第N页: 12层文字层",其中 `12` 实际是 `detect_text_layers` 检测到的**文本块数量**(`pdf_service.py:101-138`),而非"层数"。用户误以为叠加了 12 个层。

2. **文字层未有效写入(最严重)**:`add_text_layer`(`pdf_service.py:292-353`)有三个失效点:
   - `insert_textbox` 未指定字体(行 339-345),PyMuPDF 回退到 Base14 字体,**不含中文字形**,中文内容被静默丢弃;
   - 默认 `render_mode=3`(隐形,`pdf_ocr_options.py:33`),用户无可见反馈;
   - 字号重试失败时(行 346-350)静默 `break`,无日志、无计数。

3. **无法预览**:预览功能(`pdf_tab.py:661-693`)依赖 `page_info.text_layers` 非空,而问题 2 导致中文场景下该列表为空,用户点预览即收到"无文字层"提示。这是问题 2 的下游表现。

4. **布局不可拖**:`pdf_tab.py:82` 的 `setFixedWidth(200)` 把缩略图列表钉死,使 `QSplitter(Horizontal)` 的分隔条无法实际改变左右比例;右侧操作面板为单一 `QVBoxLayout`,无纵向分隔;状态标签是裸 `QLabel`,多页文字被挤压。

## 2. 目标

- 让中文文字层真正写入 PDF 并可被外部阅读器提取/搜索;
- 消除"X层"误导措辞,提供准确的块数信息;
- 加完后用户能立刻确认效果;
- 界面各部分可拖动调整大小,预览内嵌。

## 3. 不做(YAGNI 边界)

- 不引入"原生文本 vs 本次新增文本"区分机制(文案选型已排除);
- 不打包思源等外部 CJK 字体(用 PyMuPDF 内置 CID 字体);
- 不改 `delete_text_layers` / `detect_text_layers` 逻辑(它们工作正常);
- 不构建全新的预览 UI 系统(复用现有 `_PreviewCanvas`)。

## 4. 设计决策(已与用户确认)

| 决策点 | 选择 |
|---|---|
| CJK 字体 | PyMuPDF 内置 CID 字体 `china-s`(零体积) |
| 默认可见性 | 隐形 `render_mode=3`(OCRmyPDF 标准),靠自动预览给反馈 |
| 状态文案 | "已添加文字层(M 个文本块)" |
| 布局范围 | 横纵双向可拖 Splitter + 内嵌预览 |
| 预览触发 | 完成后自动预览(高亮色块) |
| 失败处理 | 日志 + 完成后汇总提示 |
| 内嵌预览初始状态 | 默认折叠,按需展开 |
| Splitter 布局记忆 | QSettings 记住并恢复 |

## 5. 详细设计

### 5.1 文字层写入修复(`services/pdf_service.py`)

`add_text_layer` 改动:

1. **CJK 字体**:`page.insert_textbox(...)` 增加 `fontname="china-s"`(PyMuPDF 对 Adobe CID 中日韩字体的封装,自动嵌入子集)。失败回退:`china-ss`(简体备选)。
2. **返回值**:签名改为返回 `tuple[int, int]` `(written_count, skipped_count)`:
   - bbox 为空/过小被 `continue` → `skipped`
   - 字号重试耗尽仍 `rc < 0` → `skipped` + `logger.warning("page %d block skipped: rect=%s text=%r", page_index, rect, block.text[:30])`
   - 成功 `rc >= 0` → `written`
3. **消除静默 break**:字号重试循环失败时记录 warning(见上),不再默默 break。
4. **render_mode**:维持 `0 if settings.text_layer_visible else 3`,默认 False(隐形)。

### 5.2 数据流:统计与信号(`managers/pdf_session_manager.py`)

1. `PdfSession` 新增字段 `_ocr_stats: dict` 累计 `{written, skipped}`。
2. `_on_ocr_page_done` 调用 `add_text_layer` 取返回值,累加到 `session._ocr_stats`。
3. **新增独立信号** `ocr_stats_ready = Signal(str, int, int)`(session_id, written, skipped),在 `_on_ocr_all_done` 中 emit。不改现有 `ocr_done(session_id, success, fail)` 签名,避免影响其他订阅者。

### 5.3 状态文案与状态区(`views/tabs/pdf_tab.py`)

1. **文案**:`_update_layer_status` 改为:
   ```python
   lines.append(f"第{p.page_index + 1}页: 已添加文字层({len(p.text_layers)} 个文本块)")
   ```
   其余分支(扫描件/无文字层)不变。
2. **状态区可滚动**:`_layer_status_label` 设 `setWordWrap(True)` + `setAlignment(Qt.AlignTop)`,外层包 `QScrollArea(setWidgetResizable=True)`,最小高度 120px,放进 `text_group` 布局。

### 5.4 布局重构(`views/tabs/pdf_tab.py`)

新结构(嵌套 Splitter):
```
QSplitter(Horizontal)  [main_splitter]          ← 真正可拖
├─ ThumbnailPanel        [min 120, 无 fixed]
└─ QSplitter(Vertical)  [right_splitter]        ← 纵向可拖
   ├─ OperationPanel     [按钮区 + 文字层操作 + 状态区]
   └─ PreviewPanel       [内嵌预览画布]
```

改动:
1. 去掉 `_thumbnail_list.setFixedWidth(200)`(`pdf_tab.py:82`),改 `setMinimumWidth(120)`。
2. 右侧新增 `QSplitter(Vertical)`(`right_splitter`),上半 `OperationPanel`,下半 `PreviewPanel`。
3. 两个 splitter 均 `setChildrenCollapsible(False)`。
4. 初始尺寸:`main_splitter.setSizes([200, 600])`;`right_splitter` 预览区默认折叠(给小尺寸如 40px),操作区占大部分。
5. **QSettings 持久化**:`main_splitter` 与 `right_splitter` 的 `saveState()`/`restoreState()` 通过项目已有偏好后端(复用 `OCRPreferences` 的存储)存取。

### 5.5 内嵌预览(`views/pdf_preview_window.py` + `views/tabs/pdf_tab.py`)

1. 把 `pdf_preview_window.py` 中的 `_PreviewCanvas` 抽为模块级公开类 `PreviewCanvas`(支持缩放/hover/高亮),供内嵌与弹窗共用。
2. `PdfPreviewWindow`(弹窗)保留,双击缩略图路径不变。
3. `PdfTab.PreviewPanel` 内嵌一个 `PreviewCanvas`,完成后由 `_on_ocr_done` 触发自动预览(见 5.6)。

### 5.6 自动预览 + 失败汇总(`views/tabs/pdf_tab.py`)

`_on_ocr_done` 完成后的流程:
1. `_update_layer_status()`(文案刷新);
2. `_refresh_thumbnails()`;
3. 接 `ocr_stats_ready` 信号:若 `skipped > 0`,弹 `QMessageBox.information("文字层已添加:成功 M 块,跳过 K 块(详见日志)")`;否则状态栏轻量提示"文字层已添加(M 块)";
4. **自动预览**:对选中页第一页(或第 0 页)调用内嵌 `PreviewCanvas`,显示高亮色块——因 render_mode=3 隐形,用色块可视化文字层位置。预览内容来自 `page_info.text_layers`(问题 2 修复后非空)。

## 6. 改动文件清单

| 文件 | 改动 | 风险 |
|---|---|---|
| `services/pdf_service.py` | `add_text_layer` 加 `china-s`、返回 `(written, skipped)`、失败 logging | 中:CID 字体兼容性需冒烟测试 |
| `managers/pdf_session_manager.py` | stats 累加、新增 `ocr_stats_ready` 信号、`PdfSession` 加字段 | 低:纯增量 |
| `views/tabs/pdf_tab.py` | 文案、状态区 QScrollArea、嵌套 Splitter、内嵌预览、接 stats 信号、QSettings 持久化 | 中:布局重构面大,需防回归 |
| `views/pdf_preview_window.py` | `_PreviewCanvas` 抽为公开 `PreviewCanvas` | 低:纯抽取 |

## 7. 风险与缓解

1. **china-s 字体兼容性**:实现时先跑冒烟测试(fitz 创建空页 → insert_textbox 中文 + `fontname="china-s"` → `page.get_text()` 回读确认含中文)。失败回退 `china-ss` 或系统字体探测。
2. **嵌套 Splitter 布局回归**:现有按钮点击、缩略图、文件选择行为需测试覆盖。
3. **`PreviewCanvas` 抽取**:确保弹窗式 `PdfPreviewWindow` 仍工作。

## 8. 测试矩阵

| 测试 | 类型 | 验证点 |
|---|---|---|
| `add_text_layer` 中文写入 | 单元 | `get_text` 回读含中文、`written > 0` |
| 过小 bbox 失败计数 | 单元 | `skipped` 正确、有 warning(caplog) |
| `_update_layer_status` 文案 | 单元 | 输出含"已添加文字层(N 个文本块)" |
| `ocr_stats_ready` 信号累加 | 单元 | 多页累加后 written/skipped 正确 |
| Splitter 可伸缩性 | 单元(GUI) | thumbnail 无 fixedWidth、right_splitter 存在 |
| QSettings 保存/恢复 | 单元 | saveState → restoreState 后比例一致 |
| `PreviewCanvas` 抽取 | 单元 | 弹窗与内嵌共用,行为不变 |

## 9. 验证清单(实现完成后)

- 全量 `pytest` 通过;
- 手动:打开中文扫描 PDF → 添加文字层 → 确认:
  1. 状态文案"已添加文字层(N 个文本块)"正确;
  2. 自动预览弹出高亮色块;
  3. 拖动两个分隔条可调,布局记忆并在重启后恢复;
  4. 导出 PDF 后用外部阅读器(SumatraPDF)能搜索到中文。
