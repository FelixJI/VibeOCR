# Progress Log — DUAL_UI_IMPLEMENTATION_PLAN.md

## 2026-07-15

- 从本地 `main@590f92e` 创建 `codex/dual-ui-completion`，恢复既有 Phase 0–3 上下文并完成差距审计。
- 补齐 Python typed client：OCR 导出、PDF、settings、批量、取消、资源释放与同步包装。
- 建立 PySide 进程级唯一 BackendSession；单图、二维码、批量、PDF 共享一个 WorkerHost。
- 把 pipeline/MinerU/frontend 元数据迁移到纯 contracts；UI→backend import 从 53→39→8→0，删除 allowlist。
- 为 PDF 新增 `pdf.command` Python/C#/schema/golden 契约；Qt session manager/workers 移入 `vibeocr.pyside`。
- 修复生产 dispatcher 方法漏注册、Named Pipe overlapped I/O 死锁和 WorkerHost stdout/stderr 回压。
- 用 WorkerHost 内 `InProcessPdfBackendClient` 替换嵌套 FastAPI 子进程；真实 PDF open/model/load/close 通过。
- 修复 pytest 结束阶段共享 WorkerHost 未及时关闭；PDF manager 39 passed 且正常退出。
- 建立显式 backend wheel allowlist、构建器、AST verifier、PySide/WinUI artifact binder/verifier 与 product manifest。
- 建立 contracts/backend/pyside/winui CI；release 改为单 backend wheel 扇出 Classic/Next 两个 ZIP。
- 建立四个 uv workspace 项目并重新生成 `uv.lock`；移除 backend PDF 模型/服务中的 Qt 类型。
- 更新 README、正式实施计划状态和 `docs/releases/dual-ui-release-checklist.md`。

## 当前执行点

- 最终 Ruff、全量 pytest、backend wheel/verifier、PySide artifact 绑定冒烟与 Git diff 门禁均已通过。
- 构建 shell 锁已补齐 FastAPI、PyMuPDF、pytest-qt、uvicorn；`uv lock --check` 通过。
- 下一步提交 `codex/dual-ui-completion`，切回 `main` 并 `--no-ff` 合并，随后推送 Gitee。

## 验证记录

| 检查 | 结果 |
|---|---|
| `tests/architecture tests/contracts tests/worker_host tests/client tests/release_layout` | 327 passed |
| `tests/managers/test_pdf_session_manager.py` | 39 passed |
| 真实 WorkerHost settings snapshot | passed |
| 真实 WorkerHost PDF open/model/load/close | passed |
| targeted Ruff（client/contracts/pyside/worker_host/scripts） | passed |
| `ruff check src tests scripts` | passed |
| 全量 pytest | 2758 passed, 10 skipped, 1 warning（ccache 缺失提示） |
| 最终 backend wheel | 99 files；SHA-256 `64953c07627f9cc5582d79c86db12f2e5d0e8200bed4f44679ccd2d41ab34206` |
| PySide artifact binder/verifier smoke | passed |
| uv workspace lock | `uv lock` 成功，新增 4 个 workspace package |
| `uv lock --check` / `git diff --check` | passed |
| .NET 10.0.301 | 本机无 SDK；由 CI required gate 执行 |

## 提交与合并

- 尚未提交。
- 尚未合并。
