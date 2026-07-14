# ADR: 双前端独占 WorkerHost 架构

日期：2026-07-14
状态：已接受（Phase 0 架构冻结）
取代：原“迁移完成后删除 PySide UI”的方向

## 背景

VibeOCR 当前是单仓库、单份后端实现的桌面 OCR 应用。仓库内同时存在两套前端：

- **PySide6 Classic**：成熟的 Qt 桌面 UI，仍直接 `import` `services` / `managers` / `workers` / `core` / `models` 等后端包，尚未成为“薄壳”。
- **WinUI Next**：基于 WinUI 3 的下一代 UI，已通过 Named Pipe、随机 session token、父进程 watchdog、Job Object、版本化 RPC 和共享内存传输连接到独占 WorkerHost。

早期设想是“WinUI 迁移完成后删除 PySide UI”。但在实际推进中发现：

1. PySide 经典版仍有大量稳定用户和完整功能，不应被强制淘汰；
2. 两套 UI 面向不同的交互与发版节奏，强制统一会拖慢两者；
3. WinUI 已证明“前端独占一个 WorkerHost”的运行模型可行且健壮。

因此架构方向从“替换”改为“并存 + 独占”。

## 决策

VibeOCR 保持**单仓库、单份后端实现**，同时交付**两套互斥运行的桌面产品**：

1. **进程与会话一一对应**：每个前端启动并独占自己的 WorkerHost；两套前端和两套 WorkerHost 实例**不发现、不连接、不接管对方**。“一一对应”指的是进程/会话级对应，不是复制两份后端代码。
2. **跨产品互斥**使用当前登录会话范围内的 Windows 命名 Mutex（`Local\VibeOCR.Frontend.Exclusive.v1`），**不扫描进程名**。任一产品运行时启动另一产品只显示退出提示，不启动第二个 WorkerHost。
3. **同产品单实例**保留现有语义（参数转发到已有实例），与跨产品互斥 Mutex 解耦。
4. **职责边界**：UI 只保留展示、输入采集和平台壳层能力（截图、拖放、剪贴板、托盘、热键、文件选择、窗口）。OCR、PDF、二维码、模型、依赖和业务设置只在后端实现。UI 只能依赖 `BackendClient` 接口 + 协议 DTO，禁止跨过客户端直接调用后端实现。
5. **单份后端、双份制品**：两条发布流水线从同一提交构建同一份后端 wheel，再分别组合自己的前端。两个产品 manifest 记录 frontend name/version、backend version 和 wheel SHA-256、protocol major/minor、源提交。
6. **WorkerHost 通用化**：移除 `worker_host.main` 对 `winui-dev` profile 的硬编码限制，引入 `frontend_id = pyside | winui`（只用于日志/临时目录/UI 设置路径，不参与业务能力选择）和 `profile = production | dev`。

## 强制边界（依赖方向）

唯一允许的依赖方向：

```
Frontend UI
    ↓
Frontend application/ViewModel
    ↓
BackendClient interface + protocol DTO
    ↓ Named Pipe / Shared Memory
WorkerHost handlers
    ↓
Application facade/orchestrator
    ↓
Domain services and infrastructure adapters
```

- 禁止反向依赖。
- 禁止 UI 跨过 `BackendClient` 直接调用后端实现。
- 协议 DTO 只能包含 JSON 基础类型、稳定 enum/string code、文件路径和内容类型、shared-memory descriptor，以及与 Qt/.NET 无关的几何/文本块/PDF page/任务状态。禁止 `QPixmap`、`QImage`、Qt Signal、XAML 类型、Python traceback 或 UI 控件状态。

## 自动化架构守卫（Phase 0 建立）

新增 `tests/architecture/`，至少包含：

1. **AST import scanner**：PySide 前端（`views`/`widgets`/`ui`）禁止 import 后端包（`services`/`managers`/`workers`/`core`/`models`/`application`/`migration`），以临时 allowlist 的“只减不增”棘轮（ratchet）约束。
2. **WorkerHost UI-free import smoke test**：未安装 PySide6 时可 import 和 `--self-test`。
3. **后端禁止 import UI**：后端包禁止 import `views`/`widgets`/`ui`。
4. **C# project-reference gate**（后续 Phase）：Contracts 不引用 Platform/App，Platform 不引用 App。
5. **artifact import scan**（后续 Phase）：WinUI 后端制品不存在 PySide6、Qt UI 目录和 Qt-only 模块。
6. **协议方法表一致性**：`contracts/v1/methods.schema.json`、C# `RpcMethods.All`、Python `PUBLIC_METHODS` 三方方法名集合一致。
7. **allowlist 只减不增**：迁移每完成一个功能必须减少一项，禁止新增。

## 非目标

- 不引入 HTTP、本机常驻守护进程或系统服务；
- 不允许一个前端复用另一个前端启动的 WorkerHost；
- 不做 WorkerHost 多客户端；
- 不通过扫描进程名实现互斥；
- 不要求两套 UI 布局/交互/发版节奏完全一致；
- 不在迁移期间发布可切换“直连后端/RPC 后端”的隐藏生产开关。

## 现状基线（Phase 0 冻结）

PySide UI 层对后端的直接 import 基线为 **90 处**（`services`=38, `models`=21, `core`=17, `managers`=12, `workers`=2）。这些 import 是临时 allowlist 的起点，后续按功能切片迁移（二维码 → 单图 → 批量 → PDF → 设置/更新），每完成一个切片 allowlist 必须减少。

## 后果

- **正面**：保留两套 UI 的独立价值；后端实现仍原子可演进；边界由自动化测试强制，不依赖团队约定。
- **负面/代价**：需要将 PySide 从“直接调用后端”迁移到“通过 Python BackendClient 调 RPC”，工作量集中在 PySide 侧；物理拆包前需先建立逻辑边界。
- **后续阶段**：Phase 1（互斥 Mutex）、Phase 2（通用 WorkerHost + Python BackendClient）、Phase 3（PySide 垂直迁移）、Phase 4（物理拆包与去 Qt 化）、Phase 5（双 CI 与双发布）、Phase 6（稳定性签核）。详见 `DUAL_UI_IMPLEMENTATION_PLAN.md`。

## 参考

- `DUAL_UI_IMPLEMENTATION_PLAN.md`（完整实施计划）
- `contracts/v1/`（协议 schema、errors、golden）
- `docs/protocol/v1.md`（协议文档，gitignored）
- `src/vibeocr/worker_host/`（WorkerHost 实现）
- `src/dotnet/VibeOCR.Contracts/RpcMethods.cs`（C# 协议常量）
