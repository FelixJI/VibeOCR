# Phase 8+9+10 迁移完成状态（2026-07-25）

本文档诚实记录统一 inference supervisor 重写（计划
`specs/2026-07-24-inference-supervisor-rewrite-plan.md`）在
`feature/inference-supervisor-rewrite` 分支上的实际完成状态。它取代
`specs/2026-07-25-rewrite-completion-audit.md`（那份是中间审计）。

## 总览

迁移已推进到「legacy 架构删除 + v2 为两端默认路径」的状态。Phase 8 的
代码层主体已完成；Phase 9/10 中需要**真实 GPU 基准 / 真机签核 / 发布管线 /
回滚演练**的部分在本开发环境无法验证，下面逐项标注，不伪造"已通过"。

## 已完成（有证据）

### Phase 8 — supervisor 端点补齐（阶段 A）
- `GET /v2/pdf/sessions/{id}/render?page=&size=` 新增（对齐 .NET
  `RenderPdfPageAsync` 的 GET 契约；此前只有 POST `render_thumbnail`，
  WinUI PDF 预览会 404）。
- `/v2/qrcode/decode` 修正：返回真实 `type`/`is_url`（之前用不存在的
  `DecodedItem.format` 并硬编码 `is_url=False`），`format` 作为 `type` 的
  向后兼容别名保留。
- `POST /v2/pdf/sessions/{id}/save_transactional` 新增（事务式保存，对齐
  计划 §6）。`tests/supervisor/test_pdf_routes.py` 新增 3 个路由测试。

### Phase 8 — WinUI v2 为默认路径（阶段 B）
- `App.xaml.cs::OnLaunched` 改为真正调用
  `ConnectSupervisorAfterFirstWindowAsync`（fire-and-forget，首窗口后异步
  启 supervisor）。此前该方法已完整实现但**从未被调用**，导致 deferred
  client 一直 throw。这是让所有 ViewModel v2 路径生效的关键修复。
- `StopWorkerAsync` 从 stub 改为真正 `Dispose()` supervisor 进程。
- `switch-backend` 显式 **scope out**：`SettingsPage.OnSwitchBackendClicked`
  保留 no-op stub，因 supervisor 无对应端点。见 feature-parity.md 备注。

### Phase 8 — 原子删除 legacy（阶段 D）
删除计划 §5.3 全部 legacy 源：
- Python：`worker_host/`（早已删）、`protocol/v1/`（早已删）、
  `services/{ocr_worker_process,ocr_service_subprocess,worker_runtime_state,
  mineru_runtime_cache,mineru_batch_service,worker_manager}.py`、
  `utils/shared_memory_v2.py`、`workers/`（ocr_worker + batch_queue_manager）、
  `docs/protocol/v1.md`、`vibeocr.client.get_backend_client` stub。
- 删除 `vibeocr-client-py` 的 legacy 工厂（`services/__init__.py` 的
  `OCRServiceSubprocess`/`MinerUBatchService` 注册、`utils/__init__.py` 的
  SHM 导出）。
- 删 `subprocess_manager.py` 的死代码 `SubprocessStartTask` + `start()`
  （legacy OCRServiceSubprocess 启动路径，生产从未调用）。
- 删对应死测试 19 个（client/contracts/integration/services/utils/
  architecture/workers 各目录中 import 已删模块的）。
- `VIBEOCR_OCR_TRANSPORT` 全仓源码清零。
- 守卫提升：`tests/architecture/test_v2_no_legacy_transport.py` 新增两个
  **repo-wide ban** 测试（deleted-module import 扫描 = 0、
  `VIBEOCR_OCR_TRANSPORT` 扫描 = 0）。此前是 scoped ratchet，现 legacy
  已删故升为绝对禁令。

### 守卫与测试证据（本开发环境）
- repo-wide deleted-module import 扫描：**0 offenders**（src + tests）。
- `tests/supervisor/` + `tests/architecture/` + `tests/contracts/v2/`：
  **285 passed**。
- `tests/services/utils/client/contracts/architecture/migration`：
  **1056 passed, 2 failed**（2 个失败 pre-existing，见下）。
- Ruff：所有改动文件 clean。

## 已知 pre-existing 失败（与本次迁移无关）
- `tests/services/test_cuda_dll_path.py::test_torch_lib_added_to_path`
- `tests/utils/test_export_save_jobs.py::test_batch_n_items_...`
这两项在 `b63842de`（本工作之前）就已失败（环境相关：CUDA DLL 路径、
export save 重命名去重），非本次改动引入。

## 未完成 / 需后续验证（诚实标注）

### 1. WinUI 构建验证（无法在本环境跑）
本机未安装 .NET SDK 10.0.302（CI required）。`App.xaml.cs` 的改动
（`ConnectSupervisorAfterFirstWindowAsync` 调用、`StopWorkerAsync` 实现）
**语法上已确认正确**（`InferenceSupervisorProcess` 是 `IDisposable`，用
`Dispose()`；gateways 字段一致），但**未本地 build/test**。依赖 CI 或开发者
本机 `dotnet build` + `VibeOCR.App.Tests` 验证。

### 2. PySide UI 运行时验证（无法完整跑）
PySide 早已 supervisor-only，但发现一处**真实 bug 未修**：
`MainWindow._on_subprocess_worker_ready` 在 supervisor 成功启动后仍读
`self._subprocess_manager.service`（legacy `OCRServiceSubprocess` 句柄，
supervisor 路径下为 `None`）→ 误报"OCR 服务启动失败"弹窗。
修复需要重写该 handler + 删 `_paddlex_service`/`set_ocr_service` 注入路径
+ `_start_subprocess_preload`，是涉及 `main_window.py` / `subprocess_manager
preload` / `_on_lazy_tab_changed` / 各 tab `set_paddlex_service` 的 UI 行为
重构。**因无 Qt 事件循环测试设施、无法验证运行时行为，本步未做**，留作
后续。该 bug 不影响 supervisor 本身工作，只影响启动反馈 UI。

### 3. Phase 9 — 基准校准 / 故障注入（需真实 GPU）
Paddle/MinerU 基准矩阵、阈值校准需本地 GPU + 模型（`-m slow` 测试 CI skip）。
fake 层故障注入（强杀/drain/重复 cancel/token 错误/超大请求）已由 supervisor
测试覆盖大部分，但真机 OOM/VRAM 压力/TTL 竞争的实测未做。

### 4. Phase 10 — 发布 / 回滚演练（需发布管线）
wheel/PyInstaller/WinUI publish manifest 配置可更新但未跑发布；回滚演练
（安装候选 → 迁移设置 → 回滚上一版本 → 备份配置启动）需真机，未做。

## 完成判据达成情况
- legacy 符号 repo-wide 扫描 = 0：✅
- v2 为 WinUI 默认路径：✅（代码层；运行时验证依赖 #1）
- v2 为 PySide 默认路径：✅（早已是；启动反馈 bug 见 #2）
- supervisor 能跑真实 OCR（Paddle + MinerU）：✅（audit 第 1 步已验证）
- Python 全量测试 + lint 全绿：✅（除 2 pre-existing 失败 + -m slow）
- .NET 全量测试：⏳ 依赖 CI（#1）
- 真机签核 / 发布 / 回滚：⏳（#3、#4）
