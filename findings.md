# 调查发现

## 2026-07-20 P2-C 关闭返修

- 当前 `ShutdownCoordinatorJob -> ShutdownCoordinator` 会再次创建 daemon `threading.Thread`，并在线程中调用 settings/pdf/batch 等 QObject 的 drain，违反 Qt 线程亲和。
- `PdfSessionManager.drain()` 当前既等待又创建 `PdfIpcCloseWorker`、修改 QObject owner 持有的集合；不能从后台协调线程调用。
- 固定 UX 超时不能作为析构许可：Qt worker 未 native-finished 时必须继续保活 owner 和轮询，不能 accept/delete。
- MinerU preflight 取消路径提前清 `_ocr_running`/发 `ocr_done`，而 `_preflight_worker` 尚未 native-finished；业务终态必须推迟到 finished。
- Settings、Batch、QR 的现有 `drain()` 均可能 wait 或修改 widget/引用，不能作为后台调用；Main 需要优先使用纯 `is_drained` hook，并为尚未迁移的组件仅在 GUI 定时器里做 `drain(0)` 兼容探测。
- `SubprocessManager` 本身是 QObject，当前 `shutdown()` 会取消任务、断信号、等待线程池并清 QObject state，不能直接交给后台清理线程；需拆成 GUI request/poll 与非 Qt service shutdown，或保持 GUI owner 存活到后台工作完成。
- QR `drain(0)` 会在成功时清 `_save_job` 并启用按钮，也不是纯观察；Main 的动态 hook 必须优先 `is_drained`，兼容调用仍限定 GUI 线程。
- `PdfSessionManager.request_shutdown()` 当前只 cancel active workers；全部 session 的 `PdfIpcCloseWorker` 仍由 `drain()` 创建，必须迁到 request 阶段。随后 `is_drained()` 只检查所有 native worker 是否 finished，最终状态清理也留 GUI 槽处理。
- ThumbnailModel 已有 request-only，但 `wait_for_draining()` 会 wait/释放引用；需新增纯 `is_drained()`，PdfTab 聚合 thumbnail/session 状态。
- BackendOptions 的 `drain_gpu_detection(0)` 会在已结束时清引用；Settings 尚无纯探测 hook，因此 Main 兼容轮询必须在 GUI 线程执行。
- `SubprocessManager._service` 的生产类型（WorkerHost client adapter / OCRServiceSubprocess）不是 QObject；可以在 GUI 线程待 QThreadPool active=0 后 detach 为纯 callable，再由单一 QThread 顺序执行 service/backend shutdown。
- `shutdown_backend_client()` 通过进程级锁取得并清空普通 SyncBackendClient，然后调用其 shutdown；适合作为 Qt owner 全部 drained 后的外部清理 callable，但不应再套 daemon step timeout。

- 诊断流程要求在推断根因前，先建立能对用户精确症状报红的自动化回路。
- 系统 PATH 中无 `python`；可用捆绑运行时 `C:\Users\felji\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`。
- session-catchup 改用捆绑 Python 后成功，未报告遗留上下文。
- 仓库同时包含 PySide 和 WinUI 前端；已有 CUDA/GPU 检测、依赖管理、工具栏及 WinUI 设置页面相关测试。
- 顶层文件列表未发现 `CONTEXT.md`；有双前端 ADR，待确认实际故障路径后阅读相关部分。
- `tests/test_env_manager_gpu_info.py` 已覆盖 GPU 信息、CUDA 检测失败/超时日志，但尚未覆盖具体版本输出解析失败的症状。
- `tests/managers/test_dependency_manager.py` 覆盖依赖检测任务和管理器基本状态，但没有验证检测期间 UI 事件处理或任务生命周期。
- 工作树已有多项用户修改（CI/QA 与多个测试文件），后续只修改与本次 Bug 直接相关的文件，不触碰其余差异。
- 故障路径集中在 PySide：`env_manager.detect_cuda_version()` 负责 CUDA 版本映射，`DependencyManager` 在 `QRunnable` 后台任务中调用嵌入式环境检测。
- `env_manager.py` 的依赖检测附近特别注释了它运行在 `DependencyCheckTask(QRunnable)` 后台线程，这是为 UI 卡死/闪退构建回归回路的候选边界。
- 本机 `nvidia-smi` 可稳定运行，GPU 为 RTX 4090；新驱动输出中版本标题是 `CUDA UMD Version: 13.3`，而现有测试/搜索显示实现只匹配 `CUDA Version: X.Y`。这提供了 CUDA 检测失败的真实、确定性输入。
- `uv` 和常规 Python launcher 环境不可用：`Get-Command uv,...` 因 `uv` 缺失返回退出码 1，`py -0p` 报告无已安装 Python；需继续用 Codex 捆绑 Python。
- 真实调用命令 `...python.exe -c "... print(em.detect_cuda_version())"` 稳定返回 `None`，同一台机器的 `nvidia-smi` 明确报告 `CUDA UMD Version: 13.3`；CUDA 问题已有约 2 秒的真实红灯回路。
- `DependencyManager.check_dependencies()` 创建局部 `DependencyCheckTask`，将其 `finished` 信号连接到 Python lambda，再交给自有 `QThreadPool`；完成回调更改 manager 状态并发出 UI 可见信号。
- 实时依赖检测在后台任务中可能执行多个嵌入式 Python 导入子进程，并在末尾检测 GPU/写入缓存；因此需将回归回路放在真实 `QThreadPool` + Qt 主事件循环边界。
- 用户所述“工具栏”在代码中有明确对应：`EdgeToolbar` 是无边框置顶浮窗，拖动时在 `mouseMoveEvent`/按钮事件过滤器中频繁调用 `move()`，释放后发出 `position_changed`。
- 测试工程已提供 `qapp`/pytest-qt，可在同一主线程事件循环中同时驱动 `EdgeToolbar` 拖动与真实 `QThreadPool` 依赖任务完成。
- Codex 捆绑 Python 不含 pytest；仓库自有 `.venv\Scripts\python.exe` 存在，应改用该环境运行项目测试。
- `.venv` 可正常加载 pytest/PySide，且不需 `tmp_path` 的 14 个相关测试通过；其余 8 个因 pytest 默认临时根目录在沙箱不可写的 `%LOCALAPPDATA%\Temp` 失败，与产品逻辑无关。
- 单纯设置 `TEMP`/`TMP` 未改变 pytest 已选定的临时根目录；下一次改用唯一的 `--basetemp=C:\tmp\...` 精确指定。
- 沙箱实际不允许 pytest 在 `C:\tmp` 创建 basetemp，但允许在工作区内创建；改用工作区唯一 basetemp 后，CUDA/依赖管理/工具栏现有 22 项测试全部通过（0.35s）。
- `logs/vibeocr.log` 提供了同一现象的历史证据：RTX 4090 能被 `nvidia-smi -L` 识别，紧接着 CUDA 版本检测却回退失败；这与当前真实复现一致。
- 日志中大量 MainThread GPU 检测来自同一 pytest 进程内多个用例/对话框，未发现能直接指向产品闪退的 Python traceback 或 Qt 致命信息。
- `EdgeToolbar.position_changed` 只连接到 `MainWindow._on_toolbar_position_changed`；GPU 实时检测的 UI 调用点在 backend 选项组件和依赖安装对话框，需继续区分“启动依赖检查”与“安装对话框硬件检测”的并发边界。
- 依赖安装对话框的 `InstallWorker(QThread)` 在工作线程中执行网络/GPU/环境/安装检测，进度和结果通过 Qt 信号回主线程；它未在已读部分直接操作 QWidget。
- 设置页 `_GpuDetectWorker(QThread)` 也明确将 `nvidia-smi` 放在后台，并由父 QWidget 持有；这条路径的寿命周期/关闭测试可作为对照。
- `InstallDialog` 持有 `InstallWorker`，进度 slot 有 `@Slot`；安装进行中关闭会在 GUI 线程 `wait(5000)`，这会有最长 5 秒的界面停顿，但与“仅拖动工具栏”还没有直接因果证据。
- `MainWindow` 在显示后 100ms 启动后台依赖检查，200ms 又调度 `_apply_gpu_gating_to_all`；后者与启动检测时间重叠，需确认是否在主线程内同步调用 `nvidia-smi`。
- 已确认 `_apply_gpu_gating_to_all()` 由 GUI 线程的 `QTimer.singleShot` 直接执行，其首次 `get_runtime_gpu_capability()` 可同步调用 `detect_gpu()`/`nvidia-smi`；`singleShot` 只是延迟，没有将工作移出 GUI 线程。
- 新增精确 UI 红灯用例：让 GPU 探测持续 250ms，在 25ms 时投递真实 `EdgeToolbar` 鼠标拖动事件；实际拖动延迟 0.263s，超过 0.15s 上限，稳定报红。
- CUDA 最小化输入仅需 `CUDA UMD Version: 13.3`；mock `nvidia-smi` 返回该行、`nvcc` 不存在时，`detect_cuda_version()` 稳定得到 `None`（期望 `cu126`）。
- 两个最小回归用例同时运行时均稳定报红：CUDA `None != cu126`；工具栏拖动 0.263s > 0.15s。
- 假设 3（`DependencyManager` 完成回调落在非 GUI 线程）已被定向线程亲和性测试证伪：真实 `QThreadPool` 执行后，`_on_task_finished` 记录到的是 `qapp.thread()`，测试 0.13s 通过。
- 假设 4（`EdgeToolbar` 自身拖动有故障）同样不受支持：无慢探测时现有 3 项 toolbar 测试通过，只有将同步 GPU 探测投递到主线程后才能复现延迟。
- 综合判定：假设 1（CUDA 新字段名未匹配）和假设 2（GPU 门控同步阻塞 GUI）为真；其余排除。
- `MainWindow` 当前只有一个用于状态栏的跨线程信号，尚无 GPU 门控专用 worker/信号。
- 设置控制器已持有一个 `BackendOptionsWidget` GPU 检测线程并实现关闭期取消/排空，但结果没有作为公共信号转发给 MainWindow；直接读其私有属性会形成不必要耦合。
- `MainWindow.closeEvent` 已将设置控制器纳入统一取消/drain 预算；如复用 `BackendOptionsWidget` 现有探测结果，无需再新增一套 MainWindow 线程关闭机制。
- 设置控制器目前是组合对象，`request_shutdown()` 已会通知 backend GPU worker 取消，`drain()` 会在共享截止时间内等待；这是可复用的安全生命周期边界。
- 仅转发 `BackendOptionsWidget` 的物理 GPU 信息还不够：它当前在主线程 `_apply_detected_state()` 再调 `resolve_use_gpu()`，缓存无效时仍会同步执行 `nvidia-smi`。
- 更完整的最小边界是：让现有 `_GpuDetectWorker` 同时计算物理 GPU 信息与“实际运行后端”布尔值，主线程只应用结果并将运行能力转发给 MainWindow。
- 首轮修复后定向回归中，CUDA 新格式和工具栏响应性用例已转绿，backend widget/toolbar 测试也全部通过；总计 32/33 通过。
- 唯一失败是新加的 `DependencyManager` 线程亲和性排除用例在整组运行时等待超时；它单独运行已通过，且不在本次生产代码修改路径上，需判定是测试隔离问题还是真实时序。
- 重跑整个 `test_dependency_manager.py` 时 13 项在 0.15s 内全部通过，确认上述超时是跨文件 Qt 测试时序污染，不是产品故障。该用例只用于排除假设 3，将在清理时移除，避免引入无关不稳定性。
- 差异审查确认修复范围为 4 个生产文件：CUDA 解析/运行能力缓存、backend GPU worker、设置控制器转发、MainWindow 应用结果；未触碰其他用户修改。
- 运行时 GPU 能力新边界已有两个额外不变式测试：后台物理探测结果会被直接复用（不二次调 `detect_gpu`）；用户 `pending_backend=cpu` 仍优先于物理 GPU。
- 清理排除性临时测试后，定向套件 34 项全部通过（0.74s）；包含 CUDA 新表头、UI 拖动响应性、backend worker 生命周期和依赖 manager 基线。
- 修复后重跑本机原始真实 CUDA 命令，`detect_cuda_version()` 已从 `None` 变为 `cu126`。
- Ruff 首轮定向检查仅报 3 个新代码样式问题：类型专用 `Callable` 应放入 `TYPE_CHECKING`，UI 回归测试导入顺序，以及未启用的 `N802` noqa。
- 上述 3 项已清理，定向 Ruff lint 现为 `All checks passed`。
- Ruff format 全文检查显示 6 个既有大文件将被整体重排；为避免扩大差异，不对旧文件做全量格式化，只格式化本次新建的 UI 回归测试。
- 新建 UI 回归测试已单独格式化并通过 format check；`git diff --check` 无空白错误。
- 文档参数检查发现 `detected_has_gpu` 说明曾被泛化上下文误插到依赖详细检查函数；已用唯一上下文移到 `get_runtime_gpu_capability` 的 Args。
- 扩大到 117 项的首轮 UI 套件在约 55% 处进程无 pytest 失败摘要地退出 1，最后完整显示的是 `test_settings_preload.py` 3 项通过。这更像 Qt/QThread 进程级退出或套件污染，需拆文件隔离，不盲目重跑同一组合。
- 并行拆文件尝试显示 `test_settings_reinstall.py` 单文件收集 14 项、运行到第 11 项后同样无摘要退出；这已将异常收窄到该文件后 3 项或前序对话框/线程残留。其他并行进程输出未完整返回，后续改为串行精确用例，避免多 GUI 进程干扰。
- collect-only 确认第 12–14 项依次为 detailed fresh check、direct dependencies 展开、批量重装。只运行这 3 项时，第 12 项通过后进程立即退出，故障进一步收窄到第 12 项 teardown 或第 13 项启动。
- 第 12 和第 13 项各自单独运行都通过（0.35s/0.39s），只有连续运行才会进程退出。这证实是用例 teardown 遗留的 backend GPU QThread 与下一个 Qt 对象生命周期冲突，而不是第 13 项功能断言。
- 该设置控制器测试的目标是依赖表/按钮，backend GPU worker 已有独立专项测试；设置页测试应在 fixture 中禁用真线程，以避免 patch 恢复时 worker 跨用例运行。
- `test_settings_reinstall.py` fixture 已按上述边界禁用 backend 真探测线程；整文件 14 项现在稳定通过（0.69s），不再无摘要退出。
- 扩大 UI 回归套件重跑已全绿：117 项中 114 passed / 3 skipped（2.96s），覆盖 MainWindow、设置页、backend 切换、GPU 门控、截图/预处理选项和 EdgeToolbar。
- 包含新增测试隔离修正后，所有本次相关文件的 Ruff lint 再次通过。
- 清理检查未找到任何 `[DEBUG-...]` 临时仪器标记；`rg` 因零匹配按约定返回退出码 1。
- 已逐一验证 13 个 `codex_test_tmp_019f7eaa*` pytest 临时目录均位于工作区内，并全部删除。
- 最终 `git diff --check` 通过；本次 7 个已跟踪代码/测试文件差异为 145 行新增、50 行删除，另有 1 个新 UI 回归测试文件。
- `tests/managers/test_dependency_manager.py` 在 status 中因工作树换行符混合显示 `M`，但 `git diff --exit-code -- <file>` 返回 0，确认内容与索引一致；需只做换行符机械规整。
- `ruff format --diff` 预览确认 manager 测试所谓“重格式化”仅是将本次临时补丁触及的 LF 行恢复为文件主体使用的 CRLF，无语义内容变化；可安全机械规整。
- manager 测试换行符已用 formatter 恢复，其 `git diff --exit-code` 为 0 且不再出现于定向 status。

## 根因与预防

- CUDA 根因：驱动输出升级为 `CUDA UMD Version`，解析器只接受旧标签。预防手段是保留真实新版输出回归用例。
- UI 根因：`QTimer.singleShot` 被误当作后台执行，实际仍在 GUI 线程同步 shell out `nvidia-smi`；设置页完成 slot 中还有第二个同步回退。预防手段是将所有探测/解析放入既有 QThread，并用工具栏事件延迟上限作为回归信号。
- 本次已有合适测试缝，无需额外架构改造。
- planning-with-files 最终检查为 `ALL PHASES COMPLETE (6/6)`。
- 最终定向 status 只包含 4 个生产文件、3 个既有测试文件和 1 个新 UI 回归测试；临时 manager 测试差异已清除，`git diff --check` 仍通过。

## 2026-07-20 UI 主线程阻塞审计

- 用户明确要求调用合适子代理；按路由技能拆为三个互补只读工作包，并保留主代理负责交叉验证。
- 当前审计范围覆盖 PySide 主窗口/设置、业务页面与控件，以及底层同步 I/O/子进程/重计算到 GUI 的反向调用链。
- 本轮只允许更新审计计划文件，不改业务代码；候选项必须经过“是否真的在 GUI 线程执行”的二次核验。
- 前一轮已确认 `QTimer.singleShot` 只延迟执行、不切换线程，因此本轮会专门检查类似“看似异步、实际仍在主线程”的路径。
- 初始阻塞原语扫描命中多处 `QThread.wait(...)`：应用退出、PDF 会话/标签、批量页，以及安装/后端切换对话框；需要逐项判断是否只发生在关闭路径、等待上限是否可接受。
- 主窗口存在少量同步 JSON 状态文件读写；二维码页存在 PNG/SVG 编解码与写盘；单图页存在图片 `read_bytes()` 后调用识别函数。下一步重点确认这些调用是否已被 worker 包裹。
- 设置控制器中的 `subprocess.run` 位于第 96 行附近，但静态命中本身不足以判定 UI 阻塞，需沿调用者和线程类复核。
- `QFileDialog`/`dialog.exec()` 命中大多是用户主动模态交互，不能仅因嵌套事件循环就列为异步改造项；只在伴随重计算或 qasync 重入风险时保留。
- 批量页“导出全部”在 GUI 方法中串行遍历所有完成项，并逐项调用 `export_result(...)` 写文件；文件越多阻塞越线性，属于高置信候选，需继续定位 slot 与导出实现。
- 应用 `aboutToQuit` 清理会对每个可取消 QThread 最多 `wait(3000)`，且按线程串行等待；这是退出卡顿而非日常交互卡顿，但最坏时长可随线程数增长。
- PDF 会话 `drain()` 在关闭预算内等待多个 worker，之后仍同步关闭所有后端 session、停止 client 和清理字体解析器；关闭阶段存在超出预算的潜在同步尾部，需核对各 stop/cleanup 实现。
- `SettingsPageController._create_windows_shortcut()` 同步运行 PowerShell，超时上限 15 秒；如果由设置页 checkbox/按钮直接调用，则应列为高优先级候选。
- `_create_windows_shortcut()` 在设置控制器第 371/388 行直接调用；待读取其 UI 连接可确认桌面/开始菜单快捷方式按钮路径。
- 批量页第 398 行把 `export_all_requested` 直接连到 `_on_export_all()`，该方法第 640 行开始同步逐项导出；相比 PDF 页已经使用 `export_all_async()`，这是清晰的异步改造对照。
- PDF “批量导出”已由按钮回调转交 `PdfSessionManager.export_all_async()`，不应重复列为问题。
- `_wait_thread()` 由 PDF 页第 342 行调用，需判断它是页面切换/文件打开时的频繁等待还是只在退出时触发。
- 快捷方式按钮的 `clicked` 信号直接进入 `_on_create_desktop_shortcut()` / `_on_create_start_menu_shortcut()`，随后在 GUI 线程调用 `_create_windows_shortcut()`；同步 PowerShell 超时 15 秒，确认为高置信交互阻塞候选。
- 批量页不仅“导出全部”，`_on_export_current()` 也在 GUI callback 中同步调用 `SyncBackendClient.export_ocr_sync()`；单文件可能受 WorkerHost IPC、DOCX/XLSX 生成和磁盘影响，应一并异步化，但优先级低于批量循环。
- `get_unique_output_path()` 会同步反复查询文件是否存在；通常轻量，但大量同名冲突会放大，适合随导出 worker 一起移出 GUI，而非单独设计线程。
- PDF 缩略图模型启动新渲染 worker 前会在 GUI 线程 `_stop_render_worker()`，协作取消旧 worker 后调用 `_wait_thread()`；该 helper 通过 `processEvents()+短 wait` 轮询，可能造成可见停顿与重入，且不是仅退出路径。
- 结果组件的 Word/Excel 按钮（`result_view_widget.py:954-955`）直接调用 `_on_export_file()`，在保存对话框返回后同步执行 `export_result()`；与批量页共享同一高置信同步 IPC/生成/写盘问题，应统一复用 ExportWorker。
- PDF 预览打开与翻页信号直接进入 `_render_preview_page()`，其中同步调用 `backend_client.render_preview()` 并在 GUI 线程解码 PNG；方法注释也明确“同步、GUI 短暂阻塞”。后续文字层按需检测可能还有第二次同步 IPC。
- 二维码 SVG 保存直接调用 `generate_qrcode_svg_sync()` 后 `Path.write_text()`；普通 PNG/JPEG 保存也在 GUI callback 对 PIL Image 执行 `.save()`，SVG 的同步 WorkerHost RPC 风险更高，二者可一起迁入后台保存任务。
- `PdfSessionManager.open_sessions_async()` 每次启动新批量打开前调用默认 `wait=True` 的 `_cancel_open_worker()`，会在 GUI 调用线程最多等待旧 worker 3 秒；应采用 generation/迟到信号丢弃而不是 cancel 后同步等待。
- PDF 预览不仅同步 `render_preview`，当页面只有延迟文字层标记时还会紧接着同步 `detect_text_layers()`；单次翻页可能串行两次 WorkerHost IPC，风险高于注释中的单次 50–200ms。
- 截图 `start_capture()` 在 GUI 线程对每块屏幕调用两次 `screen.grabWindow(0)`：第一次构建 mapper、第二次合成虚拟桌面。Qt 抓屏本身通常需留在 GUI 线程，优先改为“一次抓取复用”，再把后续 QImage 合成/编码移到后台；不能简单把 QPixmap 搬到 worker。
- 截图复制路径同步执行全画布 `export_image()`、QPixmap→PNG 编码、临时文件写入与清理；保存路径同步 `pixmap.save()`。在 4K/多屏选区或多标注场景属于中高风险，异步边界应在 GUI 快照完成后的 detached QImage/bytes。
- `PdfSessionManager.open_session()` 本身包含同步 open + 流式逐页 load，但当前搜索未发现 UI 生产调用者；主路径使用 `open_sessions_async()`，暂不列改造项，保留为静态调用面风险。
- 对 views/widgets 的同步后端方法做了全量名称扫描：除已确认的 PDF preview、QR SVG 保存和导出路径外，单图/QR 正常识别方法虽命名 `_sync`，调用点均被 `asyncio.to_thread` 包裹，不应误报。
- PDF 缩略图替换等待上限为 500ms；重新打开 PDF 取消旧加载的等待上限为 3000ms。前者偏高频短卡顿，后者偏低频明显卡顿，应分别处理。
- 大图同步加载命中主窗口、单图页、二维码页和 PreviewWidget 的 `QPixmap(path)`；需要按实际 UI 入口合并为一个“文件读取/解码异步化”共性改造，而不是按每个调用点重复立项。
- 结果页在 GUI 线程构建 HTML，并可能对多张图片 base64 编码后 `QWebEngineView.setHtml()`；复杂 OCR 结果会放大 CPU/内存拷贝，属于中优先级响应性候选。
- 主窗口“打开图片”与预览区“选择文件”两个 slot 在 GUI 线程 `QPixmap(file_path)` 解码完成后才启动异步 OCR；单图页自己的文件按钮也有同样同步解码，统一改造应避免三条入口行为分叉。
- 批量文件列表 `add_files()` 对每个新路径用 `any(...)` 扫描现有列表，并逐行 `QTableWidget.insertRow/setItem`；大量拖入时为 O(N²)+高频 UI 更新，适合改用 path set 与 model/view 或分帧批量插入，但这不是后台线程能完全解决的问题。
- `ResultViewWidget.display_result()` 从 OCR 完成信号进入后同步遍历所有 content blocks、渲染图片/base64、拼接完整 HTML 并调用 WebEngine；可把纯数据 HTML 构建移到 worker，主线程只做结果代次校验和 `setHtml()`。
- 设置控制器仍在 GUI 方法中调用 `env_manager.detect_dependency_updates()` 以及 `get_dependency_versions()`；这些函数可能启动嵌入式 Python/读取包元数据，需视实现与触发点判为高风险，正在深入复核。
- 更新器的大 ZIP CRC、解压和握手已明确使用 `asyncio.to_thread`；其内部 `time.sleep`/`write_bytes`/`Popen` 不在 GUI 事件循环执行，不应误报。
- `SingleInstanceGuard` 的 `waitForConnected/ReadyRead/BytesWritten` 只在第二实例启动/本地 IPC 路径，且短超时；不属于当前主窗口交互异步改造重点。
- 设置页环境状态 `_refresh_env_maintenance_state()` 已把 Python/依赖/直接依赖多轮探测整体交给 `_run_cache_operation()` 后台执行，再以 generation 应用 UI；这是正确异步基线，不应因内部同步子进程命中而误报。
- 设置页“更新依赖”按钮 `_on_update_deps()` 则直接在 GUI slot 调用 `env_manager.detect_dependency_updates()`，与上述环境刷新边界不一致；若该函数逐包调用嵌入式 Python，属于高优先级修复。
- `_populate_deps_tree()` 有 snapshot 缺失时同步回退探测，但生产调用由异步刷新传入完整 snapshot；应优先消除/保护回退而非另建后台任务，避免未来调用者误用。
- 设置页 pipeline TTL、缓存状态刷新和释放操作均通过 `_run_cache_operation(QRunnable)` 执行同步 WorkerHost RPC；这些按钮已正确异步，不应列入整改。
- `env_manager.detect_dependency_updates()` 另有主窗口第 830 行调用，需要确认该路径是否已经位于依赖检测 worker 的完成数据中，还是 GUI 回调内再次同步探测；它可能与设置页主动入口构成重复问题。
- `_populate_deps_tree()` 的生产调用点只有异步 snapshot 路径，当前无需单列整改，只建议收紧接口契约。
- `detect_dependency_updates()` 调用 `get_dependency_versions(python_exe)` 获取已安装版本；主窗口在依赖检测完成的 GUI slot `_on_dependency_check_finished()` 中直接进入 `_maybe_prompt_dependency_updates()`，因此启动时仍可能在 GUI 线程再次执行嵌入式环境探测。
- 同一同步更新检测也由设置页按钮直接触发，建议抽成单一 `DependencyUpdateCheckTask`/QRunnable，返回 updates 后再决定是否展示对话框；这样同时覆盖启动自动检测和主动检测。
- 主窗口 pending_sync JSON 读取/删除仅为小型本地状态文件，通常可保持同步；真正需要移出的不是该标记访问，而是后续环境版本探测。
- 更新安装对话框虽然使用 `dialog.exec()`，安装任务本身由 worker 执行；模态性属于产品交互选择，不应与同步版本检测混为一项。
- `MainWindow.__init__()` 在 `show()` 前直接 `_try_load_cache()`；缓存文件存在且版本匹配时，`is_cache_valid()` 首次调用 `generate_machine_id()`，Windows 下串行运行 CPU/主板两个 WMIC，各 5 秒超时。重复启动的可见 splash 可能冻结，确认为高优先级启动异步候选。
- 设置页“刷新缓存”按钮直接调用 `refresh_cache()`；若进程尚未生成 machine id，同样会触发两次 WMIC。常规重复启动已缓存 machine id 时很快，因此列中优先级条件风险，可复用现有 `_run_cache_operation()`。
- 机器码生成有进程级 `_cached_machine_id`，因此 WMIC 风险主要集中在每个进程第一次需要校验/创建缓存；异步化时也需保证单飞，避免并发重复探测。
- `get_dependency_versions()` 对每个 OCR 顶层依赖先启动一次 metadata Python 子进程（15 秒超时），失败再启动 import 回退子进程；因此启动/按钮更新检测不是轻量比较，而是 N 个串行进程，必须整体移出 GUI。
- `resolve_use_gpu()` 在机器缓存缺失/失效或没有 `hardware_info.has_gpu` 时同步回退 `detect_gpu()`，会执行 `nvidia-smi`/CUDA 探测。设置页补装、更新确认和 WorkerHost 启动均有条件到达；应复用后台 GPU worker 结果，避免任何 GUI 槽触发 shell-out。
- 懒加载标签页在 `currentChanged` 回调里同步冷导入并构造整个 QWidget 树；QWidget 构造不能搬到 worker，因此应把数据/服务预热移到后台并先显示 skeleton，必要时将 UI 构造分帧。这是 P2 架构优化，不应简单包装成 QThread。
- 当前工作树已有多项用户/前序任务改动；本轮子代理未修改业务文件，新增变化仅限审计计划三文件。最终报告不将现有 dirty status 误认为本轮产出。
- 主关闭流程先发取消，再用 `ShutdownCoordinator` 把各 drain 放到 daemon 线程，并由 GUI 线程在统一 5 秒预算内等待；关闭期间可能无响应，但这是资源生命周期安全权衡，暂不列普通异步改造，若优化需设计“关闭中”状态而非直接 fire-and-forget。
- `PdfTab.closeEvent()` 单独调用默认 5 秒 `shutdown()`，但主窗口正常关闭已先统一 drain；独立页签关闭才明显阻塞。安装/切换对话框关闭的 5 秒等待同样用于回收 pip 子进程，不能简单删除。
- PDF `start_ocr()` 在创建 `_OcrRunner` 之前检查 MinerU 首次使用，并直接调用 `_ensure_mineru_models_blocking()`；后者在 GUI 线程下载数 GB 模型，仅靠 `QApplication.processEvents()` 泵事件，可能持续数分钟并产生重入，属于本次最高优先级 P0。
- 所有 PDF mutate“异步”入口在 `worker.start()` 前同步调用 `reset_cancel(session_id)`；该 RPC 经同步客户端等待 Future，意味着保存/旋转/删页等操作仍有阻塞前缀。应把 reset 作为 worker 的第一步。
- PDF OCR 启动同样在 `_OcrRunner.start()` 前同步 `reset_cancel()`；可与 MinerU preflight 一并纳入后台状态机。
- 自动摆正主体在 worker，但完成 slot `_on_deskew_all_done()` 回到 GUI 后同步 `get_model()` 刷新完整模型；这是典型“后台任务尾部回调再次阻塞主线程”，应让 worker 携带 model/diff 返回。
- PDF 预览双击编辑文字块直接在 GUI slot 调用同步 `update_block_text()`，成功后又立即同步重新渲染当前页；单次提交形成“写命令 + render（必要时再 detect）”串行阻塞链，应改为带 revision/generation 的后台串行命令队列。
- 从 PDF 文件列表移除文件时 `close_session()` 同步通知后端关闭 session，之后才更新镜像/UI；应异步关闭或乐观移除后后台 best-effort close，避免 WorkerHost 忙时卡界面。
- MinerU 模型准备内部还同步做网络源探测并 `Popen(...models_download)`、`proc.wait()`，默认超时可达约 30 分钟；P0 结论有明确原语证据，不只是推测。
- 主实例收到第二实例连接时，在 `newConnection` GUI 回调里 `waitForReadyRead(1000)` 再 `waitForBytesWritten(1000)`；异常客户端可让现有界面冻结约 2 秒，应把服务端改为 readyRead/bytesWritten 信号状态机。第二实例自身的同步短等待可保留。
- 所有同步 WorkerHost 客户端最终经 `SyncBackendClient._run_sync()` 的 `Future.result(timeout)` 阻塞调用线程；“后台有 asyncio loop”不等于调用方非阻塞。PDF command 默认 600 秒、预览渲染 120 秒、close session 60 秒，结构性风险明确。
- PDF 文件列表“移除”按钮直接调用同步 `close_session()`；保存/旋转按钮虽然标注异步，但在 manager 创建 worker 前仍有 `reset_cancel` 的同步 10 秒命令前缀。
- 因此 PDF 建议不是零散给每个按钮套线程，而是统一规定：GUI 不得直接调用同步 `PdfBackendClient`；所有命令（含 reset、编辑、预览、close、model refresh）必须进入同一后台调度/代次状态机。

## 审计结论分组

- P0：MinerU 首次模型准备必须立即移出 GUI；当前路径可能同步数分钟并以 `processEvents()` 引入重入。
- P1：建立统一 PDF 后台命令边界，覆盖 preview/detect、编辑+重渲染、close session、reset_cancel、摆正后 get_model、打开/缩略图 worker 替换等待。
- P1：将启动与设置页的依赖版本检测合并为共享后台任务；将首次机器缓存/WMIC 校验从 MainWindow 构造移出。
- P1：统一 ExportWorker，覆盖批量页当前/全部导出及结果组件 DOCX/XLSX 导出；QR SVG/PNG/JPEG 保存也应使用后台保存任务。
- P1：设置页 PowerShell 快捷方式创建异步化；GPU 选择只消费后台探测结果，不在 GUI 回退 shell-out。
- P2：截图一次抓屏复用，快照后的编码/写盘后台化；图片文件解码、复杂结果 HTML 构建、批量列表 O(N²)、单实例服务端 wait、懒加载 Tab 冷构造按遥测逐步处理。
- 保持现状：正常依赖检测、WorkerHost 启动/预加载、设置页环境/缓存 RPC、单图 OCR、批量 OCR、PDF 主体 mutate/OCR/批量导出、QR 预览/解码、在线更新的大文件操作均已有真实后台边界。
- 不直接改：模态 `dialog.exec()`、短小配置 JSON、主关闭统一有界 drain 和安装取消 wait 属交互/生命周期安全选择；若优化退出体验，应实现两阶段关闭状态机。

## 建议回归护栏

- 为每个 P0/P1 GUI 入口注入 250ms 以上的阻塞假实现，同时用 `QTimer`/工具栏拖动事件测量主事件循环延迟；目标上限建议 100–150ms。
- PDF preview/edit/open/mutate worker 必须有 generation/revision 测试，验证取消、快速翻页、重复打开和关闭后迟到结果不会回写当前 UI。
- 增加架构测试：views/widgets 的 GUI slot 不得直接调用 `SyncBackendClient` 或同步 `PdfBackendClient`；允许列表仅限明确运行于 QThread/QRunnable/to_thread 的函数。
- 导出/保存 worker 测试覆盖进度、取消、同名路径、错误提示和关闭时 drain；不可在线程中创建/操作 QPixmap/QWidget。
- 启动缓存与依赖更新任务测试覆盖 single-flight、关闭后不弹窗、开发态快速短路和 portable 慢探测时主界面可响应。

## 2026-07-20 实施计划决策

- 先建立响应性和架构护栏，再实施 P0；避免“代码看似进线程”但 GUI callback 仍同步回退的历史问题。
- PDF 同步点必须作为一个 session 命令边界治理，不能按按钮零散套线程，否则会破坏写操作顺序、取消和迟到结果语义。
- P1-B（启动/设置）、P1-C（导出）、P1-D（外部命令）文件重叠较少，可在 P0/P1-A 稳定后并行实施。
- P2 截图/图片路径需要区分 Qt 线程亲和性：QPixmap、QWidget、WebEngine 最终操作留在 GUI，纯数据转换和 I/O 才迁入 worker。
- 退出 wait 暂不直接删除；P2 若优化退出体验，必须使用两阶段关闭状态机维持资源安全。

## 2026-07-20 全量执行路由

- 用户明确要求设置目标、调用合适子代理完成全部计划并注意审核；已创建覆盖 Gate 0、P0、P1、P2、回归和独立审查的活动目标。
- 第一波按文件所有权拆为 PDF、启动/设置、导出三包，主代理只写新的架构/响应性测试并负责集成，避免并行写同一文件。
- 当前工作树已有大量用户/前序任务差异，尤其 `main_window.py`、`settings_page_controller.py`、`env_manager.py`；所有实现必须增量编辑，禁止 reset/checkout。
- P2 延后为第二波，因为它与第一波在 main_window、qrcode、result/batch 文件上重叠；顺序派发比同时写更安全。
- 独立审查将放在实现和集成测试之后，审查者不得复核自己刚完成的同一工作包。
- 测试树已有 `tests/architecture` 边界扫描体系和 `tests/conftest.py` 的 qasync/Qt 等待 helper；Gate 0 应在这些现有约定上增量扩展，不另建第二套测试框架。
- 现有单图识别测试已有通过 QTimer 验证异步期间事件循环可运行的案例，可复用其模式构造统一响应性断言。
- Gate 0 采用两层护栏：可复用的 Qt timer/in-flight 动态断言，以及针对已知 GUI entrypoint 的 AST 静态禁调用规则；同步 client 在明确 worker 内仍允许使用。
- Gate 0 初始红灯稳定捕获 34 个直接调用点，分布与审计清单一致；说明静态规则覆盖了两波目标，没有把 worker 内同步 client 一刀切禁用。
- P2 入口预扫描确认：单实例服务端等待可在独立文件先实现，但懒加载与两阶段关闭都修改主窗口；截图/大图与批量/结果优化也分别和第一波启动、导出文件所有权重叠，因此第二波必须等第一波完成并验收后再重分配。
- 第一波主审证明仅依赖包内测试不足以捕获跨入口竞态：共享依赖检查若由设置页先占用，启动请求被 single-flight 拒绝后必须显式继续启动状态机，否则 WorkerHost 永不启动；该顺序需要专门交叉测试。
- QWidget 的 `closeEvent` 不能调用即使“有界”的 `drain(1000)`；正确做法是同步设置 closing/generation、请求取消并立即返回，由全局保活或上层两阶段关闭在后台执行 drain。
- PDF 打开 worker 的异步边界必须包含 backend `start()`，不能只把 `open_session/load_stream` 放入线程；generation 丢弃已经成功的 open 结果时还必须异步关闭未知 session，避免 WorkerHost 隐形泄漏。
- 两阶段关闭可以复用现有 `ShutdownCoordinator.coordinate()` 的顺序/预算语义，但必须把 coordinate 本身移出 GUI；它内部虽逐步使用 daemon thread，调用方仍会在 `Event.wait()` 阻塞，因此不能直接留在 `closeEvent`。
- 静态门禁只覆盖已登记入口，P2-B 首轮虽然门禁归零，主审仍通过行为调用链发现 `PreviewWidget._load_image_file()` 和 `QrcodeTab._on_select_image()` 的同步 `QPixmap(path)`；最终验收必须同时做架构扫描和人工入口复核。
- 批量文件去重若用 `Path.resolve()` 会触发文件系统解析，尤其 UNC/断连盘可能阻塞 GUI；用于 UI 去重的路径键应保持纯词法规范化，不能为“更精确”重新引入 I/O。
- P2-A 的通用图像任务保活、generation 丢弃和 close-no-wait 基本边界已经建立；独立审查需重点确认快速再次截图是否会无声取消用户已确认的保存任务，以及单图页脱离主窗口使用时信号化加载是否保持兼容。
- 独立审查确认 `GenerationImageJobs` 的 latest-wins 语义适合图片加载/屏幕合成，但不适合用户已在保存对话框确认的写盘：下一次 `start_capture()` 不应取消先前保存，保存任务需要独立保活和完成/失败通知。
- QThread 的 `quit()` 不能中断重写 `run()` 中的同步 `nvidia-smi`；若 `closeEvent` 等待时间短于探测超时，又让 worker 以 widget 为 parent，既会冻结 GUI，也可能触发 `QThread: Destroyed while thread is still running`。正确边界是 request-only close、模块级保活到 finished、取消/代次校验阻止 UI 回写，上层协调器才可在后台 drain。
- SingleRecognitionTab 是可独立构造的公开 QWidget，不能把自身文件按钮和 `process_file` 语义隐式绑定到 MainWindow signal receiver。修复采用 tab 自有 generation-aware QImage loader；MainWindow 顶部入口仍可独立协调，不再重复消费 tab 信号。
- QThread 的业务 `done/all_done` 信号不等于原生 `finished`：`run()` 可能还在 finally、线程池 shutdown 或信号发射后的返回路径。worker 唯一引用、写独占门和“允许下一任务”都必须以原生 `finished` 为最终边界。
- PDF open 在 `doc_opened` 后才进入逐页 load；取消发生在此区间时，session 已被 manager 接纳，不能只 break。必须明确撤销/关闭该 session，且不能让后续 `open_done` 把半加载模型标为完整。
- 关闭 drain 面对 GUI 事件循环仍在处理迟到信号时，worker 集合不是静态的；入口单次快照不足以证明排空。应先用 shutdown gate 阻止新生产任务，迟到资源只走受统一跟踪的回收路径，并在同一绝对截止时间内稳定迭代到集合为空。

## 2026-07-20 最终验收结论

- CUDA 检测失败的实际输入包含 `CUDA UMD Version`；解析器现可识别该字段并将驱动报告的 13.3 映射到受支持的 `cu126`，本机实测不再返回 `None`。
- OCR 依赖检查、依赖更新、GPU 探测和工具栏设置写入已拆离耗时检测路径；拖动工具栏期间不再在 GUI 回调中执行同步 shell/版本探测。
- 所有异步任务都必须同时满足业务终态与原生线程终态；最终实现统一使用 generation/closing、强引用注册表和可观察 drain，关闭阶段不销毁仍在运行的 QObject/QThread。
- 大结果渲染、50k content 聚合、50k overlay 命中、批量文件插入、导出快照和图片编码已迁移为后台纯数据任务；QWidget、QPixmap、WebEngine 最终提交仍留在 GUI 线程。
- MainWindow 最终偶发竞态来自 QObject-owned 轮询 timer 之外额外注册的 Python bound-method `QTimer.singleShot`；窗口先销毁时回调会命中已删除的 C++ 对象。改为当前 GUI 事件内直接推进并同步发出最终 close 后，全量回归稳定通过。
- 最终验证：3132 passed、7 skipped；Gate 0 为 8 passed；MainWindow/依赖/关闭分组为 80 passed；Ruff、compileall、`git diff --check` 均通过。

## 2026-07-20 终审返修补充

- 大结果 latest-wins 不能只记录 `(old_text, new_text)` 并对聚合字符串 `replace(..., 1)`；重复文本会改错块。最终实现仅在唯一命中时保留原格式，存在歧义则按当前块顺序安全重建。
- ResultView 不可在 GUI 提交阶段深复制 50k 模型，也不能让 worker 无条件接纳活模型快照。最终使用 source/render generation 与后台连续稳定快照比较，只接纳稳定代次；未知对象与未知字典键一律保守判为不稳定。
- 快照失效后的 50k 复制必须是独立可取消作业；Markdown/文本复制不再计算无用 HTML，每 128 项检查取消，剪贴板只在通过 generation/token 的 GUI 完成槽写入。
- WebEngine 文档只有在 `loadFinished` 后回读 token 匹配时才标为 rendered；旧 DOM 编辑与迟到 JS copy payload 均被 document token 和 generation 拒绝。
- PDF open worker 的 cancel 与 session ownership snapshot、最终 pop/保留关闭权必须共用同一锁；barrier 回归验证竞态 session 恰好关闭一次。
- 最终发布级验证：3156 passed、7 skipped；独立终审确认无剩余 P0/P1/P2 阻塞。

## 2026-07-21 GitHub Actions 失败证据

- `v0.5.2` 的 Release 运行 `29787873143` 已成功；同一提交 `43a29aa` 的 Quality Gates 运行 `29787873103` 失败。
- 失败仅发生在 `backend` 作业；`pyside`、`contracts`、`winui` 均成功。
- 精确失败为 `tests/services/test_pdf_backend_render.py::TestRenderParallelization::test_concurrent_render_is_faster_than_serial`。
- GitHub runner 实测：serial=18.466s、parallel=18.545s、speedup=0.9957x；断言要求 `speedup > 1.0`。
- backend 其余结果为 858 passed、8 skipped；该失败带有明显的计时/runner 噪声特征，但仍需本地重复运行并审查测试意图后才能下结论。
- 本地单项命令已运行并通过：`.venv\\Scripts\\python.exe -m pytest tests/services/test_pdf_backend_render.py::TestRenderParallelization::test_concurrent_render_is_faster_than_serial -q`，耗时 53.66s；说明失败并非稳定逻辑回归。
- 该测试历史上已先后把阈值从 1.15x 降到 1.05x、再降到 1.0x；注释还记录共享 runner 真并行实测仅 1.01x。这进一步表明“严格大于 1.0”的计时断言缺乏噪声裕量。
- `v0.5.1..v0.5.2` 仍需核对是否改动渲染实现；当前搜索已确认实现中有独立文档渲染函数 `_render_page_pixels` 和容量 8 的 `_RENDER_SEMAPHORE`。

### 可证伪假设（按优先级）

1. 测试判据不稳定：重复运行会在 1.0 附近跨线，但结构路径仍使用独立文档。
2. 后端入口实际串行化：受控并发探针会显示最大并发数为 1。
3. v0.5.2 引入真实回归：版本差异会命中渲染实现或并发执行器。

### 根因与修复

- 根因是假设 1：测试使用 `serial / parallel > 1.0` 证明结构性并发，没有任何噪声裕量；GitHub 共享 runner 的 0.4% 抖动即可误报。
- 假设 3 已排除：`v0.5.1..v0.5.2` 对目标实现与测试无差异，仅 CI 新增了后续 Python 测试步骤。
- 假设 2 已排除：当前入口没有获取 `session.fitz_lock`，而是调用每次独立 `fitz.open` 的 `_render_page_pixels`，并由容量 8 的信号量限流。
- 修复将耗时比替换为 `threading.Barrier` 并发重叠探针，直接验证两个 `render_preview` 请求能同时进入栅格化函数。
- 红能力验证：临时注入 `fitz_lock` 后，新测试 6.26s 确定性失败（`BrokenBarrierError`）；撤销注入后 0.70s 通过。临时产品代码改动已撤销。
- 目标测试文件全量已通过：3 passed in 8.61s；Ruff lint 通过，格式化后 format check 通过。
- 仓库发布约定：先提交普通修复并推送 main，通过 Quality Gates 后用 `scripts/bump_version.py patch --no-edit --yes` 统一升级 workspace 版本、CHANGELOG、uv.lock，创建 `release: vX.Y.Z` 提交和标签并推送触发 Release。
- 下一补丁版本应为 `0.5.3`；当前 `v0.5.2` Release 已成功，不应覆写既有标签/资产。
- 与失败 Actions 相同的 backend 门禁已本地通过：862 passed、5 skipped in 32.96s（本机可用 CUDA 条件使跳过数与 runner 略有不同）。

### 第二个远端失败

- 修复提交 `327ba9a` 的 Quality Gates 运行 `29788869845` 中，原 backend 测试步骤已经成功；新失败发生于 `Run remaining Python unit tests`。
- 精确失败为 `tests/core/test_pipeline_table.py` 的 3 个 `TestCheckTableDeps` 用例，均在测试体直接 `import paddlex.utils.deps` 时因 build-shell 未安装可选 `paddlex` 而失败。
- 这些用例本意是 mock PaddleX 依赖探测函数，却先依赖真实可选包，属于测试隔离缺陷；CI 轻量依赖策略明确不应安装大型 OCR 运行时。
- 修复应在测试中注入最小假的 `paddlex.utils.deps` 模块，保留对 `_check_table_deps` 导入/判定路径的真实覆盖。
- 已建立无 PaddleX 复现脚本：修复前 3 failed、1 passed；测试注入假模块后 4 passed in 0.11s。临时复现脚本随后删除。
- CI 对应的完整 remaining Python 集合本地通过：1157 passed in 102.87s。
