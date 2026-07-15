# VibeOCR 双前端独占 WorkerHost 实施计划

日期：2026-07-14
状态：实施完成；自动化门禁已落地，真实设备矩阵按发布 Runbook 签核
范围：PySide6 Classic、WinUI Next、Python WorkerHost、构建与发布流水线

## 实施结果（2026-07-15）

- PySide Classic 的二维码、单图/批量 OCR、导出、PDF、设置、预热/缓存与 shutdown 已统一接入进程级 `BackendSession`；一个 UI 只启动一个独占 WorkerHost。
- UI→backend AST 门禁已从 90 条基线收敛为零，临时 allowlist 已删除；contracts/client/backend/PySide workspace 与无 Qt backend wheel 门禁已建立。
- Named Pipe 的 overlapped 读写、父进程清理和 pytest 会话收尾已补齐，PDF 不再在 WorkerHost 内二次启动 localhost/FastAPI 子进程。
- CI 已拆为 contracts/backend/pyside/winui 四类质量门禁；release 单次构建 backend wheel，再以同一 SHA-256 绑定 Classic 与 Next 两个独立 ZIP。
- 本机可执行的 Python 契约、架构、WorkerHost、客户端、PDF 与制品验证均纳入回归；本机缺少固定版本 .NET SDK，C# 构建以及 Win10/Win11、DPI、托盘、热键、真实模型矩阵由 required CI 与发布签核 Runbook 执行。

## 1. 目标

VibeOCR 保持单仓库和单份后端实现，同时交付两套互斥运行的桌面产品：

- PySide6 Classic 只启动并连接自己的 WorkerHost；
- WinUI Next 只启动并连接自己的 WorkerHost；
- 两套前端和两套 WorkerHost 实例不发现、不连接、不接管对方；
- 任一产品运行时，启动另一产品只显示退出提示，不启动第二个 WorkerHost；
- OCR、PDF、二维码、模型、依赖和业务设置只在后端实现；
- UI 只保留展示、输入采集和 Windows/Qt 平台壳层能力；
- 两条发布流水线从同一提交构建同一份后端制品，但分别组合自己的前端。

这里的“一一对应”是**进程与会话的一一对应**，不是复制两份后端代码。

## 2. 非目标

- 不引入 HTTP、本机常驻守护进程或系统服务；
- 不允许一个前端复用另一个前端启动的 WorkerHost；
- 不做 WorkerHost 多客户端；
- 不通过扫描进程名实现互斥；
- 不要求两套 UI 布局、交互和发版节奏完全一致；
- 不在迁移期间发布可切换“直连后端/RPC 后端”的隐藏生产开关。

## 3. 已确认的现状

1. WinUI 已具备随机 Named Pipe、随机 session token、父进程 watchdog、Job Object、版本化 RPC、任务取消和共享内存传输。
2. WorkerHost 已暴露 OCR、PDF、二维码和设置能力，并有 Python/C# 双语言契约测试。
3. PySide UI 仍直接依赖 `services`、`managers`、`workers`，尚不是薄壳。
4. `worker_host.main` serving 模式目前只允许 `winui-dev` profile，仍带有迁移阶段的 WinUI 专属限制。
5. `[project].dependencies` 混合了 PySide、后端、GPU、PDF 和导出依赖；`[dependency-groups]` 只有 `dev`。
6. WinUI 构建当前复制整个 `src/vibeocr` 后删除 UI 目录，属于容易漂移的排除式 staging。
7. `core/models/services/managers/workers` 中仍存在 Qt 类型，不能直接整体认定为后端包。

## 4. 目标架构

```text
PySide6 Classic
├─ PySide Platform Shell
│  ├─ 截图、拖放、剪贴板、托盘、热键
│  ├─ 文件选择、窗口、Qt WebEngine
│  └─ PySide View / ViewModel
├─ Python BackendClient
│  ├─ WorkerHostLauncher
│  ├─ RPC correlation / events / cancellation
│  └─ SharedPayloadClient
└─ 独占 WorkerHost(PID-A, pipe-A, token-A)

WinUI Next
├─ WinUI Platform Shell
│  ├─ 截图、拖放、剪贴板、托盘、热键
│  ├─ 文件选择、窗口、WebView2
│  └─ WinUI View / ViewModel
├─ C# WorkerHostClient
└─ 独占 WorkerHost(PID-B, pipe-B, token-B)

共同源码/制品
├─ Protocol contracts v1+
├─ WorkerHost
├─ Application services
└─ OCR/PDF/QR/domain implementation
```

由于跨产品 Mutex 的约束，A、B 不会同时进入运行态。

## 5. 强制边界

### 5.1 依赖方向

唯一允许的依赖方向：

```text
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

禁止反向依赖，也禁止 UI 跨过 `BackendClient` 直接调用后端实现。

### 5.2 职责表

| 能力 | 前端负责 | 后端负责 |
|---|---|---|
| 截图、拖放、剪贴板 | 获取输入、编码为 payload | 不接触屏幕和剪贴板 |
| OCR | 展示参数、提交任务、展示结果 | 推理、编排、进度、取消、重试策略 |
| 批量识别 | 文件选择、列表呈现 | 队列、并发预算、任务状态、导出 |
| PDF | 文件选择、缩略图和页面交互 | session、渲染、修改、OCR、保存 |
| 二维码 | 输入和预览 | 编码、解码、格式校验 |
| 设置 | UI 偏好及表单 | 业务设置、CPU/GPU、模型和依赖状态 |
| 更新 | 提示、确认、进度显示 | 检查、下载、校验、替换输入准备；最终替换仍由独立 updater 完成 |
| 错误 | 按稳定错误码显示和引导 | 产生稳定 code/detail/retryable，不泄露行为判断给 UI |

### 5.3 DTO 约束

协议 DTO 只能包含：

- JSON 基础类型；
- 稳定 enum/string code；
- 文件路径和内容类型；
- shared-memory descriptor；
- 与 Qt/.NET 无关的几何、文本块、PDF page 和任务状态。

禁止出现 `QPixmap`、`QImage`、Qt Signal、XAML 类型、Python traceback 或 UI 控件状态。

### 5.4 自动化架构守卫

新增 `tests/architecture/`，至少包含：

1. AST import scanner：PySide 前端禁止 import 后端包；
2. WorkerHost import smoke test：未安装 PySide6 时可 import 和 `--self-test`；
3. 后端禁止 import `views/widgets/ui`；
4. C# project-reference gate：Contracts 不引用 Platform/App，Platform 不引用 App；
5. artifact import scan：WinUI 后端制品不存在 PySide6、Qt UI 目录和 Qt-only 模块；
6. allowlist 只用于迁移，迁移每完成一个功能必须减少，禁止新增。

## 6. 启动互斥与一一对应

### 6.1 不扫描进程名

跨产品互斥使用当前登录会话内的 Windows 命名 Mutex：

```text
Local\VibeOCR.Frontend.Exclusive.v1
```

原因：创建 Mutex 是原子的，不受 exe 改名、PID 复用和两个产品同时启动的竞态影响；前端崩溃后由操作系统释放。

### 6.2 保留同产品单实例

建议使用三个标记：

```text
Local\VibeOCR.PySide.SingleInstance.v1
Local\VibeOCR.WinUI.SingleInstance.v1
Local\VibeOCR.Frontend.Exclusive.v1
```

- 同产品第二实例：保持现有语义，把打开文件等参数转发给本产品已有实例后退出；
- 不同产品：不转发参数、不激活对方、不连接对方，只提示“另一套 VibeOCR 正在运行，请退出后重试”。

### 6.3 启动顺序

1. 解析命令行，但不执行迁移、更新和 WorkerHost 启动；
2. 获取本产品 single-instance 标记；若失败，向同产品转发参数并退出；
3. 原子获取 cross-product exclusive Mutex；若失败，显示提示并退出；
4. 检查 runtime、协议和产品 manifest；
5. 生成本实例随机 pipe、256-bit token 和 instance ID；
6. 启动自己的 WorkerHost，传入 `frontend_id`、`parent_pid`、profile 和 token；
7. handshake 校验 protocol、worker version、frontend ID 和 instance ID；
8. WorkerHost ready 后创建主窗口；
9. 前端退出时先 drain/stop WorkerHost，再释放 exclusive Mutex；
10. 前端异常退出时由 Job Object 和 parent watchdog 清理 WorkerHost，Mutex 由 OS 释放。

### 6.4 必测竞态

- PySide 和 WinUI 同时启动，必须恰好一个成功；
- 失败方不得创建 WorkerHost 子进程；
- 同产品第二次启动仍能完成参数转发；
- UI 被强制结束后 Mutex 自动释放，WorkerHost 无孤儿进程；
- WorkerHost 被强制结束只影响自己的 UI，不触发另一产品；
- updater 也必须持有 exclusive Mutex，更新期间禁止启动任一 UI。

## 7. 数据所有权

“进程不互通”不等于所有数据都必须复制。互斥运行允许安全复用只读和业务数据。

| 数据 | 策略 |
|---|---|
| Python runtime、模型 | 共享；下载/替换使用文件锁和原子替换 |
| 后端业务设置 | 共享语义和 schema；由 WorkerHost 独占写入 |
| 输出、历史结果 | 共享或由用户配置统一目录 |
| UI 布局、导航、主题 | 按 `pyside` / `winui` 分开 |
| task registry、PDF session | WorkerHost 内存内独占，不持久共享 |
| 临时文件、共享内存 | 按 frontend ID + instance ID 隔离 |
| 日志、crash marker、startup trace | 按产品和实例隔离 |

建议将 `profile` 从“WinUI 迁移开关”改为路径/产品配置，增加明确的 `frontend_id`：

```text
frontend_id = pyside | winui
profile     = production | dev
```

`frontend_id` 不参与业务能力选择，只用于日志、临时目录、UI 设置路径和诊断。正式 WorkerHost 不再硬编码只接受 `winui-dev`。

## 8. Python 包与依赖策略

### 8.1 结论

可以依靠 `pyproject.toml` 管理两条流水线，但不能把所有东西只塞进 `[dependency-groups]`：

- **各可分发项目的 `[project].dependencies`**：描述运行时真实依赖；
- **`[dependency-groups]`**：描述 lint/test/build 等不随产品分发的环境；
- **锁文件与 artifact manifest**：保证流水线可复现，并绑定前端与后端制品；
- **制品包含白名单**：保证 WinUI 包里没有 PySide UI。

### 8.2 目标 monorepo workspace

迁移完成后的建议布局：

```text
pyproject.toml                    # uv workspace、公共工具配置、dependency groups
packages/
├─ vibeocr-contracts-py/
│  └─ pyproject.toml              # 纯 DTO/schema helper，无 Qt、无重依赖
├─ vibeocr-client-py/
│  └─ pyproject.toml              # Named Pipe/RPC/shared payload 客户端
└─ vibeocr-backend/
   └─ pyproject.toml              # WorkerHost + OCR/PDF/QR 后端运行时依赖
apps/
└─ vibeocr-pyside/
   └─ pyproject.toml              # PySide6/qasync + Python client
src/dotnet/
├─ VibeOCR.Contracts/
├─ VibeOCR.Platform/
└─ VibeOCR.App/
```

不要求第一阶段立即移动所有文件；先通过逻辑边界和架构测试清债，最后再物理拆包。

### 8.3 建议 dependency groups

```toml
[dependency-groups]
lint = ["ruff", "pyright"]
test-contracts = ["pytest", "jsonschema"]
test-backend = ["pytest", "pytest-asyncio"]
test-pyside = ["pytest", "pytest-qt"]
build-common = ["hatchling", "uv"]
build-pyside = ["pyinstaller"]
build-winui = []
```

具体 backend/PySide 运行时库分别进入对应 workspace 项目的 `[project].dependencies`，不放进 build group。

### 8.4 构建原则

发布 job 先构建一次 backend wheel，再扇出两个产品 job：

```text
backend-wheel
├─→ pyside-artifact = PySide shell + Python client + exact backend wheel
└─→ winui-artifact  = WinUI shell + C# client + exact backend wheel
```

两个产品 manifest 至少记录：

- frontend name/version；
- backend version 和 wheel SHA-256；
- protocol major/minor；
- Python runtime/dependency manifest version；
- source commit；
- artifact file list/hash。

WinUI 构建必须从 backend wheel/白名单安装或解包，替换现在“复制整个 `src/vibeocr` 再删除 UI”的方式。

## 9. 实施阶段

### Phase 0：架构冻结与基线

工作项：

1. 新增架构决策文档，替换原“切换后删除 PySide UI”的结论；
2. 生成 PySide 直接后端 import 清单，作为只减不增的临时 allowlist；
3. 建立 `tests/architecture` 和 UI-free import gate；
4. 给协议方法表、C# RpcMethods、Python handler table 增加一致性检查；
5. 固化当前 PySide/WinUI 功能和 E2E 基线。

验收门禁：

- 当前测试全绿；
- 新直接依赖无法进入主分支；
- WorkerHost 在无 PySide6 环境完成 import/self-test；
- 本阶段不改变用户行为。

回退：仅删除新增架构 gate 和文档，不影响产品运行。

### Phase 1：跨产品互斥与独占生命周期

工作项：

1. Python 和 C# 分别实现同名 `FrontendExclusiveLock`；
2. 接入启动顺序，保证 Mutex 成功前绝不启动 WorkerHost；
3. 保留同产品 single-instance activation；
4. PySide WorkerHost 纳入 Job Object/parent watchdog；
5. updater 获取同一 exclusive Mutex；
6. 增加双进程竞态与崩溃清理测试。

验收门禁：

- 两产品并发启动 100 次，始终只有一个 UI 和一个 WorkerHost；
- 失败方提示正确，且零后端子进程；
- 强制结束 UI 后无孤儿 WorkerHost；
- 同产品参数转发无回归。

回退：回退启动锁接入，不改变 RPC 或后端业务代码。

### Phase 2：通用 WorkerHost 与 Python BackendClient

工作项：

1. 移除 `worker_host.main` 对 `winui-dev` 的硬编码限制；
2. 增加并验证 `frontend_id`、instance ID 和 production/dev profile；
3. 基于现有低层 `NamedPipeClient` 实现 Python 高层客户端：
   - 请求关联；
   - typed request/response；
   - event sequence；
   - cancellation；
   - deadline；
   - shared payload ownership；
   - bounded shutdown；
4. 定义与 C# 客户端一致的 `BackendClient` 接口；
5. 添加 Python client ↔ WorkerHost 集成测试和跨语言 golden tests。

验收门禁：

- Python/C# 对所有公开协议方法序列化结果一致；
- 一个 WorkerHost 只接受启动它的 token 和唯一连接；
- Python client 能覆盖 OCR、PDF、二维码、设置、取消和崩溃恢复；
- 无 PySide UI 参与集成测试。

回退：WinUI 继续使用现有 C# 客户端；Python client 尚未接生产 UI。

### Phase 3：PySide 垂直功能迁移

每个功能按“补契约 → client 方法 → ViewModel 接入 → E2E → 删除直接 import”闭环，不做跨功能大爆炸迁移。

建议顺序：

1. 二维码生成/识别；
2. 单图 OCR、结果复制与导出；
3. 批量 OCR、进度、取消和导出；
4. PDF open/render/mutate/OCR/save；
5. 后端设置、CPU/GPU、依赖、预热和缓存；
6. 更新检查/下载的 UI-free 部分；
7. 诊断和 shutdown。

每个切片的验收门禁：

- PySide 与 WinUI 共用相同 RPC 方法和错误码；
- PySide 对应页面不再 import 后端 service/manager/worker；
- 原有 Qt 交互和 E2E 通过；
- allowlist 至少减少一项；
- 不在 release 包保留 legacy direct-call fallback。

回退：按功能切片回退提交；生产包始终只有一个调用路径。

### Phase 4：物理拆包和类型去 Qt 化

工作项：

1. 将纯协议、Python client 和 backend 拆成独立 workspace 项目；
2. 把 `core/base_worker`、toolbar icon、Qt worker 等移到 PySide app；
3. 将 `QPixmap/QImage` 后端返回值改为 bytes/shared payload + DTO；
4. 拆分 `log_service` 为标准 logging 后端和 Qt signal adapter；
5. 拆分 `update_service` 的 UI-free 下载校验与 Qt dialog；
6. 将 PDF session/domain model 与 Qt view model 分开；
7. 清空并删除迁移 allowlist。

验收门禁：

- backend 和 Python client 的 dependency tree 中无 PySide6/qasync；
- PySide app 不能在依赖图上引用 backend 项目，只能引用 client/contracts；
- WorkerHost wheel 可在无 Qt 环境安装和 self-test；
- AST/import-linter gate 零例外。

回退：workspace 拆分以机械移动提交独立进行，可整体回退，不与业务功能改动混合。

### Phase 5：双 CI 与双发布制品

CI 拆为四个质量 job：

1. contracts：schema/golden/Python/C#；
2. backend：UI-free、application、worker_host、集成测试；
3. pyside：Qt 单测、E2E、PyInstaller 制品；
4. winui：.NET、Web assets、E2E、WinUI 制品。

发布 job：

1. 构建并签名 backend wheel；
2. 构建 PySide Classic artifact；
3. 构建 WinUI Next artifact；
4. 各自运行 artifact verifier；
5. 在干净环境执行互斥启动 smoke test；
6. 发布两个独立命名的 ZIP 和 SHA-256。

验收门禁：

- WinUI artifact 不含 PySide6、Qt UI 或 PySide launcher；
- PySide artifact 不含 WinUI executable/runtime；
- 两者 manifest 指向构建时绑定的 exact backend wheel hash；
- 两个 artifact 均不能连接任意预先存在的 WorkerHost；
- 任一制品校验失败都不得发布。

### Phase 6：稳定性和完成签核

工作项：

1. 双产品各自执行真实模型 OCR/PDF/二维码完整回归；
2. 执行启动竞态、UI crash、WorkerHost crash、更新中断和休眠恢复；
3. 验证 Win10/Win11、混合 DPI、托盘、热键和剪贴板；
4. 比较两套 UI 的协议覆盖率，而非强制布局一致；
5. 更新 release checklist 和用户文档。

最终完成条件：

- PySide/WinUI 前端对后端实现零直接依赖；
- 任一时刻最多一套产品、一个 UI、一个专属 WorkerHost；
- 两产品都只通过自己的 client/session 调用自己的 WorkerHost；
- 后端 wheel 单份构建、双产品复用、hash 明确绑定；
- 架构和制品守卫全部进入 required CI；
- 不再存在 `winui-dev only` 的 WorkerHost 生产限制。

## 10. 关键测试清单

| 测试 | 预期 |
|---|---|
| 同时启动 PySide/WinUI | 恰好一方获得 exclusive Mutex |
| 另一产品已运行 | 提示退出；不创建 WorkerHost |
| 同产品二次启动 | 参数转发到本产品已有实例 |
| 使用另一实例 pipe/token | 连接或认证失败 |
| UI crash | WorkerHost 被 Job Object/watchdog 清理 |
| WorkerHost crash | 仅所属 UI 进入恢复状态 |
| backend import，无 PySide6 | 成功 |
| WinUI artifact import scan | 无 Qt/PySide UI |
| PySide AST boundary scan | 无 backend 直接 import |
| 协议方法新增 | schema、golden、Python、C# 任一遗漏即 CI 失败 |
| backend wheel hash 被替换 | 两个 artifact verifier 均失败 |
| updater 运行时启动 UI | UI 因 exclusive Mutex 被阻止 |

## 11. 风险和控制

| 风险 | 控制 |
|---|---|
| 把进程检测写成进程名扫描 | 只使用命名 Mutex，进程扫描不进入设计 |
| PySide 迁移范围过大 | 按二维码→单图→批量→PDF→设置切片 |
| 同一 Python 包使边界继续渗漏 | 先 AST gate，最终 uv workspace 物理拆包 |
| dependency groups 被误当成产品依赖 | 运行时依赖放各项目 `[project].dependencies` |
| WinUI staging 漏删新 UI 文件 | 改为 backend wheel/白名单包含式 staging |
| 共享模型更新损坏 | exclusive Mutex + 文件锁 + 原子替换 + manifest |
| 协议漂移 | schema/golden/Python/C# 四重 required gate |
| 为回退保留两条生产调用路径 | 只允许提交/版本回退，不发布 runtime fallback |

## 12. 建议的提交/PR 边界

1. ADR、架构测试和 allowlist；
2. Python/C# exclusive Mutex 与启动测试；
3. WorkerHost production/frontend identity；
4. Python BackendClient；
5. QR PySide RPC 迁移；
6. 单图 OCR PySide RPC 迁移；
7. 批量 OCR PySide RPC 迁移；
8. PDF PySide RPC 迁移；
9. 设置/依赖/预热/更新拆分；
10. workspace 物理拆包；
11. 双 CI/双 artifact；
12. 稳定性签核和旧直连代码清理。

每个 PR 必须可独立回退；业务迁移、目录移动和发布脚本重写不混在同一个 PR。
