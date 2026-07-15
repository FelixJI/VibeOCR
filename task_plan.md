# Task Plan：完成双前端独占 WorkerHost 实施

## Goal

完整执行 `DUAL_UI_IMPLEMENTATION_PLAN.md`，优化已有实现，完成自动化验证，提交全部相关改动并将工作分支合并回 `main`。

## Current Phase

Phase F：最终验证、提交与合并（complete）

## Phases

### Phase A：恢复上下文与差距审计

- [x] 核对正式计划、Git 基线、已有提交与工作区
- [x] 从代码、测试、构建、CI 四个维度重建差距清单
- **Status:** complete

### Phase B：typed client 与共享会话

- [x] 补齐 OCR 导出、PDF、设置、批量、取消和资源释放 typed API
- [x] 建立 PySide 进程级唯一 `BackendSession`
- [x] 单图、二维码、批量与 PDF 复用同一 WorkerHost
- **Status:** complete

### Phase C：PySide 垂直迁移

- [x] 批量 OCR、五种导出、进度与取消走 RPC
- [x] PDF open/load/render/mutate/OCR/save 走 RPC
- [x] 预热、缓存、业务设置与有界 shutdown 接入客户端边界
- [x] UI→backend AST 扫描清零并删除 allowlist
- **Status:** complete

### Phase D：物理边界与去 Qt 化

- [x] 建立 contracts/client/backend/PySide workspace 项目及锁文件
- [x] Qt PDF manager/workers/render helper 移到 `vibeocr.pyside`
- [x] backend wheel 使用显式包含清单并拒绝 PySide6/qasync import
- [x] WorkerHost 不再为 PDF 二次启动 localhost/FastAPI 子进程
- **Status:** complete

### Phase E：CI 与双制品

- [x] 建立 contracts/backend/pyside/winui 四类质量 job
- [x] release 单次构建 backend wheel
- [x] Classic/Next 分别生成 manifest、SHA-256 和 verifier
- [x] 两个前端精确复用同一 backend wheel hash
- **Status:** complete

### Phase F：稳定性、文档、Git

- [x] 完成本机可运行的契约、架构、WorkerHost、客户端、PDF 与构建验证
- [x] 更新 README、正式计划状态与双前端发布签核清单
- [x] 全量 pytest、Ruff、wheel/verifier、Git diff 最终门禁
- [x] 提交工作分支并非快进合并到 `main`
- [x] 确认 `main` 工作区干净
- **Status:** complete

## Decisions

| Decision | Rationale |
|---|---|
| 从本地 `main@590f92e` 创建 `codex/dual-ui-completion` | 本地主分支已包含此前 19 个领先远端的实施提交 |
| PDF 在 WorkerHost 内直接调用 UI-free route/domain 实现 | 保持一个 UI 对一个 WorkerHost，消除嵌套 localhost 子进程 |
| UI import 门禁从临时棘轮改为永久零例外 | 垂直迁移已经完成，不再允许新增技术债 |
| backend wheel 使用包含式清单和 AST import 校验 | 防止排除式 staging 漂移并保证无 Qt 运行时依赖 |
| 真实设备矩阵用 required CI + 发布 Runbook 签核 | 当前机器缺少固定 .NET SDK，也无法替代 Win10/Win11/GPU/DPI 实机 |
| 最终使用普通 `--no-ff` 合并回 `main` | 保留任务边界和审计历史，不改写用户已有提交 |

## Errors Encountered

| Error | Resolution |
|---|---|
| 沙箱不能写 `.git/refs` | 经受控权限创建工作分支；提交/合并同样使用受控 Git |
| pytest 默认 temp/cache ACL 与插件加载失败 | 使用 `C:\tmp` 唯一 basetemp、禁用 cacheprovider，并在受控环境运行 |
| 本机无项目固定的 .NET 10.0.301 SDK | 不降低 SDK；保留 CI required gate，并在发布 Runbook 明确签核 |
| WorkerHost 握手后 RPC 卡死 | 修正 Named Pipe overlapped ReadFile/WriteFile，实现真实 OVERLAPPED event |
| 生产 dispatcher 未注册全部公开方法 | 依据 `PUBLIC_METHODS` 注册所有非控制 handler |
| PDF WorkerHost 内再次启动 HTTP 子进程 | 改为 `InProcessPdfBackendClient`，保留兼容形状但直接调用 UI-free 实现 |
| PDF manager 测试显示通过后 pytest 不退出 | 在 `pytest_sessionfinish` 显式关闭进程级 WorkerHost，避免先等待 executor pipe thread |
| uv workspace 初始表结构/source 映射无效 | 改为 `[tool.uv.workspace]` 并添加 workspace sources，重新生成 `uv.lock` |
