# Progress Log — DUAL_UI_IMPLEMENTATION_PLAN.md 执行

分支：`feat/dual-ui-phase0`（从 `main` 创建）

## 已完成

### Phase 0：架构冻结与基线 ✅
提交：`arch(phase0): freeze dual-frontend boundary with architecture guards`
- ADR：`specs/2026-07-14-dual-frontend-exclusive-workerhost-adr.md`
- UI→backend import 棘轮（allowlist）：`tests/architecture/ui_backend_import_allowlist.txt`，基线 90 处（services=38, models=21, core=17, managers=12, workers=2）
- `tests/architecture/` 四重守卫（26 测试）：
  1. UI→backend import 棘轮（只减不增）
  2. backend→UI 禁止（已知 1 处 update_service.py Qt 对话框债务，Phase 4 清理）
  3. WorkerHost UI-free（AST 扫描 + sys.modules delta）
  4. 协议方法表一致性（schema ↔ C# ↔ Python，21 方法）
- 验证：305 测试全绿；planted-leak 被拒绝（gate 有效）

### Phase 1：跨产品互斥与独占生命周期 ✅
提交：`feat(phase1): cross-product exclusive Mutex for PySide/WinUI mutual exclusion`
- Python：`vibeocr.utils.frontend_exclusive_lock.FrontendExclusiveLock`（ctypes CreateMutexW，Qt-free）
- C#：`VibeOCR.Platform.Windows.FrontendExclusiveLock`（mirrors SingleInstanceService Mutex 模式 + MessageBoxW 提示）
- Mutex 名：`Local\VibeOCR.Frontend.Exclusive.v1`（两侧一致）
- PySide `main.py`：同产品单实例后、后端初始化前获取；失败提示退出
- WinUI `App.xaml.cs`：`IsPrimary` 后、WorkerHost 启动前获取；失败提示退出
- 9 测试（acquire/mutual-exclusion/release/context-manager/non-windows/name-contract）
- 注意：本机无 .NET SDK（仅 runtime 10.0.9），C# 编译由 CI 验证

### Phase 2：通用 WorkerHost + Python BackendClient ✅
提交 1：`feat(phase2): generalize WorkerHost — frontend_id + production profile`
- `worker_host.main`：新增 `--frontend-id`（pyside|winui），移除 `winui-dev` 硬编码限制
- production 与 winui-dev profile 均可（ADR §7）
- 5 新 process-lifecycle 测试

提交 2：`feat(phase2): high-level Python BackendClient for WorkerHost RPC`
- `vibeocr.worker_host.backend_client.BackendClient`：C# WorkerHostClient 的 Python 对等实现
- request correlation、typed call、event 去重、deadline/cancel、shared payload、bounded shutdown
- 10 单元测试（fake connection，无需真实 pipe）

## 待办（Phase 3–6）

| Phase | 范围 | PR 边界（计划 §12） |
|---|---|---|
| 3 | PySide 垂直功能迁移（二维码→单图→批量→PDF→设置/更新） | #5–#9 |
| 4 | 物理拆包与类型去 Qt 化（uv workspace） | #10 |
| 5 | 双 CI 与双发布制品 | #11 |
| 6 | 稳定性与完成签核 | #12 |

## Test Results
| Suite | Count | Status |
|---|---|---|
| architecture | 27 | ✅ |
| worker_host + contracts | 273 | ✅ |
| utils (含 FrontendExclusiveLock) | 9+ | ✅ |
| 总计（安全子集） | 300+ | ✅ |

## 分支提交历史
```
beaf0c3 feat(phase2): high-level Python BackendClient for WorkerHost RPC
d73f886 feat(phase2): generalize WorkerHost — frontend_id + production profile
5a051fb feat(phase1): cross-product exclusive Mutex for PySide/WinUI mutual exclusion
bd1116d arch(phase0): freeze dual-frontend boundary with architecture guards
```

## Notes
- `.NET SDK` 本机不可用，C# 改动由结构/模式核对 + CI 验证。
- `docs/` 被 `.gitignore` 忽略；ADR 放在已跟踪的 `specs/`。
- Phase 0 的 allowlist 是迁移期间的棘轮：每完成一个 PySide 功能切片必须减少。
