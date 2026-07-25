# VibeOCR 统一 Inference Supervisor 一次性重写实施计划

**日期**：2026-07-24
**状态**：已完成架构决策，待实施
**范围**：Python contracts/client/backend、PySide、WinUI/.NET、PaddleOCR、MinerU、PDF worker、构建与发布
**实施方式**：内部按依赖阶段开发，生产版本只进行一次原子切换；不提供运行时双栈或旧 transport 回退

---

## 1. 目标

把当前分叉的 OCR/WorkerHost/MinerU/PDF 调用路径重写为一个前端专属、UI-free 的 HTTP inference supervisor：

```text
PySide Classic / WinUI Next
          │
          │ localhost HTTP v2 + session token + job id
          ▼
Inference Supervisor（每个前端会话独占一个实例）
  ├─ JobRegistry / EventLog / ResultStore
  ├─ DeviceScheduler / BudgetPlanner / RecoveryPolicy
  ├─ ResidencyManager / TTL / Pin / VRAM pressure
  ├─ PaddlePipelineAdapter（模型直接驻留 supervisor）
  ├─ MinerUProcessAdapter（按需复用 MinerU API 子进程）
  ├─ PdfProcessAdapter（无模型 PyMuPDF 子进程）
  └─ QR / export / settings 等快速能力
```

最终达到：

- GUI 主进程不加载 Paddle/MinerU，不同步执行 OCR，不直接管理 MinerU/PDF 子进程。
- PySide 和 WinUI 共用同一个 supervisor 后端实现和同一份 HTTP v2 合同，各自启动独占实例。
- 单张与批量共用 `recognize_many`；单张是立即执行的一元素 job。
- Paddle 和 MinerU 都按受控预算分批；阈值通过基准测试校准。
- 模型支持全局默认 TTL、按管道覆盖、显存压力 LRU 和显式永久 pin。
- 识别/PDF OCR 使用 job 状态、进度、分级取消、部分结果和失败项续跑。
- MinerU 首次实际使用时自行下载模型；UI 只负责充分提示真实阶段，不做独立预下载/组合验证。
- 删除旧 Named Pipe、共享内存、WorkerHost RPC v1、嵌套 OCR worker、主进程 MinerU 旁路和 runtime monkey patch。

## 2. 非目标

- 不重写 Paddle/MinerU 已稳定的结果解析、文本块转换、表格/公式处理算法。
- 不引入本机常驻守护进程、系统服务或跨前端共享 supervisor。
- 不让 HTTP supervisor 接受非 loopback 连接或多用户/远程访问。
- 不默认引入共享内存 fast path；只有完成重写后的基准证明 HTTP 复制是显著瓶颈时才另立设计。
- 不承诺立即中断正在执行的 Paddle GPU kernel。
- 不维护 MinerU backend 到模型文件组合的第二份映射。
- 不在生产包中保留 `VIBEOCR_OCR_TRANSPORT`、旧 RPC 或兼容开关。

## 3. 已锁定的产品与架构决策

| 主题 | 决策 |
|---|---|
| 进程边界 | GUI 与模型物理隔离；Paddle 驻留 supervisor；MinerU/PDF 为其管理的子进程 |
| 前端实例 | PySide/WinUI 共用实现，但每个前端会话独占一个 supervisor |
| transport | localhost HTTP；不默认使用共享内存 |
| 协议 | HTTP v2 + job；不扩展 WorkerHost RPC v1 |
| 单张 | 保留产品入口，后端执行 batch-size=1 |
| MinerU 批量 | 预算内一次原生多文件请求，超限才切块；文件名/stem 唯一 |
| Paddle 批量 | 稳定传输安全上限 + 按管道/设备动态计算微批 |
| GPU 调度 | 每张 GPU 默认一个重型任务；微批边界调度；交互优先并防饥饿 |
| 错误恢复 | OOM 缩批、坏输入二分隔离、瞬态错误有限重试、确定性错误快速失败 |
| 取消 | queued 立即取消；Paddle 微批边界停止；独占子进程可升级为硬终止 |
| 部分结果 | 保留已成功项，失败/取消项可续跑；PDF 最终文件事务式发布 |
| TTL | 全局默认 + 按管道覆盖；普通 TTL 为软驻留；永久驻留为硬 pin |
| MinerU 下载 | 首次真实解析时由 MinerU 下载；页面提示阶段/日志，不做单独下载窗口 |
| 切换方式 | 一次性重写并原子切换；故障通过回滚上一版本处理 |

---

## 4. 目标深模块与 interface

### 4.1 外部 seam：HTTP v2

前端只学习以下 interface，不感知 Paddle、MinerU、PDF 进程、GPU 微批或 TTL watcher。

| 方法 | 路径 | 类型 | 说明 |
|---|---|---|---|
| GET | `/v2/health` | 快速 | 版本、instance id、capabilities、ready/draining |
| POST | `/v2/jobs/recognition` | multipart | 提交图片/文档识别，返回 `JobRef` |
| POST | `/v2/jobs/pdf-ocr` | JSON | 对已打开 PDF session 提交页级 OCR |
| GET | `/v2/jobs/{job_id}` | 快速 | job/item 状态与摘要 |
| GET | `/v2/jobs/{job_id}/events` | long-poll | `after_sequence` 后的有序事件 |
| GET | `/v2/jobs/{job_id}/result` | 快速/流式 | 获取稳定排序的部分/最终结果 |
| POST | `/v2/jobs/{job_id}/cancel` | 快速 | 设置取消请求，返回实际 cancel mode |
| POST | `/v2/jobs/{job_id}/retry` | 快速 | 仅为 failed/cancelled item 创建新 job |
| DELETE | `/v2/jobs/{job_id}` | 快速 | 释放已终态 job 的临时输入/结果 |
| GET | `/v2/runtime/residency` | 快速 | 模型状态、TTL、pin、lease、显存与驱逐原因 |
| PUT | `/v2/runtime/residency` | 快速 | 设置全局/按管道 TTL 与 pin |
| POST | `/v2/runtime/release` | 快速 | 请求释放指定或全部空闲模型 |
| POST/GET | `/v2/pdf/sessions/...` | 快速/有界 | 打开、预览、编辑、保存等 PDF session 操作 |
| POST | `/v2/qrcode/decode|generate` | 有界 | QR/条码能力 |
| GET/PUT | `/v2/settings` | 快速 | 后端设置快照与受控更新 |

约束：

- 所有业务请求必须带 `Authorization: Bearer <session-token>`。
- JSON 响应必须含 `schema_version`、`instance_id` 和 typed error code。
- 不把 Python traceback、Qt/.NET 类型、内部路径或模型对象放进合同。
- job events 使用递增 sequence；断线后从 `after_sequence` 继续 long-poll。
- recognition multipart 包含一个 manifest JSON 和重复 `files` 字段；服务端生成内部 item id，不信任上传文件名作为路径。

### 4.2 Job interface

Job 状态机：

```text
accepted → queued → running → completed
                    ├──────→ completed_with_errors
                    ├──────→ cancel_requested → cancelled
                    └──────→ failed
```

Item 状态机：

```text
queued → running → succeeded
                 ├→ failed
                 └→ cancelled
```

必备字段：

- `job_id`、`kind`、`priority`、`created_at`、`started_at`、`finished_at`
- `state`、`stage`、`progress_current`、`progress_total`
- `items[]`：`item_id`、`display_name`、`state`、`attempt`、`error`
- `cancel_requested_at`、`cancel_mode`、`degraded`
- `summary`：成功/失败/取消数量
- `event_sequence`、`result_available`

不变式：

- 输入顺序和结果顺序一致。
- terminal job 不再回到非 terminal 状态。
- retry 创建新 job 并引用 source job/item，不修改原 job 历史。
- cancel 送达不等于 cancelled；只有执行资源真正停止后进入终态。
- completed item 的结果在 job retention 期内始终可读取。
- job 不在 GUI 退出后继续后台运行；前端会话结束即 drain/cancel supervisor。
- partial result 只保证当前 supervisor 会话和 retention 期内可用；用户显式导出的文件才是持久产物。
- supervisor 异常退出后不恢复执行旧 job；下次启动清理 stale staging，并可从日志诊断中断原因。

### 4.3 内部模块

| 模块 | 小型 interface | 隐藏的实现复杂性 |
|---|---|---|
| `SupervisorModule` | submit/status/events/result/cancel/retry/runtime/shutdown | job、调度、adapter、生命周期总编排 |
| `JobRegistry` | create/transition/append_event/snapshot/purge | 状态机、sequence、retention、并发一致性 |
| `InputStager` | stage/resolve/release | multipart spool、配额、文件名清理、临时目录 |
| `DeviceScheduler` | enqueue/cancel/drain | GPU lease、优先级、老化、微批边界 |
| `BudgetPlanner` | plan(items, runtime_profile) | 文件数/字节/像素/页数预算与 oversized item |
| `RecoveryPolicy` | classify/next_action | OOM 缩批、二分隔离、瞬态重试与预算 |
| `ResidencyManager` | lease/status/configure/release | TTL、pin、LRU、VRAM pressure、MinerU process TTL |
| `PipelineAdapter` | capabilities/recognize_many/release | Paddle/MinerU 差异；这是两个生产 adapter 的真实 seam |
| `PdfPort` | session/render/mutate/save/cancel | 生产 HTTP child adapter + 测试 fake |

测试只通过这些 interface 验证可观察行为；不再为 pass-through handler/monkey patch 保留细碎测试。

---

## 5. 目标文件结构

### 5.1 新增

```text
packages/vibeocr-contracts-py/src/vibeocr/protocol/v2/
  openapi.snapshot.json
  errors.json
  golden/
  schemas/

packages/vibeocr-client-py/src/vibeocr/supervisor/
  client.py
  process.py
  contracts.py
  errors.py
  job_handle.py

packages/vibeocr-backend/src/vibeocr/supervisor/
  main.py
  app.py
  bootstrap.py
  auth.py
  composition.py
  module.py
  jobs/
    models.py
    registry.py
    events.py
    retention.py
    staging.py
  inference/
    scheduler.py
    budgets.py
    recovery.py
    residency.py
    paddle_adapter.py
    mineru_adapter.py
  pdf/
    adapter.py
    orchestrator.py

tests/supervisor/
tests/contracts/v2/
tests/benchmarks/inference/

src/dotnet/VibeOCR.Contracts/HttpV2/
src/dotnet/VibeOCR.Platform/Inference/
```

### 5.2 重点复用

- `services/ocr_service.py`
- `core/pipelines/pipeline_*.py`
- OCRResult/TextBlock/表格与公式结果转换
- `services/mineru_service.py` 中 API 启动、日志和结果解析逻辑
- `services/pdf_backend_process.py` 中 PyMuPDF session 实现
- QR、导出、设置领域逻辑
- Job Object、日志转发、双前端互斥和单实例能力

### 5.3 最终删除

- Python `packages/vibeocr-*/src/vibeocr/worker_host/` 全部
- `protocol/v1/` 与 `docs/protocol/v1.md`
- `services/ocr_worker_process.py`
- `services/ocr_service_subprocess.py`
- `services/worker_runtime_state.py`
- `services/mineru_runtime_cache.py`
- `services/mineru_batch_service.py`
- `utils/shared_memory_v2.py`
- 旧 `workers/ocr_worker.py`、`workers/batch_queue_manager.py`（算法素材迁移后）
- Python `BackendClient`/`SyncBackendClient`/`OcrHttpClient`
- .NET `WorkerHostClient.cs`、`SharedPayloadClient.cs`、`FrameCodec.cs`
- .NET `RpcEnvelope`、`RpcMethods`、`SharedPayloadRef` 及其旧 DTO
- `VIBEOCR_OCR_TRANSPORT` 和所有 SHM/Named Pipe fallback
- PySide `MinerUPreflightWorker`、主进程 `MinerUService` 分流和直接 `PdfBackendClient` ownership
- 旧 WorkerHost/SHM/patch 对应测试

保留 `pdf_backend_process.py`，但其 client ownership 移入 supervisor；是否改名在删除阶段一次完成。

### 5.4 目标 distribution 依赖

```text
vibeocr-contracts-py
    ↑                 ↑
vibeocr-client-py   vibeocr-backend
    ↑
vibeocr-pyside
```

- `vibeocr-contracts-py`：协议 schema、稳定 enum/DTO，无 UI/模型依赖。
- `vibeocr-client-py`：supervisor 进程启动、HTTP client、job handle 和前端可用结果 DTO；只依赖 contracts。
- `vibeocr-backend`：supervisor、Paddle/MinerU/PDF/QR/export/settings 实现；只依赖 contracts，不反向依赖 client。
- `vibeocr-pyside`：Qt UI/application adapter；只依赖 client + contracts，不依赖 backend。
- `core/pipelines`、模型加载和后端专用 options 从 client distribution 移入 backend；稳定行为和测试保留。
- 共享结果的 wire DTO 放 contracts；PySide/WinUI 各自把 DTO 映射为展示模型，不共享 UI 类型。

---

## 6. 内部实施阶段

> 以下阶段发生在同一重写分支中。阶段完成只代表内部门禁通过，不代表可发布。生产入口在 Phase 8 才改变一次。

### Phase 0：冻结基线并取代旧 ADR

**目标**：让重写有可测量的行为和性能真源。

**任务**

- [ ] 新增 superseding ADR，明确 HTTP/job/supervisor 决策，并标注取代 2026-07-14 WorkerHost ADR。
- [ ] 固化两套前端功能对等矩阵；语义真源改为 supervisor interface/领域结果，而非某个 UI。
- [ ] 建立固定测试语料：
  - 小/中/大图片及不同分辨率；
  - 纯文本、表格、公式、结构化文档；
  - 正常/损坏/加密/超大 PDF；
  - MinerU 多文件与唯一 stem 冲突样例。
- [ ] 记录当前发布版本基线：冷/热单张延迟、批量吞吐、RAM/VRAM 峰值、启动、取消等待、PDF 页吞吐。
- [ ] 冻结旧架构允许行为清单；禁止在重写期间继续向旧 WorkerHost/SHM 增加功能。
- [ ] 设计一键基准和故障注入命令，输出机器信息和 JSON 结果。

**主要文件**

- Add: `specs/2026-07-24-inference-supervisor-adr.md`
- Modify: `docs/quality/feature-parity.md`
- Add: `tests/fixtures/inference/manifest.json`
- Add: `tests/benchmarks/inference/`
- Modify: CI workflow paths

**退出条件**

- 当前正式版本的功能矩阵全部有自动化或明确人工证据。
- 基准可在同一机器重复至少 5 次，关键指标变异系数达到可设门槛的水平。
- ADR 无未决架构问题。

---

### Phase 1：HTTP v2 与跨语言合同

**目标**：先冻结调用者必须知道的 interface。

**任务**

- [ ] 定义 job/job-item/event/result/error/residency/settings/PDF DTO。
- [ ] 定义错误分类：validation/auth/not_found/conflict/cancelled/oom/transient/backend_unavailable/internal。
- [ ] 定义 multipart manifest、请求体上限、item/file 映射和唯一内部文件名规则。
- [ ] 生成/冻结 OpenAPI snapshot 与 JSON Schema/golden fixtures。
- [ ] Python contracts parser 严格拒绝未知必需字段、非法状态跃迁和错误 enum。
- [ ] C# 建立 HTTP v2 DTO 与 source-generated `JsonSerializerContext`。
- [ ] 冻结目标 distribution 依赖图，并建立 wheel metadata/AST import gate：backend 不依赖 client，PySide 不依赖 backend。
- [ ] Python/C# 对同一批 golden 请求、响应和错误做双向反序列化测试。
- [ ] 定义 capability/version negotiation；major 不匹配直接拒绝启动。
- [ ] 不修改旧 v1；v2 独立存在，最终切换时整体删除 v1。

**主要文件**

- Add: `packages/vibeocr-contracts-py/src/vibeocr/protocol/v2/**`
- Add: `packages/vibeocr-client-py/src/vibeocr/supervisor/contracts.py`
- Add: `src/dotnet/VibeOCR.Contracts/HttpV2/**`
- Add: `tests/contracts/v2/**`
- Add/modify: `.NET Contracts.Tests`

**测试**

- Schema/golden 正反例。
- Python ↔ C# enum、时间、UUID、nullable、unknown field 一致性。
- 旧 v1 payload 对 v2 必须失败，防止假兼容。

**退出条件**

- Python 与 C# golden 100% 一致。
- OpenAPI snapshot 的变化必须显式审查。
- 无业务实现也能用 fake server/client 完成 submit → event → result → cancel 合同测试。

---

### Phase 2：Supervisor 启动、安全与通用 Job 引擎

**目标**：不加载真实模型即可验证完整进程和 job 生命周期。

**任务**

- [ ] 新建 `vibeocr-supervisor` entry point，绑定预创建的 `127.0.0.1:0` socket，消除选端口竞态。
- [ ] 通过 stdout ready envelope 返回 port、PID、instance id、protocol、capabilities；后续 stdout 全部作为日志。
- [ ] 每次启动生成 256-bit session token；token 只通过继承环境或受控 stdin/bootstrap handle 传递，禁止放入命令行、ready envelope 或日志。
- [ ] FastAPI middleware 强制 loopback、Bearer token、instance id、body/part/count 配额。
- [ ] 保留父进程 watchdog、Job Object 和整棵子进程终止。
- [ ] 实现 `InputStager`：UploadFile spool → job 私有临时目录；清理文件名、限制配额、支持单个超限项独立失败。
- [ ] 实现 `JobRegistry`、状态跃迁、event sequence、long-poll、result retention 和 purge。
- [ ] job store 为会话级临时状态，不做跨 supervisor 恢复；启动时识别并清理 stale staging。
- [ ] 实现 supervisor draining/shutdown：拒绝新 job，取消 queued，等待 bounded running，再清理子进程。
- [ ] 用 fake executor 实现完整 happy path、partial result、cancel、retry。
- [ ] 日志加入 `instance_id/job_id/item_id/stage`，并脱敏 token、用户绝对路径和文档内容。

**主要文件**

- Add: `packages/vibeocr-backend/src/vibeocr/supervisor/{main,app,bootstrap,auth,module,composition}.py`
- Add: `.../supervisor/jobs/**`
- Add: `packages/vibeocr-client-py/src/vibeocr/supervisor/{process,client,job_handle,errors}.py`
- Add: `tests/supervisor/test_{bootstrap,auth,staging,registry,events,lifecycle}.py`

**关键测试**

- 无 token/错 token/非 loopback/过大请求全部拒绝。
- 端口并发启动 100 次无占用竞态。
- GUI/客户端强杀后 supervisor、MinerU/PDF mock child 无孤儿。
- event long-poll 断线重连不丢不重。
- retention 清理不删除活动 job；Windows 文件占用时有界重试。

**退出条件**

- fake executor E2E 可由 Python 和 C# 客户端完成。
- 进程强杀/超时/重复 shutdown 无泄漏。
- GUI 事件循环测试中没有同步 HTTP 等待。

---

### Phase 3：调度、预算、恢复与驻留

**目标**：建立不依赖具体 OCR 实现的深调度模块。

**任务**

- [ ] `DeviceScheduler` 按 device 建立默认单重型执行 lease。
- [ ] 实现 interactive/background priority；每个微批后重新调度；加入 priority aging。
- [ ] `BudgetPlanner` 分离：
  - transport safety：文件数、编码总字节、解码像素/预计页数；
  - compute microbatch：adapter capability、device、VRAM、输入尺寸。
- [ ] oversized 单项独立运行，不因超过普通批上限而静默丢弃。
- [ ] `RecoveryPolicy`：
  - OOM：清缓存并减半微批，有界重试；
  - 疑似输入错误：二分隔离；
  - transient：指数退避且受总时间/次数预算；
  - cancellation/config/model error：不重试。
- [ ] `ResidencyManager`：
  - 全局 default TTL + pipeline override；
  - active lease 不驱逐；
  - TTL 到期释放；
  - VRAM pressure 下 LRU 提前释放普通 idle model；
  - pin 容量检查与冲突错误；
  - status 包含剩余 TTL、实际/预计显存、驱逐原因。
- [ ] 用 fake clock、fake GPU、fake adapters 做确定性并发测试，不靠 sleep。

**主要文件**

- Add: `.../supervisor/inference/{scheduler,budgets,recovery,residency}.py`
- Reuse/refactor: `gpu_memory_monitor.py`
- Add: `tests/supervisor/inference/**`

**关键测试**

- 单图在批量微批边界插队，但后台 job 最终仍前进。
- 两个 GPU 可各跑一个重型任务，同一 GPU 不并发。
- cancel_requested 不提前变 terminal。
- active lease 与 TTL/LRU/release 竞争无提前卸载。
- pin 冲突返回 typed error。
- OOM 重试有界且记录 degraded；坏文件只影响对应 item。

**退出条件**

- 所有调度/TTL/恢复测试使用 fake，无真实模型也可稳定重复。
- scheduler interface 不暴露 Paddle/MinerU 类型。
- 没有 monkey patch、import-time patch 或 transport-specific TTL 字段。

---

### Phase 4：Paddle adapter 与统一 `recognize_many`

**目标**：让所有 Paddle 系管道只通过统一 adapter 进入 scheduler。

**任务**

- [ ] 新建 `PaddlePipelineAdapter`，显式注入 `OCRService`/registry，不再使用 worker 单例/子进程 wrapper。
- [ ] 外部只实现 `recognize_many(items, options, context)`；单张立即传一项。
- [ ] 通用 OCR 保留一次 `pipeline.predict(list)` 真批量。
- [ ] PP-StructureV3/PaddleOCR-VL/公式/表格在 capability 中如实声明当前 fallback；禁止把循环单张报告为真批量。
- [ ] 将稳定 OCRResult、TextBlock、content_list、markdown/html 解析原样复用。
- [ ] 每个计算微批持有 residency/device lease；完成后 touch TTL。
- [ ] 把当前固定 16/10/8 等常量移入 benchmark-derived profile 或 adapter capability。
- [ ] 建立 mock pipeline 测试和至少一组真实通用 OCR GPU/CPU E2E。

**主要文件**

- Add: `.../supervisor/inference/paddle_adapter.py`
- Modify: `services/ocr_service.py`（仅为显式依赖/统一 batch interface）
- Reuse: `core/pipelines/pipeline_*.py`
- Replace tests: OCR worker/BatchQueueManager 实现测试 → adapter/interface 测试

**退出条件**

- 单张与一元素批结果完全等价。
- 同一输入序列结果顺序稳定。
- 真实 OCR 真批量只调用一次外层 predict；fallback 管道调用次数符合 capability。
- 无 `OCRServiceSubprocess`、RCBG 或 SHM 依赖进入新路径。

---

### Phase 5：MinerU 多文件 adapter 与首次下载体验

**目标**：让 MinerU 成为 supervisor 内的真实 pipeline adapter，不再由 UI/主进程分流。

**任务**

- [ ] 把 `MinerUService` 拆为显式 `MinerUProcessAdapter`，由 supervisor ownership 启停。
- [ ] 修复端口 0 bootstrap/token/日志关联；保留上游 mineru-api 作为独立子进程。
- [ ] 实现 `recognize_many`：
  - 预算内一次 `/file_parse` 多文件请求；
  - 每项唯一内部 stem；
  - 响应按 item id/顺序还原；
  - 超限按文档预算切块。
- [ ] 默认 backend 下不承诺跨文档计算批；metrics 区分 HTTP batch 与 compute batch。
- [ ] 首次解析阶段发布 `starting_backend/downloading_models/loading_models/parsing`。
- [ ] 删除 `ensure_mineru_models` preflight 调用与首次成功标记判定；不展示虚假百分比。
- [ ] cancel：先协作停止后续块；独占 MinerU job 超过宽限期后终止子进程并重建。
- [ ] TTL/LRU：MinerU idle release 的实际动作是停止 API 子进程；磁盘模型不删除。

**主要文件**

- Add: `.../supervisor/inference/mineru_adapter.py`
- Refactor/reuse: `services/mineru_service.py`
- Delete later: `mineru_batch_service.py`、`mineru_runtime_cache.py`
- Modify: PySide MinerU 状态文案测试、MinerU service tests

**退出条件**

- N 个预算内文件只产生一次 `/file_parse`。
- 重名输入不冲突；结果顺序稳定。
- 首次下载期间 UI 持续展示真实 stage/log；取消后无假完成状态。
- MinerU crash、超时、坏文档和硬取消不带倒 supervisor/Paddle。

---

### Phase 6：PDF、QR、导出与设置功能对等

**目标**：所有后端能力都经 supervisor，避免切换后仍存在 UI 旁路。

**任务**

- [ ] `PdfProcessAdapter` 独占管理现有无模型 PDF child；GUI 不再实例化 `PdfBackendClient`。
- [ ] 快速 PDF session 操作通过 supervisor 有界代理。
- [ ] PDF OCR 作为 job：
  - render batch → recognition microbatch；
  - 页面 item 独立状态；
  - 微批/页边界取消；
  - partial page result；
  - 最终 save 使用临时文件 + fsync/replace 的事务式发布；
  - 原有 sidecar/续传语义按产品要求保留或显式迁移。
- [ ] PDF worker 无响应时先协作 cancel，再终止并重建；不影响 Paddle/MinerU。
- [ ] QR decode/generate、OCR export、settings snapshot/switch/install 全部移入 supervisor。
- [ ] 依赖安装等长操作复用 job engine；快速设置保持同步 HTTP。
- [ ] settings 使用新 residency schema；执行一次旧配置数据迁移。

**主要文件**

- Add: `.../supervisor/pdf/{adapter,orchestrator}.py`
- Reuse: `services/pdf_backend_process.py`
- Refactor: application facades/orchestrator
- Add/replace: PDF/QR/settings supervisor tests

**退出条件**

- PySide/WinUI 功能矩阵中 PDF、QR、导出、设置均有 supervisor E2E。
- supervisor 是 MinerU/PDF child 的唯一 owner。
- PDF 取消/崩溃不会覆盖原文件或留下被宣称成功的半成品。

---

### Phase 7：PySide 与 WinUI 客户端同时迁移

**目标**：两套 UI 都只依赖 client interface + v2 DTO。

#### 7A PySide

- [ ] 新 `SupervisorClient` 使用 `httpx.AsyncClient`；进程启动和 job handle 集中在一个 session module。
- [ ] 提供 Qt-safe application adapter：提交、long-poll、取消、结果通过 signal/async callback 回主线程。
- [ ] Single tab 改为一元素 recognition job。
- [ ] Batch tab 一次提交逻辑 job，不在 UI 切 GPU 微批。
- [ ] PDF session manager 不再持有 PDF client，不再运行 MinerU preflight。
- [ ] Settings 读取/写入 residency status/config。
- [ ] QR/export 全部改用新 client。
- [ ] UI close 只发 supervisor drain/shutdown，不自行枚举子进程。

#### 7B WinUI

- [ ] 新 `IInferenceClient`/`InferenceHttpClient`，基于 `HttpClient`、typed DTO 和 multipart streaming。
- [ ] 新 `InferenceSupervisorProcess` 复用日志、Job Object、整棵终止和启动错误呈现。
- [ ] RecognitionViewModel 改为一元素 job；取消等待真实 job terminal。
- [ ] BatchViewModel 一次提交逻辑 job，删除 `SemaphoreSlim + RpcMethods.Recognize` 循环。
- [ ] Pdf/Settings/QrCode ViewModel 改用 v2 client。
- [ ] progress event 通过 long-poll sequence 驱动；generation 仍只用于 UI 丢弃过时显示，不替代 server cancel。

**主要文件**

- Modify: `packages/vibeocr-client-py/src/vibeocr/client/**`
- Add: `packages/vibeocr-client-py/src/vibeocr/supervisor/**`
- Modify: PySide managers/views/tabs
- Add: `src/dotnet/VibeOCR.Platform/Inference/**`
- Modify: WinUI Recognition/Batch/PDF/Settings/QR ViewModel

**退出条件**

- 两套 UI 的 client contract tests 对同一 fake HTTP server 全绿。
- PySide UI import scanner 不允许 `services/mineru_service`、`pdf_backend_client`、`worker_host`。
- WinUI 不再引用 `SharedPayloadRef`、`RpcMethods`、Named Pipe。
- GUI 响应性门禁证明所有 HTTP/job 等待都不在 UI 线程。

---

### Phase 8：原子切换与遗留删除

**目标**：一次改变生产入口，并在同一阶段删除旧架构。

**任务**

- [ ] PySide/WinUI 启动入口同时改为 `vibeocr-supervisor`。
- [ ] 删除第 5.3 节所有旧模块、DTO、测试和环境变量。
- [ ] 删除 v1 schema/golden/docs；v2 成为唯一协议资产。
- [ ] 删除 runtime monkey patch、旧 TTL diagnostics workflow 和对应安装调用。
- [ ] 更新 Python package description/entry point、README、ADR、开发文档。
- [ ] 完成 distribution ownership 调整：pipeline/model runtime 移入 backend，移除 `vibeocr-backend → vibeocr-client-py` 反向依赖。
- [ ] 更新 PyInstaller hidden import、wheel staging、WinUI publish 和 artifact manifest。
- [ ] 更新 release workflow 的测试选择和 protocol asset copy。
- [ ] 加入禁止遗留架构守卫：
  - 源码/制品不存在 `VIBEOCR_OCR_TRANSPORT`；
  - 不存在 `SharedPayloadRef`/`SyncBackendClient`/`WorkerHostClient`；
  - UI 不 import backend implementation；
  - backend 不 import PySide/WinUI。
- [ ] 一次性迁移设置数据：
  - 新增 residency schema；
  - 旧 `pipeline_ttls` 转换为等价 TTL/pin；
  - 保留迁移前配置备份，支持版本回滚；
  - 不保留旧 transport runtime 代码。

**原子切换规则**

- 不提交“生产可选 old/new”的环境变量或设置。
- 在 Phase 8 完成前，重写分支不得发布。
- Phase 8 合入后的任何产物都只能启动新 supervisor。
- 回滚依赖上一发布版本及配置备份，不依赖新版本中的旧代码。

**退出条件**

- 遗留符号扫描为零。
- 两种发布产物都只包含 v2/supervisor。
- 功能对等矩阵全部 PASS。
- 旧版本使用配置备份可正常启动，验证回滚通道。

---

### Phase 9：阈值校准、故障注入与发布级验证

**目标**：确定批量参数并证明一次性重写可发布。

#### Paddle 基准矩阵

- 管道：OCR、PP-StructureV3、PaddleOCR-VL、表格、公式。
- device：CPU、低/中/高显存 GPU。
- 图片：小/中/大像素、不同长宽比、不同压缩率。
- batch sweep：1/2/4/8/16/32，直到吞吐不再增长或资源门槛触发。
- 冷/热模型分别测量。

#### MinerU 基准矩阵

- backend：当前支持的 pipeline/hybrid/VLM 配置。
- 文档数、总字节、总页数分别变化。
- 小文件多文档、单个大 PDF、混合格式和损坏输入。
- 比较逐文件请求、预算内多文件请求和不同 chunk。

#### 指标

- warm/cold 单张 P50/P95
- images/pages per second
- 首个 item 完成时间
- peak RAM/VRAM
- HTTP upload/serialization 占总耗时比例
- 取消到 terminal 延迟
- OOM/retry/degraded 次数
- 模型加载、TTL 到期、LRU/pin 行为

#### 初始发布门槛

基线采集后按波动修订，建议起点：

- 通用 OCR 热态吞吐不低于旧版本中位数的 95%。
- 单张热态 P95 不高于旧版本的 110%。
- 无新 OOM；峰值 RAM/VRAM 不超过旧版本 115%，除非有已审查原因。
- HTTP 传输/序列化不是代表性 OCR 总耗时的主要部分；若成为瓶颈，先优化 streaming/spool/压缩，不自动回退共享内存。
- 所有阈值必须来自 profile/benchmark，不在 UI 和多个 manager 中重复硬编码。

#### 故障注入

- 强杀 GUI、supervisor、MinerU、PDF worker。
- supervisor 启动失败、端口异常、token 错误。
- HTTP 中断、超时、重复提交、重复 cancel。
- MinerU 首次下载断网/取消/重试。
- Paddle OOM、坏图片、混合成功失败。
- TTL 到期与新任务、release、pin 竞争。
- Windows 休眠/恢复、用户退出、更新替换。

#### 自动化门禁

- Python：全量 pytest、Ruff、Pyright、coverage。
- Contracts：v2 schema/golden/OpenAPI snapshot。
- .NET：Contracts/Platform/App tests，SDK 10.0.302 CI。
- E2E：PySide/WinUI 分别启动真实 supervisor。
- Soak：连续 job、反复启停、强杀，无孤儿/句柄/临时目录泄漏。
- Packaging：wheel、PyInstaller、WinUI artifact scan 与 manifest/hash。
- `git diff --check`、禁止遗留符号扫描。

**退出条件**

- 所有自动门禁通过。
- 双前端真实设备矩阵签核完成。
- 批量阈值配置有基准证据和回归测试。
- 发布负责人能从日志按 instance/job/item 追踪一次完整失败。

---

### Phase 10：一次性发布与回滚演练

**目标**：从同一提交发布两套前端，并证明可恢复。

**任务**

- [ ] 从最终候选提交构建 contracts/client/backend wheels、PySide、WinUI。
- [ ] 两个 manifest 记录同一 backend wheel SHA-256、protocol v2 和源提交。
- [ ] 完成 Classic/Next 真实设备启动、识别、PDF、MinerU 首次下载、取消、TTL 和更新测试。
- [ ] 先执行回滚演练：安装候选 → 运行设置迁移 → 回滚上一版本 → 使用备份配置成功启动。
- [ ] 发布后监控启动失败、supervisor crash、OOM、MinerU 下载失败和平均 job 延迟。
- [ ] 若触发严重回归，停止分发并回滚上一版本；不在已发布版本中动态启用旧 transport。

**发布阻断条件**

- 任一前端无法启动 supervisor。
- 出现孤儿 MinerU/PDF 进程或不可清理临时数据。
- PDF 保存可能覆盖原文件半成品。
- 部分结果顺序错误或 retry 重算成功项。
- pin/TTL 能驱逐活跃模型。
- 首次 MinerU 下载页面无状态反馈。
- 旧 WorkerHost/SHM 代码或协议资产进入制品。

---

## 7. 阶段依赖与建议工作量

| 阶段 | 依赖 | 粗略工作量（单人净工程日） |
|---|---|---:|
| 0 基线/ADR | 无 | 2–4 |
| 1 HTTP v2 合同 | 0 | 3–5 |
| 2 Supervisor/job | 1 | 5–8 |
| 3 调度/TTL/恢复 | 2 | 5–8 |
| 4 Paddle | 3 | 4–7 |
| 5 MinerU | 3 | 4–7 |
| 6 PDF/功能对等 | 2–5 | 5–8 |
| 7 双客户端 | 1–6 | 7–12 |
| 8 原子切换/删除 | 7 | 3–6 |
| 9 验证/校准 | 8 | 5–10 |
| 10 发布/回滚 | 9 | 2–4 + 观察期 |

合计约 **45–79 净工程日**。这是一次性重写的风险预算，不是日历承诺；真实模型、GPU 档位和双前端人工签核通常决定尾部时间。

## 8. 配置与数据迁移

建议新配置：

```json
{
  "residency": {
    "default_ttl_seconds": 300,
    "pipelines": {
      "OCR": {"ttl_seconds": null, "pinned": false},
      "MinerU": {"ttl_seconds": 600, "pinned": false}
    }
  }
}
```

语义：

- `ttl_seconds=null`：继承全局默认。
- `ttl_seconds>0`：空闲最长驻留时间。
- `pinned=true`：硬 pin；设置前做容量检查。
- 不再用 `0` 同时表达“继承、无限、pin”等多个含义。

迁移要求：

- 首次启动新版本前备份原设置文件。
- 一次性把旧 `pipeline_ttls` 转成新结构，保持用户可观察语义。
- 旧字段可留在备份中供上一版本回滚，但新运行时不读取旧字段。
- 不迁移或删除已下载 Paddle/MinerU 模型。
- 旧 `pipeline_status` 中 MinerU“曾成功”不再控制下载提示。

## 9. 风险与控制

| 风险 | 控制 |
|---|---|
| 一次性切换集成面过大 | 每阶段独立 interface 门禁；Phase 8 前禁止发布 |
| HTTP 大请求内存峰值 | UploadFile spool、请求配额、job staging、预算切块 |
| HTTP 取消仍无法停 GPU kernel | job 状态诚实表达；微批边界停止 |
| MinerU 首次下载看似卡住 | stage + 实时日志 + 明确耗时提示；不展示虚假百分比 |
| MinerU 多文件扩大失败域 | 分类恢复、二分隔离、部分结果与 retry |
| TTL/pin 导致 OOM | active lease、容量检查、LRU、统一 GPU scheduler |
| 两套客户端语义漂移 | 同一 v2 golden/fake server contract suite |
| 旧代码未删干净 | 源码 + artifact 禁止符号扫描 |
| 本机无法跑固定 .NET SDK | required CI 是最终门禁；本地结果不得替代 |
| 新配置导致旧版无法回滚 | 原文件备份 + 回滚演练，不靠运行时双栈 |

## 10. Definition of Done

只有全部满足才算完成：

- [ ] 两套前端只通过 HTTP v2 `InferenceClient` 调用后端。
- [ ] GUI 进程不 import/load Paddle、MinerU、PyMuPDF backend implementation。
- [ ] 单张、批量、PDF OCR、MinerU 都使用统一 job/scheduler。
- [ ] Paddle 真批量/fallback capability 如实，阈值有基准证据。
- [ ] MinerU 预算内单请求多文件、唯一 stem、首次下载提示正确。
- [ ] TTL、pin、LRU、GPU lease、分级取消和部分结果符合已确认语义。
- [ ] PDF 最终输出事务式，无半成品覆盖。
- [ ] PySide/WinUI 功能对等矩阵全部 PASS。
- [ ] 旧 RPC v1、Named Pipe、SHM、嵌套 OCR worker、monkey patch 和回退环境变量全部删除。
- [ ] Python/.NET/E2E/soak/benchmark/package/真实设备门禁全部通过。
- [ ] 更新 ADR、README、协议文档、打包与发布清单。
- [ ] 上一版本回滚演练成功。
