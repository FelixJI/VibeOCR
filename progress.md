# Progress Log — DUAL_UI_IMPLEMENTATION_PLAN.md 执行

分支策略：每阶段完成后合并到 `main` 并删除特性分支。

## 整体进度

### Phase 0：架构冻结与基线 ✅
- ADR + `tests/architecture/` 四重守卫（26 测试）

### Phase 1：跨产品互斥 Mutex ✅
- Python `FrontendExclusiveLock` + C# `FrontendExclusiveLock`

### Phase 2：通用 WorkerHost + Python BackendClient ✅
- `--frontend-id` + production profile
- `BackendClient` + `SyncBackendClient` 同步桥

### Phase 3：PySide 垂直迁移 🔄

| 切片 | 状态 | allowlist 变化 |
|---|---|---|
| 二维码生成/识别 | ✅ 完整 RPC 迁移 | 90→88 |
| 单图 OCR — 显示格式化器 | ✅ TextBlockProcessor + HTML 表格 → utils | 88→84 |
| 单图 OCR — toolbar_icons | ✅ core/ → ui/ | 84→83 |
| 单图 OCR — 执行路径 | ✅ 完整 RPC 迁移（ocr.recognize DTO 丰富化） | 83→74 |
| 批量 OCR | ⏳ 待续 | |
| PDF | ⏳ 待续（PdfSessionManager 深度耦合） | |
| 设置/更新 | ⏳ 待续（UpdateService Qt/backend 混合） | |

### Phase 4：物理拆包与去 Qt 化 ⏳
- `models.*` 共享数据模型需移到 contracts 包（21 条 allowlist）
- `core.pipelines`/`core.constants` 共享枚举/常量需拆分
- `log_service`/`update_service` Qt/backend 需拆分

### Phase 5–6：双 CI/发布 + 稳定性 ⏳

## Allowlist 轨迹
| 阶段 | 数量 |
|---|---|
| Phase 0 基线 | 90 |
| Phase 3 QR | 88 |
| Phase 3 格式化器 | 84 |
| Phase 3 toolbar_icons | 83 |
| Phase 3 OCR 执行路径 | **74** |

## main 提交历史（最新 10 条）
```
ce31d80 docs: document dual-frontend architecture in README
8558373 merge: Phase 3 single-image OCR execution migrated to RPC
a214943 feat(phase3): migrate single-image OCR execution to RPC
b8e7cd2 refactor(phase3): move toolbar_icons from core/ to ui/ layer
98a6e85 docs: update progress
d39e47a merge: Phase 3 single-OCR slice — move display formatters
ebff010 refactor(phase3): move HTML table utilities to UI utils layer
e69d2d0 refactor(phase3): move TextBlockProcessor to UI utils layer
d8475e7 merge: Phase 3 QR slice
2d6fe38 feat(phase3): migrate PySide QR generate/decode to RPC
```
