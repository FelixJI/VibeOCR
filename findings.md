# Findings & Decisions

## 最终架构事实

- PySide Classic 通过 `vibeocr.client.session` 持有进程级唯一 `SyncBackendClient`；单图、二维码、批量、PDF 共用一个随机 Named Pipe/token 会话。
- `worker_host.main` 的生产 dispatcher 注册所有公开协议方法；Python/C#/schema/golden 的 `pdf.command` 兼容契约已同步。
- PDF session、fitz document、渲染与修改全部留在 WorkerHost。兼容层不再启动第二个 FastAPI/uvicorn 子进程。
- UI 源码对 `services/managers/workers/core` 后端实现的直接 import 已清零，临时 allowlist 文件已删除，架构测试成为永久零例外门禁。
- 纯 pipeline/MinerU/frontend DTO 位于 `vibeocr.contracts`；Qt PDF manager/workers/render 位于 `vibeocr.pyside`；旧路径只保留兼容 re-export。
- backend wheel 从 `config/backend_artifact_include.txt` 的显式清单构建；verifier 同时检查路径和 AST import，拒绝 PySide6/qasync。
- uv workspace 包含 contracts、client、backend、PySide app；运行时依赖与 lint/test/build groups 分离并写入 `uv.lock`。
- release 从同一提交只构建一次 backend wheel；Classic/Next manifest 记录相同 wheel SHA-256、source commit 和 protocol major。

## 关键缺陷与修复

- Named Pipe 句柄使用 `FILE_FLAG_OVERLAPPED`，但旧实现以空 `lpOverlapped` 读写，导致握手后死锁。现每次传输创建 event/OVERLAPPED，并等待 `GetOverlappedResult`。
- 旧 dispatcher 只注册少量 composition handler，typed client 已存在的方法在真实进程中不可用。现由公开方法表完整注册并标注 retryability。
- 共享 WorkerHost 的 stdout/stderr 未持续 drain，重依赖导入日志可能填满管道。同步客户端现用 daemon drain thread 持续消费。
- pytest 在用例通过后会先等待 asyncio executor 的阻塞 pipe thread，普通 atexit 尚未执行。测试 session finish 现在显式关闭共享客户端，PDF manager 39 个用例可在约 2 秒内正常退出。
- `PdfService`/`PdfPageInfo` 仍残留 QPixmap 类型。Qt pixmap 渲染已移到 `pyside/pdf_render.py`，共享模型只持有不透明对象。

## 已验证结果

- 核心契约/架构/WorkerHost/client/release layout：327 passed（中期门禁）。
- PDF session manager：39 passed。
- 真实 WorkerHost：settings snapshot 成功；PDF open/model/load/close 成功；进程级 start/shutdown 成功。
- 最终 backend wheel 已构建并通过 verifier：99 个文件，SHA-256 `64953c07627f9cc5582d79c86db12f2e5d0e8200bed4f44679ccd2d41ab34206`。
- 全量 Python/Qt/WorkerHost 测试：2758 passed、10 skipped；全量 Ruff passed。
- 本机不可执行项：.NET 10.0.301 编译，以及 Win10/Win11、混合 DPI、托盘、热键、真实 GPU 模型矩阵；这些不以推测标记通过，转入 CI/发布签核清单。

## Git 约束

- 工作分支：`codex/dual-ui-completion`。
- 本地 `main` 在任务开始时为 `590f92e`，领先 `gitee/main` 19 个提交。
- 用户要求最终提交全部本任务改动并合并回主分支；不自动 push。
