# ADR: Supervisor-only 深 Job Interface

日期：2026-07-25
状态：已接受（全面迁移的目标接口）
依赖：`2026-07-24-inference-supervisor-adr.md`

## 背景

现有 HTTP v2 已能提交 recognition job，但外部 seam 仍然偏浅：

- Python/.NET 客户端要自行组合 submit/status/events/result/cancel；两端能力不一致。
- pipeline/options/priority 没有完整进入 wire、record 和 executor。
- PySide Batch 绕过 client 私有拼 HTTP，并在 UI 侧切成多个 job。
- PySide/.NET PDF OCR 都在 UI 侧 render 后再提交 recognition，不是 PDF OCR job。
- executor 按列表位置关联结果；staging 剔除、短结果和空 payload 会造成错位或伪成功。
- retry 不保留输入，以空 staged 列表重新 dispatch。
- scheduler、budget、recovery、residency 没有隐藏在真实 production execution loop 中。

目标是形成一个有 depth/locality 的外部 module：删除它时，调度、关联、重试、取消、输入保留和
PDF 发布复杂性会重新散落到两个前端；正常调用方只需理解一套 job 生命周期。

## Design It Twice 比较

| 方案 | 外部入口 | 优点 | 主要代价 |
|---|---|---|---|
| Minimal | `submit` / `observe` / `command`，强类型 union | 调用面最小；错误和选项最严格；PDF 编辑保持独立 | 新 kind/result 需要同步扩展 Python/C# union |
| Maximum extensibility | `submit` / `observe` / `cancel` / `retry` / `release`，开放版本化 envelope | 未来 kind/source/result 扩展灵活；observe 仍原子 | 开放字符串和 envelope 更容易退化成万能字典 |
| Default simple | `submit` / `watch` / `command`，客户端 item_id | 最易解释；HTTP/内存 adapter 对称 | 客户端生成权威 item_id 会扩大不可信输入面 |

## 决策

采用折中后的最小深 interface：

```python
class InferenceJobs(Protocol):
    async def submit(self, request: SubmitRequest) -> JobRef: ...
    async def observe(
        self,
        job_id: str,
        *,
        after_sequence: int = 0,
        wait_seconds: float = 25.0,
    ) -> JobUpdate: ...
    async def command(self, command: JobCommand) -> CommandResult: ...
```

.NET 保持同构：`SubmitAsync`、`ObserveAsync`、`CommandAsync`。PySide 只增加 Qt signal adapter，
不重新实现 polling、分批、结果对齐或 retry。

非 OCR 的 PDF 打开、预览、旋转、删页、保存等操作继续属于独立的 `DocumentSessions` bounded
interface；PDF OCR 本身必须通过 `InferenceJobs`。

### SubmitRequest

`SubmitRequest` 是版本化的强类型 discriminated union：

- `RecognitionRequestV1`：Paddle OCR、PP-StructureV3、PaddleOCR-VL、表格、公式。
- `DocumentParseRequestV1`：MinerU 文档解析。
- `PdfOcrRequestV1`：supervisor 内部完成 render → recognition → text-layer write → publish。

每个请求冻结：

- `request_id`：幂等键；同 id + 同 digest 返回原 `JobRef`，同 id + 不同 digest 返回 conflict。
- `priority`：`interactive` 或 `background`。
- `pipeline`：稳定 pipeline id + `options_version` + 对应强类型 semantic options。
- `items`：客户端稳定 `client_item_key`、ordinal、display name 和强类型 source。

普通 recognition/document parse source 首期只支持 multipart upload。PDF OCR source 使用
`session_id + session_revision + page_index`，不让 UI 上传自己渲染的页面。设备 id、微批大小、
显存、重试次数、TTL watcher、子进程端口和 staging 路径都不是业务请求字段。

### Item identity

- 客户端提交 job 内唯一的 `client_item_key` 和连续 ordinal。
- supervisor 生成权威 `item_id`。
- `JobRef` 立即返回 `item_id ↔ client_item_key ↔ ordinal` 映射。
- adapter、event、outcome、retry 和 store 全部按 `item_id` 关联，不按列表位置、文件名或 stem。
- retry 创建新 job 和新 item_id，并保留 `source_job_id/source_item_id`。

### Observe

`JobUpdate` 在同一 registry 锁/事务水位下原子返回：

- 权威 `JobSnapshot`
- `after_sequence` 之后的 events
- 新增的 typed item outcomes
- `through_sequence`
- 响应截断时的 `more`

这替代客户端分别读取 status/events/result 的竞态组合。成功 outcome 必须有合法 typed payload；
失败 outcome 必须有 typed error；二者互斥。空文本可以是合法 OCR 结果，但 `{}`、缺项、重复项、
未知 item_id 和少返回项都是 `ADAPTER_PROTOCOL_VIOLATION`，不能补空后标成功。

### Command

`JobCommand` 首期是：

- `CancelJob(job_id, command_id)`：幂等记录请求；只有资源真正停止后才进入 `cancelled`。
- `RetryItems(source_job_id, command_id, item_ids=None, priority_override=None)`：默认只重试失败/
  取消项，继承 pipeline/options 和 retained input artifact。
- `ForgetJob(job_id, command_id)`：只允许 terminal job，递减 artifact 引用并清理结果。

输入已过 retention 时 retry 返回 typed `INPUT_EXPIRED`，不得 dispatch 空输入。

## 内部 module 边界

```text
InferenceJobs
  → strict request/options admission
  → ArtifactStore (streaming, immutable, ref-counted)
  → JobEngine / Registry / EventJournal / OutcomeStore
  → DeviceScheduler
  → BudgetPlanner
  → PipelineAdapter
  → RecoveryPolicy
  → ResidencyController
```

内部 `PipelineAdapter.execute_batch(...)` 返回
`Mapping[item_id, PipelineOutcome]`，不能直接修改 `JobRecord`。`JobEngine` 是唯一能推进 job/item
状态和提交 outcome 的 module。

每个 execution slice：

1. scheduler 按动态 priority/aging 授予唯一 heavy device lease；
2. budget 根据 capability、encoded bytes、decoded pixels/pages 和可用资源决定微批；
3. residency 与 device lease 在 `try/finally` 中成对获取/释放；
4. recovery 有界执行 OOM 减半、坏输入二分和 transient 退避；
5. slice 边界检查 cancel，然后重新排队，使 interactive 可插队；
6. engine 校验 adapter 返回的 item_id 集合并提交 typed outcome。

`HttpInferenceJobs` 与 `InMemoryInferenceJobs` 只替换 transport/content I/O，必须组合同一个
`JobEngine` 并运行同一 conformance suite。内存 adapter 不复制一套“自动成功”的浅状态机。

## PDF OCR

- 非 OCR PDF 编辑继续由 `DocumentSessions` 管理。
- `PdfOcrRequestV1` 以 session revision + page 为 item。
- supervisor 在 job-private working copy 上 render、recognize、写文字层。
- 默认所有目标页成功后才事务式发布；若支持部分发布，必须使用显式 publish policy。
- 取消或 crash 不覆盖原文件；发布使用同目录临时文件和原子 replace。
- PySide `PdfSessionManager._ocr_service.recognize_batch` 与 .NET ViewModel 的 render→recognition
  编排在迁移完成时删除。

## 迁移顺序

1. 在 contracts 冻结 request/ref/update/command/outcome 和 typed options；更新 Python/C# golden、
   schemas、OpenAPI。
2. 建立 `JobEngine + MemoryArtifactStore + InMemoryInferenceJobs` 与 conformance tests。
3. 改 registry/observe/outcome；禁止 positional mapping 和 empty success。
4. staging 改为 immutable ref-counted artifact；retry 复用 retained input。
5. 修 scheduler 的队首授予/动态 aging，并把 budget/recovery/residency 接入 execution slice。
6. 接 Paddle 全 pipeline 与 MinerU，完整传输并验证 options/priority。
7. 接 `PdfOcrRequestV1` 与事务式 publish。
8. 实现 HTTP adapter；提交使用 manifest + multipart attachments，observe 用 bounded long-poll。
9. 同时迁移 PySide Single/Batch/PDF 与 .NET ViewModels，删除 UI 分批和私有 HTTP。
10. 删除旧 v2 recognition 专用 route/client、legacy WorkerHost/SHM/PDF OCR 编排和 backend→client 反向依赖。
11. 更新 repo-wide architecture guards、门禁、wheel/PyInstaller/.NET publish/manifest/verifier、
    E2E/soak/benchmark/rollback。

最终生产版本不保留 runtime dual stack；迁移可以在分支上分阶段验证，但原子切换提交必须同时删除
旧生产路径。

## 验收不变式

- 单图是一项 interactive job；批量是一个含 N 项的 logical job。
- pipeline/options/priority 在 Python/.NET wire → record → adapter 全链一致。
- terminal job/item 不回到 non-terminal；cancel ack 不伪装 terminal。
- staging 失败、坏输入、短/乱序/重复结果只影响对应 item，并形成准确 job summary。
- retry 只重试目标 item，复用真实输入；无输入时 typed fail。
- 同一 GPU 默认最多一个 heavy lease；每个 slice 后重新调度。
- settings/TTL/pin 驱动真实 load/unload/stop，驻留只有一个真源。
- HTTP 与内存 adapter 通过相同 conformance trace；Python/C# golden 100% 一致。
- Batch/PDF 前端不持有 OCR service，不感知 transport/compute microbatch。

## 后果

- 外部方法数减少，但 contracts 的 typed union/schema 维护成本上升。
- 一个 logical batch job 会增加 registry/staging 规模，需要 bounded observe/result。
- retained input 支持真实 retry，但必须有配额、引用计数和 retention。
- PDF working copy 增加磁盘 I/O，是换取 cancel/crash 原子安全的必要成本。
- supervisor crash 后不恢复运行 job；若未来需要持久恢复，另立 ADR。
