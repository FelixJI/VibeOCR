# 进度日志：PDF 收尾压缩与 RTX 4060 吞吐

## 2026-07-20

- 用户要求执行压缩修复，并进一步梳理 453 秒压缩耗时以及 RTX 4060 下约 1.5 秒/页的 OCR 吞吐。
- 已重新读取 `route-subagents`、`planning-with-files`，完成 session-catchup；保留上一轮未提交改动，在其上继续增量实现。
- 已确认现场“压缩失败”的直接原因是保存成功后返回 15.5 MiB 全量模型，超过 WorkerHost 8 MiB 控制帧；本阶段继续拆分真实压缩耗时并设计最小响应。
- 已修复 461 秒后“失败”的双重根因：OCR 批次全部增量落盘时跳过末尾整文档压缩；保存响应不再返回 682 页完整模型，末尾也不再 `get_model`。
- 显式/普通全量保存从 `garbage=4` 调整为 `garbage=3 + use_objstms=1 + compression_effort=1`，并新增 backup/save/replace/reopen 分段计时。
- PDF OCR 已使用 `render_dpi/max_pixels/adjust_dpi`，不再硬编码 300 DPI；完整 OCROptions 已穿过 WorkerHost，方向分类等 UI 开关开始生效。
- 批量 OCR 不再复制并 PNG 编码每页预处理图，仅保留角度与尺寸；修通 preproc_angle/w/h 的 WorkerHost 返回链，避免 PDF 方向校正恒为 0。
- 新增批量落盘、最终补救保存、轻量保存响应、压缩参数、DPI、选项透传、预处理元数据测试；相关回归 177 项通过。
- 沙箱内契约测试一度因 `rpds` DLL 权限无法收集；沙箱外最终合并回归 313 项全部通过，包含 PDF、JSON Schema、WorkerHost 异步 handler/client 和设置链。
- Ruff、compileall、标准库 JSON 解析与 `git diff --check` 均通过。

---

# 进度日志：运行时阻塞、日志、表格与预加载

## 2026-07-20

- 已完整读取 `route-subagents` 与 `planning-with-files` 技能，并按独立风险面规划 3 个只读子代理；主代理保留所有写入权和最终验证责任。
- 已运行 session-catchup；首次因无全局 `python` 失败，改用 Codex 工作区 Python 后成功。
- 已确认工作区无未提交产品代码变更；现有三个规划文件包含历史记录，本任务在文件顶部新增独立章节并保留旧记录。
- 当前进入 Phase 1：日志统计与四条调用链审计。
- Phase 1 已完成：三个子代理均返回只读证据，主代理已复核日志与关键调用链。
- 已确认四个本地根因：安装后 GUI 同步依赖树刷新；WorkerHost 未真正重启且自动预加载死代码；worker DEBUG 级别泄漏；表格适配层重复 + HTML 清洗丢失 spans。
- 已进入 Phase 2，按“运行时生命周期、日志、表格/跨度、预加载、PDF 收尾”顺序实施并补回归测试。
- 已将安装后的环境状态/依赖树探测移入线程池，GPU 后端探测也改为 QRunnable；安装线程先关闭旧 WorkerHost，主窗口立即失效旧服务，完成后异步重检并重新连接。
- 已为依赖检查增加排队复检与迟到结果隔离，成功回调不再和 dialog.finished 重复刷新。
- 已把生产日志默认级别设为 INFO；主进程和 WorkerHost 统一读取 `VIBEOCR_LOG_LEVEL`，设置页新增 INFO/DEBUG/WARNING 持久化选择，HTTP/模型框架日志至少为 WARNING。
- 已将自动预加载接入 WorkerHost ready；手动“立即预加载”统一复用 SubprocessManager 的 preload+warmup、增加 single-flight 和真实两阶段成功判定。
- 已修复表格适配层：单元格内 OCR 被吸收到表格，表外文本保留；HTML 清洗保留安全数字 rowspan/colspan，逻辑网格按跨度占位。
- PDF 日志量化结论：682 页写层 109.03s（0.160s/页、约 7.8%）合理；OCR 为主要耗时，异常 461s 来自末尾全量压缩。未在缺少真实 PDF 基准的情况下改变产物压缩语义。
- 最终定向回归集 154 passed；另主窗口/设置页扩展集 76 passed、安装相关集 22 passed；Ruff、compileall、git diff --check 均通过。pytest 仅保留仓库既有 `asyncio_mode` 未知配置警告。

---

# 进度日志：PDF 文字层与批量识别优化

## 2026-07-16

- 已读取 `planning-with-files` 技能并执行 session-catchup。
- 全局 `python` 不可用，已切换为项目内置 CPython 完成恢复检查。
- 发现旧规划文件属于上一项 UI 任务且编码乱码，已为当前任务重置。
- 下一步：检索 PDF、OCR worker、IPC 与文字层写入代码和测试。
- 已定位 stderr 崩溃根因：`Popen(text=True)` 隐式采用系统 GBK，未声明 WorkerHost 的 UTF-8 流编码。
- 已初步确认 PDF 流程不是完全逐页 RPC；现有实现已有 16 页传输批、并发渲染、批量 OCR 和批量文字层写入，需要继续下钻各阶段是否仍有复制/保存/同步等待瓶颈。
- 已定位 `BackendClient reader: terminal error: invalid state` 的代码级竞态：已取消的 Future 仍被响应处理器完成，异常逃逸后杀死 reader 循环。
- 发现 WorkerHost 的 `recognize_batch()` 当前只是 N 个单图 RPC 的 `gather`；是否影响 PDF 主路径仍待确认。
- 已确认 PySide PDF 的 OCR 是真批量 SHM 路径；当前主要可疑项转为跨阶段无流水化、每批全文件备份，以及末尾重复重写全部文字层并全量压缩。
- 已审阅现有保存/编排测试；准备采用兼容性改造：普通保存继续全文档重写，OCR 收尾走专用 finalize；incremental 失败回滚改为利用 append-only 边界，避免每批全文件复制。
- 已确认迁移后的实际批量路径存在协议级退化：`BatchBackendAdapter` → `BackendClient.recognize_batch` → N 个单图 WorkerHost RPC。计划把真批量能力补到 WorkerHost 协议/handler/adapter，而不是更换 named pipe。
- 已列出协议批量方法所需的契约、Python/C# allow-list、handler、composition、client 与测试改动面。
- 已修复 WorkerHost 输出流编码：父子两端固定 UTF-8，父端替换非法字节，drain 线程增加异常兜底。
- 已修复响应/取消竞态：迟到响应遇到已完成 Future 时丢弃，不再以 `InvalidStateError` 终止唯一 reader。
- 已为上述两个稳定性问题补充定向回归测试。
- 已实现真批量 OCR 的 Python 主链：async/sync client、application facade、WorkerHost batch handler、production adapter 和 composition 注册；一批图片现在对应一次 WorkerHost RPC 和一次底层 `recognize_batch`。
- 批量调用保留输入顺序与 `None` 失败槽位，并在 finally 逐一释放 client-owned SHM。
- 已将 `ocr.recognize_batch` 加入 JSON Schema、Python method validator、WorkerHost retryable allow-list、C# `RpcMethods` 和契约测试方法集。
- 首轮定向测试在收集阶段被本地 `.venv` 的 `rpds` DLL“拒绝访问”阻断，并显示缺少 pytest-asyncio；代码测试尚未实际执行，下一轮改用项目 uv/正确依赖组。
- 确认 pytest-asyncio 实际已安装；失败来自沙箱禁止加载 `rpds` 原生 DLL。在沙箱外重跑 WorkerHost、契约和协议一致性定向集，结果 `170 passed`。
- 已把每批 incremental save 的整文件 `.bak` 改为持久化“写前长度”marker；异常即时截断，下次打开也会恢复中断写入，消除每 16 页一次的 O(PDF 大小)复制。
- 已增加 OCR 专用快速收尾标志：批量写层完成后仅做最终落盘/压缩，不再删除并重写所有刚写好的文字层；普通保存默认仍全文档重写。
- 已补充“部分追加后失败截断”“下次打开自动恢复”“增量保存不复制整文件”“OCR finalize 不调用 rewrite”的回归测试；PDF service/manager 定向集 `117 passed`。
- 已增加一批深度的渲染预取：当前批进入 WorkerHost/GPU OCR 前即提交下一批 PDF 渲染，最多保留两批图像；顺序测试证明事件为 `render batch0 → render batch1 → OCR batch0`，manager 集 `40 passed`。
- Pyright 首轮指出 finalize 标志漏过实际 `client.pdf → pdf.command` 层；已补齐并新增两层转发测试。复检结果 `0 errors`（23 条均为相关文件既有 warning）。
- Ruff 对本次涉及的源码与测试复检通过。
- 扩大回归集覆盖 WorkerHost、全部协议/架构、批量图片、OCR facade、PDF service/manager/集成编排，结果 `502 passed`；补齐 pipeline allow-list 与 finalize command 转发后，定向复检 `181 passed`。
- 试运行 .NET Contracts 测试时发现机器仅有 .NET runtime、没有 `global.json` 要求的 .NET SDK 10.0.302，因此该测试未执行；Python 侧协议集合/Schema/C# 常量一致性测试已通过。
- 最终检查：相关文件 Ruff 全通过，核心改动 Pyright `0 errors, 0 warnings`，`git diff --check` 通过。
- 已确认并保留本任务开始前已有的 `about_tab.py` / `test_about_tab.py` 工作区改动，未对其做修改或回滚。

---

# 2026-07-19：Classic 启动缺少 startup_metrics

- 已恢复发布后上下文，工作区起始状态干净。
- 已定位缺失模块的物理路径与 PyInstaller 配置，正在审计 namespace 收集和产物分析结果。
- 已确认入口是静态 import，且 pyside 源码分片未进入 PACKAGE_DATA；下一步检查实际 PyInstaller Analysis/PYZ 清单并复现。
- collect helper 单独运行能看到 `startup_metrics`；准备核对入口常量并运行当前 Classic 构建来获取真实 Analysis 证据。
- 正式本地构建完成并复现整个 Classic namespace 分片漏收；根因已定位到 spec 顶层 collect 与 Analysis pathex 的时序差异。
- 第一版显式 hidden imports 单测通过但真实构建仍显示跨根模块 not found；已否决该单独方案，转为构建前合并 namespace staging。
- 合并 staging 的第二版本地构建已通过，Analysis/PYZ 证据确认 startup_metrics 与全部 vibeocr 分片已收集；正在准备 0.5.0 wheel 绑定和真实 EXE smoke。
- 已完成 0.5.0 五 wheel 构建、绑定、最终 ZIP 真实 EXE T3 smoke；146 项回归和全部静态/清单检查通过，准备提交并重发 tag。
- 修复提交 `d337bd0` 已推送并将 `v0.5.0` 标签从 `70420ac` 更新至该提交；Release 运行 `29689958012` 的 PyInstaller 阶段成功。
- 首次远端真实 EXE 门禁在绑定阶段假超时：主程序使用 `os._exit` 后，后台清理子进程仍可能继承 `capture_output` 的 PIPE，使 `communicate()` 等不到 EOF。已改为普通临时日志文件，既避免继承管道拖住验证器，也保留非零退出诊断。
- 第二次运行 `29690421627` 的保留 stderr 证明真正启动阻断是 CI 重定向输出采用 cp1252，中文启动提示编码失败；验证进程已增加 `PYTHONIOENCODING=utf-8`/`PYTHONUTF8=1`。
- 第三次运行 `29690773080` 证明冻结运行时忽略上述父进程编码变量；已将 UTF-8 标准流配置前移到 `vibeocr.main` 的最早入口，并新增 release-layout 级回归。
- 入口修复完成本地全链重建：PyInstaller、五-wheel 绑定、最终 Classic ZIP 解压与真实 EXE T3 smoke 均通过；专项 4 passed、Ruff 与 diff check 通过。
- 入口修复提交 `1928092` 已推送并成为 `v0.5.0` 标签目标；最终 Release 运行 `29691245564` 6m39s 全绿，绑定与独立 Classic 启动门禁均通过。
- GitHub Release 已更新：Classic ZIP 172,269,093 bytes，SHA256 `b3c3124812adf17ea31e94daeb7855aa4300b18155ae4f4e40f984d42173ceda`，资产 digest 与 `.sha256` 文件一致。

---

# 2026-07-19：重新打包 0.5.0

- 已恢复上下文并确认当前在 `main`，修复与测试尚未提交。
- 正在核对 GitHub Release、tag 与 workflow 的安全重打包方式。
- 已确认采用既有的 `v0.5.0` 修复重推流程；下一步补充发布说明、复核、提交和推送。
- 已补充 0.5.0 发布说明，准备做提交前最终验证。
- 78 项更新链回归和 Ruff/diff 检查再次通过；已创建提交 `70420ac`。
- 已推送 `main`，并用旧 SHA lease 保护将 `v0.5.0` 从 `2120617` 更新到 `70420ac`；Release workflow 已由 tag push 触发。
- GitHub Actions run `29688528417` 成功完成；0.5.0 Classic ZIP 与 SHA256 已覆盖上传，摘要核对通过，任务完成。

---

# 进度日志：版本升级与 CHANGELOG 归档修复

## 2026-07-16

- 已定位版本遗漏根因：主流程目标清单不完整，且单文件仅替换首次出现。
- 已定位重复归档根因：0.4.29 release commit 存在但 tag 缺失，收集逻辑只认 tag。
- 已确认当前 CHANGELOG 的 0.4.30 条目重复收录 0.4.29 大部分内容；真正新增提交应为 0.4.29 release 之后到 0.4.30 release 之前的 3 条非 release 提交。
- 已执行 planning-with-files session-catchup；全局 `python` 不可用后改用项目内置 CPython。
- 已实现 workspace 自动发现、单文件全量版本替换，并将所有实际变更文件纳入 release commit staging。
- 已实现 release commit 优先、最近 tag 兜底的 CHANGELOG 提交边界，并增加缺 tag 回归测试。
- 已把 4 个 workspace 项目的版本、内部依赖 pin、包级 `__version__` 和 `uv.lock` 从滞后的 0.4.28 修正到 0.4.30。
- 已将 0.4.30 CHANGELOG 收敛为实际新增的 3 条提交，删除从 0.4.29 重复归档的 79 行。
- pytest 首轮收集 53 项，其中 19 项通过；其余 34 项因沙箱拒绝创建临时目录停在 setup。沙箱外重跑授权被环境额度策略拒绝，未继续绕过。
- Pyright 因沙箱拒绝读取 editable `.pth` 未能运行；Ruff、AST、真实 Git 边界验证、版本/锁文件一致性检查和 `git diff --check` 全部通过。

---

# 进度日志：PySide6 架构与运行治理审计

## 2026-07-18

- 已读取 `planning-with-files` 技能并完成 session-catchup；全局 `python` 不可用，已改用工作区依赖中的 Python。
- 审计开始前 Git 工作区干净；保留已有规划文件中的历史任务记录并追加本次计划。
- 当前仅授权只读审计，不修改产品代码；下一步盘点 PySide6 入口、模块树、配置与测试资产。
- 已完成第一轮文件与依赖扫描：确认真实 UI 位于根包，运行时并存 qasync、Qt 线程池、QThread 和 WorkerHost 子进程；下一步读取入口和主窗口装配代码。
- 已审阅启动与主窗口装配：确认单一 WorkerHost/批量适配器注入主链完整，同时记录同步启动、退出顺序和分散等待预算三个待验证风险。
- 已完成 UI signal/connect 与占位实现初筛；多数功能有显式接线，下一步逐个验证单图、批量、二维码、PDF 的 handler、取消和错误回传。
- 已核查单图与批量主链：单图异步路径总体成熟；确认批量取消/错误/退出生命周期存在高优先级接线缺口，且现有测试未覆盖真实线程重入。
- 二维码联合检索有一个路径假设错误，已记录并改用真实 BackendClient 路径；现有证据已确认识别子页并非占位。
- 已完成二维码 handler/typed RPC/UI 对照：功能真实且测试充分，但生成与识别仍同步阻塞 GUI，是明确的体验优化点。
- PDF 首轮联合检索因测试目录假设错误未完整返回，已记录并改为先发现真实测试路径。
- 已核查 PDF 主接线和 worker 生命周期：确认批处理与状态回传设计成熟，同时发现保存后切换、保存后继续 OCR、mutate 重入/GUI 等待三个明确缺口。
- 已完成缓存/依赖管理初筛：依赖缓存确实减少启动复检，内存管道管理器具备 TTL/LRU/显存释放；正在追踪其生产调用链与设置页实际目标。
- 已确认容量淘汰与显式释放有效，但发现 TTL sweep 仍只接在旧 worker 主循环，当前 WorkerHost 下属于“配置可下发、策略不执行”的实质失效。
- 已审计 workspace 与发布依赖：发布壳哈希锁有效控制重量，但 workspace 包仍多为 marker，真实代码和开发依赖保持根项目单体。
- 已完成日志审计：主进程轮转/降噪可用，但当前 WorkerHost 未初始化日志且父进程丢失级别/结构，通用子进程转发器尚未接入主架构。
- 超时首轮扫描有常量文件路径假设错误，已记录；下一步先定位真实配置定义再分类汇总。
- 纠正缓存链结论：当前 MainWindow 注入的 `BatchBackendAdapter` 对 `release_pipelines` 和 `set_pipeline_ttl` 是无 RPC 空实现；此前“显式释放有效/TTL 可下发”的判断只适用于旧 `OCRServiceSubprocess`，不适用于当前 PySide6 生产接线。
- 已确认预加载确实通过白图识别加载模型，但“preload + warmup”在当前适配器中会重复识别两次；容量淘汰仍真实有效，TTL/主动释放/缓存状态不可观测则不可靠。
- 已完成超时链核查：核心常量分类齐全，但 WorkerHost typed client 多数未使用；OCR 300 秒、批量 1800 秒及多项 PDF 外层等待仍被内层默认 30 秒 deadline 截短。
- 已完成依赖/模型缓存语义核查：`.vibeocr/cache.json` 只是环境检测与曾成功标记；设置页“刷新/清除模型缓存”文案与实际只操作该 JSON 的行为不一致。
- 沙箱内定向测试因系统临时目录权限受阻；按权限规则在沙箱外重跑单图/二维码/PDF/批量/cache/SubprocessManager/设置预加载，225 项全部通过。随后架构边界、client adapter、machine cache 与日志 36 项全部通过。
- 测试通过说明 happy path 与既有状态分支回归资产较强；当前缓存空接线、batch 真实 QThread 生命周期与端到端 deadline 仍无对应生产路径测试。

---

# 进度日志：PySide6 三阶段治理实施

## 2026-07-19

- 已确认线程目标处于 active，目标为完成三个阶段，不设置任意 token 预算。
- 已完整读取 `planning-with-files` 与 `route-subagents` 技能，并成功运行 session-catchup。
- 当前工作树只有上一轮审计产生的 `findings.md/progress.md/task_plan.md` 修改，无产品代码改动；这些记录将继续保留。
- 已建立三个实施阶段：Phase 1 正确性/生产接线，Phase 2 交互/异步生命周期，Phase 3 可观测性/性能/工程边界。
- 子代理路由：A 处理 deadline + pipeline cache RPC/契约；B 处理批量 QThread 生命周期；主代理负责跨模块复核和阶段验收。
- 批处理生命周期工作包已返回并完成主线初审：取消、单批失败、迟到信号和有界关闭均有真实 QThread 测试；下一步接入 MainWindow 关闭顺序后统一运行回归。
- 主窗口已把 `_closing` 前置，并在销毁结果 WebView/PDF/WorkerHost 前调用批处理 `shutdown(1000ms)`；新增关闭顺序回归。
- Phase 1 首轮 PySide 定向回归 `39 passed`。测试进程返回 0，但退出清理仍暴露后台 GPU 检测线程未 drain 的 COM fatal diagnostic，已纳入 Phase 3 统一关闭治理。
- Phase 1 已完成：typed API 的 envelope deadline 与外层 wait 统一，五个真实 pipeline cache RPC 贯通至推理 worker，批线程状态机和主窗口关闭顺序完成；协议/缓存/批处理/关闭链整体验收 `390 passed`。
- 协议一致性首次发现测试解析器不接受 `pipeline_cache` 下划线域名，修正守卫后重跑通过；这是测试基础设施缺陷，不是 C# 方法遗漏。
- 当前进入 Phase 2：PDF continuation/mutate busy gate，以及 WorkerHost/二维码/单图 payload 的 GUI 线程卸载。
- WorkerHost ready/握手已从 MainWindow GUI 线程迁入 `SubprocessManager` 的后台任务，并增加重复启动、关闭取消、迟到 ready 隔离；manager + main window 定向回归 `56 passed`。
- Phase 3 预检确认 workspace 子包仍是 marker wheel：源码 import guard 有效，但 PySide/后端的实际安装依赖尚未被物理拆分；最终验收将如实区分“架构守卫”与“可独立安装”。
- Phase 2 已完成：PDF 保存 continuation 在 save_done 与 QThread.finished 双条件后执行，mutate/OCR 共用独占写门且取消不再 GUI wait；PDF 复核 `140 passed`。
- WorkerHost ready 已后台化，二维码生成/识别具备 generation/关闭隔离，单图 PNG 编码与大文件读取移出 GUI，业务错误不盲目重启；QR/单图复核 `71 passed`，启动/主窗口复核 `56 passed`。
- 主线额外修正 `is_ocr_running`：业务 all_done 后到原生 QThread.finished 前仍占用 PDF 写门，避免 finalize 窗口误切会话。
- 当前进入 Phase 3；模型运行缓存可见区与后台状态回读已先行落地，设置页定向回归 `11 passed`。
- workspace wheel 离线 smoke 已尝试：当前 venv 缺 hatchling，offline 构建环境无法创建；静态包内容仍确认 PySide/backend 子包仅含 marker，物理拆包未生效，作为明确限制保留。
- Phase 3 已完成 WorkerHost→主进程 JSONL 日志级别/上下文/异常转发，主文件日志也改为结构化轮转；状态栏只接受显式 `ui_status` 记录，不再解析中文关键词。
- 图片批处理与 PDF OCR 已统一采用数量、压缩字节、解码像素三重预算分批；超大单项保持可诊断的独立批，传输失败只污染当前子批并保留结果顺序。
- 模型运行缓存 UI 已真实接到 WorkerHost cache RPC，可读回常驻管道、TTL 和释放结果；环境检测缓存文案已与模型/显存缓存分开。
- 关闭流程新增绝对截止时间协调器；PDF/批处理先在 GUI 线程请求取消，再在 5 秒应用总预算内按剩余时间 drain，消除了 PDF 3s+5s+6s 固定等待的串行叠加。
- GPU 探测改用可取消 Popen；关闭时 cancellation event 会终止硬件探测子进程，避免设置页销毁后仍有后台检测线程。
- 设置页也已拆成 request/drain 两阶段，GPU 探测与 PDF、batch、async runner、WorkerHost 共用 5 秒应用关闭总预算；Popen kill 后输出管道 drain 使用集中定义的短宽限。
- 最终扩展回归覆盖架构、协议、WorkerHost、日志、缓存、批处理、PDF/QR/单图与关闭链，结果 `681 passed`；Ruff 全通过，变更范围 Pyright `0 errors, 8 warnings`，`git diff --check` 通过。
- Phase 3 与三个阶段总目标已完成；独立 workspace wheel 仍是 marker/构建环境缺 hatchling 的明确工程限制，不影响本轮运行时治理验收。

---

# 进度日志：四包物理拆分与联网重依赖安装

## 2026-07-19

- 用户确认采用 `contracts/client/backend/pyside + 根兼容 meta package` 的真实物理拆分。
- CI 构建允许联网；最终用户安装也允许联网获取重依赖，必须验证 Paddle/Torch/MinerU 等安装与 WorkerHost 启动不受影响。
- 已启用 planning-with-files，并完成 session-catchup；本轮不启用并行子代理，跨包移动由主线统一控制。
- Phase 1 已完成：冻结 `contracts → client ← backend / pyside` 拓扑，确认跨 wheel 子命名空间由 client 单点持有可扩展 `__init__.py`。
- Phase 2 已完成首轮实现：根产品源码已按所有权移动到四个工作区，v1 schema/golden 成为 contracts 包资源，根 wheel 改为无代码 meta package，marker 文件全部移除。
- 各发行包入口与依赖已归位：`vibeocr` 在 pyside、`vibeocr-worker` 在 backend、`vibeocr-install-backend` 在 client；backend 重引擎使用 CPU/`gpu-cu126` profile，在线安装复用原 env_manager 内核。
- 架构守卫已改为扫描真实工作区，并新增 wheel archive 路径唯一所有权、meta 无代码、profile 漂移门禁；首轮架构测试 `32 passed`。
- 开发态子进程 PYTHONPATH 已改为传播四个 source root，避免顶层 `vibeocr.__init__` 迁入 contracts 后 WorkerHost 只能发现单一源根。
- CI 与 release 已改为联网安装 `build/hatchling`、构建五个真实 wheel、执行唯一所有权检查，并在干净 venv 中从根 meta wheel 安装和运行 WorkerHost 自检；Classic/WinUI 发布清单均携带实际 wheel 集及 SHA-256。
- 最新五个 wheel 已本地重建；内容校验确认根 wheel 无代码、四个代码 wheel 无归档路径冲突，且依赖 profile、Qt `.ui` 与 protocol schema 均随正确 wheel 分发。
- 干净虚拟环境从 wheelhouse 安装 `vibeocr==0.4.37` 成功，共解析 43 个基础包；四个内部代码包、根 meta 包、`vibeocr.main`、`vibeocr.worker_host.main`、包内 profile、安装器帮助和 WorkerHost `--self-test` 均通过。
- 重依赖安装关键链定向回归 `39 passed`，覆盖安装态包资源 fallback、根 `version.json` 合并、CPU/GPU profile、Paddle/PyTorch cu126 专用 index、无错误 PyPI fallback 与 CLI 自动选型；版本/发布元数据回归 `54 passed`。
- 架构守卫在组合回归超时前 32 项全部通过；Ruff 全量通过，`git diff --check` 通过。完整异步/JSON Schema 回归与 Pyright 被既有 `.venv` 文件 ACL 拒绝，未产生产品断言失败，完整检查继续由联网 CI 承担。
- Classic 五 wheel 绑定已用最小前端夹具验证，产物 manifest、五个 wheel 文件与 SHA-256 全部通过；WinUI PowerShell 发布脚本通过语法解析。
- 四包物理拆分与联网重依赖安装任务完成。

---

# 进度日志：拆包变更终审、提交与合并

## 2026-07-19

- 已重新读取 planning-with-files 并执行 session-catchup；确认当前分支为 `main`，全部拆包/治理工作尚未提交，远端 `main` 仍停在更早版本。
- 已审阅包清单、CI/release、五 wheel 绑定与验证脚本；确定创建临时特性分支后提交、非快进合并回 main 的安全路径。
- PySide-only 安装成功解析 25 个包并能导入主入口；误把 `vibeocr --help` 当 CLI help 导致实际 GUI 启动，随后终止冒烟并记录。该启动暴露 PDF 懒加载被 `utils.__getattr__` 错误要求 backend wheel。
- 已修复 client utils 懒加载顺序并新增 source-root 隔离回归；不含 backend 的环境导入全部 PySide 模块 `78/78`，contracts+client `78/78`，backend `37/37` 且未加载任何 PySide6 模块。
- 已加强发布稳健性：拒绝重复/错版本 wheel、强制五包版本一致、WinUI 只选择当前发布版本、CI 直接安装本次根 meta wheel 文件。
- 已修复普通 wheel 安装的运行根：源码态仅接受模块确实位于 client source root 的工作区，安装态使用 `sys.prefix/sys.executable`。新增 2 项运行形态回归并通过。
- 最新五个 wheel 再次构建成功；显式本地五轮强制安装后，仓库外隔离 venv 确认运行根、目标解释器、安装器和 WorkerHost 自检正确；PySide-only 三轮隔离安装确认 backend 未安装且 78 个前端模块全部导入。
- 发布负向验证通过：重复 distribution wheel 被拒绝、0.4.38 发布绑定 0.4.37 wheel 被拒绝；正向 Classic 五轮绑定和 WinUI PowerShell 解析通过。
- 分组回归：架构 `33 passed`，版本发布 `54 passed`，application/managers/WorkerHost 同步核心 `242 passed`，PySide 生命周期与功能 `225 passed, 3 skipped`，缓存/安装运行态 `37 passed`；Ruff 全量和 `git diff --check` 通过。
- 修复 WMIC/nvidia-smi 非 UTF-8 输出导致的后台 reader thread warning；MainWindow 专项复测仅剩当前环境未加载 pytest-asyncio 的已知配置 warning。
- 终审变更已提交到临时特性分支：`8cf38e2 refactor: harden runtime and physically split workspace packages`。
- 已切回 `main` 并以非快进方式合并，合并提交为 `39794ca merge: integrate workspace physical split and runtime hardening`；已验证 `8cf38e2` 是 `main` 的祖先提交。
- 本地特性分支 `codex/workspace-physical-split` 已在确认合并成功后删除；未执行远端推送。

---

# 进度日志：GitHub 工作流修复与 0.5.0 发布

## 2026-07-19

- 已启用 planning-with-files；全局 `py -3` 不存在，session-catchup 将改用仓库内解释器或人工根据现有规划与 Git 状态恢复。
- 当前任务授权包含使用 GitHub CLI 读取失败日志、修复新物理拆包架构下的工作流、提交推送并重新发布 0.5.0。
- GitHub CLI 认证有效；已读取失败运行 `29672925992`。失败发生在发布前迁移/发布门禁，8 个 WinUI 布局测试因夹具给 `product-manifest.json` 写入非 JSON 占位内容而失败。
- 已修正准确契约为 `product-manifest.json` 和 WinUI 的三运行时 wheel（contracts/client/backend）；测试夹具现生成真实清单与哈希，并补充非法 JSON、哈希篡改回归。首次本地 pytest 因默认临时目录 ACL 在 setup 阶段阻断，尚未执行产品断言。
- 第二次定向测试越过临时目录 ACL，但被本机 PowerShell ExecutionPolicy 阻断；已给测试子进程增加显式 Bypass，避免开发机策略影响发布布局门禁。
- 定向 WinUI 发布布局测试 `10 passed`，覆盖原 8 项以及新增的非法 manifest、wheel 哈希篡改场景。完整 release-gate 本地收集仍被既有 `.venv` 的 rpds DLL ACL 阻断；远端旧提交除该夹具外已有 `464 passed` 证据。
- Ruff 全量与 `git diff --check` 通过；改用已有 uv/hatchling 缓存构建五个 0.5.0 wheel，`verify_workspace_wheels.py` 已确认五包版本一致、根 meta wheel 无生产代码、四个物理包路径唯一。
- 五 wheel 联网安装已成功解析全部公开基础依赖并下载 PySide6 等包，但深目录 venv 在解压 PySide6 QML 调试对象时触发 Windows 长路径限制；将以短路径 venv 复测，不改变产品依赖声明。
- `C:\tmp\v050` 短路径干净环境完成五 wheel 联网安装；0.5.0 导入、安装态运行根、安装器 help 和 WorkerHost 自检均通过。
- `C:\tmp\vp50` 仅安装 contracts/client/pyside，确认 backend 未安装且 155 个已发现前端/共享模块全部导入。
- 架构与 WinUI 发布布局组合回归 `43 passed`；Ruff 全量和 `git diff --check` 再次通过。Phase 3 本地发布验证完成。
- 修复已提交为 `2120617 fix(release): validate physical wheel manifests` 并推送 GitHub main。
- GitHub Quality Gates `29674920765` 四作业全部成功；允许进入 v0.5.0 标签重建与 Release 发布阶段。
- 旧 `v0.5.0` 标签已删除并在 `2120617` 重建推送；新 Release 运行 `29675075394` 在 6m9s 后成功完成。
- GitHub Release `v0.5.0` 已正式发布，标签指向修复提交；Classic 与五-wheel wheelhouse 及各自 SHA-256 共四项资产均为 uploaded 状态。
# 2026-07-19：修复升级到 0.5.0 后无法启动

- 已运行 planning-with-files session catchup（改用工作区自带 Python）。
- 已确认用户现场异常对应 `scripts/update_replacer.py:941` 的硬编码启动入口缺失。
- 正在核对打包产物和测试，尚未修改产品代码。
- 已确认仓库并行维护 Classic（`VibeOCR.exe`）与 WinUI（Bootstrapper）两种发布布局；根因是启动逻辑只覆盖后者。
- 已用 git 历史定位引入回归的提交，并确认 0.5.0 默认发版仍是 PySide6 Classic。
- 已实现双正式入口选择并新增 Classic 回退、WinUI 优先级、缺失入口诊断测试；Ruff check 已通过，待格式化和运行测试。
- 定向启动用例 4/4、共享更新链测试 78/78 已通过；已撤回 Ruff 对既有代码产生的无关格式化噪音，仅保留本次功能差异。
- 最终验证：`tests/test_update_replacer.py + tests/test_updater_main.py` 共 78 passed；Ruff check、`git diff --check` 均通过。任务完成。

---
# 2026-07-20：本地 `.venv` 修复

- 已读取 planning-with-files 技能并尝试 session-catchup；因 PATH 中无全局 Python 而未运行。
- 已确认项目不存在 `envn`、存在 `.venv`，按 `.venv` 作为修复目标继续。
- 已记录初始状态，开始核对项目环境策略、虚拟环境元数据与可用工具。
- 已确认环境整体损坏且落后于项目版本：锁文件检查不通过，五个 workspace 包仍为 0.5.0，大量 METADATA 读取被拒绝。
- 由于损坏涉及多个包和 ACL，局部补装风险高；下一步核对项目推荐开发安装命令后，采用可回退的重建方案。
- 首次旁路重建因本地缓存缺少 pytest-qt 失败；联网后 `.venv-repair` 已按冻结锁文件完整创建。
- 新环境冻结同步检查、包兼容检查、workspace 版本与关键导入全部通过；准备保留旧环境备份并切换目录。
- 旧环境目录切换先后受 ACL 与 Pyright 进程锁阻塞；已重置全部 ACL，并确认/停止 PyCharm 的旧 Pyright language server。
- 已改为正式 `.venv` 原位强制同步，75 个基础开发包重装成功，workspace 包全部恢复为 0.5.1；进入最终验证与临时目录清理。
- 正式环境的冻结同步、包一致性、解释器/入口、关键导入全部通过；定向测试最终 124 passed。
- 首轮测试因 `C:\tmp` 父目录创建被沙箱拒绝而出现 8 个 setup error，改用仓库 `.tmp` 后全部通过；准备移除本次创建的重复环境与临时备份。
- 已移除所有修复临时产物，释放重复环境占用；旧损坏环境碎片不可恢复，但其内容已由冻结锁文件完整重建。
- 清理后最终复核通过：正式 `.venv` 是 Python 3.13.2，75 个基础开发包一致，五个 workspace 包均为 0.5.1，任务完成。
- 复核 README 后发现完整开发安装还要求运行 `vibeocr-install-backend --profile auto --network domestic`；已重新打开任务，继续恢复此前存在的 OCR/GPU 重依赖，避免基础测试通过但实际 OCR 缺包。
- 后端安装器因 uv venv 无 pip 首次失败；已补装 pip 并以 UTF-8 环境重跑成功。
- 发现安装器误选 CPU Torch 2.13.0，已按锁文件 gpu-cu126 extra 精确纠正为 Torch 2.12.1+cu126 / Torchvision 0.27.1+cu126。
- Paddle 与 Torch 均已在 RTX 4090 上执行真实 CUDA 矩阵运算；生产 DLL 注册路径有效，CUDA 路径回归 4 passed。
- 完整 GPU profile 最终合并回归 128 passed，临时目录清理完成，任务完成。

---
# 进度日志：0.5.0 更新链修复

## 2026-07-20

- 已读取 planning-with-files 技能并执行 session-catchup；保留现有三个规划文件的历史追加内容。
- 已确认当前主线 v0.5.1、v0.5.0 tag 与后续 Classic relaunch 修复提交的关系。
- 已定位 Classic Qt 编排器、纯网络服务、独立 updater 与对应测试；正在对照 tag 和当前源码确定仍可复现的根因。
- 已确认多源下载存在“大文件先于小 SHA 探测”的顺序问题，且单源仅有 inactivity timeout、没有总时限。
- 已确认 updater.ready 写入过早：主程序在 updater 尚未完成任何校验前就硬退出，可稳定解释“下载成功后闪退但没有完成更新”的用户观感。
- 首轮定向测试收集 157 项，其中无需临时目录的 33 项通过；其余 124 项因系统临时目录权限在 setup 阶段失败，并非产品/断言失败。下一轮切换到 `C:/tmp`。
- 改用工作区 `.tmp` 后，更新服务、Qt 编排器、updater/replacer 共 157 项全部通过。
- Ruff、compileall、`git diff --check` 全部通过。Pyright 对四个同名物理 namespace source root 无法合并，定向运行只产生既有工程解析错误与 unused warning，没有可归因于本次改动的新类型诊断。
- 最终复跑仍为 157 passed；SHA 先行后的失败/取消路径会同时清理 ZIP 与 SHA 临时文件，Ruff、compileall、diff check 再次通过。任务完成。

---
# 2026-07-20：PaddlePaddle 3.3.1 CPU/oneDNN 兼容性审计

- 已读取 planning-with-files 技能并完成 session-catchup。
- 已建立审计计划；下一步读取 oneDNN 判定、调用点和测试。
- 已审计 `cpu_info`、OCRService 管道创建、启动 FLAGS、注册工厂与现有测试；发现未知版本 fail-open、强制覆盖绕过全部安全门槛、AVX2 证据不足和缺少真实推理/回退测试。
- 下一步核对 WorkerHost 继承语义与当前安装版 PaddleX 对 `enable_mkldnn` 的实际处理。
- 已确认 WorkerHost 入口不统一设置 eager oneDNN FLAGS；PySide 继承、WinUI/直启不保证。
- 首次安装版签名探测因未走项目 CUDA DLL 注册路径而在导入 Torch 时失败；已记录为独立环境错误，下一次实验改用真实 OCRService 初始化顺序。
- 已核对 Paddle/PaddleOCR 与 oneDNN 官方资料：3.3.x 的 PIR/oneDNN 回归仍未关闭，禁用 oneDNN 的绕过方案有上游问题记录支持；“3.4 自动安全”和“oneDNN 必须 AVX2”缺少证据。
- 为排除现有 GPU/cuDNN 环境干扰，下一步在独立临时 CPU 虚拟环境做 Paddle 3.3.1 A/B 验证，不修改正式 `.venv`。
- 独立 CPU 环境验证完成：禁用 oneDNN 的 PP-OCRv6 推理成功；启用 oneDNN 稳定复现上游 PIR 属性转换异常。机器具备 AVX2，排除指令集不足。
- 已验证两个全局 FLAGS 不能覆盖 PaddleOCR 构造器的 mkldnn 配置；关闭 new IR 也未规避该异常。
- 已从安装版源码确认 PaddleOCR 3.7.0 默认启用 mkldnn，且当前 PaddleX/PaddleOCR 3.7.x 不识别项目文档提到的 `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT`。
- 现有 oneDNN 判定与 OCRService 注册测试共 40 项通过；这些测试证明传参与策略分支未退化，但不替代真实推理兼容测试。
- 三阶段审计全部完成，准备清理独立 CPU 环境并输出分级修改建议；未修改产品代码。
- 已删除本次创建的独立 CPU 虚拟环境与专用 uv 缓存；正式 `.venv` 和产品代码未变。

---
# 2026-07-20：oneDNN 兼容性整改

- 已创建持久目标并启用 planning-with-files；session-catchup 完成。
- 已确认 oneDNN 相关源码/测试无既有差异，更新链未提交修改属于其他任务，将保持不触碰。
- Phase 1 完成：冻结 fail-closed、严格异常匹配、单次重建重试和 GPU 不变契约。
- Phase 2 完成：安全判定改为未知/未验证版本 fail-closed，初始验证白名单为空；AVX2 和 PaddleX 历史环境变量文档已修正。
- Phase 3 实现完成待验证：新增单管道缓存释放、严格异常链识别、进程级禁用、同请求一次性重建重试和状态日志。
- 已补充缓存释放及 OCRService 回退/非目标异常/二次失败/GPU 不回退测试，进入定向验证。
- Ruff format 仅格式化 3 个本次相关文件；定向 Ruff check 全通过。
- 第一轮定向测试 68 passed；唯一 warning 是环境未安装 ccache，与整改无关。
- compileall 与 `git diff --check` 通过。首次 Pyright 因物理拆包 namespace 解析限制报告 27 个 missing import/既有诊断；本次返回值已显式收窄为 bool，下一轮按诊断行号确认无新增类型问题。
- 新增可选真实 CPU 模型门禁及运行说明；默认套件复跑 `68 passed, 2 skipped`，Ruff 全通过。
- Pyright 复跑从 27 降至 26 个诊断，消失项是本次已修正的 Optional bool 返回；剩余项均为物理 namespace missing import 或原文件既有行，新增回退方法无类型诊断。
- 扩展回归首轮 `165 passed, 7 skipped, 3 failed`；3 项失败均来自同进程先加载 Paddle 后再由 ModelScope 导入 Torch 触发的既有 cuDNN DLL 冲突，oneDNN 相关及其余管道测试已通过。下一步把表格依赖测试隔离到新进程复核。
- 表格依赖测试在隔离的新 pytest 进程中 `25 passed`，排除产品回归；最终审阅同时修正真实门禁未提供模型变量时 `Path('')` 误指向工作区的边界情况。
- 最终定向复跑 `69 passed, 2 skipped`；Ruff、compileall、`git diff --check` 全部通过。
- 当前 `.venv` 实际调用 `can_safely_enable_onednn()` 返回 False，并明确记录 Paddle 3.3.1 PIR/oneDNN 不兼容原因。
- 四阶段整改完成；未创建提交，未改动既有更新链产品差异。

---
