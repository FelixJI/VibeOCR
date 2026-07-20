# 调研记录：PDF 收尾压缩与 RTX 4060 吞吐（2026-07-20）

## 已确认起点

- WorkerHost 批量 OCR 还存在确定的无效开销：每页复制约 8.7MP 预处理图并重新编码 PNG，结果经共享内存回传后又在合同转换处丢弃；5 页返回一度达到 13–17MiB。应让 PDF/WorkerHost 批量路径只回传角度与尺寸，不回传预处理图。
- `text_recognition_batch_size=8` 是文字行识别批次，不代表 8/16 个 PDF 页面并行。300 DPI 下 16 页外批被 48MP 预算切为 5+5+5+1；稳态 5 页子批约 6.2–7.6 秒，单纯增大页批无法把 1.5 秒/页降到 0.5 秒/页。
- PyMuPDF 文档说明与本地 1.28.0 API 均支持 `garbage=3`、`use_objstms=1`、`compression_effort`；`garbage=4` 会额外比较大 stream 去重，是扫描文档 453 秒保存的最可信热点，但现场旧日志无子阶段计时，不能宣称已经精确量到。

- 01:14:07 发起最终保存；01:21:40 PDF 后端返回 HTTP 200，说明文件压缩/落盘成功，后端耗时约 453.5 秒。
- HTTP 保存响应约 15,543,259 字节；WorkerHost 封装后的控制帧 15,543,403 字节，超过 8,388,608 字节上限，导致连接关闭和前端假失败。
- 保存响应过大的直接原因是 `SaveResponse(path, diff=_diff_full(pdf_document))` 序列化 682 页及全部 OCR 文本块；OCR finalize 调用方并不需要该全量 diff。
- 随后的全文档 `get_model` 也可能再次超过 8 MiB，修复不能只扩大帧上限或只裁剪 save 返回。
- 约 1.547 秒/页是 OCR 阶段均摊，不是压缩耗时；是否符合 RTX 4060 需要结合具体模型、批大小、GPU 利用和尾部退化判断。
- `PdfSessionManager._on_ocr_page_done_signal` 已把每页 `text_blocks`、角度和文字层状态增量写入本地 `session.pdf_document`，因此 OCR 完成后再取全文档 `get_model` 不是保持 UI 正确所必需，且对大文档会再次触发控制帧超限。
- `PdfService.save_with_rewrite(..., rewrite_text_layers=False)` 的 OCR 收尾没有逻辑页模型变更；保存接口返回 `_diff_full` 属于过度响应。该路径可安全返回空 `ModelDiff`/最小 modified 状态，普通结构编辑保存仍可保留原契约。
- OCR 每批已经是真批量调用，并在当前批 GPU OCR 时预取下一批 300 DPI 渲染；写层使用批量 HTTP 和共享字体，项目并非逐页串行。但识别阶段单个批次仍是一次 GPU predict，不能通过同时启动多个 GPU OCR 简单线性加速。
- 最终压缩由 `doc.save(tmp, garbage=4, deflate=True, clean=False)` 完成，并在前后复制 `.bak`、关闭、原子替换、重开；当前没有子阶段计时，必须先埋点或建立基准才能区分 453 秒主要耗在垃圾回收/deflate 还是文件复制。
- 日志中的 43 个 16 页渲染批实际被像素预算拆成 170 次 GPU 传输/推理调用：128 次 5 页、42 次 1 页，平均每次 4.01 页。每张 300 DPI 页面约 8.70 MP，48 MP 上限最多容纳 5 页；因此“16 页批”不是一次 16 页模型推理。
- OCR 吞吐中位数约 1.49 秒/页，前 10 批 1.541、中央 10 批 1.488；总体 1.547 被最后两批 2.37 和 5.85 秒/页明显拉高。常态吞吐约 1.5 秒/页，末尾退化是另一个需观测的异常，不应拿总体均值直接当 RTX 4060 稳态能力。
- PDF 渲染当前硬编码 300 DPI；虽然 `PdfGlobalSettings` 有 `render_dpi/max_pixels/adjusted_dpi`，OCR 热路径没有使用用户设置。现场 A4 页约 8.7 MP，低于默认 16 MP 单页上限，故不会自动降 DPI。降低到 200–240 DPI 可减少渲染、PNG、SHM 和解码成本，但是否降低 GPU OCR 核心耗时需基准验证。

---

# 调研记录：运行时阻塞、日志、表格与预加载（2026-07-20）

## 用户报告

- 依赖安装期间及安装结束后重新连接 Python 运行时期间，主窗口疑似同步阻塞。
- 一早晨日志达到约 3 MB，底层日志过密；希望设置中可配置日志级别。
- 需从日志评估 PDF 文字层添加速度和优化空间。
- 表格识别结果同时保留表格和表格内部独立文本框，表格格式也可能失真。
- “立即预加载”似乎没有触发模型加载，需要判断功能价值并修正语义。

## 调研方法

- 对 `C:\Users\felji\Downloads\vibeocr.log` 做级别、logger、消息模板和耗时统计。
- 对四条调用链做静态审计，并以现有/新增测试约束行为。
- 子代理只读并行收集证据，产品代码由主代理统一写入和复核。

## 初步证据

- 表格管线已有较完整回归资产；`tests/core/test_pipeline_table.py` 明确包含“保留未被表格吸收的内部 OCR 文本”测试，这与用户看到的表格内独立文本框高度相关，可能是现有契约而非纯渲染错误。
- 表格 HTML 会经过 `result_view_widget.py` 的规整化再渲染/复制，需区分模型 `pred_html` 本身结构错误、后端回填策略和前端规整化造成的格式变化。
- 当前代码同时存在 `preload_pipelines` 与 `warmup_pipelines` 两条 WorkerHost RPC；“预加载”是否真正推理取决于 UI 调用的是哪条以及 backend cache manager 的实现。
- 用户日志实际为 3.35 MiB、12,780 行，跨度约 53 分 39 秒；其中 DEBUG 11,433 行（89.5%）、INFO 1,156、WARNING 189、ERROR 2。
- 日志膨胀主要来自第三方 HTTP 底层：`httpcore.http11` 7,440 行、`httpcore.connection` 2,108 行、`httpx` 744 行，合计 10,292 行（80.5%）。默认生产日志显然不应采集这些 DEBUG 帧。
- PDF 记录含 43 批、682 页：渲染累计 227.87s、OCR 1055.05s、写层 109.03s、合计 1391.94s。写层平均 0.160s/页，只占三阶段合计约 7.8%；OCR 平均 1.547s/页，是主要耗时。写层本身总体合理，但尾部批次出现 7.79s/16页、10.65s/10页的退化，仍需看保存/后端资源状态。
- `SettingsPageController` 的“立即预加载”已在 `QRunnable` 中逐管道调用 preload，再执行白图 warmup；后台线程设计存在，但用户日志开头明确记录“预加载功能: 禁用”，需要区分自动预加载开关和手动按钮行为。
- `MainWindow._on_settings_install_succeeded()` 当前直接在主线程调用 `DependencyManager.check_dependencies()`；如果该函数执行外部探测/重连，安装结束瞬间会冻结 UI。安装过程本身还需继续检查对话框 worker 的信号与 post-install 逻辑。
- 日志子代理定位到级别泄漏边界：主进程 `services/log_service.py` 已压低 `httpcore/httpx`，但 WorkerHost/PDF 后端使用 `packages/vibeocr-client-py/src/vibeocr/logging_context.py::configure_worker_stderr_logging()` 重置 root 为 DEBUG 且未重设 noisy logger，子进程第三方 DEBUG 因而被转发回主日志。
- PDF 真正显著的尾部热点不是文字层批量写入，而是最终全量压缩：日志显示约 461 秒后失败并伴随 WorkerHost connection closed，约占该次 PDF OCR 端到端时长的 35%。应优先修复压缩的超时/后台状态/可选语义，而非微优化平均 0.16s/页的写层。
- 当前批量 16 页、共享字体和增量保存设计已有合理性能基础；需要保留，并为尾部压缩增加内部阶段计时及可控策略。
- 现场日志证明安装主体在 `Dummy-28` 后台线程执行；但从“所有OCR依赖安装完成”(00:45:31.660) 到主窗口启动 WorkerHost (00:47:03.226) 有约 91.6 秒，期间后台进行了多项导入校验且至少两个 import 超时。即使事件循环未阻塞，这段缺少细粒度进度/阶段反馈也会造成“安装完成后卡住”的感知。
- WorkerHost 进程 00:47:05.999 已 ready，但 `SubprocessManager` 到 00:47:18.222 才报告服务就绪，另有约 12.2 秒初始化间隙；需核对 adapter/服务构造是否在后台任务中以及 UI 状态文本是否覆盖该阶段。
- 现场日志也直接证明“立即预加载”在 00:48:17–00:48:59 成功完成：OCR preload 约 11 秒+warmup 0.85 秒，表格 preload 约 23.7 秒+warmup 6.5 秒。功能真实有效；用户感知问题主要是 DEBUG 才记录关键过程、完成状态不够可验证，以及“自动预加载开关/立即执行”语义可能混淆。
- 表格重复的直接代码原因已确认：`pipeline_table._recognize_table()` 只把“回填进空单元格”的 OCR 标为 consumed；其余位于表格 cell/bbox 内的 OCR 无条件追加为独立 `text` 块，且注释明确采用“宁可重复，不可漏字”。现有测试还把这种重复固化为期望行为。
- 表格格式失真的本地原因也已确认：`utils/html_tables.py::normalize_table_html()` 为去除 inline style 会剥掉单元格所有属性，因此模型输出中的 `rowspan`/`colspan` 被一并丢失；同时按每行标签数量补空格，没有考虑跨行/跨列占位，会进一步改变合并单元格表格结构。
- 更合理的边界是：保留并规范化安全的结构属性 `rowspan/colspan`，只移除 style/class 等展示属性；将几何上落入表格单元格的 OCR 吸收到表格语义中并去重，表格外文字才保留独立块。

## 实施结果

- 安装主体、GPU 检测、安装后环境/版本/直接依赖探测均不再占用 GUI 线程；运行时维护先让旧服务失效，再由安装线程关闭 WorkerHost，成功后异步重检并重新建会话。
- 日志默认 INFO，WorkerHost 不再泄漏 httpcore/httpx DEBUG；设置页可选普通、调试、仅警告与错误。
- 自动预加载原本是未调用的死代码，现于 WorkerHost ready 后触发；手动按钮和自动路径共享同一任务，预加载与预热都成功才显示完整成功。
- 表格内 OCR 不再输出独立文本框：相同内容消费，模型结构漏字则追加回对应格；rowspan/colspan 在清洗、渲染与网格转换中保留。
- 本轮没有直接取消 PDF 最终压缩：它影响产物体积/结构，且现场写层并非瓶颈。461 秒压缩失败属于独立的大文档收尾策略问题，建议以该 PDF 做 A/B 基准后再决定“大文档跳过压缩”或显式设置项。

---

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

# 2026-07-19：Classic 启动缺少 startup_metrics

- 用户现场在 `main.py:56` 导入 `vibeocr.startup_metrics` 时立即失败，属于 PyInstaller PYZ 漏收模块，不是 wheel 下载问题。
- 模块实际位于 `apps/vibeocr-pyside/src/vibeocr/startup_metrics.py`；该 source root 虽已作为 `--paths` 传给 PyInstaller，但共享 namespace `vibeocr` 横跨四个物理 source root。
- 当前只使用一次 `--collect-submodules vibeocr`，需要核对 PyInstaller 是否只从被解析为主 package 的物理根收集，导致 app root 下模块遗漏。
- 现有 `verify_pyside_artifact.py` 只检查 ZIP 布局、manifest 和 wheel 哈希，不执行 `VibeOCR.exe`，因此没有发现冻结入口导入失败。
- 首次重发运行 `29689958012` 已成功完成 PyInstaller 冻结，但真实 EXE smoke 在绑定阶段等待 45 秒后失败。堆栈停在 `subprocess.communicate()`；验证器使用 `capture_output=True`，而应用启动的后台清理子进程可能继承 PIPE，导致主 EXE 已 `os._exit` 后管道仍不关闭。验证日志应重定向到普通文件，避免把继承管道误判为应用未退出。
- 第二次运行 `29690421627` 的普通文件 stderr 揭示更深一层原因：CI 冻结进程的重定向 stdout 使用 cp1252，`main.py:663` 的中文启动提示触发 `UnicodeEncodeError`。启动 smoke 必须显式传入 UTF-8 Python I/O 环境；否则验证环境本身会在进入 Qt 前破坏被测程序。
- 第三次运行 `29690773080` 仍在同一行报告 cp1252，证明 PyInstaller 冻结运行时不受父进程 `PYTHONIOENCODING/PYTHONUTF8` 影响。最终修复必须进入应用入口，在任何中文 `print` 前直接重配 stdout/stderr；只修改 CI 环境不足以保护真实的日志重定向场景。
- `main.py` 在设置环境后静态导入 `vibeocr.startup_metrics`，因此正常 PyInstaller Analysis 本应收集；缺失说明 namespace 解析/分析发生偏差，而非运行时可选功能。
- 四个 workspace root 中只有 contracts 根提供 `vibeocr/__init__.py`（使用 `pkgutil.extend_path`），其余为无 `__init__.py` 的 namespace 分片；PyInstaller 入口同时传四个 `--paths`，但 package 主体首先解析到 contracts 根。
- `PACKAGE_DATA` 只把 contracts/client/backend 三个源码分片作为原始 `.py` 数据合并进 `_internal/vibeocr`，没有加入 app/pyside 分片；即便主进程 PYZ 漏收 app 模块，运行时数据目录也无法兜底找到 `startup_metrics.py`。
- 在本地按 release 的四段 `PYTHONPATH` 调用 PyInstaller `collect_submodules('vibeocr')`，可正确解析四个 `__path__` 分片并包含 `vibeocr.startup_metrics`（共 193 个模块）。因此问题可能发生在实际命令分析顺序、入口选择或最终 PYZ/EXE 组装，而不是 collect helper 本身完全不支持 namespace。
- 本地 `dist/` 只有 0.4.28 主程序构建缓存和 updater 分析文件，没有 0.5.0 Analysis/PYZ 可直接审阅；需要以当前 release 命令本地重建复现。
- 已按正式命令成功本地构建 0.5.0；`warn-VibeOCR.txt` 明确列出 `vibeocr.startup_metrics`、`vibeocr.env_manager`、`vibeocr.pyside/views/managers/utils` 等整个 Classic 分片缺失。
- 生成的 spec 在 `Analysis(...)` 前执行 `hiddenimports += collect_submodules('vibeocr')`；此时 CLI 的 `pathex` 尚未进入 Analysis，导致 namespace 分片收集取决于 spec 进程环境。实际 spec 的 hiddenimports 最终只有第三方显式项，没有任何 `vibeocr.*`。
- `Analysis-00.toc/PYZ-00.toc` 均不含 `startup_metrics`，用户报错已在本地构建证据中完整复现。
- 修复应改为从仓库四个已知 source root 静态枚举 `vibeocr` 模块，并逐项生成 `--hidden-import`，避免依赖 spec 执行期 `sys.path`；同时在 release 中执行冻结入口 smoke。
- 现有主程序已支持 `VIBEOCR_SELF_TEST_SMOKE=1/t3`：创建首窗并在 150ms 后 `flush_startup()` + `os._exit(0)`；配合 `VIBEOCR_STARTUP_TRACE` 可作为真实冻结入口 smoke，无需新增产品 CLI。
- `check_production_dependencies()` 只导入 PyInstaller 包内的 PySide6/PIL，不要求 Paddle/Torch 等嵌入式 AI 环境，因此 t3 smoke 可在干净 CI artifact 上执行。
- 第一版“逐项 hidden import + 四个 pathex”构建仍失败：PyInstaller ModuleGraph 将 `vibeocr` 锁定到含 `__init__.py` 的 contracts 根，除 contracts 外的大多数 hidden import 均报 not found。ModuleGraph 不执行/不采纳运行时 `pkgutil.extend_path` 来扩展静态包路径。
- 下一策略是在构建前把四个物理分片合并到临时 `workspace-src/vibeocr`，并把其父目录作为最高优先 pathex；这样 Analysis 面对一个完整物理包，hidden import 与静态 import 均可确定解析。
- 合并 staging 后真实构建成功：`warn-VibeOCR.txt` 不再含任何缺失 `vibeocr` 模块，`PYZ-00.toc` 明确包含 `vibeocr.startup_metrics`。
- 修复后的原始 Classic ZIP 约 162.4MB（此前错误产物约 70MB）；体积增长主要来自此前整段 Classic GUI/服务模块根本未进入冻结产物，需在 CI 最小依赖环境继续观察最终尺寸。
- 本地 `dist/wheels` 仍是 0.4.37 wheel，无法直接绑定 0.5.0；需重建五 wheel 或下载新 wheelhouse 后再执行最终 ZIP smoke。
- 已在工作区缓存下重建并验证五个 0.5.0 wheel，最终 Classic ZIP 完成绑定后由 verifier 真正启动到 T3，冻结入口 smoke 通过。
- 定向回归共 146 项通过；workspace wheel verification、Ruff、`git diff --check` 全部通过。
- 本地最终 Classic ZIP 为 170,993,151 bytes，SHA256 `4bbab9cdab7fbf76df7ae829940fae2a5ca14f1f1287f54374d24451c39339f5`；该摘要仅用于本地证据，CI 重建后会产生不同但应自洽的摘要。
- 最终运行 `29691245564` 在 6m39s 后全绿：绑定后的首次真实 EXE smoke 与独立 `Verify PySide Classic artifact` 均通过。发布 ZIP 为 172,269,093 bytes，GitHub digest 和随附 `.sha256` 内容一致，均为 `b3c3124812adf17ea31e94daeb7855aa4300b18155ae4f4e40f984d42173ceda`。

---

# 2026-07-19：重新打包 0.5.0

- Release workflow 支持 tag push 与手动 dispatch，默认只打 Classic。
- workflow 的版本解析直接读取 `GITHUB_REF_NAME` 并要求 `vX.Y.Z`；在 `main` 上手动 dispatch 会因 ref 名不是版本号而失败。
- `v0.5.0` 现有 tag 指向修复前提交，因此直接 rerun 旧任务无法带入更新器入口修复。
- 当前成功 Release run 为 `29675075394`，tag 指向 `2120617`；Classic zip 当前摘要为 `1a5f274e...`，发布时间为 2026-07-19 13:41（北京时间）。
- 工作流使用 `softprops/action-gh-release@v3` 上传同名资产；同一 tag 的上一轮修复重推已成功覆盖现有 0.5.0 Release，证明仓库支持该恢复路径。
- 0.5.0 CHANGELOG 已补充 Classic 更新重启修复，重打包上传时 Release 正文会同步包含该说明。
- 新 Release run `29688528417` 在提交 `70420ac` 上成功完成，Windows job 6m03s；Classic 构建、结构校验、上传与 CNB 镜像均成功。
- 新 Classic ZIP 上传时间为 2026-07-19T13:21:08Z，GitHub 资产摘要与 `.sha256` 文件内容一致：`c94c0193109d85d78411786cb0701a27716cbda2ac323fef3b889213bbaaf569`。
- 远端 `main` 与 `v0.5.0` 均已确认指向 `70420ac`；Release 正文包含本次 update 修复说明。

---

# 调研记录：版本升级与 CHANGELOG 归档

- `update_file_version()` 使用 `replace(..., 1)`，每个文件只替换第一个旧版本；因此子项目 `pyproject.toml` 内的内部依赖约束即使文件被处理也会漏掉。
- 主流程只更新根 `pyproject.toml` 和 `src/vibeocr/__init__.py`，完全没有遍历 `[tool.uv.workspace]` 下的 `apps/*`、`packages/*`。
- 当前根版本和根包为 0.4.30，但四个 workspace 项目的版本、内部包精确依赖与包级 `__version__` 仍停留在 0.4.28。
- Git 历史存在 `release: v0.4.29` commit `3843945`，但本地 tag 列表没有 `v0.4.29`；`get_commits_since_last_tag()` 因而从 `v0.4.28` 开始收集，0.4.30 条目重复包含了 0.4.29 的全部内容。
- `v0.4.30` 之后目前没有工作区改动；开始修复前 git 状态干净。
- 修复后自动发现 10 个受控版本文件（根项目 2 个 + 4 个 workspace 项目的 pyproject/init），全部与根版本 0.4.30 对齐；`uv.lock` 中 5 个内部发行包也一致为 0.4.30。
- 用真实仓库调用新边界逻辑：0.4.29 release commit 之后只得到 `fix(ci)`、`build(deps)`、PDF 优化提交以及 0.4.30 release 本身；不再包含 0.4.29 已归档的 preview/WinUI 等提交。

---

# 调研记录：PySide6 架构与运行治理审计

## 初始事实

- 仓库是包含 `apps/`、`packages/`、`src/`、`contracts/`、`requirements/` 的多项目工作区，PySide6 审计需同时覆盖应用层与共享包，不能只看 `src/vibeocr/views`。
- 审计开始时 `git status --short` 无产品代码改动；命令仅提示无法读取用户级 Git ignore，不影响仓库状态判断。
- 历史记录显示项目近期已重构 WorkerHost 真批量 OCR、PDF 流水线和 UTF-8 子进程日志，但本次仍需基于当前代码重新核实 UI 接线、缓存、依赖和超时的全局一致性。

## 架构初筛

- `apps/vibeocr-pyside` 目前基本是发布/工作区壳，真实 PySide6 产品代码仍集中在根包 `src/vibeocr`；UI 相关代码横跨 `main.py`、`views/`、`widgets/`、`managers/`、`workers/`、`pyside/`，业务边界同时延伸到 `application/`、`client/`、`worker_host/` 和 `services/`。
- 主入口 `src/vibeocr/main.py` 创建 `QApplication` 后使用 qasync 统一 Qt/asyncio 事件循环；同时仓库仍大量使用 `QThread`、`QRunnable/QThreadPool`、子进程和 WorkerHost asyncio，属于四种并发模型并存，必须检查它们的所有权和关闭顺序。
- 现有测试资产较丰富，至少覆盖主窗口后端切换/待处理配置、单图/PDF/批量/二维码 Tab、Qt async、批量队列与取消、WorkerHost 生命周期/协议、pipeline cache 和模型预加载；但测试“存在”不等同于 UI 全接线，需要逐项映射控件到用例。
- 依赖已拆成 `vibeocr-pyside`、`vibeocr-backend`、client/contracts 等 workspace 包，但根 `pyproject.toml` 仍同时声明 PySide6 与完整 GPU/Paddle/Torch 后端栈；发布壳是否真正实现前后端依赖隔离需要进一步核对构建入口与锁文件组。
- 锁文件把 PySide6 6.11.1、qasync 0.28.0、Paddle GPU、PaddleOCR、Torch CUDA、ONNX Runtime 等集中锁定，并对 Torch/Paddle CUDA ABI 做了显式来源约束；版本可复现性较强，但 GPU 运行时耦合较重且升级存在人工同步点。

## 入口与 composition root

- 启动顺序总体合理：`QApplication` → 单实例/跨前端互斥 → 配置/OCRPreferences → qasync loop → `MainWindow` → 延迟更新检查与 Worker 初始化；首窗前不主动启动 WorkerHost，并用 splash + Tab 懒加载改善冷启动感知。
- `MainWindow` 是事实上的 UI composition root，但职责很重：它同时管理依赖检测、后端进程、预加载、所有 Tab 的服务注入、设置、截图、托盘、布局和退出协调。后端注入集中在 `_on_subprocess_worker_ready()`，单图/批量/PDF 共用同一个 `BatchBackendAdapter(get_backend_client())`，避免重复 WorkerHost，这条主接线是完整的。
- 懒加载 Tab 会错过 Worker-ready 信号，因此代码专门缓存 `_paddlex_service/_mineru_batch_service` 并在构造后补注入；这是正确的生命周期补偿点，批量/PDF 延迟构造后都能获得同一个适配器。
- 后端启动在 `_start_subprocess_worker()` 中同步调用 `get_backend_client()`；该方法会在调用线程等待 WorkerHost ready/握手，最坏约 40 秒，因此明确占用 GUI 线程。虽然 `SubprocessManager` 具备后台启动任务，当前主路径绕过了其异步启动能力。
- 退出路径有集中协调器和有界等待，但仍存在顺序风险：`_closing` 直到关闭流程后段才置 `True`，在此之前已关闭 PDF/WorkerHost；若依赖检测或安装完成信号同期到达，可能触发重新启动。`os._exit(0)` 也会绕过 Python 常规清理/日志 flush，必须确认文件日志在此之前同步落盘或显式 flush。
- `aboutToQuit` 对安装线程逐个 `wait(3000)`，主窗口关闭又有 `ShutdownCoordinator.coordinate(5000)`；最坏退出等待可能叠加，当前超时属于分散魔数而非统一 shutdown budget。

## UI 接线初筛

- 单图、批量、PDF、二维码、设置、更新/安装对话框和编辑器的大部分可见按钮都有显式 `connect`；主窗口复制按钮则通过独立 `ClipboardController` 接线，整体不是“只有界面没有槽”的空壳。
- `qrcode_tab.py` 的识别子页仍留有“Task 5 填充真实内容，先占位”的注释，但实际选择/粘贴/识别/清空/复制 handler 和测试均已落地；这是陈旧注释，不是空功能。
- `BaseRecognitionTab.cancel_recognition()` 默认只记录“取消功能未实现”，但单图界面没有暴露取消按钮，批量/PDF 均各自实现了取消；因此没有直接的“可点击假取消”，真正缺陷在批量/PDF 的取消完成语义与线程等待。
- 搜索到的 `return None/[]`、`pass` 多数位于防御性分支、绘制/缓存 miss 或异常清理，不能据文本搜索直接判缺陷；后续只把可由用户操作到达且缺少状态/结果的分支列为问题。

## 单图与批量功能核查

- 单图主链正确且 UI 线程安全意识较好：所有入口汇入 `run_ocr/_dispatch_recognize`，重入有 `_is_processing` 守卫，阻塞 RPC 通过 `asyncio.to_thread` 执行，完成/失败回到 qasync loop 更新 Qt，关闭时取消 task 并阻止写入已销毁 WebView；相关异步响应性、错误与关闭测试存在。
- 单图仍有两处体验/性能空间：大文档 `Path.read_bytes()` 和大图 PNG 编码发生在 GUI 线程，极端文件可能造成可见卡顿/内存峰值；`_call_backend_recognize()` 对所有异常无差别重启 WorkerHost 再重试，会把参数/格式等确定性错误也执行两遍，应该只对明确的传输/进程死亡错误重试。
- **高优先级：批量取消的 UI 生命周期不正确。** `_on_cancel()` 只设置协作取消标志，随后立即 `_reset_ui()`、启用“开始”并把 `self._worker=None`，但当前 16 张的阻塞 `recognize_batch` 仍可能运行。用户可立即启动第二批，旧线程也仍会继续发信号；无 parent 的运行中 `QThread` 失去最后 Python 引用还存在被销毁风险。
- **高优先级：批量错误信号语义与控制流冲突。** worker 遇到单批异常会发 `error`，明确继续下一批；Tab 的 `_on_error()` 却立即重置 UI/清空 worker 引用。结果是“后台继续、前台显示空闲”，可造成重入和结果串批。
- **中优先级：取消状态被伪装成完成。** worker 无论是否在批边界取消，最终都发 `progress(total,total,"完成")` 与普通 `finished(results)`；界面无法区分成功完成、部分取消与部分失败。
- `MainWindow.closeEvent()` 没有调用批量 Tab 的 `cancel()+wait()`/`shutdown()`，而是较早关闭共享后端。批量线程若仍在 RPC，退出顺序与资源所有权不完整；现有取消测试只验证 `cancel()` 在 100ms 内返回，worker 测试也直接同步调用 `run()`，没有覆盖真实 QThread 取消→等待→重启/关闭的生命周期。
- 批量真批处理本身是合理优化：16 个文件一次 `recognize_batch`，每批返回后逐文件流式更新，显著减少 RPC/SHM 固定开销；问题在于批大小固定且只按文件数量，不按解码后像素、压缩文件字节或可用显存动态约束，16 个超大图仍可能形成高峰值。
- `BaseOcrTab.set_ocr_service()` 默认不改变按钮状态；批量 `_on_start()` 又会自行构造 shared backend adapter，因此服务未就绪时按钮并未被硬禁用，点击可能在 GUI 线程触发 WorkerHost 懒启动/失败。建议把 readiness 做成显式 UI 状态而不是依赖“通常已注入”。

## 二维码功能判定

- “识别子页先占位”的注释已经过时：测试和 WorkerHost handler 证据表明生成 PNG/SVG、图片解码、URL 打开、单项/全部复制、空结果提示、拖放与粘贴均有真实 typed RPC 实现和行为测试。应删除陈旧注释以免后续审计/维护误判。
- **中高优先级：二维码 RPC 全部在 GUI 线程同步执行。** 300ms 防抖后的实时预览直接调用 `generate_qrcode_sync`，识别按钮直接做 QPixmap→PIL→PNG 再 `decode_qrcode_sync`，并用 `QApplication.processEvents()` 人工刷新一次。首次调用还可能懒启动 WorkerHost；大图转换、进程启动或 RPC 超时时界面都会冻结。应复用全局 async runner/`to_thread`，并用 generation token 丢弃过时的预览结果。
- 二维码接线和错误展示完整，数据写入 rich text 前也做了转义；PNG 生成与 decode 只对 `SyncBackendError` 重启一次，比单图的“所有异常均重启”更合理。SVG 保存路径没有相同的重启策略，属于小型一致性缺口。

## PDF 测试资产初筛

- PDF Tab 测试不仅验证控件存在，还覆盖文字层选择/覆盖决策、状态网格、进度/统计、选择保持、编辑后保存、字体嵌入/ToUnicode 和实际 PDF 往返，功能正确性证据明显强于普通 UI 冒烟测试；仍需补查真实 worker 生命周期、并发冲突与取消。

## PDF 接线与异步生命周期

- PDF 的按钮→SessionManager→worker→signal→UI 主链基本完整：打开、保存、另存、批量导出、旋转/重排/删插页、摆正、OCR 写层、删除/预览文字层都有真实后端调用；批量打开、按需缩略图、结构变更和 OCR 采用后台线程，且 model diff、状态网格、错误汇总与缩略图失效有集中回传。
- PDF OCR/摆正的批处理设计合理：16 页为一批、4 个渲染线程复用连接、渲染→真批量 OCR→串行写 PDF 三阶段流水，带 task generation 丢弃旧任务迟到信号；QThread 引用延迟到 `finished` 后释放，修复了典型“运行中线程被 GC”风险。
- **高优先级：切换文件时“保存后切换”接线错误。** `_on_file_selected()` 用户选择 Save 后调用异步 `_on_save()`，但不等待 `save_done` 就立即 `switch_session()`。保存完成回调因当前 active file 已变化而 early-return，可能导致按钮持续禁用/进度条不消失，也无法保证切换语义。应保存 pending target，在 `save_done` 成功后再切换；失败则留在原文件。
- **中优先级：OCR 前“先保存”没有 continuation。** 修改态下用户选择 Save 后启动异步保存，随即检查 `session.is_modified`（此刻通常仍为 True）并退出，因此不会在保存成功后继续 OCR；文案暗示会“先保存再识别”，实际需要用户再次点击。
- **高优先级：通用 mutate 的并发策略会阻塞 GUI 且可能静默丢操作。** 每次 `_start_mutate()` 都先对旧 worker `cancel()+wait(5000)`；旋转、重排等入口没有统一 busy gate/按钮禁用，连续点击会在 GUI 线程最多冻结 5 秒，并把上一操作取消。若 5 秒后仍未结束，代码忽略 `wait()` 返回值、清引用并启动共享会话的新变更，存在并发写风险。
- PDF 的取消同样在 GUI 线程调用 `wait(5000)`；即使最终能靠 worker 的迟到完成信号复位界面，点击“取消”本身可能冻结数秒，而且完成信号没有独立 canceled 语义，容易把部分完成显示成普通完成。
- Deskew manager 已正确把内部 `session_id` 翻译为 `file_path` 再发 UI；PdfTab handler 参数仍命名为 `session_id` 但实际值是路径，属于易误导的类型/命名债，不是当前功能错误。
- 所有请求页已由 sidecar 落盘时，manager 会显式发 `ocr_done` 复位 UI，这个曾经容易卡死的提前返回分支已正确补齐。

## 缓存与依赖管理初筛

- 依赖检测缓存是真实生效的，不是展示字段：`.vibeocr/cache.json` 有 schema version、machine_id、原子替换和 7 天 TTL；有效期内的已安装项跳过复检，缺失项每次复核，过期后 true 项也重验。`pipeline_status` 已收敛到同一 SSOT，测试覆盖版本/机器不匹配、损坏文件、空依赖和 TTL 过期。
- `PipelineCacheManager` 管理的是后端进程链路中已实例化的管道，不是磁盘模型文件：记录 last-used，对重模型实施 TTL/LRU 容量回收，显式 release 后调用 Paddle CUDA `empty_cache()`；最大重模型数可按显存估算。机制本身合理，但 UI 是否真实到达它取决于 WorkerHost RPC 接线。
- 设置中的预加载开关、管道列表、TTL、释放重模型/全部模型都有 UI handler；配置已从历史 machine cache 字段迁到 `app_settings.json`。既有生命周期集成测试针对旧 `OCRServiceSubprocess`，没有覆盖当前 shared WorkerHost 适配器。
- 当前存在三个名为“缓存”的不同概念（依赖检测结果、内存管道实例、模型下载磁盘文件），设置页如果只写“清缓存”会让用户误以为能释放显存或删除模型；应按作用域分别命名并展示大小/命中/最近使用。

## 管道缓存生产链核查

- `OCRService.get_or_create_pipeline()` 的生产路径确实会 touch last-used，并在加载新重管道前执行显存分档容量淘汰；因此“最大并存重管道数/LRU”有实际效果。
- **高优先级：当前 PySide6 的 TTL 与主动释放接线实际是空实现。** `MainWindow` 注入的是 `BatchBackendAdapter(SyncBackendClient)`；该适配器的 `release_pipelines()` 固定返回 `[]`，`set_pipeline_ttl()` 仅返回 `ttl_seconds >= 0`，没有任何 WorkerHost RPC。设置页因此会把 TTL 更新显示为成功，把释放操作显示为“没有需要释放的管道”，但后端状态未改变。WorkerHost 的公开方法表也没有 pipeline cache mutate/status RPC。
- **高优先级：即使只看 WorkerHost 内部，TTL 自动回收仍不生效。** `cache_manager.evict_idle()` 仅存在于 WorkerHost 下面再次启动的旧 `workers/ocr_worker.py` 主循环；但 PySide6 保存的 TTL 未穿透到这个内部 worker，启动快照只用于 backend 选择。旧 worker 会保留默认 300 秒，且 UI 后续修改不会同步。因此当前只有容量淘汰可靠，TTL 配置与主动释放不可靠。
- 即使复用“每次消息后 evict”旧模式也不科学：真正空闲时没有消息便不会回收，只会在下一次请求附近才触发，既晚释放显存又可能把刚要使用的模型先卸载。应在 WorkerHost 内设置低频定时 sweep（如 `min(TTL/4, 30s)`，有下限），并在任务 in-flight 时跳过当前管道。
- 配置层声明 `TTL=0` 表示禁用，但设置页恢复值时强制 `max(1, ttl//60)`，UI 无法表达 0；需要统一为明确的“自动回收开关 + 分钟数”，并在 WorkerHost 返回当前生效策略供诊断页显示。
- 目前缓存管理缺少可观测性：UI 只能看到“释放了哪些”，看不到当前常驻管道、last-used、估算显存、TTL 下一次回收时间和预加载/命中来源，难以判断缓存是否带来收益。建议增加只读 `settings.cache_status` RPC，而不是依赖日志猜测。
- 预加载本身有实际效果，因为适配器通过一次 100×100 白图识别触发模型创建；但启动预加载任务随后又调用 `warmup_pipelines()`，而该方法同样再次执行白图识别。手动预加载也先 `preload_pipeline()`（已经识别一次），再显式 `service.recognize()`，因此每个管道重复跑两次识别。可合并为单一 `preload_and_warmup` RPC，并返回 loaded/downloaded/warmed/duration 等真实状态。
- `.vibeocr/cache.json` 的 `pipeline_success` 只表示“此机器曾跑通”，用于首用提示；它不验证 Paddle/ModelScope/HuggingFace 的磁盘模型文件是否仍存在，也不记录目录大小。设置页的依赖缓存清理同样不是删除模型文件。三类缓存应在命名和诊断页中明确分开。
- 设置页“刷新缓存”的日志写成“依赖缓存 + 模型缓存”，“清除所有缓存”的对话框也容易扩大用户预期；实际 `refresh_cache()/clear_cache()` 只重建/删除 `.vibeocr/cache.json`，不会刷新或删除任何磁盘模型。建议改名为“重新检测环境/清除检测记录”，并另设带目录、大小和二次确认的模型文件管理入口。

## 超时治理审计

- `core/constants.py` 已按 OCR、预加载、Worker、IPC、MinerU、Qt 毫秒等待做了较完整的语义常量，这是正确方向；但当前主 WorkerHost client 没有消费多数常量，文件内“所有超时收敛到此处”的注释与运行代码不符。
- **最高优先级：RPC 内外层截止时间冲突。** `BackendClient` 默认 deadline 是 30 秒，`recognize()`、`recognize_batch()` 以及多数 PDF typed helper 调用 `call()` 时没有传 timeout。`SyncBackendClient.recognize_sync(timeout=300)`、`recognize_batch_sync(timeout=1800)`、`save_pdf_sync(timeout=300)` 等只延长外层 `Future.result()` 等待，无法覆盖内层 30 秒 envelope/`asyncio.wait_for`；实际任务仍会在约 30 秒被取消。首次模型下载、复杂结构识别、大批量和大型 PDF 最容易误超时。
- `pdf.command`、`pdf.start_ocr` 与依赖安装能把 timeout 传到 envelope，属于正确范式；但 `render_pdf_page_sync(timeout=120)` 也没有把 120 秒传给 async typed helper，仍是内层 30 秒。需要让所有 typed API 显式接收同一个 deadline/budget 并端到端传递，禁止“外层更长、内层更短”。
- 启动、关闭和取消仍有散落魔数：WorkerHost ready 30+10 秒、client close 5 秒、loop thread join 5 秒、PDF 多处 GUI `wait(5000)`、主窗口 coordinator 5 秒、aboutToQuit 安装线程逐个 3 秒。它们会串行叠加，且没有共享 shutdown budget；关闭 UI 最坏可能卡住十余秒后再被 `os._exit(0)` 强制结束。
- 科学规划应区分 connect、queue、execution、stall/heartbeat、cancel grace、shutdown total 六类预算：根据模型是否已载入、页数、像素/字节和管道类型计算 execution；只生成一次绝对 deadline 并沿 RPC 传播；进度事件刷新 stall timer 但不无限延长 total；取消必须有 ACK 和短 grace，超时后再隔离/重启 worker；shutdown 从一个总预算倒推各阶段剩余时间。

## 定向验证

- 定向运行单图、二维码、PDF、批量、pipeline cache、SubprocessManager 与设置预加载测试，共 225 项全部通过（9.39 秒）。这证明现有 happy path 与大量 UI 状态分支有回归保护，但不否定上述真实线程生命周期、空 RPC 接线和分层 timeout 缺陷：pipeline lifecycle 集成测试仍直接覆盖旧 `OCRServiceSubprocess`，没有覆盖 MainWindow 实际注入的 `BatchBackendAdapter`；也没有测试 WorkerHost 缓存状态或 envelope deadline。
- pytest 退出后打印一次 Windows Qt COM `0x8001010d` fatal exception 栈，但进程退出码为 0 且 225 项均已完成；应作为测试环境 teardown 噪声单独跟踪，不计为产品功能失败。
- 另行运行 UI→backend import 边界、`BatchBackendAdapter`、machine cache 与日志服务测试 36 项，全部通过；合计本次定向验证 261 项通过。

## 优化决策与实施顺序

1. **P0 正确性止血**：统一 typed client 的绝对 deadline 传播；为 pipeline cache 增加真实 `status/set_ttl/release/preload` RPC；修复批量 QThread 的 cancel/error/finished 状态机并在 MainWindow 退出时 drain。三项都应先补生产路径测试再改 UI 文案。
2. **P1 交互与生命周期**：PDF 保存成功后再切换/继续 OCR，mutate 改为单一串行队列或明确 busy gate，所有取消不在 GUI 线程 `wait()`；WorkerHost 启动、二维码生成/识别和单图大文件读取统一下沉到 async runner。
3. **P1 日志与诊断**：应用最早期初始化文件日志，WorkerHost 输出结构化 JSONL 并保留 severity/logger/exception，加入 request/task/session/pipeline/page/batch/duration；状态栏改用显式业务信号。
4. **P2 性能与工程边界**：批大小从固定 16 改为文件数 + 总字节/像素/显存预算；暴露缓存命中与常驻模型指标后再调 TTL；完成 `vibeocr-pyside` 可独立安装/启动与 wheel smoke test，减轻开发环境的全栈依赖。

验收指标建议：GUI 主线程单次阻塞 P95 < 50ms；取消按钮 100ms 内恢复为“正在取消”且禁止重入；所有长任务只有一个端到端 deadline；首次/缓存命中耗时、batch throughput、峰值内存/显存、关闭总时长均可从结构化日志统计；缓存设置修改后可由 `cache_status` 读回验证。

## 依赖与工作区拆分判定

- 依赖版本治理较强：Python 锁在 3.13，uv.lock 集中锁定，Torch/Paddle CUDA 12.6 来源和 DLL 复用关系有明确注释；发布 CI 另用带 hash 的 `requirements/build-shell.lock`，避免拉取数 GB 后端依赖，壳构建可复现性和成本控制合理。
- **workspace 拆包目前主要是清单/迁移脚手架，不是实际代码隔离。** `apps/vibeocr-pyside` 只打包 `vibeocr_pyside` 分发标记，真实入口和 UI 仍在根 `vibeocr`；`packages/vibeocr-backend/client/contracts` 的 wheel 配置同样指向各自 marker 包，而生产代码仍在根 `src/vibeocr`（后端 wheel另有自定义构建脚本）。因此不能仅凭 workspace pyproject 认为前端已不依赖后端实现。
- 根项目 `pyproject.toml` 仍无条件同时声明 PySide6、Paddle GPU、Torch、MinerU、FastAPI 等全栈依赖；普通 `uv sync` 的开发环境仍是重量级单体。真正的发布壳通过 build-shell lock 规避了这一点，所以“发布依赖优化有效、开发/包边界隔离未完成”。
- `vibeocr-pyside` 清单只依赖 client/contracts/PySide6/qasync，却没有可执行脚本也不包含真实 `vibeocr.main`，单独安装该 workspace 包无法启动当前应用。若它被当作可发布产品包，这是功能缺口；若仅作迁移标记，应在文档/包名中明确，避免误用。
- 建议以“可独立安装并运行”为拆包完成标准：把 Qt composition/视图迁入 pyside 包、UI-free 实现迁入 backend 包、共享 DTO/typed client 迁入对应包，并用 import-boundary + wheel smoke test 验证，而不是继续维护重复依赖清单。

## 日志结构审计

- 主进程日志有基本生产能力：统一 root logger、UTF-8、10MB×3 轮转、7 天旧文件清理、第三方降噪；文件包含时间/级别/logger 名。对单机桌面应用足够作为底座。
- **高优先级：当前 WorkerHost 没有初始化 logging。** `worker_host/main.py` 没有 `basicConfig/dictConfig`，因此 INFO/DEBUG 通常丢失，WARNING+ 走 Python lastResort 到 stderr；父进程 `sync_client` 又把 stdout 一律记 DEBUG、stderr 一律记 WARNING，原始级别、logger 名和异常结构全部丢失。近期新增的通用 `SubprocessLogForwarder` 并未用于当前 WorkerHost。
- WorkerHost 原始 stdout/stderr 被逐行原样写入主日志，未采用 `SubprocessLogForwarder` 的“结构化级别还原 + 裸 print 折叠/防文档内容泄漏”策略；Paddle/模型库如果打印用户文本或大段调试内容，既可能泄漏也会污染日志。
- 主日志缺少结构化上下文：没有 session/request/task/page/batch/pipeline/backend/duration 等统一字段，排查同一时刻的单图、批量和 PDF 任务只能靠中文自由文本与手写前缀；多个模块同时存在 `[MainWindow]`、`[OCR]`、logger name 的重复来源标记。
- `QtLogHandler` 通过消息关键词判断是否写状态栏，属于日志反向驱动 UI 的脆弱接线；改文案可能静默失效，第三方/子进程同词也可能误触发。状态栏应只消费显式 domain/progress signal，日志 handler 不应承担业务状态机。
- 日志初始化发生在 `MainWindow` 构造中段，之前的单实例、配置、图标、缓存和启动异常大量使用 `print`；windowed 打包态可能无控制台，这些关键启动诊断不会进入文件。应在 `QApplication` 前后尽早初始化非 Qt 文件日志，窗口创建后再附加 Qt sink。
- 建议采用一条 JSONL/结构化文本标准：`timestamp level process/thread logger event request_id task_id pipeline page batch elapsed_ms message exception`；主进程和 WorkerHost分别落源字段，父进程只转发而不改 severity。用户可见状态继续走 signal，日志仅诊断。

---

# 实施发现：PySide6 三阶段治理

## Phase 1 组合根与设置页

### 批处理线程复核

- 新增的真实 `QThread` 回归覆盖了取消窗口禁止重启、原生 `finished` 后才释放引用、单批失败继续后续批次、关闭超时保留 worker，以及旧 worker 迟到信号隔离；这组边界正好补上旧测试只直接调用 `run()` 的缺口。
- 批处理 Tab 现在暴露有界 `shutdown()/drain()`，但只有 MainWindow 在关闭 WorkerHost 和销毁结果 WebView 之前调用它才构成完整接线；该顺序仍需主线补齐。
- 当前 `batch_cancel()` 最终会走共享 `SyncBackendClient.cancel_active()`，它可能同时取消单图/PDF 调用。第一阶段先保证线程生命周期安全，第三阶段需要把取消收敛到 task/request 作用域，避免跨功能误伤。
- 设置控制器已有 TTL、释放重管道、释放全部管道和状态 label 的查找/槽函数，但 `main_window.ui` 与生成的 `ui_main_window.py` 根本没有 `spinPipelineTtl`、`btnReleaseHeavy`、`btnReleaseAll`、`labelReleaseStatus` 控件；旧逻辑因此是完全不可见的“死接线”。Phase 3 必须把模型运行缓存独立成真实 UI 区域，并保留现有“环境检测缓存”区域的准确语义。

- 设置页的 release 已在 `QRunnable` 中执行，不阻塞 GUI；Phase 1 无需重写其线程模型，重点是让 service 方法从空实现变成真实 RPC，并在完成后读取 cache status 验证后端状态。
- TTL UI 当前只能表达最小 1 分钟，而配置允许 0=禁用；Phase 1 RPC 应保留 0 语义，Phase 3 再把 UI 改成“启用开关 + TTL 数值”，避免在协议层丢失能力。
- `MainWindow.closeEvent()` 当前在 `_closing=True` 之前清理 PDF/WorkerHost，且没有 drain batch tab；Phase 1 集成时应把 `_closing` 前置，并在关闭共享 WorkerHost 前调用 batch tab 的有界 shutdown。
- MainWindow close 测试当前没有直接覆盖真实 closeEvent 顺序；需新增轻量 fake 组件测试，断言 batch drain 先于 `shutdown_backend_client()`。
- `tests/views/test_main_window.py` 的主 fixture 在 teardown 会触发完整 close 链，适合保留冒烟覆盖，但不适合精确断言顺序；应补一个绕过完整构造、直接调用 `MainWindow.closeEvent` 的 fake-object 单元测试。
- 设置预加载测试已有真实 UI fixture，可直接扩展 TTL=0 恢复、release 后 status 读回和 machine cache 文案；无需新增昂贵 E2E fixture。
- `ShutdownCoordinator` 当前把总预算平均分给步骤，但每步超时后 daemon 线程会继续运行，后续步骤可能与前一步并发清理同一资源；Phase 3 需要改为显式剩余总预算、依赖顺序和可检查完成状态，不能只靠“均分 timeout”。
- UI 文件的缓存按钮 tooltip 已明确“清除依赖检测缓存（不影响模型）”，但按钮文字/控制器日志仍写“清除缓存/依赖缓存+模型缓存”，而刷新按钮 tooltip 又写“扫描模型缓存状态”；存在三套相互冲突的语义，Phase 3 应统一为环境检测缓存与模型运行缓存两个区域。

## Phase 2 异步入口

- QR 预览 debounce 后直接同步调用 WorkerHost，decode 还用 `QApplication.processEvents()` 人工让界面刷新；改造应复用 `AsyncTaskRunner + asyncio.to_thread`，生成任务用递增 generation 丢弃过时结果，decode 用单一 in-flight task 禁止重复点击。
- QR 现有测试断言按钮点击后立即得到结果，异步化后需改成 `qtbot.waitUntil` 并增加“慢 backend 时 UI timer 仍触发”“旧预览结果不覆盖新文本”测试。
- 单图后端调用已经正确下沉到 `asyncio.to_thread`，但 QPixmap→PNG 和文档 `Path.read_bytes()` 仍在主线程；应把 payload 准备也纳入 async task，而不是再创建一套 worker。
- 单图 `_call_backend_recognize()` 捕获所有异常并重启 WorkerHost，Phase 2 应仅对 `SyncBackendError`/连接终止类错误重试，确定性的 payload/业务错误直接回 UI。
- PDF continuation 可由 `PdfTab` 自己维护一个 pending action（switch target 或 OCR 参数），在 `_on_save_done(file_path)` 校验原文件后执行；保存失败槽必须清除 pending action 并恢复原选择。
- `PdfSessionManager._start_mutate()` 现有 task generation 已能丢弃迟到 UI 信号，但不能阻止两个后端写操作并发；Phase 2 应在 manager 层拒绝/排队新 mutate，而不是仅在 tab 层禁按钮。
- 现有 PDF 测试覆盖 OCR 防重复和大量状态网格，但没有覆盖“保存后切换/保存后继续 OCR”以及 mutate busy；需要新增直接触发 save_done/failed 的状态机测试。
- WorkerHost 启动已迁入 `SubprocessManager` 自有 `QThreadPool`，由同一个 `service_ready` 信号回到 MainWindow；真实慢握手测试证明后台阻塞时 Qt timer 仍能触发。关闭时还必须以“活动 task 是否仍存在”过滤已排队 ready 信号，否则会在 widget 已销毁后弹失败对话框。
- 主窗口单元测试此前会在每个 fixture 的依赖检测完成后建立真实 WorkerHost，造成测试间跨进程生命周期污染；现在 fixture 层禁用真实启动，另由 manager 专项真实线程测试覆盖启动行为。

## Phase 3 工程边界预检

- 根 `pyproject.toml` 仍同时声明 PySide6 与完整 Paddle/Torch/MinerU/PDF 后端依赖；`apps/vibeocr-pyside` 和 `packages/vibeocr-backend|client-py|contracts-py` 虽有独立发布声明，但各自 wheel 当前只包含一个 marker `__init__.py`，真实实现仍全部来自根 `src/vibeocr`。因此 workspace 依赖边界目前只约束元数据/未来迁移，不会实际减小 PySide 安装体积或阻止 UI 导入后端。
- Phase 3 的“依赖边界验证”应区分两件事：现有 architecture import guard 对源码依赖方向确实有效；独立 workspace wheel 的运行时可用性则尚未成立。此次应增加可重复 smoke/清晰判定，不把 marker 包描述成已完成拆包。

## Phase 3 最终实施判定

- WorkerHost stdout 现在只承担 ready 握手，诊断日志固定写 stderr JSONL；父进程按原始 severity/logger/exception 转发，并保留 request/task/pipeline/page/batch 上下文，解决了此前 stderr 全部降格为 WARNING 的问题。
- 固定 16 项批次不足以控制高分辨率 PNG 解码后的内存。新预算同时约束 16 项、64 MiB 编码数据和 4800 万像素；PDF 仍只预取一批渲染，但在提交 OCR 前会进一步切分传输批，因此吞吐与峰值内存之间的边界更可信。
- ShutdownCoordinator 采用一个绝对 deadline 和逐步剩余预算，不再平均切片；PDF 的 request/drain 拆分补上了主窗口此前绕开协调器的固定等待漏洞。超时 worker 保留所有权并由原生 finished 回收，不使用 terminate。
- 模型缓存管理的后端机制与 UI 接线现已都有效：TTL=0 可表达禁用，状态可读回，释放后再查询实际常驻集合。磁盘模型文件仍不属于该功能，界面已明确区分“环境检测缓存”和“模型运行缓存”。
- workspace import guard 有实际效果，但独立 wheel 仍只有 marker 包；本轮离线 wheel smoke 又受缺少 hatchling 阻断。因此“发布壳依赖优化有效、源码物理拆包尚未完成”仍是最终工程边界结论，不应宣称已完成独立安装。

---

# 实施发现：四包物理拆分与联网重依赖安装

- 用户已确认真正拆包，不再把 workspace 清单视作迁移占位。
- 用户安装允许联网，因此重点是正确的 PEP 517 构建、依赖元数据和平台/索引解析；不需要把 hatchling 或重依赖 wheel 内嵌进应用安装包。
- 现有四个 workspace wheel 的 Hatch `packages` 都只指向各自 marker 包；真实 `vibeocr.*` 代码仍全部位于根 `src/vibeocr`。
- `scripts/build_backend_wheel.py` 虽能生成包含真实后端代码的 wheel，但实现方式是读取 `config/backend_artifact_include.txt`，从根源码临时复制并手工生成 METADATA；这属于发布制品拼装，不是 workspace 的物理所有权。
- 根 `pyproject.toml` 当前无条件声明 Paddle GPU、Torch CUDA、PaddleOCR、MinerU、PySide6 等全栈依赖。根兼容包可继续聚合四包，但子包必须各自携带准确依赖，不能依赖根环境“碰巧已装”。
- 四个 wheel 需要共享 `vibeocr` 顶层 namespace，同时每个模块只由一个 wheel 提供；根兼容发行包应主要作为 meta dependency，避免与子 wheel 重复安装相同文件。
- 当前前端不仅使用 `contracts/client`，还直接使用 `models`、部分 `core`、`ipc` 和轻量 `utils`；后端同样依赖这些模块。四包约束下不能再新增第五个 common wheel，因此 `vibeocr-client-py` 需要承担“共享 Python SDK”职责：typed transport + IPC schema/model bridge + Qt-free domain models/core helpers。backend 和 pyside 均依赖 client，client 依赖 contracts。
- `worker_host` 目录同时包含客户端 transport（`backend_client.py`、`sync_client.py`、framing/named_pipe/security/shared_payload/contracts/errors）和服务端 dispatcher/handlers/composition/main。物理拆分时可让两个 wheel共同贡献同一个 namespace 子目录，但必须消除冲突的 `worker_host/__init__.py`，并按文件级归属构建。
- PySide 层大量使用 `env_manager`、`machine_cache`、安装/更新逻辑；这些属于桌面壳的环境治理而非推理 backend。需要确认 backend 是否仍有直接引用，若无则归 pyside；若有则抽取最小 Qt-free配置到 client，不能继续整文件被两个 wheel 重复打包。
- 现有 architecture 测试硬编码扫描根 `src/vibeocr`，物理移动后会失去保护；测试必须改为扫描四个 workspace source root，并新增 wheel 文件唯一归属检查。
- `env_manager` 被 PySide 安装界面、WorkerHost composition、OCR/PDF backend 和 shared client shutdown 同时调用，且自身不依赖 Qt。它应归入 client/shared SDK，而不是 pyside 或 backend；相应的 `machine_cache`、`network_detector`、`pipeline_status`、`app_paths`、`python_path_manager` 也归 client。
- `services`、`utils`、`core` 和 `application` 都存在跨 wheel 文件级拆分需求。它们现有 `__init__.py` 有 eager imports（尤其 `services.__init__` 会选择 OCR 实现），会让轻量 client/pyside 意外加载 backend。需要由 client 持有最小 namespace `__init__`，使用 `pkgutil.extend_path` 支持 editable 多 source root，并把兼容导出改为按需加载。
- `core/base_worker.py` 是唯一明显 Qt core 文件，可归 pyside，其余 core/pipeline 配置归 client；`application/contracts.py` 归 client，facade/orchestrator 归 backend；models/ipc 全部归 client。
- `src/vibeocr/output` 含运行产生的大量文档/图片，不属于任何 wheel，也不能在物理移动时带入包目录；新 wheel 内容审核必须显式拒绝该路径。
- 官方安装文档确认 CUDA wheel 需要显式 index：PyTorch 2.6/cu126 要求 `--index-url https://download.pytorch.org/whl/cu126`，Paddle Windows GPU/cu126 也要求 Paddle 官方 cu126 index。PEP 508/Core Metadata 只能携带名称、版本、extra、marker 或直接 URL，不能把项目级 index 选择随普通 `Requires-Dist` 传播给 pip。
- 因此不能把“开发仓库 `[tool.uv.index]` 能解析”当作最终用户安装保障。发布方案必须区分：四个标准 wheel 的静态依赖元数据；以及 VibeOCR 自有的联网 backend bootstrap/profile 安装器，后者按硬件显式传入官方/镜像 index 并在安装后做 import/版本/运行检查。
- 根 meta package 默认安装四个 VibeOCR wheel 和 PyPI 可解析的公共依赖；GPU/CPU 引擎不应靠不可传播的 uv index 隐式选型。完整用户入口应调用现有 `env_manager` 安装链选择 `cpu`/`cu126`，失败时保留可重试状态，避免得到 CPU Torch + GPU Paddle 的 ABI 混装。
- 现有 `env_manager._install_paddle_stack()` 已具备显式 pip 源、Torch CUDA index、Paddle CPU/GPU profile、失败重试、取消和安装后快速验证，适合作为最终用户联网安装重依赖的唯一实现，不应另造一套 CI 专用安装逻辑。
- 当前依赖规格在开发态读取根 `pyproject.toml`、打包态读取项目根 `version.json`；物理拆包后普通 site-packages 安装不保证能找到这两个仓库文件。需要把 profile/锁定版本数据作为 `vibeocr-client-py` 的包资源随 wheel 分发，并让仓库文件只作为开发态优先源。
- 根源目录共有 16 个业务子目录和 11 个顶层模块；`output/` 与 `__pycache__/` 属于运行产物，必须排除。四个 workspace 目前均只有单个 marker 文件，迁移目标路径不存在历史产品代码冲突。
- 物理移动已按文件所有权落到四个工作区；根 `src/vibeocr` 只剩明确排除的运行产物/缓存，不再作为任何 wheel 的隐式代码来源。跨 wheel 的 `application/core/services/utils/worker_host/workers` 由 client 持有可扩展初始化文件，其它 wheel 只贡献无冲突模块。
- v1 JSON Schema、error registry 与 golden fixture 已归属 contracts wheel 的 `vibeocr.protocol.v1` 包资源；运行时错误映射通过 `importlib.resources` 读取，不再假设仓库根存在 `contracts/v1`。
- 根 `vibeocr` 改为无代码 meta wheel，精确依赖四个发行包；PySide 与 WorkerHost console entry point 分别迁入 pyside/backend wheel。backend 基础依赖不再隐式选择 GPU 引擎，CPU 与 `gpu-cu126` 作为显式 profile，client 提供 `vibeocr-install-backend` 联网安装入口并与便携安装器共用实现。
- 依赖 profile 已作为 `vibeocr-client-py` 包资源分发；开发态从 backend 工作区 manifest 读取并覆盖，安装态不再依赖仓库根 `pyproject.toml/version.json`。这同时保留了 PyInstaller `version.json` 的旧发行兼容路径。
- 最终安装策略刻意不把 Paddle/Torch/MinerU 设为根 meta package 的无条件依赖：普通 `pip install vibeocr` 安装可启动的桌面壳与后端基础层；随后 `vibeocr-install-backend --profile auto` 在用户机联网检测 CPU/GPU，并用显式 Paddle/PyTorch index 安装重引擎。这样避免 pip 无法从 `Requires-Dist` 继承自定义 index 导致 CPU/GPU 混装。
- 五 wheel 干净环境 smoke 已证明普通用户安装不再依赖 workspace、editable 路径或本机源码；client wheel 内的 `dependency_profiles.json` 也已通过 `importlib.resources` 实际读取。
- `vibeocr-backend[cpu]` / `[gpu-cu126]` 保留标准 extras 作为元数据与高级安装入口，但 GPU 的可靠推荐路径仍是 bootstrap 命令，因为只有它能显式选择 Paddle 与 PyTorch CUDA 12.6 index。README 已明确该区别。
- 发布期五 wheel 校验同时检查：根包无代码、模块路径无重复所有者、重引擎不是 backend 无条件依赖、CPU/GPU extra 存在、profile/UI/protocol 非 Python 资源进入正确 wheel。该门禁能阻止重新退化成 marker 或“构建成功但运行资源缺失”。

---

# 终审发现：拆包提交前复核

- 当前所有改动直接位于 `main` 的未提交工作树，尚无特性分支。为满足可审计合并历史，应在提交前创建 `codex/workspace-physical-split`，提交后再非快进合并回 `main`。
- PySide-only 实际安装冒烟发现 `from vibeocr.utils import ocr_sidecar` 会先触发 client `utils.__getattr__`，而该实现对任何未知属性都先导入 backend 所有的 `shared_memory_v2`，导致未安装 backend 时 PDF 页懒加载失败。根 meta 安装因包含 backend 会掩盖该问题。
- 正确修复是让 `__getattr__` 在导入 backend 模块前先验证名称；未知名称抛 `AttributeError` 后 Python 才能按标准语义继续导入真实子模块。修复后不含 backend 的环境可导入全部 78 个 PySide 物理模块，contracts+client 的 78 个模块也全部通过。
- backend 三 source-root 环境导入全部 37 个 backend 物理模块，零失败且 `PySide6` 加载数为 0，UI-free 边界成立。
- 发布脚本原先对同一 distribution 的多版本 wheel 会字典覆盖或取目录中第一个文件，存在本地/复用目录静默绑定旧版本的风险；终审改为拒绝重复 wheel、拒绝与发布版本不一致的 wheel，并让五包内容校验强制版本一致。
- CI 根 meta smoke 原先按发行名解析，理论上可能从索引选择比本地 wheel 更新的公开版本；改为定位本次唯一根 wheel 文件后直接安装，内部四包仍由精确 pin 从 wheelhouse 解析。
- 普通 wheel 安装态此前没有独立运行根语义：在仓库内 venv 会被祖先工作区误判为源码态，在普通用户机又回退到 venv 的 `Lib`，使 GUI 依赖检测寻找 `Lib/python/python.exe`，无法识别 bootstrap 已安装到当前环境的重依赖。现已要求模块自身确实位于 client source root 才判定工作区，否则返回 `sys.prefix`，并让所有 embedded-python helper 返回 `sys.executable`。
- 隔离 venv 首次只把根 wheel 作为直接文件时，uv 对内部四包复用了同版本旧缓存。这证明“find-links + 根包直装”不足以严格验证本次五轮。CI 与 README 改为显式传入五个 wheel 路径；强制重装后确认五个发行包全部来自本地 wheelhouse，运行根与解释器断言通过。
- Windows 原生 WMIC/nvidia-smi 输出可能不遵循 `PYTHONUTF8=1`。依赖/硬件探测子进程现统一使用 replacement decoding，避免日志读取线程因单个不可解码字节退出；MainWindow 回归已从两个 thread-exception warning 降为仅环境缺少 pytest-asyncio 的配置 warning。
# GitHub 工作流修复与 0.5.0 发布（2026-07-19）

## 远端失败结论

- GitHub Actions 运行 `29672925992`（Release，标签 `v0.5.0`，提交 `66e45d0`）在 `Validate Python migration and release gates` 失败；Quality Gates 在同一提交成功。
- 失败集为 `tests/release_layout/test_winui_layout.py` 的 8 个用例，统一报错：`verify_winui_artifact.ps1` 对 `product-manifest.json` 执行 `ConvertFrom-Json` 时读到普通字符串 `release-content`。
- 新物理拆包架构把 WinUI 所需的 `contracts + client + backend` 三个运行时 wheel 及其 SHA-256 纳入 `product-manifest.json`；PySide wheel 与根 meta wheel不进入 WinUI 制品。该文件因此从普通布局占位文件升级为结构化发布契约。测试夹具仍写统一占位文本，也没有创建清单引用的三个 wheel。
- 修复方向应在测试夹具层提供符合当前架构的最小合法 manifest，并保留校验脚本对真实 WinUI 制品的严格 JSON/三运行时 wheel 验证，不能通过放宽生产校验绕过。
- 本地与两个远端的 `main`、本地/远端 `v0.5.0` 当前均指向 `66e45d0`；版本源、四个子包内部 pin、`uv.lock`、协议 golden 与 CHANGELOG 已经是 0.5.0，无需再次运行版本提升脚本。
- 重新发布应把现有 `v0.5.0` 标签移动到修复提交。失败工作流在上传 Release 步骤前终止，因此需先查询是否存在残留 draft/空 Release，再按实际远端状态清理。
- GitHub CLI 已确认 `v0.5.0` Release 不存在，只有远端标签指向旧提交；重新发布只需在 main 修复通过后删除并重建该标签，不需要删除 Release 资产。
- 五个显式本地 0.5.0 wheel 在短路径干净 venv 中联网安装成功；基础公开依赖解析正常，运行根为 `sys.prefix`，WorkerHost `--self-test` 返回 0.5.0。
- 仅安装 contracts/client/pyside 三 wheel 时，backend distribution 未被安装，已成功导入安装环境中发现的 155 个 `vibeocr.*` 模块，证明前端物理包边界有效。
- 修复提交 `2120617` 的 Quality Gates 运行 `29674920765` 全部通过：contracts 1m21s、pyside 1m41s、winui 2m5s、backend 4m38s；backend 包含五 wheel 构建、所有权校验和根 meta wheel 干净安装。
- `v0.5.0` 已从失败提交 `66e45d0` 移动到已验证提交 `2120617`；Release 运行 `29675075394` 以成功结束，标签 API 也确认目标 SHA 为 `2120617`。
- Release 为正式版、非 draft/非 prerelease，共上传四个资产：Classic ZIP（约 72.4 MiB）及 SHA-256、五-wheel wheelhouse ZIP（约 714 KiB）及 SHA-256。默认发布变体为 pyside6，因此 WinUI 安装包按配置跳过；WinUI 代码仍通过主分支和发布流程中的 .NET build/test 门禁。
# 2026-07-19：0.5.0 更新启动失败调查

- 用户日志表明旧 `VibeOCR.exe` 已成功改名避让，备份、删除、复制均完成；失败仅发生在新版启动阶段。
- `scripts/update_replacer.py::launch_app` 在 Windows 默认硬编码 `VibeOCR.Bootstrapper.exe`，文件不存在就直接抛 `FileNotFoundError`。
- `scripts/updater_main.py` 虽会把旧 `VibeOCR.exe`、`VibeOCR.WinUI.exe`、`VibeOCR.Bootstrapper.exe` 都纳入替换前避让名单，但这不等于启动阶段支持这些入口。
- README 当前仍把 `VibeOCR-Classic-vX.Y.Z-win64.zip` / `VibeOCR.exe` 标为主力发布路线；仓库同时存在要求 Bootstrapper 的 WinUI 独立发布布局。因此替换器必须识别两种正式产物，而不能假设所有更新包都是 WinUI。
- 失败发生在替换成功之后，用户安装目录中很可能已有新版 `VibeOCR.exe`；安全修复应按明确优先级选择存在的正式入口，并仅对支持健康参数的入口追加 WinUI 参数。
- 本地历史证据确认回归由提交 `d5b39e6` 引入：此前 Windows 默认启动 `VibeOCR.exe` 且不等待健康文件；该提交改为硬编码 Bootstrapper + 30 秒健康握手。
- `scripts/bump_version.py` 当前明确规定默认 `pyside` 发布为 Classic，`winui` 仅为开发预览；注释还说明曾因只走 WinUI 导致 v0.4.29+ Classic 发版失败，现已恢复双路径。0.5.0 的版本与 tag 均存在，用户现场符合 Classic 更新包。
- 修复必须保留 WinUI 优先走 Bootstrapper 和健康握手，同时让 Classic `VibeOCR.exe` 恢复无参数启动；不能对 Classic 等待它不会写出的 `startup.healthy`。
- 最终入口策略：显式入口参数保持精确选择；Windows 默认按 Bootstrapper → Classic 排序；仅存在 `VibeOCR.WinUI.exe` 时仍报布局缺失，避免绕过 Bootstrapper。
- 失败/回滚语义未改变：只有文件替换成功后才调用新入口；替换或校验失败仍不会误启旧 UI。Classic 启动沿用回归前的无参数 detached Popen，WinUI 继续执行 30 秒健康握手。

---
