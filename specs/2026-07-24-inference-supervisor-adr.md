# ADR: 统一 Inference Supervisor（HTTP v2 + Job）取代双前端独占 WorkerHost

日期：2026-07-24
状态：已接受（一次性重写架构冻结）
取代：`specs/2026-07-14-dual-frontend-exclusive-workerhost-adr.md`（WorkerHost v1 / Named Pipe / Shared Memory）

## 背景

2026-07-14 ADR 把“双前端各启动独占 WorkerHost”定为架构方向，并显式把“引入 HTTP”列为非目标。在执行中
代码已偏离该 ADR：

- PySide 生产默认已切换为 `OcrHttpClient`（FastAPI 子进程 `ocr_worker_http.py`），不再走 WorkerHost +
  旧 SHM，仅保留 `VIBEOCR_OCR_TRANSPORT=shm` 逃生口。
- WinUI 仍是 `WinUI → WorkerHost → OCRServiceSubprocess → Paddle worker` 的嵌套进程。
- MinerU 由 PySide 主进程通过运行时 monkey patch 接入 TTL/cache，PDF 由 UI 直接持有 client；
  这两条路径在两套前端下语义不一致。
- 单张与批量、Paddle 与 MinerU、传输预算与 GPU 微批存在多套互不相同的分批与失败语义。

调查证据（见 `findings.md` 与 `progress.md` 的“调用链还原”段落）表明，当前分叉源于 transport/进程边界
承载了过多业务语义，而不是某个 UI 的实现缺陷。

## 决策

把所有 OCR/MinerU/PDF 调用路径重写为一个**前端专属、UI-free 的 HTTP inference supervisor**：

1. **进程边界**：GUI 与模型物理隔离。Paddle 模型直接驻留 supervisor 进程；MinerU API 与无模型 PDF
   处理作为 supervisor 管理的子进程。GUI 不加载 Paddle/MinerU/PyMuPDF backend，不同步执行 OCR。
2. **前端实例**：PySide 与 WinUI 共用同一份 supervisor 实现和同一份 HTTP v2 合同，但每个前端会话
   独占一个 supervisor 实例（一对一对应，不共享、不发现对方）。跨产品互斥 Mutex 继续保留。
3. **Transport**：localhost HTTP（绑定 `127.0.0.1:0`，session token，父进程 watchdog/Job Object）。
   不默认引入共享内存；只有完成重写后的可重复基准证明 IPC 复制是显著瓶颈时才另立设计。
4. **协议**：HTTP v2 + job。所有业务请求带 `Authorization: Bearer <session-token>`；JSON 响应含
   `schema_version`、`instance_id` 与 typed error code。socket 断开 ≠ 取消。
5. **统一执行**：单张与批量共用 `recognize_many`；单张是立即执行的一元素 job。Paddle 与 MinerU 都按
   受控预算分批；阈值来自基准。模型驻留为全局默认 TTL + 按管道覆盖 + 显存压力 LRU + 显式永久 pin。
6. **MinerU 下载**：首次真实解析时由 MinerU 按需下载；UI 只提示真实阶段与日志，不维护模型组合或
   readiness 映射，不做独立预下载/组合验证。
7. **切换方式**：一次性重写并原子切换。生产版本只进行一次切换；不提供运行时双栈或旧 transport 回退。
   任何严重回归通过回滚上一发布版本 + 配置备份恢复，不依赖新版本中的旧代码。

## 取代的约束

以下来自 2026-07-14 ADR 的约束被本 ADR 取代：

- “不引入 HTTP、本机常驻守护进程或系统服务” → supervisor 是每前端会话独占的 localhost HTTP 进程，
  不是系统服务，不接受非 loopback 连接。
- “Named Pipe / Shared Memory transport” → 删除。生产 transport 统一为 localhost HTTP v2。
- “WorkerHost RPC v1 协议资产” → 删除（v1 schema/golden/docs）。v2 是唯一协议资产。

## 保留的约束

以下约束延续自 2026-07-14 ADR：

- 单仓库、单份后端实现；两条发布流水线从同一提交构建同一份 backend wheel。
- 跨产品互斥（`Local\VibeOCR.Frontend.Exclusive.v1`），不扫描进程名。
- 同产品单实例语义不变。
- UI 只依赖 client interface + DTO；后端 UI-free。
- 协议 DTO 只含 JSON 基础类型、稳定 enum/string code、文件路径与内容类型、几何/文本块/PDF page/任务
  状态；禁止 `QPixmap`/Qt Signal/XAML 类型/Python traceback/UI 控件状态。

## 目标 distribution 依赖方向

```
vibeocr-contracts-py          （协议 schema、稳定 enum/DTO，无 UI/模型依赖）
    ↑                 ↑
vibeocr-client-py   vibeocr-backend   （client 只依赖 contracts；backend 只依赖 contracts）
    ↑
vibeocr-pyside                        （只依赖 client + contracts，不依赖 backend）
```

- 不允许 `backend → client` 反向依赖（修正当前因 pipeline 位于 client distribution 而存在的反向依赖）。
- Paddle/MinerU/PDF 实现归 backend；稳定结果 wire DTO 放 contracts；UI-only 展示模型留 client/PySide。

## 自动化守卫

Phase 8 加入的禁止遗留架构守卫：

- 源码/制品不存在 `VIBEOCR_OCR_TRANSPORT`、`SharedPayloadRef`、`SyncBackendClient`、`WorkerHostClient`。
- UI 不 import backend 实现；backend 不 import PySide/WinUI。
- `vibeocr-backend` 不反向依赖 `vibeocr-client-py`；`vibeocr-pyside` 不依赖 `vibeocr-backend`。

## 非目标

- 不重写 Paddle/MinerU 已稳定的结果解析、文本块转换、表格/公式处理算法。
- 不引入系统服务或跨前端共享 supervisor。
- 不让 supervisor 接受非 loopback 连接或多用户/远程访问。
- 不承诺立即中断正在执行的 Paddle GPU kernel。
- 不在重写期间向旧 WorkerHost/SHM 增加功能；重写分支在 Phase 8 完成前不得发布。

## 后果

- **正面**：单套业务 supervisor + 必要隔离子进程；transport 退化为 adapter；调用方不感知
  Paddle/MinerU/PDF 进程、GPU 微批或 TTL watcher。两套前端共用同一 golden/fake server 合同套件，
  防止语义漂移。
- **代价**：约 45–79 净工程日的一次性重写风险预算；Phase 8 前不发布；回滚依赖上一版本与配置备份。
- **实施计划**：见 `specs/2026-07-24-inference-supervisor-rewrite-plan.md`（Phase 0–10）。

## 参考

- `specs/2026-07-24-inference-supervisor-rewrite-plan.md`（实施计划）
- `findings.md` / `progress.md`（调用链还原、决策访谈证据）
- `packages/vibeocr-contracts-py/src/vibeocr/protocol/v2/`（新协议资产）
- `packages/vibeocr-backend/src/vibeocr/supervisor/`（supervisor 实现）
- `docs/quality/feature-parity.md`（功能对等矩阵，真源改为 supervisor interface）
