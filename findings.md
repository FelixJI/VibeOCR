# 调研记录：PDF 文字层与批量识别

## 用户报告

- PDF 添加文字层及批量识别速度仍有较大提升空间。
- 可在必要时调整进程间通信与批处理方式。
- `vibeocr-worker-stderr` 线程在 `sync_client.py:187` 遍历子进程 stderr 时按 GBK 解码 UTF-8 字节失败：`UnicodeDecodeError: 'gbk' codec can't decode byte 0xa8 ...`。
- 随后日志出现：`BackendClient reader: terminal error: invalid state`。

## 待确认

- 子进程启动时 `text/encoding/errors/bufsize` 的配置及 stderr drain 生命周期。
- IPC 是逐页、逐图片还是批量传输，是否存在 Base64/JSON/临时文件复制。
- OCR 后端内部是否已批处理，而上层又按页串行等待。
- 文字层写入是否逐页保存、重复打开/解析 PDF，或触发全量重写。
- `invalid state` 是解码线程崩溃后的次生问题，还是独立状态机竞争。

## 已确认

- `SyncBackendClient._start_async()` 以 `subprocess.Popen(..., text=True)` 启动 WorkerHost，但未显式指定 `encoding/errors`；Windows 中文系统因此采用 GBK 解码 stdout/stderr，而 Python WorkerHost/依赖输出包含 UTF-8 字节，直接导致 drain 线程的 `UnicodeDecodeError`。
- 同一文本包装也被 `_await_ready()` 使用；如果 ready 前 stdout 出现非 GBK 输出，启动路径同样可能崩溃。修复点应放在 `Popen` 的统一流配置，而不是只在 drain 内捕获。
- `_start_output_drains()` 中 drain 线程没有异常保护，任何读取/日志异常都会让管道停止排空，子进程后续可能因管道缓冲区填满而阻塞。
- `SyncBackendClient` 已通过 Windows named pipe 执行 RPC；在确认传输负载之前，没有证据表明需要整体替换 IPC。
- PDF 主流程集中在 `pyside/pdf_session_manager.py`：注释显示现有路径已采用每批并发渲染、`recognize_batch()` 批量识别、`add_text_layer_batch()` 聚合写层，默认传输批为 16。
- `BackendClient.call()` 在超时/取消时会取消 `pending.future`；reader 可能已从 `_pending` 取出同一个对象，随后 `_complete_response()` 无条件 `set_result/set_exception`，会抛 `asyncio.InvalidStateError`（日志文本即 `invalid state`），并使唯一 reader 任务退出、所有在途 RPC 统一失败。这是独立且严重的竞态，应在完成响应前检查 future 状态。
- `BackendClient.recognize_batch()` 名称虽为 batch，实际是对每张图片并发调用 `ocr.recognize`，即 N 个 SHM payload + N 个 RPC；这条 WorkerHost API 没有把整批送入 OCR 引擎。不过 PySide PDF 路径看起来使用另一套 `OCRServiceSubprocess.recognize_batch`，需确认两个前端/组合根的实际走向后再决定是否改协议。
- Named pipe 帧 I/O 使用线程池包装 Win32 overlapped `ReadFile/WriteFile`，并对大于 64 KiB 的部分写入做循环；控制通道本身已有长度帧和共享 payload，不是首要可疑点。
- PySide PDF 一批的真实流水线是严格串行的三个阶段：16 页全部渲染完成 → 整批 OCR 完成 → 整批写层并落盘完成，下一批才开始；阶段内部只有渲染并发，没有跨批渲染/OCR/写层重叠。
- `add_text_layer_batch(save=True)` 每 16 页调用一次 `PdfService.save_incremental()`；后者每次先 `shutil.copy2` 整个不断增长的 PDF 到 `.bak`，再做 append-only incremental save。大文件会产生约 `批次数 × 当前文件大小` 的额外全文件 I/O，是明确的非 OCR 瓶颈。
- OCR 完成后 `_run_ocr()` 又调用普通 `/save`。`save_with_rewrite()` 会遍历所有有 `ocr_text_blocks` 的页，删除并重写刚才已经逐批写入的文字层，再执行整文档 `tobytes(garbage=4, deflate=True)`、关闭、全量写回和重开。该收尾阶段把文字层写入工作做了第二遍，且全量压缩本身也昂贵。
- 逐批写层共享一个子集字体，末尾重写的目标是把所有批次再合并成全文档单一字体；这是文件体积与吞吐的显式权衡，不应在没有选项/测试的情况下直接取消。
- PySide OCR IPC 的 `OCRServiceSubprocess.recognize_batch()` 已实现真正的单消息 RCBG 共享内存批请求，并在 worker 内调用一次 `OCRService.recognize_batch(list)`；128 MiB SHM 下按约 90 MiB 有效预算自动分子批。这里无需整体替换通信方式。
- 工作区已有与本任务无关的用户改动：`src/vibeocr/views/tabs/about_tab.py`、`tests/views/tabs/test_about_tab.py`。后续必须避开并保留。
- `save_incremental` 已有成功持久化和失败回滚测试，但失败测试只模拟 `fitz.Document.save` 在写入前抛错，没有覆盖“已追加部分字节再失败”的回滚边界，也没有断言必须通过全量 `.bak` 复制来实现安全性。
- `save_with_rewrite` 有全文档单一子集字体的行为测试；若新增 OCR 快速收尾路径，应只供“刚刚批量写层且未编辑”的 OCR 编排调用，保留普通用户保存的重写语义。
- 当前没有针对 `SyncBackendClient` 子进程 UTF-8 流配置或 `BackendClient._complete_response()` 取消竞态的直接测试，需要新增。
- 关键架构更正：当前 `MainWindow._on_subprocess_worker_ready()` 给批量图片 Tab 和 PDF Tab 注入的是 `BatchBackendAdapter(SyncBackendClient)`，旧的 `OCRServiceSubprocess` 真批量路径已不再由前端直接使用。因此 PDF 的 `recognize_batch()` 最终确实走 WorkerHost 中 N 个 `ocr.recognize` RPC 的 `gather`，并没有单次引擎 batch；现有 PDF 代码注释与运行时架构已经脱节。
- WorkerHost 内 `OcrServiceAdapter` 只暴露单图 `recognize`，但其底层生产 service 已有真 `recognize_batch`。可以在 v1 协议新增 `ocr.recognize_batch`：客户端一次性建立多份 SHM 引用、handler 一次读入并调用 adapter batch、一次返回结果数组；这会直接恢复 PDF/批量图片的 Paddle 真批处理。
- `PdfBackendAdapter.start_ocr()` / `_PdfOcrBackendBridge` 目前仍是占位实现（识别返回空 placeholder），但 PySide PDF Tab 实际仍由 `PdfSessionManager` 主进程编排，不走该 `pdf.start_ocr`；本次不应把 WinUI 尚未完成的占位路径混进 PySide 优化范围。
- 新增 `ocr.recognize_batch` 必须同步更新四个协议真源/镜像：`contracts/v1/methods.schema.json`、`contracts/v1/golden.json`、Python `method_validation.PUBLIC_METHODS`、C# `RpcMethods.All`；架构测试会校验集合一致。
- `SyncBackendClient.recognize_batch_sync()` 已经期待一次返回结果列表，调用方无需变化；只需将 async `BackendClient.recognize_batch()` 从 N 个 `recognize()` 改为一份 batch RPC，并保持对所有 client-owned SHM 的 finally 释放。
- WorkerHost handler 可以复用单图的 wire 序列化格式，把响应定义为 `{results: [单图响应...]}`；生产 `OcrServiceAdapter` 需把底层 service `recognize_batch(images, options)` 的结果逐个转换为 application `OcrResult`，并保留结果顺序/None 失败槽位的语义。
- PDF 后端的 `SaveRequest` 是内部 Pydantic IPC 模型，HTTP 与 in-process client 共用；可安全增加默认 `rewrite_text_layers=True` 字段，在 OCR 收尾调用显式传 False，同时保持所有现有普通保存调用兼容。
- 当前 PySide `PdfSessionManager` 的实际 PDF 调用也已迁移到 `vibeocr.client.pdf.PdfBackendClient` → WorkerHost `pdf.command` → composition adapter → in-process PDF backend；OCR finalize 标志还必须穿过这一层 command params，不能只修改旧 HTTP client。
- 审计发现 WorkerHost v1 的 pipeline allow-list 仍只有 OCR/表格/公式，但前端可选枚举已有 `PP-StructureV3`、`MinerU`、`PaddleOCR-VL`。迁移到 WorkerHost 后这些管道会在单图/批量方法进入 handler 前被拒绝；批量协议应同步修正该既有契约缺口。

## 关键路径对比（页批 = 16）

- N 页 OCR 控制 RPC：`N` 次单图调用 → `ceil(N/16)` 次真批量调用；100 页即 100 → 7。
- OCR 引擎入口：`N` 次单图 `recognize` → `ceil(N/16)` 次 `recognize_batch`。
- incremental checkpoint 的整文件复制：`ceil(N/16)` 次 → 0 次；只写一个长度 marker 并执行 append。
- OCR 完成后的文字层逐页重写：`N` 页 → 0 页；保留 1 次整文档压缩。因此产物会保留“每批一个字体子集”，文件体积可能略高于全文档单字体重写，但明显降低收尾耗时。
- 渲染/OCR：原先批间完全串行；现在 OCR 当前批时预取下一批，额外内存上限为一批（最多同时持有 32 页 PNG）。

---

# 调研记录：版本升级与 CHANGELOG 归档

- `update_file_version()` 使用 `replace(..., 1)`，每个文件只替换第一个旧版本；因此子项目 `pyproject.toml` 内的内部依赖约束即使文件被处理也会漏掉。
- 主流程只更新根 `pyproject.toml` 和 `src/vibeocr/__init__.py`，完全没有遍历 `[tool.uv.workspace]` 下的 `apps/*`、`packages/*`。
- 当前根版本和根包为 0.4.30，但四个 workspace 项目的版本、内部包精确依赖与包级 `__version__` 仍停留在 0.4.28。
- Git 历史存在 `release: v0.4.29` commit `3843945`，但本地 tag 列表没有 `v0.4.29`；`get_commits_since_last_tag()` 因而从 `v0.4.28` 开始收集，0.4.30 条目重复包含了 0.4.29 的全部内容。
- `v0.4.30` 之后目前没有工作区改动；开始修复前 git 状态干净。
- 修复后自动发现 10 个受控版本文件（根项目 2 个 + 4 个 workspace 项目的 pyproject/init），全部与根版本 0.4.30 对齐；`uv.lock` 中 5 个内部发行包也一致为 0.4.30。
- 用真实仓库调用新边界逻辑：0.4.29 release commit 之后只得到 `fix(ci)`、`build(deps)`、PDF 优化提交以及 0.4.30 release 本身；不再包含 0.4.29 已归档的 preview/WinUI 等提交。
