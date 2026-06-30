# PDF 处理界面异步化与性能优化设计

**日期**: 2026-06-29
**状态**: 待实现
**范围**: PDF 标签页的全部 fitz CPU 密集操作异步化 + 删除文字层算法修正 + 保存策略优化

---

## 1. 背景与痛点

PDF 处理界面（`PdfTab`）存在 5 个性能/正确性痛点，根因同源：fitz 的 CPU 密集操作散落在主线程，导致 UI 冻结。

| # | 痛点 | 根因位置 |
|---|------|---------|
| 1 | 打开大 PDF 卡很久 | `PdfService.open_doc` 主线程 `doc[i].rotation` 遍历每页（`pdf_service.py:40-43`） |
| 2 | 批量添加文字层页面无响应 | `PdfSessionManager.start_ocr` 主线程串行 300dpi 渲染所有页（`pdf_session_manager.py:230-241`） |
| 3 | 集中删除文字层卡很久 | `PdfTab._on_delete_text_layer` 主线程串行 redact 循环（`pdf_tab.py:1257-1263`） |
| 4 | 删除文字层有遗漏 | redact 用 `get_text("dict")` 的顶层 block bbox，粒度粗（`pdf_service.py:591-597`） |
| 5 | 添加文字层后保存很慢 | `_on_save` → `rewrite_modified_pages()` 主线程逐页 redact + 落盘（`pdf_tab.py:818-829`） |

### 已有的异步基础

项目已搭好部分异步骨架：
- `PdfLoadWorker`（QThread）：逐页文字层检测 + 缩略图渲染，**已异步**
- `PdfOcrWorker`（QThread）：OCR 识别，**已异步**，但只接收预渲染数组，前置渲染仍在主线程

本设计在此骨架上扩展，把剩余的 fitz 重活搬到后台。

### 关键数据

- 默认 `render_dpi=300`（`pdf_ocr_options.py:32`）
- A4 页 300dpi ≈ 2480×3508 ≈ 870 万像素/页，单页 RGB numpy 数组 ≈ 26MB
- 批量 OCR 几十页 = 几百 MB 到 GB 级主线程同步渲染

### 调研结论：增量保存

PyMuPDF 增量保存（`incremental=True`）只追加 delta，对小修改极快；但有硬限制：**结构性改动（删页/插页/重排）不能用 incremental save**（PyMuPDF Issue #3136）。

现有代码覆盖原文件已用 `incremental=True`（`pdf_service.py:59`），保存慢的瓶颈不在落盘方式，而在前置 `rewrite_modified_pages()` 的逐页 redact。

来源：
- [How to Save a PDF Document with PyMuPDF – Artifex Blog](https://artifex.com/blog/how-to-save-a-pdf-document-with-pymupdf-encryption-incremental-saving)
- [PyMuPDF Issue #3136](https://github.com/pymupdf/PyMuPDF/issues/3136)
- [PyMuPDF Document API](https://pymupdf.readthedocs.io/en/latest/document.html)

---

## 2. 整体方案

**一条主线**：引入通用 `PdfMutateWorker`（协作式取消）承接所有 fitz 重活；批量 OCR 改为流式"边渲染边识别"；删除文字层改为词级 redact + 循环验证至清零；保存按改动类型分流（内容编辑 incremental / 结构改动 full save）。

### 设计原则

- 复用现有 worker 模式（QThread + 信号 + 协作取消）
- 协作式取消统一替换不安全的场景（`_wait_thread` 的 terminate 兜底**保留不动**，新 worker 用 `cancel() + wait`）
- doc_lock 协议：worker 持有 doc_lock 引用，每个 fitz 操作前 `with doc_lock:`
- 错误隔离：逐页异常不拖垮整批
- YAGNI：不做线程池复用、任务队列、自动保存

---

## 3. 组件设计

### 3.1 PdfMutateWorker（新增）

通用后台任务承载，单 doc 绑定，一次任务一个实例。

**职责**：接收一个任务描述（`MutateTask`），在后台逐页或一次性执行 fitz 操作，通过信号反馈进度。

**任务类型（TaskKind 枚举）**：

| TaskKind | 输入字段 | 模式 | 每页/整体动作 |
|----------|---------|------|-------------|
| `DELETE_TEXT_LAYER` | page_indices | 逐页 | 词级 redact + 循环验证 |
| `ROTATE` | page_indices, angle | 逐页 | set_rotation |
| `DELETE_PAGES` | page_indices | 整体 | doc.delete_page（批量） |
| `REORDER` | new_order | 整体 | doc.select |
| `INSERT_BLANK` | after_index, width, height | 整体 | new_page + build_page_infos |
| `INSERT_FROM` | source_path, after_index | 整体 | insert_pdf + build_page_infos |
| `SAVE` | path=None | 混合 | rewrite 逐页 + 落盘 |
| `SAVE_AS` | path | 混合 | rewrite 逐页 + 落盘 |

> 注：`OPEN` 不进 MutateWorker（见 3.5）。

**接口契约**：
```python
class PdfMutateWorker(QThread):
    page_done = Signal(int, object)   # (page_index, payload) 逐页任务
    progress = Signal(int, int)        # (current, total)
    all_done = Signal(str, object)     # (session_id, result) 成功
    failed = Signal(str, str)          # (session_id, error_msg) 整体失败

    def __init__(self, session_id, doc, pdf_document, doc_lock, task: MutateTask): ...
    def cancel(self) -> None: self._cancelled = True
```

`MutateTask`：frozen dataclass，`kind: TaskKind` + 各 kind 所需字段。

**关键设计点**：
1. 协作式取消：`cancel()` 置 `_cancelled=True`，worker 在页循环顶部检查，自然退出后 emit `all_done`（partial）。
2. doc_lock 协议：每个 fitz 操作前 `with self._doc_lock:`。
3. 错误隔离：逐页任务单页异常 → 记日志、emit `page_done(index, None)`、继续下一页；一次性任务异常 → emit `failed`。
4. 不调 `terminate()`：新 worker 取消用 `cancel() + wait(timeout)`，超时记日志。

### 3.2 PdfRenderWorker（新增）+ PdfOcrWorker（改造）

解决痛点 2（批量 OCR 主线程渲染冻结）。

**PdfRenderWorker**（轻量 QThread）：
- 输入：`(session_id, doc, doc_lock, page_indices, pdf_settings)`
- 逐页：`with doc_lock: render_page_as_array(...)` → 推入 `queue.Queue(maxsize=2)`
- 全部完成或取消后向 queue 推哨兵（None）
- emit `render_progress(session_id, current, total)` 供 UI 前置阶段提示

**PdfOcrWorker 改造为流式消费**：
- 输入：`(session_id, ocr_service, ocr_options, render_queue)`
- `run()` 循环 `render_queue.get()` → 识别 → emit `page_done`，收到哨兵（None）结束
- 保留 batch 识别（攒 queue 内积压的几页批量 predict），但受 queue maxsize 限制不会回到全预渲染
- 逐张容错（`_recognize_batch`）不变

**manager 端编排（`start_ocr` 改造）**：
```python
def start_ocr(self, page_indices, ...):
    self._cancel_ocr_pipeline()  # 取消旧 render + ocr worker
    render_queue = queue.Queue(maxsize=2)
    self._render_worker = PdfRenderWorker(session_id, doc, doc_lock, page_indices, pdf_settings)
    self._ocr_worker = PdfOcrWorker(session_id, self._ocr_service, ocr_options, render_queue)
    self._render_worker.start()
    self._ocr_worker.start()
```

**关键设计点**：
1. 背压与内存：queue `maxsize=2`，渲染最多领先 OCR 2 页，内存峰值 ≈ 3 页数组，主线程零参与渲染。
2. 顺序保证：渲染按 page_indices 顺序入队，OCR 单线程按出队顺序识别，page_index 顺序一致。
3. 错误隔离：渲染单页失败 → 推 `(idx, None)`，OCR 收到 None 计 fail。
4. 取消传播：`cancel_ocr()` 同时取消两 worker；render 取消后推哨兵，OCR 收到即结束，避免 `queue.get()` 永久阻塞。
5. UI 前置提示：render 阶段进度条显示"正在渲染页面…"（`render_progress` 信号），OCR 产出第一页后切"正在识别第 N/M 页"。

### 3.3 PdfExportWorker（新增，独立）

解决批量导出跨 session。与 MutateWorker（单 doc 绑定）正交。

**职责**：遍历多个 modified session，各经其 doc_lock，对每个 session 先 `save_with_rewrite` 再落盘到目标目录。

**接口契约**：
```python
class PdfExportWorker(QThread):
    progress = Signal(int, int, str)   # (current, total, file_name)
    done = Signal(list)                 # (exported_paths)

    def __init__(self, sessions: list[tuple[str, PdfSession]], output_dir: str): ...
    def cancel(self) -> None: self._cancelled = True
```

串行处理各 session（不并行，避免 doc_lock 跨 session 竞争）。

### 3.4 删除文字层算法修正（痛点 4）

改造 `PdfService.delete_text_layers`。

**现状（block 级，会漏）**：
```python
page_dict = page.get_text("dict")
for block in page_dict["blocks"]:
    if block["type"] != 0: continue
    page.add_redact_annot(fitz.Rect(block["bbox"]))  # block 粒度
page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
```

**改为（词级 + 循环验证，仅对有残留的页循环）**：

两层循环语义：
- **Worker 层（页间）**：遍历 page_indices，用 `page.get_text()` 预检，**无文字的页直接跳过**（emit 完成，不进 redact）。
- **PdfService 层（页内）**：单页 redact 循环，仅当 `get_text()` 仍有残留才继续，最多 5 轮。

```python
MAX_ROUNDS = 5  # PdfService 内部模块常量，不暴露配置

# PdfService.delete_text_layers 页内循环
for round_idx in range(MAX_ROUNDS):
    words = page.get_text("words")  # 词级 (x0,y0,x1,y1,word,...)
    if not words:
        break                        # 已清零，退出（绝大多数页 1 轮结束）
    for w in words:
        page.add_redact_annot(fitz.Rect(w[:4]), fill=None)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
residual = bool(page.get_text().strip())
```

**返回值**：`(deleted_count, rounds_used, has_residual)`。

**Worker DELETE_TEXT_LAYER 逐页逻辑**：
```python
residual_pages = []
for page_index in page_indices:
    if self._cancelled: break
    page = doc[page_index]
    if not page.get_text().strip():          # 无文字 → 跳过
        emit page_done(page_index, (0, 0, False))
        continue
    deleted, rounds, residual = PdfService.delete_text_layers(doc, pdf_document, page_index)
    emit page_done(page_index, (deleted, rounds, residual))
    if residual:
        residual_pages.append(page_index)
emit all_done(session_id, {"residual_pages": residual_pages})
```

**残留用户提示**（PdfTab 收到 `delete_layer_done(file_path, residual_pages)`）：
```python
if residual_pages:
    QMessageBox.warning(self, "删除文字层",
        f"第 {', '.join(str(p+1) for p in residual_pages)} 页经多轮删除仍有少量残留文字，\n"
        f"可能是特殊字体或嵌入图片文字，建议手动检查。")
```

**冗余清理**：删除末尾的 `update_page_info` 调用（删完文字层后这页已无文字，重跑 `detect_text_layers` + `is_page_scanned` 纯属浪费）。改为直接置 `info.has_text_layer=False / text_layers=[] / is_scanned=False`。

### 3.5 打开大 PDF 异步化（痛点 1）

**主线程只做最小工作**：`fitz.open` + 创建无 rotation 占位页，立即返回。

```python
# PdfService.open_doc（主线程，精简）
def open_doc(file_path):
    if not Path(file_path).exists(): raise FileNotFoundError(...)
    doc = fitz.open(file_path)
    if doc.is_encrypted: doc.close(); raise RuntimeError("不支持加密 PDF")
    pdf_document = PdfDocument(file_path=file_path)
    pdf_document.pages = [
        PdfPageInfo(page_index=i) for i in range(doc.page_count)  # 不读 rotation
    ]
    return doc, pdf_document
```

- rotation 由 `PdfLoadWorker` 后台逐页覆盖（已有逻辑，`pdf_load_worker.py:71-77`，每页 `page.rotation` 已取到）。
- 在 LoadWorker 完成前，占位页 rotation=0；`PdfService.rotate_pages` 读的是 `doc[idx].rotation`（真实值）而非占位页的 0，所以不会错。
- 加密/不存在校验保留主线程同步（前置校验必须即时反馈）。
- **不引入单独 PdfOpenWorker**：`fitz.open` 本身够快，慢的是 `doc[i].rotation` 遍历，已由 LoadWorker 兜底。

**加载提示**：LoadWorker 的 `load_progress(file_path, loaded, total)` 信号（manager 已 emit）→ PdfTab 状态栏显示"正在加载 N/M 页…"；全部完成后显示"加载完成"（`_on_load_done` 已有，补进度文案）。

占位页在 LoadWorker 填充前显示 placeholder 灰图（已有机制），不显示错误方向缩略图。

### 3.6 保存/另存为异步化 + 策略优化（痛点 5）

三层优化叠加。

#### 优化 1：结构改动追踪，区分保存策略

`PdfDocument` 新增 `has_structural_change: bool` 标志（与 `is_modified` 正交）：
- `delete_pages` / `insert_blank_page` / `insert_pages_from` / `move_page` / `reorder_pages` → 置 `has_structural_change=True`（改变页数或页序，incremental save 不支持）
- 纯文字层操作（add/delete/rewrite text layer、改字）→ 不置
- `rotate_pages` → **不置**（set_rotation 是页属性修改，incremental save 支持，走快路径）
- `save` 成功后重置 `has_structural_change=False`

保存策略：
- **覆盖原文件 + 无结构改动** → `incremental=True`（快，只追加 delta）
- **覆盖原文件 + 有结构改动** → full save（`garbage=4, deflate=True` 压缩）
- **另存为** → 永远 full save（`deflate=True`）

#### 优化 2：rewrite 只做必要页 + 异步化

`PdfService` 新增 `save_with_rewrite(doc, pdf_document, path, pdf_settings) -> SaveResult`：
```python
def save_with_rewrite(doc, pdf_document, path, pdf_settings):
    rewritten = []
    for info in pdf_document.pages:
        if not info.ocr_text_blocks: continue
        PdfService.rewrite_text_layer(doc, pdf_document, info.page_index, ...)
        rewritten.append(info.page_index)
    # 按结构改动分流落盘
    if path is None:
        if pdf_document.has_structural_change:
            # full save（备份 → 全量写）
            ...
        else:
            # incremental save（备份 → 增量）
            ...
    else:
        doc.save(path, deflate=True)
    pdf_document.is_modified = False
    pdf_document.has_structural_change = False
    return SaveResult(rewritten_pages=rewritten, path=...)
```

`SAVE` / `SAVE_AS` 作为 `MutateTask`：rewrite 阶段逐页 emit `progress`（"正在保存 N/M 页"），落盘阶段 emit 不确定进度 + 文案"正在写入文件…"。

`PdfSessionManager` 新增 `save_async(path=None)`：构造 task + 启动 worker + 连信号。

#### 优化 3：rewrite 复用词级 redact

`rewrite_text_layer` → `delete_text_layers`（第 3.4 节词级 redact + 循环）→ `_write_blocks_to_page`。保存慢和删除慢一起解决；rewrite 里 redact 的冗余重检也一并去掉。

#### UI 交互

保存期间禁用所有按钮 + 进度提示（复用 `_set_file_buttons_enabled(False)`）：
- rewrite 阶段：逐页确定进度（0/N），文案"正在保存…"
- 落盘阶段：不确定进度（`setRange(0,0)`），文案"正在写入文件…"
- 完成：`save_done` → 复位按钮 + 隐藏进度条 + `_update_status()`
- 失败：`mutate_failed` → 弹"保存失败"对话框，is_modified 保持 True，按钮恢复

---

## 4. 信号契约

### PdfSessionManager 新增信号

```python
mutate_progress = Signal(str, int, int)      # (file_path, current, total)
mutate_done = Signal(str, object)            # (file_path, result)
mutate_failed = Signal(str, str)             # (file_path, error)
save_done = Signal(str)                      # (file_path)
delete_layer_done = Signal(str, list)        # (file_path, residual_pages)
render_progress = Signal(str, int, int)      # (file_path, current, total) OCR 渲染前置
export_progress = Signal(int, int, str)      # (current, total, file_name)
export_done = Signal(list)                   # (exported_paths)
```

现有信号（session_added/removed/active_changed/page_loaded/load_progress/load_done/ocr_page_done/ocr_progress/ocr_done/ocr_stats_ready/mineru_models_status）保留不变。

---

## 5. 取消协议

- **协作式取消统一**：所有新 worker 用 `_cancelled` 标志，`cancel()` 置位，worker 在页循环顶部检查。
- **OCR 流水线取消**：`cancel_ocr_pipeline()` 同时取消 render + ocr worker；render 取消后向 queue 推哨兵（None），ocr worker 收到即结束。
- **mutate 取消**：`cancel_mutate()` 取消当前 mutate worker，已处理页保留（部分完成），UI 复位。
- **`_wait_thread` 保留**：现有 load/ocr worker 的取消仍走它（含 terminate 兜底）；新 worker 用 `cancel() + wait(timeout)`，超时记日志不 terminate。

---

## 6. 错误处理矩阵

| 场景 | 处理 |
|------|------|
| 单页 redact/render 异常 | 记日志，emit 该页 done(None/skip)，继续下一页 |
| mutate 一次性任务异常 | emit `mutate_failed`，UI 弹错误对话框，状态不变 |
| 保存落盘失败 | 回滚备份（覆盖模式），emit failed，is_modified 保持 True |
| 删除文字层残留 | emit residual_pages，UI warning 提示 |
| OCR 单页失败 | 已有逐张容错（`_recognize_batch`），计 fail |

---

## 7. 测试策略

### PdfService 层（纯函数，无 Qt）
- `delete_text_layers` 词级 + 循环：构造含嵌套/合并文本块的 PDF，验证多轮清零、residual 检测、MAX_ROUNDS=5 上限
- `save_with_rewrite`：覆盖 incremental（纯内容）/ full（结构改动）分流，备份回滚
- `open_doc`：占位页 rotation=0，不读 doc[i]

### Worker 层（QThread）
- PdfMutateWorker 各 task kind：mock doc，验证 page_done/progress/all_done 信号序列、协作取消中途退出
- PdfRenderWorker + PdfOcrWorker 流式：验证 queue 背压、顺序保证、哨兵终止、取消传播
- PdfExportWorker：多 session 遍历，各经 doc_lock

### Manager 层
- `save_async` / `delete_text_layers_async` / `start_ocr`（流式）：信号中转、has_structural_change 标志置位/复位
- 现有测试（`tests/managers/test_pdf_session_manager.py`）适配 worker 异步化（mock worker + 信号驱动，已有模式如 `TestOcrOverwritePassThrough`）

### PdfTab 层
- 新增加载提示、渲染前置提示、删除残留 warning、保存进度 UI 状态机

### 回归重点
- 删除文字层遗漏（痛点 4）端到端验证：构造历史上会漏的 PDF（表格 cell、ActualText span、合并异常块），确认循环清零
- 保存正确性：rewrite 后重新打开验证文字层内容一致

---

## 8. 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/vibeocr/services/pdf_service.py` | `delete_text_layers` 词级+循环、新增 `save_with_rewrite`、`open_doc` 精简、`save` 分流、去掉冗余 `update_page_info` |
| `src/vibeocr/models/pdf_document.py` | 新增 `has_structural_change` 字段 |
| `src/vibeocr/workers/pdf_mutate_worker.py` | **新增** PdfMutateWorker + MutateTask + TaskKind |
| `src/vibeocr/workers/pdf_render_worker.py` | **新增** PdfRenderWorker |
| `src/vibeocr/workers/pdf_export_worker.py` | **新增** PdfExportWorker |
| `src/vibeocr/workers/pdf_ocr_worker.py` | 改造为 queue 流式消费 |
| `src/vibeocr/managers/pdf_session_manager.py` | `start_ocr` 流式编排、新增 `save_async`/`delete_text_layers_async` 等、新信号、worker 生命周期管理 |
| `src/vibeocr/views/tabs/pdf_tab.py` | `_on_save`/`_on_save_as`/`_on_delete_text_layer`/`_on_export_all` 改异步、加载提示、渲染前置提示、删除残留 warning、UI 状态机 |
| `tests/managers/test_pdf_session_manager.py` | 适配异步化 |
| 新增 worker 测试 | 各 worker 单元测试 |
| 新增 pdf_service 测试 | delete_text_layers/save_with_rewrite/open_doc |

---

## 9. 不做的事（YAGNI）

- 不做线程池复用（一次一实例足够，PDF 操作低频且需序列化）
- 不做任务队列（manager 串行发起）
- 不把 LoadWorker/OcrWorker 合并进 MutateWorker（职责清晰，改动风险高）
- 不并行多页 OCR（保持单 OCR worker 串行消费，避免 GPU/模型并发竞争）
- 不做自动保存/草稿
- 不把 `fitz.open` 本身异步化（本地文件够快）
- 不把 `MAX_ROUNDS` 暴露为用户配置（内部实现细节）
