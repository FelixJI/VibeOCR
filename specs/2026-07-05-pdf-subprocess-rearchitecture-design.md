# PDF 模块进程化重构设计

**日期**: 2026-07-05
**状态**: Phase 1 已实现并接入生产（2026-07-18）。子进程服务器 `pdf_backend_process.py` 与 HTTP 客户端 `pdf_backend_client.py` 完整实现并通过集成测试；`worker_host/composition.py:default_pdf()` 切换到 `PdfBackendClient.instance()`，原 `InProcessPdfBackendClient` 已删除（不留回退）。触发原因：PyMuPDF 1.28.0 在密集表格页（数百块/页）上原生内存损坏（0xC0000409），in-process 路径让整个 WorkerHost 崩溃；子进程隔离把崩溃限制在 PDF 子进程内，客户端透明重启。Phase 2（model_diff 增量、检查点、自动重开 session）待办。
**范围**: 把整个 PDF 处理模块(fitz + PdfDocument 模型 + 全部渲染/变更/保存)从主进程的 QThread 下沉到常驻子进程,主进程退化为纯 UI + IPC 客户端

---

## 1. 背景与动机

### 1.1 触发问题

用户报告 PDF 标签页三类症状:

1. **自动摆正无任何进度/开始/结束提示** —— 进度信号 `deskew_progress` 已定义但 PdfTab 未 connect(`pdf_tab.py:668-670`),`_on_auto_deskew`(`pdf_tab.py:1422`)只禁用了摆正按钮本身,未设置进度条/状态文字/独占锁。
2. **执行保存/删除文字层/摆正时滚动卡顿** —— GUI 线程上的同步页操作(旋转/删除/重排/预览渲染,均 `with session.doc_lock` 在 GUI 线程)与后台 mutate worker 抢同一把 `RLock`。
3. **缩略图渲染失败** —— (a)摆正期间持锁过久,缩略图 worker 抢锁失败 3 秒后放弃且无重试无日志(`pdf_render_thumb_worker.py:116-119`);(b)save 全量压缩替换 `session.doc` 后缩略图 worker 持旧 doc 引用(`pdf_session_manager.py:691`);(c)失效后旧 pixmap 回写无 generation 校验(`pdf_tab.py:204`)。

这三类症状在当前 QThread+RLock 架构下都能修(已批准的"进度 UI + 独占锁 + 缩略图三修"方案,1-2 天)。但调研中暴露了一个**更根本的隐患**,促成本设计文档。

### 1.2 根本隐患:PyMuPDF 不支持多线程

PyMuPDF 官方明确警告:

> **"PyMuPDF does not support running on multiple threads — doing so may cause incorrect behaviour or even crash Python itself."**
> —— [PyMuPDF multiprocessing 文档](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html)

根因是底层 MuPDF C 库**非重入**(not reentrant)。官方推荐用 `multiprocessing`,且给出了**"Qt GUI 主进程 + 子进程访问 PDF 文档"**的官方示例架构(用 `mp.Queue` 传回 pixmap 字节)。来源:

- [PyMuPDF Issue #107 — Is PyMuPDF re-entrant / thread-safe?](https://github.com/pymupdf/PyMuPDF/issues/107)
- [PyMuPDF Discussion #1151 — Extracting words using threading](https://github.com/pymupdf/PyMuPDF/discussions/1151)
- [PyMuPDF Discussion #4916 — global lock pattern](https://github.com/pymupdf/PyMuPDF/discussions/4916)
- [PyMuPDF FAQ — thread safety](https://pymupdf.readthedocs.io/en/latest/faq/index.html)

**本仓库现状**:`PdfMutateWorker`、`ThumbnailRenderWorker`、`PdfLoadWorker`、`PdfRenderWorker`、`PdfOcrWorker`(前置渲染)、`PdfOpenWorker` 全是 QThread,共享同一 `fitz.Document` + 一把 `threading.RLock`(`pdf_session.py:27`)。这是 PyMuPDF 官方**不支持**的用法,只是靠 RLock 强行串行化才没崩——代码注释里没有任何崩溃记录,但隐患真实存在。

### 1.3 本仓库已有的子进程范式

| 范式 | 实现 | 适用场景 |
|---|---|---|
| **SHM + pickle** | `SharedMemoryProtocolV2`(16MB 固定环形 + 9 字节头 + ping-pong)+ `OCRWorkerProcess` + `WorkerManager`(崩溃重启)+ `JobObjectGuard`(Windows 孤儿清理) | OCR:无状态(图片进、识别结果出),paddle/torch CUDA 对 Qt 线程敌对 |
| **HTTP/FastAPI** | `MinerUService`(httpx 客户端 + `_ensure_api_running` 托管子进程 + `JobObjectGuard`) | MinerU:文档解析,流式友好 |

PDF 模块下沉可借鉴其中之一(详见 §4 传输层选型)。

### 1.4 关键约束:Windows 文件锁

Windows 锁定被 fitz 打开的文件(`pdf_service.py:73-77, 118-120` 注释明确记载)。当前单进程内的全量压缩保存靠 `tobytes→close→write→reopen` 绕开(`_compress_in_place`,`pdf_service.py:109-139`)。

**这条约束直接决定了进程化方案的"全下沉不可分阶段"特性**:若主进程和子进程同时开同一 PDF,Windows 上互相阻塞覆盖,无任何先例。所以"只下沉变更、保留主进程渲染"行不通——要么全下沉,要么不下沉。

---

## 2. 目标与非目标

### 目标

- G1. **彻底消除 PyMuPDF 多线程隐患**:fitz 调用全部收敛到子进程单线程内,主进程零 fitz 直接访问。
- G2. **进程隔离**:fitz 崩溃带不垮 GUI(对齐 OCR 子进程已受益)。
- G3. **删除 `doc_lock`**:子进程内单线程,无需锁。
- G4. **解决 §1.1 三类症状**:进度 UI、操作原子化、缩略图稳定。
- G5. **复用现有子进程基础设施**:JobObjectGuard、崩溃重启、日志通道。

### 非目标

- N1. 不改 OCR 子进程架构(它已经是进程化的,只是被 PDF 模块的前置渲染喂图)。
- N2. 不改 PdfDocument/PdfPageInfo 的数据结构(只改所有权与同步方式)。
- N3. 不引入分布式/远程(子进程在本地,IPC 走 localhost)。

---

## 3. 架构设计

### 3.1 目标拓扑

```
┌──────────────────── 主进程(GUI)────────────────────┐
│  Qt UI: MainWindow / PdfTab / PreviewWindow          │
│  PdfSessionManager:                                   │
│    _sessions: dict[path → SessionHandle]              │
│      SessionHandle = { id, path, mirror: PdfDocument }│
│    (不再持有 fitz.Document / doc_lock)                │
│  IPC 客户端(传输层见 §4)                            │
└──────────────────────┬───────────────────────────────┘
                       │ 请求: {session_id, op, params}
                       │ 响应: {result, model_diff, pixmaps}
                       │ 进度/取消: 流式或独立消息
┌──────────────────────▼───────────────────────────────┐
│              PDF 子进程(常驻,单例)                  │
│  ┌──────────────────────────────────────────────┐    │
│  │ SessionRegistry: dict[id → PdfSession]       │    │
│  │   PdfSession = { doc: fitz.Document,          │    │
│  │                  model: PdfDocument(规范) }   │    │
│  └──────────────────────────────────────────────┘    │
│  PdfService(全部 @staticmethod,原地执行)            │
│  渲染: thumbnail(96dpi→160px) / preview(150dpi)     │
│        → 字节流(RGB 或 PNG)回传                     │
│  OCR 调用:复用现有 OCR 子进程(SHM)                  │
│  日志: stdout → 主进程后台线程(复用 OCR 范式)       │
└──────────────────────────────────────────────────────┘
```

### 3.2 核心设计决策

**D1. 子进程持有规范模型,主进程持有序列化镜像**

当前 PdfDocument 模型被 ~25 处 mutation 分散写入(详见 §5.2),且**双写**(PdfService 写 + worker/manager 直接写字段)。下沉后:

- 子进程是 PdfDocument 的**唯一信源**(canonical)。
- 主进程持有 `mirror: PdfDocument` 快照,只用于 UI 显示(页数、旋转角标、文字层状态、OCR 块)。
- 每次变更操作的回包携带 **model_diff**(变更页的 PdfPageInfo 增量),主进程 apply 到 mirror。
- 大刷新(打开文件、批量 OCR 完成)回传完整 model。

**D2. 渲染产物以字节流回传**

- 缩略图:160×160×3 RGB ≈ 75KB。批量回传或单条均可,SHM/HTTP 都轻松。
- 预览:150dpi Letter ≈ 1275×1650×3 ≈ 6.3MB。SHM 单消息可容纳(占 16MB 的 38%);HTTP 流式天然支持。
- 主进程收到字节后构 QPixmap(`QImage(rgb_bytes, ...)` → `QPixmap`)。

**D3. 主进程的 PdfSessionManager 重塑**

当前 `_sessions: dict[path → PdfSession(doc, model, lock)]` → 改为 `_sessions: dict[path → SessionHandle(id, path, mirror)]`。`active_session` / `switch_session` / `close_session` 变成"通知子进程切换/关闭"。

**D4. 5 个 QThread worker 的归宿**

| Worker | 现状 | 下沉后 |
|---|---|---|
| `PdfOpenWorker` | 后台 `fitz.open` | **删除**:open 是子进程的 IPC 调用 |
| `PdfLoadWorker` | 后台逐页文字层检测 | **移入子进程**:open 后子进程内部异步加载,流式回传 page_infos |
| `ThumbnailRenderWorker` | 后台按需渲染,持 doc_lock | **改造为主进程的 IPC 客户端**:发请求 → 收字节 → 构 QPixmap。generation 校验(§6.3)依然需要 |
| `PdfRenderWorker` | OCR 前置 300dpi 渲染 | **移入子进程**(OCR 流程已在子进程链路里) |
| `PdfMutateWorker` | 变更/保存/摆正 | **删除**:变更变为子进程的 IPC 调用 + 流式进度 |

### 3.3 操作生命周期示例:自动摆正

```
[主进程] PdfTab._on_auto_deskew(indices)
   │ 显示进度条(不确定) + 独占锁(禁用所有页操作按钮) + 状态"正在摆正…"
   │ IPC: POST /session/{id}/deskew  {pages: indices}
   ▼
[子进程] 收到 deskew 请求
   │ 阶段1 批量渲染(持 doc,无锁,子进程单线程)
   │   ├─ 流式回传 progress {phase:"render", n, total}
   │ 阶段2 recognize_batch → 调现有 OCR 子进程(SHM)
   │   └─ 回传 progress {phase:"detect"}
   │ 阶段3 逐页纠正(原地改 doc + model)
   │   ├─ 每页回传 progress {phase:"correct", n, total}
   │   └─ 每页回传 page_done {idx, corrected, pixmap?(可选预渲染缩略图)}
   │ 回传 result {summary, model_diff, invalidated_pages}
   ▼
[主进程] 收到 progress → 更新进度条/状态文字
        收到 page_done → 更新角标
        收到 result → apply model_diff,刷新缩略图(invalidate + 重请),弹完成框,解锁
```

关键:整个过程中,**主进程不碰 fitz,不持锁,GUI 事件循环完全响应**。滚动只是触发"向子进程要缩略图字节"——子进程单线程排队处理,不会卡 GUI。

---

## 4. 传输层选型

### 4.1 候选对比

| 维度 | HTTP/FastAPI(对齐 MinerU) | SHM(复用 OCR `SharedMemoryProtocolV2`) | `multiprocessing.Queue` |
|---|---|---|---|
| **本仓库复用度** | 高(`MinerUService` 范式完整) | 高(`OCRWorkerProcess` + `WorkerManager` 范式完整) | 低(需新建托管) |
| **大 payload** | 流式/分块天然支持 | ping-pong 单消息 ≤16MB,>16MB 需应用层分块(预览 6.3MB OK,OCR 300dpi 25MB 超限) | pickle 队列,无硬限但慢 |
| **并发请求** | 天然多请求并发(多路由) | ping-pong 单消息 → 需串行化或多个 SHM region | 队列天然串行 |
| **进度/取消** | SSE/WebSocket 或轮询 | 独立消息类型(OCR 已有 `BATCH_PROGRESS`/`BATCH_CANCEL`) | 队列消息 |
| **延迟** | localhost HTTP ~1-5ms/往返 | SHM ~0.1ms/往返 | 队列 ~0.5ms/往返 + pickle |
| **调试** | 可 curl/httpx 直连 | 需专门的 SHM 调试工具 | 进程间队列难窥视 |
| **崩溃重启** | MinerU 已有 `_ensure_api_running` | OCR 已有 `WorkerManager` 健康检查 | 需新建 |
| **Pixmap 字节** | 二进制流响应直接 | 长度前缀二进制(OCR 已有 serialize_request) | pickle QPixmap 不可行,需手转 RGB 字节 |

### 4.2 PDF 模块的载荷与并发需求

- **缩略图**:高频、小载荷(75KB)、可批量、需按需触发(滚动)。希望**并发**:用户滚动时连续发多个请求。
- **预览**:低频、中载荷(6.3MB)、单页。
- **OCR 前置渲染**:中频、大载荷(300dpi 25MB/页,但流向 OCR 子进程,PDF 子进程只是中转)。
- **变更/保存**:低频、小载荷(参数 + model_diff 回传)、长耗时(需流式进度)。
- **批量摆正**:中频、需多阶段流式进度。

### 4.3 推荐:HTTP/FastAPI

**理由**:

1. **并发友好**:PDF 模块同时有"缩略图按需 + 预览 + 后台 save"并发需求。SHM 的 ping-pong 模型强制串行,要么多 region(复杂),要么排队(缩略图被 save 阻塞)。HTTP 天然多请求。
2. **流式进度**:FastAPI 的 SSE / WebSocket 或 chunked 响应天然支持"摆正三阶段进度"。
3. **复用度高**:`MinerUService` 的 `_ensure_api_running` + 端口探测 + `JobObjectGuard` + httpx 客户端 + 健康检查(`/health`)可直接套用。
4. **载荷无上限**:预览 6.3MB / OCR 25MB 都走 HTTP body 流式,无 SHM 16MB 硬限。
5. **调试友好**:开发期可 curl 直连子进程,极大降低跨进程调试难度。

**代价**:延迟比 SHM 高几个 ms,但 PDF 操作本身耗时远大于这个差(渲染一页 150dpi ~50-200ms,摆正推理秒级),HTTP 开销可忽略。

**待评审确认**:是否接受 localhost HTTP 的实现复杂度(FastAPI 路由 + 序列化约定),还是更倾向 SHM 的低延迟但需处理并发与分块。

### 4.4 接口草图(HTTP 路径)

```
POST /health                                  → {status, sessions}
POST /session/open        {path}              → {session_id, page_count, model, thumbnails?}
POST /session/{id}/close                      → {ok}
POST /session/{id}/save   {path?, settings}   → stream: progress* → {ok, model_diff, new_doc_id?}
POST /session/{id}/deskew {pages}             → stream: progress{phase}* → {summary, model_diff}
POST /session/{id}/rotate {pages, angle}      → {model_diff}
POST /session/{id}/delete_pages {pages}       → {model_diff}
POST /session/{id}/insert_blank  {after, w,h} → {model_diff}
POST /session/{id}/insert_from  {src, after}  → {model_diff}
POST /session/{id}/reorder    {new_order}     → {model_diff}
POST /session/{id}/add_text_layer   {page, ocr_result, settings}
POST /session/{id}/delete_text_layer {page}   → stream: progress* → {model_diff}
POST /session/{id}/rewrite_text_layer {page, blocks, settings}
POST /session/{id}/render_thumbnail {page}    → image/png bytes
POST /session/{id}/render_preview    {page, dpi} → image/png or RGB stream
POST /session/{id}/detect_text_layers {page}  → {text_layers}
POST /session/{id}/ocr   {pages, options, settings}
                              → stream: page_done* / progress* → {model_diff}
POST /session/{id}/cancel                      → {ok}
GET  /session/{id}/model                       → {model}   (全量刷新)
```

进度用 SSE(`text/event-stream`)或 chunked JSON line stream。取消用独立 POST(协作式,子进程检查标志)。

---

## 5. 影响范围盘点

### 5.1 PdfService API 面(24 方法,19 个收 fitz.Document)

详见 [Explore 报告 §1](#)。**好消息**:全部 `@staticmethod` 无状态,抽象干净,直接变成子进程的服务端方法。3 个几何工具方法(`_denormalize_and_unrotate_bbox`、`bbox_to_pixel`、`invalidate_thumbnails`)可留在主进程。

### 5.2 fitz 泄漏点(4 处需先折回 PdfService)

| 位置 | 行为 | 折回方式 |
|---|---|---|
| `pdf_load_worker.py:67-69` | `doc[i].rotation` + `get_text("text")` | 加 `PdfService.page_rotation(doc,i)` + 复用已有 `detect_text_layers` 的快路径 |
| `pdf_render_worker.py:67-70` | `doc[i].rect` 读几何 | 加 `PdfService.page_rect(doc,i)` |
| `pdf_mutate_worker.py:223` | `doc[i].get_text()` 预检查 | 删除冗余(PdfService.delete_text_layers 内已检查) |
| `pdf_tab.py:1603,1619` | `session.doc[idx].rect` | 改用 `mirror.pages[idx].rect`(模型已缓存) |

### 5.3 PdfDocument 模型 mutation 点(~25 处)

详见 [Explore 报告 §3](#)。**关键**:下沉后子进程是唯一信源,所有 mutation 收敛到子进程内部。主进程 mirror 只通过 model_diff 更新,不再直接写字段。

需在主进程消除的直接字段写:
- `pdf_mutate_worker.py:186-197`(AUTO_DESKEW 后写字段)→ 子进程内完成,主进程收 model_diff
- `pdf_session_manager.py:727-729, 760-768`(update_page_block_text / rewrite_modified_pages)→ 转 IPC
- `pdf_session_manager.py:389`(`_on_page_ready` 写 pages[i])→ 改为 apply model_diff
- `pdf_tab.py:1598`(`page_info.text_layers = ...`)→ 改为 IPC 调用后 apply diff

### 5.4 5 个 QThread worker

见 §3.2 D4 表。

### 5.5 载荷预算

| 载荷 | 大小 | SHM 单消息 | HTTP |
|---|---|---|---|
| 缩略图 160×160 RGB | ~75KB | ✅ | ✅ |
| 预览 150dpi Letter RGB | ~6.3MB | ✅(占 38%) | ✅ |
| OCR 前置 300dpi RGB | ~25MB | ❌(超 16MB,需分块) | ✅ |
| PdfDocument 完整(100 页 OCR'd) | ~几 MB | ✅(接近上限,大文档风险) | ✅ |
| model_diff(单页) | ~KB 级 | ✅ | ✅ |

---

## 6. 关键设计细节

### 6.1 Windows 文件锁的彻底解决

下沉后,只有子进程开 PDF 文件,主进程根本不碰文件句柄。当前 `_compress_in_place` 的 `tobytes→close→write→reopen` 仍在子进程内执行——只是从"主进程的单进程内绕锁"变成"子进程的单进程内绕锁",逻辑不变。**双进程开同一文件的冲突场景彻底消失**。

### 6.2 崩溃恢复

复用 OCR 的 `WorkerManager` 健康检查范式(或 MinerU 的 `_ensure_api_running`):

- 主进程定期 `/health` 探测(或 SHM 心跳)。
- 子进程死亡 → `JobObjectGuard` 清理孤儿 → 主进程重启子进程 → 重开所有未保存的 session(从磁盘文件)→ 提示用户"PDF 后端已恢复,未保存的改动丢失"。

**重要**:下沉后,**未保存的 OCR/编辑改动只存在于子进程内存**。崩溃会丢。需评估:是否要子进程定期把 PdfDocument model 落盘到 `.vibeocr/` 临时文件做检查点?(对齐 Office 的自动恢复)—— 待评审决策。

### 6.3 缩略图 generation 校验(从 QThread 方案继承)

即便下沉,主进程的缩略图缓存 + 按需请求模式不变,失效后旧字节回写的 ABA 问题依然存在:

- `ThumbnailModel` 每行维护单调递增 generation。
- `invalidate(row)` 自增该行 gen。
- 请求带 gen,响应回传 gen,主进程只在 gen 匹配时 `put`。

这条改动与传输层无关,无论走 QThread 还是进程都该做。

### 6.4 进度 UI 与独占锁(从 QThread 方案继承)

- 任何变更/保存/摆正/OCR 操作期间:进度条 + 状态文字 + `_set_file_buttons_enabled(False)` 独占锁 + 取消按钮可见。
- 取消按钮路由到子进程的 `/cancel`(协作式)。
- 这套 UI 逻辑在 PdfTab 内,与后端是 QThread 还是子进程无关。

### 6.5 日志与可观测

复用 OCR 的 stdout → 后台日志线程范式:子进程 print 日志,主进程读 PIPE 写入统一日志。开发期可额外开 FastAPI 的 `/debug/logs` 端点。

---

## 7. 迁移路线(分阶段,但需注意 §1.4 约束)

⚠️ **关键约束重申**:Windows 文件锁决定"主进程与子进程不能同时开同一 PDF"。这意味着**不能渐进地把操作一个个下沉**——只要主进程还持 doc 做渲染,子进程就不能开文件做变更。可行路线如下:

### 阶段 0:预备(无行为变化,可独立合并)

- P0.1 折回 4 处 fitz 泄漏到 PdfService(§5.2)。
- P0.2 给每个 PdfService 方法加稳定的 `op` 字符串标识(为 IPC 路由做准备)。
- P0.3 PdfPageInfo 增加 `rect` 字段(供主进程预览 highlight 用,不再直接读 fitz)。
- P0.4 把 model mutation 收敛到 PdfService 内部(消除 worker/manager 的直接字段写)。

**交付**:无 UI 变化,代码更干净,为下沉铺路。可单独 PR。

### 阶段 1:子进程骨架 + 全量下沉(一次到位)

因 §1.4 约束,这是最小不可分单位:

- P1.1 新建 `services/pdf_backend_process.py`(子进程入口,FastAPI 或 SHM server)。
- P1.2 新建 `services/pdf_backend_client.py`(主进程 IPC 客户端)。
- P1.3 改造 `PdfSessionManager`:从持 doc+lock 改为持 SessionHandle(§3.2 D3)。
- P1.4 删除/改造 5 个 QThread worker(§3.2 D4)。
- P1.5 主进程所有 PdfService 调用点改为 IPC client 调用。
- P1.6 缩略图 + 预览渲染改走 IPC(字节回传 + generation 校验)。
- P1.7 model_diff 同步机制(§3.2 D1)。
- P1.8 崩溃恢复 + JobObjectGuard 接入。

**交付**:PDF 模块完全进程化。这是大 PR,需充分测试。

### 阶段 2:优化与收尾

- P2.1 `doc_lock` 彻底删除。
- P2.2 model_diff 增量优化(避免大文档全量回传)。
- P2.3 自动恢复检查点(§6.2,若评审通过)。
- P2.4 性能对比测试(对比 QThread 方案的延迟/内存)。
- P2.5 进度 UI / 取消路由的全链路验证。

### 工程量估算

- 阶段 0:1-2 天
- 阶段 1:2-3 周(核心难点:模型同步 + 渲染字节回传 + 崩溃恢复)
- 阶段 2:3-5 天

**总计:2-3 周专注开发 + 测试**。

---

## 8. 风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | 渲染延迟上升(IPC 往返) | 缩略图/预览首次出现变慢 | localhost HTTP ~1-5ms,相对渲染本身 50-200ms 可忽略;子进程常驻避免冷启动 |
| R2 | 大文档 model 同步开销 | 几百页 OCR'd 文档 model 几 MB,全量回传慢 | model_diff 增量;打开时分页流式 |
| R3 | 子进程崩溃丢未保存改动 | 用户损失 OCR/编辑 | 检查点机制(§6.2);评审权衡频率与磁盘 IO |
| R4 | 跨进程调试难 | 开发效率下降 | 日志通道 + FastAPI 可 curl;复用 OCR 的 stdout→日志线程 |
| R5 | IPC 协议演化维护成本 | 后期改字段要双端同步 | 用 pydantic schema 共享(子进程导出,主进程 import) |
| R6 | 阶段 1 大 PR 难评审 | 合并风险 | 阶段 0 充分铺路;阶段 1 拆成多个 commit 但同 PR;先开 feature branch |
| R7 | 主进程 mirror 与子进程规范模型不一致 | UI 显示陈旧 | 所有 mutation 必经 IPC;mirror 只读;定期 `/model` 全量校准 |
| R8 | OCR 子进程链路变长(主→PDF 子进程→OCR 子进程) | OCR 启动/数据流多一跳 | PDF 子进程与 OCR 子进程都在本地,SHM 直连;延迟可忽略 |

---

## 9. 与"先修 bug"方案的关系

**重要结论**:[已批准的 QThread 方案](#)(进度 UI + 独占锁 + 缩略图三修)**不应该因为本重构而搁置**。理由:

1. **及时价值**:QThread 方案 1-2 天交付,立刻解决用户报告的三类症状;重构 2-3 周周期。
2. **改动可继承**:QThread 方案的以下改动在重构后**依然有效**:
   - 进度 UI + 状态文字 + 独占锁(纯 PdfTab UI 逻辑,与后端无关)
   - cancel 路由(路由到 `cancel_ocr` 还是 `cancel_deskew`,重构后改为路由到 IPC `/cancel`,UI 层不变)
   - 缩略图 generation 校验(§6.3,传输层无关)
   - 缩略图超时重试(若 QThread 阶段加,重构后改为子进程内的请求重排,语义类似)
3. **降低重构紧迫性**:修完 bug 后,重构变成"长期架构优化"而非"救火",可以从容评审。

**唯一会被重构覆盖的改动**:`PdfMutateWorker` 的 `deskew_phase` 信号 + manager 转发 + worker 内三阶段发进度——这部分在重构后会被 IPC 进度流取代。但这部分工作量很小(半天),即便废弃也不亏。

### 建议执行顺序

1. **先落地 QThread 方案**(1-2 天)→ 解决用户症状。
2. **本设计文档评审通过后**,启动进程化重构(阶段 0 → 1 → 2)。

---

## 10. 待评审决策点

| # | 决策 | 选项 | 推荐 |
|---|---|---|---|
| D-1 | 是否启动重构 | 立即启动 / 先修 bug 再启动 / 不启动 | 先修 bug 再启动 |
| D-2 | 传输层 | HTTP/FastAPI / SHM / Queue | HTTP/FastAPI(§4.3) |
| D-3 | 自动恢复检查点 | 子进程定期落盘 PdfDocument / 不做 | 做(防崩溃丢改动) |
| D-4 | 阶段 1 是否合并为单 PR | 单大 PR / 拆多 PR(受 §1.4 约束,拆 PR 需 feature flag) | feature flag 下拆多 PR |
| D-5 | 是否保留 QThread 方案的 deskew_phase 信号 | 保留(过渡)/ 直接等 IPC 进度流 | 等决策 D-1 |

---

## 参考资料

- [PyMuPDF multiprocessing 文档(官方推荐)](https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html)
- [PyMuPDF Issue #107 — thread safety](https://github.com/pymupdf/PyMuPDF/issues/107)
- [PyMuPDF Discussion #4916 — global lock pattern](https://github.com/pymupdf/PyMuPDF/discussions/4916)
- [PyMuPDF FAQ — thread safety](https://pymupdf.readthedocs.io/en/latest/faq/index.html)
- [InDesign 后台导出 PDF](https://helpx.adobe.com/indesign/desktop/troubleshoot/file-and-output-issues/pdf-export-hangs-in-background.html)
- [Revit 2026 后台导出 PDF](https://help.autodesk.com/view/RVT/2026/ENU/?guid=GUID-BECFA1C6-5A10-4A40-92CB-0DCBE07D434C)
- [PDF Studio 常驻后台进程](https://kbpdfstudio.qoppa.com/keep-pdf-studio-running-in-the-background/)
- [Artifex:MuPDF 多线程使用](https://artifex.com/blog/multi-threaded-use-of-mupdf-in-java)
