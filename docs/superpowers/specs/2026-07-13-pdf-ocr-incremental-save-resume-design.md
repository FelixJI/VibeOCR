# PDF OCR 逐批增量落盘 + 断点续传 + UI 进度细化 设计

**日期**: 2026-07-13
**状态**: 待实现
**范围**: OCR 文字层从"逐批写内存 + 手动落盘"升级为"逐批增量落盘 + 末尾自动聚合压缩 + sidecar 断点续传"；UI 格子从二态扩展为四态；预览数据增量写入消除滞后

---

## 1. 背景与痛点

审查发现当前 PDF 文字层链路存在三个问题：

### 1.1 OCR 期间文字层不落盘，崩溃即全丢

OCR 编排在 `managers/pdf_session_manager.py:857` `_run_ocr`，三阶段批处理（渲染→识别→写层），批大小 `_OCR_BATCH_SIZE=16`。阶段 3（`:947-1019`）每批调 `client.add_text_layer_batch(...)` → 后端 `pdf_service.py:588` `add_text_layer_batch` → `:688` `_write_blocks_to_page` 调 `page.insert_text(...)`。

**核心问题**：这只写入后端子进程**内存**的 `fitz.Document`（`pdf_backend_process.py:202-218` 的 `BackendSession.doc`），全程没有 `doc.save()`。只有用户手动点"保存"（`pdf_tab.py:1633` `_on_save` → `pdf_service.py:176` `save_with_rewrite`）才真正写文件。

后果：
- **后端进程崩溃** → 内存 doc 丢失，所有已识别文字层全丢（无 checkpoint/resume，全代码库搜不到 OCR 断点续传逻辑）。
- **用户必须记得手动点保存**，否则 OCR 白做。
- 与最近 commit `d57c4bd fix(pdf): 修复 PDF 大文件 OCR 结束崩溃` 的痛点同源——大文件 OCR 跑完攒了全部文字层在内存，最后落盘阶段是高风险点。

### 1.2 UI 格子状态过粗

`LayerStatusDelegate`（`pdf_tab.py:118-179`）只有「有/无文字层」二态 + 已纠偏角标。排队中的页与正在识别的页视觉相同（都灰），用户看不出哪些页在排队/在算/失败。

### 1.3 预览数据滞后

预览用的 `ocr_text_blocks` 是**整批 OCR 结束后**由 `get_model` 全量刷新到 model（`pdf_session_manager.py:1028-1030`）。但 `page_done` 信号其实已携带该页完整 `result`（含 text+bbox），manager 槽 `_on_ocr_page_done_signal`（`:1035-1040`）只转发、不落 model。→ OCR 进行中打开预览，已变绿的页可能还拿不到 OCR 原始块，回退到 `detect_text_layers` 懒加载（~180ms，且显示 PDF 合并块而非 OCR 细粒度块）。

同时预览窗正打开显示某页时，该页 OCR 完成后 `_on_ocr_page_result` 不触发预览重绘（只有手动编辑块文字才走 `_refresh_preview_window_if_current:1209`）。

---

## 2. 关键事实与技术约束（已核实）

| 事实 | 来源 | 对设计的影响 |
|---|---|---|
| 增量保存 `incremental=True`（`pdf_service.py:248-256`）**不重开 doc**，内存对象可继续用 | 代码核实 | 逐批增量落盘可行，不影响下一批 OCR |
| 全量压缩 `_compress_in_place`（`:138-173`）必须 `close/reopen` doc | Windows 文件锁 | 只能放在所有 OCR 完成后 |
| 纯加文字层 OCR 不触发 `has_structural_change` | `:458/471/486/504/525` | OCR 续传场景天然走增量分支 |
| 现有代码刻意"整文档聚合单一子集字体"以压缩体积 | `pdf_service.py:209-220, 661-665` | 逐批落盘期间有批级字体冗余，需末尾全量压缩清理 |
| `_run_ocr` 被设计为纯执行层，信任传入的 page_indices | `:857` 无 has_text_layer 过滤 | 续传过滤接在 UI/manager 入口，不进 `_run_ocr` |
| `.vibeocr/` 目录已存在且被 git 忽略，有原子写模式 | `machine_cache.py:169-225` | sidecar 复用该目录与写模式 |
| `page_done` 信号已携带完整 `OCRResult` | `pdf_session_manager.py:1004` | 预览增量写入零后端改动 |

---

## 3. 整体方案

一条主线：**逐批增量落盘 + 末尾自动聚合压缩 + sidecar 记录已落盘页实现断点续传**，配套 UI 格子四态与预览增量刷新。

```
每批（16 页）：
  阶段1 渲染 → 阶段2 OCR 识别 → 阶段3 写文字层（内存）
                                    ↓ 新增
                              增量落盘 (incremental save, doc 不重开)
                                    ↓ 新增
                              更新 sidecar (标记本批页为"已落盘")
                                    ↓
                              逐页 emit page_done（携带 persisted=true）
全部批结束：
  ↓ 新增
  整文档聚合子集字体 + 全量压缩落盘（_compress_in_place，close/reopen）
  ↓ 新增
  更新 sidecar（completed=true）
```

**崩溃恢复**：重新打开同一 PDF 时，读 sidecar → 比对文件指纹 → 若匹配且 `completed=false`，把"已落盘"页标记为 `has_text_layer=true`，下次 OCR 自动跳过。

---

## 4. 组件设计

### 4.1 后端：逐批增量落盘

#### 4.1.1 `save_incremental`（`pdf_service.py` 新增）

薄封装现有增量路径（复用 `save_with_rewrite:248-256` 的 incremental 分支逻辑），作为独立静态方法：

```python
@staticmethod
def save_incremental(doc: fitz.Document, save_path: str) -> bool:
    """增量保存（纯加文字层场景）。doc 不重开，内存对象继续可用。
    先备份 → incremental save → 删备份；异常从备份回滚。
    Returns: True 成功（已落盘），False 失败（已回滚，doc 文字层保留）。"""
```

返回 bool 让调用方决定是否写 sidecar（失败回滚则不写）。

#### 4.1.2 batch 路由加 `save` 字段（不新增独立路由）

在现有 `add_text_layer_batch` 路由（`pdf_backend_process.py:596`）的 `fitz_lock` 块内，写层成功后紧跟一次增量落盘。**不新增独立路由**——在现有 batch 路由上加一个 `save: bool` 请求字段，减少 HTTP 往返（一次调用完成写层+落盘）。

schema 改动（`ipc/schemas.py:185` `BatchAddTextLayerRequest`）：
```python
class BatchAddTextLayerRequest(BaseModel):
    pages: list[BatchAddTextLayerPage]
    pdf_settings: dict[str, Any] | None = None
    overwrite: bool = False
    save: bool = False          # 新增：写层后是否增量落盘
```

路由伪代码（`save_path` 取自后端 session 的 `pdf_document.file_path`，即原 PDF 路径）：
```python
results = PdfService.add_text_layer_batch(s.doc, ..., cancel_check=...)
if req.save and results:  # 有成功写的页才落盘
    save_path = s.pdf_document.file_path  # 原 PDF 绝对路径
    saved = PdfService.save_incremental(s.doc, save_path)
    if not saved:
        # 回滚了，这批未落盘；返回 extra 标记让 manager 不写 sidecar
        return MutateResponse(diff=..., extra={"saved": False})
return MutateResponse(diff=..., extra={"saved": True})
```

客户端 `pdf_backend_client.py:476` `add_text_layer_batch` 加 `save: bool = False` 参数。

#### 4.1.3 OCR 末尾自动聚合压缩

在 `_run_ocr`（`pdf_session_manager.py:857`）批循环全部结束后、`get_model`（`:1028-1030`）之前，新增一步：调用现有 `save_with_rewrite(path=None)` 做整文档聚合子集字体 + 全量压缩。

复用现有代码（`pdf_service.py:176` `save_with_rewrite` + `_compress_in_place`），只是触发时机从"用户点保存"提前到"OCR 全部完成"。全量压缩会 `close/reopen` doc，后端路由按现有 save 路由（`:735-741`）的 doc 替换模式处理 `new_doc`。

进度上报：末尾压缩阶段发新 phase `COMPRESS`（`ipc/schemas.py:99` `ProgressPhase` 新增），文案"正在压缩并保存…"，不确定进度（`total=0`，主进程滚动条）。

### 4.2 体积权衡说明

逐批增量落盘期间，每批页固化了自己的批级子集字体（可能多个字体对象）。OCR 末尾全量压缩会 `rewrite` 所有 OCR 页——重新整文档聚合字符 → 单一子集字体（`save_with_rewrite:216-220`），然后 `tobytes(garbage=4, deflate=True)` 全量重写，把批级冗余字体清理掉。**最终落盘文件体积与现有"手动保存"结果一致，没有膨胀**。中间过程文件（崩溃留下的）会略大，但下次完整 OCR 后会被压缩修正。

### 4.3 Sidecar 断点续传

#### 4.3.1 存储位置与格式

放在 `.vibeocr/ocr_sessions/<fingerprint>.json`，复用 `machine_cache.py` 的原子写模式（`save_cache:195-225` 的 tmp + os.replace）。

> 不用 PDF 旁的 `<pdf>.vibeocr.json`：便携应用 install_root 是唯一数据边界；PDF 可能在只读目录或移动存储，旁文件污染用户目录且跟随移动失效。

**文件指纹**：`f"{st.st_size}:{int(st.st_mtime_ns)}"`——O(1)，不读全文件，足够检测外部改动。放在 sidecar 内部，打开 PDF 时校验。项目无现成 PDF 文件指纹机制（现有 hash 全是机器码/字体名用途），需自建。

**schema**（带版本号，借鉴 `machine_cache.py:18` `CACHE_VERSION`）：
```python
{
  "version": 1,
  "file_path": "/abs/path/to.pdf",
  "fingerprint": "1234567:1757800000000",  # size:mtime_ns
  "completed": false,                       # 整体是否已完成（含末尾压缩）
  "pages": {
    "0": {"has_text_layer": true, "ocr_preproc_angle": 0},
    "1": {"has_text_layer": true, "ocr_preproc_angle": 90}
  }
}
```

**不持久化 `ocr_text_blocks`**（text+bbox）。预览用的 OCR 块随进度增量写入主进程 model（见 4.4.2）；sidecar 只负责"哪些页已落盘可跳过"。崩溃恢复时已落盘页的文字层已在磁盘 PDF 里，预览走 `detect_text_layers` 读 line 级 bbox（commit 660acad）即可。

#### 4.3.2 写入时机

| 事件 | sidecar 操作 |
|---|---|
| 每批 incremental save 成功 | 本批页加入 `pages`，`completed=False`，原子写 |
| 每批 incremental save 失败/回滚 | **不写 sidecar**（这批未落盘，下次需重做） |
| 末尾全量压缩成功 | `completed=True`，原子写 |
| sidecar 写入失败（磁盘满/权限） | 记日志，**不阻断 OCR**（续传是锦上添花） |

完成后**保留 sidecar 并标 `completed`**（不删除）——下次打开该 PDF 时，指纹匹配且 `completed=true` 说明无未完成 OCR，直接忽略；逻辑统一（始终读 sidecar 判状态），不引入"有/无 sidecar"两种分支。

#### 4.3.3 新增 sidecar 读写模块

在 `utils/` 或 `services/` 新增模块（如 `utils/ocr_sidecar.py`），提供：
```python
def compute_fingerprint(file_path: str) -> str:
    """f"{st.st_size}:{int(st.st_mtime_ns)}" """

def sidecar_path(file_path: str) -> Path:
    """<install_root>/.vibeocr/ocr_sessions/<fingerprint>.json"""

def load_sidecar(file_path: str) -> dict | None:
    """读 sidecar；指纹不匹配或损坏返回 None"""

def save_sidecar(file_path: str, data: dict) -> bool:
    """原子写（复用 machine_cache 的 tmp+replace 模式）"""

def mark_pages_saved(file_path: str, page_indices: list[int], angles: dict[int, int]) -> bool:
    """增量更新 sidecar：合并 page_indices 到 pages"""

def mark_completed(file_path: str) -> bool:
    """置 completed=true"""
```

#### 4.3.4 读取与恢复流程

打开 PDF 时（session 加载阶段或 `start_ocr` 入口）：
1. 算 PDF 指纹，拼 sidecar 路径。
2. sidecar 不存在 → 正常流程，全量 OCR。
3. sidecar 存在但指纹不匹配（PDF 被外部改过）→ **作废 sidecar，正常全量 OCR**。
4. sidecar 存在且指纹匹配：
   - `completed=true` → 忽略（已完整）。
   - `completed=false` → 把 `pages` 里的页标记 `has_text_layer=true` 恢复到 model，`get_pages_without_text_layer`（`pdf_session_manager.py:1112-1119`）自动跳过。UI 状态栏提示"检测到上次未完成的 OCR（已保存 N/M 页），可继续识别剩余页"。

**续传接入点**：在 UI 层算 `page_indices`（`pdf_tab.py:2027`）或 `start_ocr`（`:712`）入口合并 sidecar 恢复的"已落盘页"做减法。**不进 `_run_ocr` 内部**（纯执行层）。

### 4.4 UI 格子与预览

#### 4.4.1 格子四态

`LayerStatusDelegate`（`pdf_tab.py:118-179`）从二态扩展。新增 item role（视觉投影，不改 model schema）：
```python
_LAYER_STATE_ROLE = Qt.UserRole + N  # 新增，替代 _HAS_LAYER_ROLE 的视觉职责
# 枚举：none / processing / done / failed
```

`paint` 按枚举映射颜色：
- `none` 灰（`Colors.text_subtle`）
- `processing` 蓝（`Colors.accent`，可选脉冲动画）
- `done` 绿（`Colors.success`）
- `failed` 红 + 感叹号

保留 `_HAS_LAYER_ROLE` 作为数据真值（model 里 `has_text_layer` 仍是唯一持久事实）。

**状态转换时机**：
| 事件 | 该页格子状态 |
|---|---|
| 该页进入当前批（开始渲染/识别） | `processing` |
| 该页写层成功（`page_done`） | 保持 `processing`（等本批落盘） |
| 该批 incremental save 成功 | 批内页全部 `done` |
| 该页写层失败 | `failed` |
| 末尾全量压缩完成 | 兜底全部 `done` |

> 注：省略 `pending_save` 中间态常态显示——配合"写层后立即 incremental save"，写层成功后极短时间内即落盘，直接 `processing→done`。`pending_save` 仅在 save 失败回滚时短暂出现（回退为 `processing`）。

#### 4.4.2 预览数据增量写入（消除滞后）

在 `PdfSessionManager._on_ocr_page_done_signal`（`pdf_session_manager.py:1035-1040`）里，把 `result.text_blocks` 增量写入 `session.pdf_document.pages[idx]`：
```python
info = session.pdf_document.pages[idx]
info.ocr_text_blocks = result.text_blocks
info.ocr_preproc_angle = result.preproc_angle  # 若有
info.has_text_layer = True
```

这样 OCR 进行中打开预览，已识别的页立刻拿到 OCR 原始块（细粒度、可编辑、置信度着色），不回退到 `detect_text_layers` 懒加载。零后端改动。

#### 4.4.3 预览窗自动刷新

`_on_ocr_page_result`（`pdf_tab.py:1015`）更新完格子后追加调用 `_refresh_preview_window_if_current(page_index)`（复用 `:1209`）。OCR 进行中盯着某页看，识别完瞬间文字层高亮叠加。

---

## 5. 错误处理矩阵

| 场景 | 处理 |
|---|---|
| 单页渲染/识别异常 | 现有逻辑不变，该页计 fail，格子 `failed` |
| 单页写层异常 | 计 fail，格子 `failed`，该页不进 incremental save、不写 sidecar |
| 批 incremental save 成功 | 批内"写层成功"页落盘 → sidecar 标记 → 格子转 `done` |
| 批 incremental save 失败/回滚 | 从 `.bak` 恢复原文件；内存 doc 文字层保留；**sidecar 不写**；格子保持 `processing`；状态栏提示"第 X 批保存失败，将在最终保存时重试"；不中断后续批 |
| 末尾全量压缩失败/回滚 | 从 `.bak` 恢复；doc 已 close（`_compress_in_place:165-172` 固有问题），后端需 reopen 原 doc 替换 `s.doc`；**sidecar 保持 `completed=false`**（已 incremental 落盘的页仍有效）；弹"最终保存失败，但中间结果已保存，可手动重试" |
| 末尾压缩失败后 doc 重开失败 | 后端 session 不可用，emit `failed`，UI 提示"严重错误，请重新打开文件"；sidecar 保留，重开可恢复已落盘页 |
| sidecar 写入失败 | 记日志，**不阻断 OCR**（续传锦上添花） |
| sidecar 指纹不匹配 | 作废 sidecar，全量 OCR |

**核心原则**：sidecar 和 incremental save 都是"尽力而为"——失败时降级，不阻断 OCR 主流程。最坏情况退化为现状行为（全内存，崩溃丢失）。

---

## 6. 取消协议

现有取消机制（`reset_cancel` + `cancel_event` + `_cancelled`）保留。增量落盘引入新边界：

- **取消发生在批写层中途**：`add_text_layer_batch` 的 `cancel_check`（`pdf_service.py:670`）已逐页检查，已写页保留、停止写后续页。本批**不触发 incremental save**（写层未完整），**不写 sidecar**。下一批不再执行。已完成的上一批 incremental save 结果保留在磁盘 + sidecar 里。→ **取消不丢已落盘的批**，相对现状（取消即全丢）的重大改进。
- **取消发生在末尾全量压缩中途**：`_compress_in_place` 原子（失败回滚），取消时若已开始压缩则等它完成或回滚，**不强制中断**（避免文件损坏）。压缩完成后或回滚后按取消语义结束。sidecar 保持 `completed=false`。

---

## 7. 测试策略

### 7.1 后端服务层（纯函数，无 Qt）
- `save_incremental`：验证 incremental save 后 doc 可继续写、不重开；备份/回滚正确；返回 bool 准确。
- sidecar 读写（`utils/ocr_sidecar.py`）：原子写、指纹校验、版本失效、指纹不匹配作废、增量合并页。

### 7.2 后端路由层
- `add_text_layer_batch` 带 `save=True`：mock doc，验证写层 → incremental save → 返回 diff 的调用顺序；save 失败时回滚且 extra 标记 `saved=False`。

### 7.3 Manager 层（`_run_ocr`，绕过 QThread 同步执行）
- 参考模板 `tests/managers/test_pdf_session_manager.py:368`（`PdfSessionManager.__new__` + 手动设字段 + mock client/ocr_service）。
- 新测试：mock 多批，验证每批后 sidecar 写入 + 格子状态转换 + 末尾全量压缩被调用一次。
- 取消测试：在批中途触发取消，验证已落盘批的 sidecar 保留、未完成批不写 sidecar、不中断已落盘结果。
- 续传测试：构造 `completed=false` sidecar，验证 `start_ocr` 过滤掉已落盘页。

### 7.4 UI 层
- `LayerStatusDelegate` 四态绘制（构造各状态 item，验证颜色映射）。
- `_on_ocr_page_done_signal` 增量落 model 后，预览 `set_ocr_blocks` 能拿到块。
- 预览窗自动刷新：mock 预览窗显示某页，OCR 该页后验证重绘调用。

### 7.5 回归重点
- 大文件 OCR 端到端（几十页），中间 kill 后端进程 → 重开 → 续传跳过已落盘页。
- 体积验证：完整 OCR 后的最终文件与现有"手动保存"结果体积一致（聚合压缩生效）。

---

## 8. 改动文件清单

| 文件 | 改动 |
|---|---|
| `src/vibeocr/services/pdf_service.py` | 新增 `save_incremental`（`:248-256` 逻辑提取） |
| `src/vibeocr/services/pdf_backend_process.py` | `add_text_layer_batch` 路由（`:596`）加 `save` 分支 + doc 替换处理；末尾压缩路由接入 |
| `src/vibeocr/services/pdf_backend_client.py` | `add_text_layer_batch`（`:476`）加 `save` 参数 |
| `src/vibeocr/ipc/schemas.py` | `BatchAddTextLayerRequest`（`:185`）加 `save` 字段；`ProgressPhase`（`:99`）加 `COMPRESS` |
| `src/vibeocr/managers/pdf_session_manager.py` | `_run_ocr`（`:857`）阶段3加 incremental save + sidecar；末尾加全量压缩；`_on_ocr_page_done_signal`（`:1035`）增量落 model；`start_ocr`（`:712`）续传过滤 |
| `src/vibeocr/utils/ocr_sidecar.py` | **新增** sidecar 读写模块（指纹 + 原子写 + 版本） |
| `src/vibeocr/views/tabs/pdf_tab.py` | `LayerStatusDelegate`（`:118`）四态；`_on_ocr_page_result`（`:1015`）加预览刷新；格子状态转换；续传提示 |
| `tests/` | 各层新增测试（见第 7 节） |

---

## 9. 不做的事（YAGNI）

- 不做逐页增量落盘（每页一次 save，I/O 太频繁；每页独立子集字体体积膨胀明显）。
- 不持久化 `ocr_text_blocks` 到 sidecar（预览数据随进度增量进 model，sidecar 只管续传判断；恢复时已落盘页走 `detect_text_layers` 读 PDF 文字层）。
- 不把 sidecar 放 PDF 旁（便携应用数据边界在 install_root）。
- 不做 sidecar 跨机器同步（机器级本地状态，无需云同步）。
- 不改缩略图列表（OCR 写隐形文字层，无视觉变化）。
- 不做自动保存/草稿（OCR 末尾已自动落盘，手动保存仍保留用于编辑后落盘）。
- 不把 `_compress_in_place` 的 close/reopen 异步化（OCR 已全部完成，时序安全）。
