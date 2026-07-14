# Progress Log — DUAL_UI_IMPLEMENTATION_PLAN.md 执行

分支策略：每阶段完成后合并到 `main` 并删除特性分支。

## 整体进度

### Phase 0：架构冻结与基线 ✅
- ADR + `tests/architecture/` 四重守卫（26 测试）
- UI→backend import 棘轮基线 90

### Phase 1：跨产品互斥 Mutex ✅
- Python `FrontendExclusiveLock` + C# `FrontendExclusiveLock`
- 共享 `Local\VibeOCR.Frontend.Exclusive.v1`

### Phase 2：通用 WorkerHost + Python BackendClient ✅
- `--frontend-id` + production profile
- `BackendClient`（关联/事件/取消/共享内存）+ `SyncBackendClient` 同步桥

### Phase 3：PySide 垂直迁移 🔄

**二维码生成/识别** ✅（allowlist 90→88）
- `qrcode.generate` 扩展 11 样式选项 + `qrcode.generate_svg`
- `QrcodeTab` → `SyncBackendClient`，删除 2 直接 import
- 31 测试（FakeBackend）+ 5 handler 测试

**单图 OCR — 显示格式化器迁移** ✅（allowlist 88→84）
- `TextBlockProcessor` → `vibeocr.utils.text_layout`（输出排版逻辑）
- HTML 表格工具 → `vibeocr.utils.html_tables`（表格规整化/转换）
- 两处均为纯函数、无 Qt 依赖，按 ADR §5.2 属 UI 层
- 6 个 allowlist 条目删除

**单图 OCR — 执行路径迁移** ⏳（待续）
- 剩余 9 处 `single_recognition_tab.py` + 3 处 `base_tab.py` 导入
- 涉及 `get_ocr_service`/`USE_SUBPROCESS`/`OCRPipeline`/`Constants`
- 需要丰富 `ocr.recognize` 响应 DTO（text_blocks/text_with_scores/content_list/preprocessed_image）
- 高风险切片，需专门设计

## Allowlist 轨迹
| 阶段 | 数量 | services | models | core | managers | workers |
|---|---|---|---|---|---|---|
| Phase 0 基线 | 90 | 38 | 21 | 17 | 12 | 2 |
| Phase 3 QR | 88 | 36 | 21 | 17 | 12 | 2 |
| Phase 3 格式化器 | 84 | 32 | 21 | 17 | 12 | 2 |

## main 提交历史
```
d39e47a merge: Phase 3 single-OCR slice — move display formatters to UI utils
ebff010 refactor(phase3): move HTML table utilities to UI utils layer
e69d2d0 refactor(phase3): move TextBlockProcessor to UI utils layer
d8475e7 merge: Phase 3 QR slice — PySide QR generate/decode migrated to RPC
2d6fe38 feat(phase3): migrate PySide QR generate/decode to RPC (first vertical slice)
71c8be9 merge: dual-frontend Phase 0–2 (...)
beaf0c3 feat(phase2): high-level Python BackendClient for WorkerHost RPC
d73f886 feat(phase2): generalize WorkerHost — frontend_id + production profile
5a051fb feat(phase1): cross-product exclusive Mutex
bd1116d arch(phase0): freeze dual-frontend boundary with architecture guards
```
