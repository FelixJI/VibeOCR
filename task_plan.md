# 任务计划：修复 CUDA/OCR 依赖检测问题

## 目标

- 修复 CUDA 版本检测失败。
- 修复 OCR 依赖检测进行中拖动工具栏导致界面卡死/闪退。
- 为两个精确症状建立可自动运行的回归信号，并运行项目相关验证。

## 阶段

### Phase 1：建立失败回路

**Status:** complete

### Phase 2：复现并最小化

**Status:** complete

### Phase 3：验证可证伪假设

**Status:** complete

### Phase 4：回归测试与最小修复

**Status:** complete

### Phase 5：原始复现、相关测试和静态检查

**Status:** complete

### Phase 6：清理与根因记录

**Status:** complete

## 当前状态

- 已完成

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| `python` 命令不在 PATH，无法运行 session-catchup | 1 | 已加载 Codex 工作区依赖，后续改用捆绑 Python 绝对路径 |
| `Get-Command uv, nvidia-smi, py` 因 `uv` 不存在退出 1 | 1 | 已获得 `nvidia-smi`/`py` 信息，不再依赖 `uv` |
| `py -0p` 报告无系统 Python | 1 | 继续使用 Codex 捆绑 Python 绝对路径 |
| Codex 捆绑 Python 运行 pytest 时报 `No module named pytest` | 1 | 已找到仓库 `.venv\Scripts\python.exe`，后续测试改用该解释器 |
| pytest `tmp_path` 访问 `%LOCALAPPDATA%\Temp\pytest-of-felji` 被沙箱拒绝 | 1 | 拟用唯一 `--basetemp` 指向可写 `C:\tmp` |
| 设置 `TEMP`/`TMP` 后 pytest 仍用旧临时根目录 | 2 | 不再重复，改用 pytest `--basetemp` 参数 |
| `--basetemp=C:\tmp\...` 仍被拒绝 | 3 | 改用工作区内唯一 basetemp 后成功；后续固定该方式 |
| 首次合并补丁因 `env_manager.py` CUDA 上下文匹配失败未应用 | 1 | 改为按文件拆分小补丁，先重新定位精确片段 |
| 新增 DependencyManager 线程亲和性用例单跑通过、整组定向测试中超时 | 1 | 重跑 manager 文件确认是否可复现；若仅测试污染，收紧或移除该排除性用例 |
| 清理/补测的第二个合并补丁因注释空格不同校验失败 | 2 | 已确认无部分应用；后续每个文件单独补丁，使用新定位的精确上下文 |
| Ruff 定向检查报 TC003/I001/RUF100 | 1 | 将类型导入移入 `TYPE_CHECKING`，整理回归测试导入并移除多余 noqa |
| Ruff format 检查报 6 个既有文件需全文格式化 | 1 | 不扩大用户差异；仅格式化新增测试，lint 已全通过 |
| `detected_has_gpu` Args 说明因泛化上下文误插到其他函数 | 1 | 使用函数特有文档段上下文删除并精确加入目标函数 |
| 117 项 UI 扩大套件在 55% 处无失败摘要退出 1 | 1 | 不原样重跑；按最后组件拆分运行，判定是哪个文件/线程污染 |
| 并行拆分 UI 文件时 `test_settings_reinstall.py` 第 11/14 项后无摘要退出 | 2 | 不再并行 GUI 进程；先 collect-only 定位后 3 项，再串行单用例/前缀组合 |
| 清理时 `rg "[DEBUG-"` 零匹配退出 1 | 1 | 这是 rg 的正常“无匹配”语义，确认无临时调试标记 |
| manager 测试文件因临时补丁留下 mixed EOL | 1 | `ruff format --diff` 确认仅换行符变化，用 formatter 恢复一致 EOL |
| check-complete 首次未识别中文编号阶段，报 0/0 | 1 | 将已完成阶段改为脚本可识别的 `Phase N`格式后重跑 |
| check-complete 改为 `Phase N` 后仍报 0/0 | 2 | 不再猜测格式；读取脚本的实际匹配规则后做最后一次调整 |
| 脚本要求三级阶段标题和状态行，而非 checkbox | 3 | 已按脚本源码的精确格式重写六个阶段 |

## 2026-07-20 UI 主线程阻塞审计

**Goal:** 找出仍可由 GUI 主线程触发的同步耗时操作，区分已异步、应异步与无需异步的路径，并形成带源码证据和优先级的改造清单。本轮仅审计，不改业务代码。

### Phase 7: 子代理路由与审计边界
**Status:** complete

- 将主窗口/设置、业务页/控件、底层阻塞原语与反向调用链拆为三个只读工作包。
- 子代理不得修改文件、运行 GUI 或产生外部副作用，也不得继续派生子代理。

### Phase 8: 并行扫描与证据采集
**Status:** complete

- 每项候选必须给出准确文件与行号、UI 触发点、同步调用链、可能耗时和现有异步边界。
- 重点排查同步子进程、网络、磁盘、模型/OCR/PDF/图像重计算，以及在主线程中的 wait/sleep。

### Phase 9: 主代理交叉验证与去重
**Status:** complete

- 逐项复核高风险路径，排除只在 worker 中运行、轻量配置访问和有意的模态交互。
- 评估触发频率、最长阻塞时间、崩溃/死锁风险和生命周期约束。

### Phase 10: 排序与交付
**Status:** complete

- 输出 P0/P1/P2 改造清单、推荐异步边界、建议测试，以及无需改写项。
- 明确残余不确定性，不对业务代码做变更。

## 本轮错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| 读取 `packages/vibeocr-client-py/src/vibeocr/utils/env_manager.py` 失败，路径假设错误 | 1 | 先用 `rg --files` 定位真实模块，再读取实现；不重复猜路径 |
| 直接执行 `check-complete.ps1` 被系统 ExecutionPolicy 拒绝 | 1 | 使用独立 PowerShell 进程的 `-ExecutionPolicy Bypass -File` 只读运行该检查脚本 |
| Gate 0 新架构测试首次 Ruff format check 报 1 文件需格式化 | 1 | 只格式化两个新测试文件，不触碰既有文件 |
| 预读两阶段关闭实现时误写 `pyside/shutdown_coordinator.py` 路径 | 1 | 已用 `rg` 定位真实 `managers/shutdown_coordinator.py`，后续按真实模块审查 |
| 复核 basetemp 清理时目录已由 pytest 自动移除，`Resolve-Path` 报不存在 | 1 | 确认无残留，无需执行删除；后续只清理实际存在的临时目录 |

## 2026-07-20 异步改造实施路线图

### Phase 11: 编制 P0/P1/P2 实施计划
**Status:** complete

**目标：** 将 UI 主线程阻塞审计转换为可按批次实施、可独立回滚、每批都有响应性回归门槛的工程计划。本阶段只制定计划，不修改业务代码。

#### Gate 0：统一回归护栏（实施前置，约 0.5–1 人日）

- 抽取 Qt 事件循环 heartbeat/工具栏拖动延迟测试 helper；阻塞假实现持续至少 250ms，GUI 事件延迟门槛设为 100–150ms。
- 增加架构护栏：GUI slot 不得直接调用 `SyncBackendClient` 或同步 `PdfBackendClient`；仅允许在 QThread、QRunnable、`asyncio.to_thread` 的明确运行体中调用。
- 定义后台任务共同契约：generation/revision、cancel、closing、finished/error、引用持有与 drain。
- 完成标准：护栏能让至少一个现有同步候选稳定报红，且不改变产品行为。

#### Milestone P0：MinerU 首次模型准备（约 1–2 人日）

- 将 `_ensure_mineru_models_blocking()` 整体迁入独立 preflight worker；网络源探测、Popen、输出读取和 wait 全部离开 GUI。
- UI 只负责进入“模型准备中”、展示进度、允许取消；成功后按原请求参数启动 OCR，失败/取消时恢复按钮与进度状态。
- 使用 task generation 防止用户切换 PDF、关闭页签或重新发起 OCR 后的迟到结果回写。
- 完成标准：模拟模型准备阻塞 500ms 时，工具栏/窗口事件延迟不超过 150ms；取消、失败、关闭、成功续跑四条路径均有测试。

#### Milestone P1-A：统一 PDF 后台命令边界（约 3–5 人日）

- 建立 PDF session command queue/worker，禁止 GUI 直接调用同步 PDF client。
- 第一批迁移：preview + detect_text_layers、文字块编辑 + 重渲染、close session。
- 第二批收口：将 `reset_cancel` 移入 mutate/OCR worker 第一阶段；deskew worker 返回 model/diff；打开与缩略图 worker 取消后不再 GUI wait。
- 预览采用 generation；文字编辑采用 revision 串行化；结构修改保持 session 独占写顺序。
- 完成标准：快速翻页、连续编辑、重复打开、切换 session、移除文件、关闭窗口时无主线程 wait、无旧结果覆盖、无丢失 worker 引用。

#### Milestone P1-B：启动与设置环境探测（约 2–3 人日）

- 抽取共享 `DependencyUpdateCheckTask`，同时服务启动自动检测与设置页主动检测；按钮提供“检测中”状态并防重复触发。
- 将机器缓存/机器码/WMIC 校验移出 `MainWindow.__init__`，缓存结果异步回填；实现进程内 single-flight。
- 设置页刷新缓存复用现有 `_run_cache_operation()`；GPU 选择只消费后台探测结果，不允许 `resolve_use_gpu()` 在 GUI 回退 shell-out。
- 完成标准：portable 慢探测、开发态短路、关闭后迟到完成、并发点击、缓存损坏/缺失均有覆盖；首窗可先显示且持续响应。

#### Milestone P1-C：统一导出与保存 worker（约 2–3 人日）

- 新建可复用 Export/Save job，覆盖批量页当前/全部导出、结果页 DOCX/XLSX、QR SVG/PNG/JPEG 保存。
- 工作线程只处理序列化结果、PIL/QImage detached 数据、IPC、编码和文件写入；禁止创建或操作 QWidget/QPixmap。
- 批量导出提供逐项进度、取消、同名重命名摘要；关闭时纳入统一 drain。
- 完成标准：大结果、批量 N 项、写盘失败、取消、关闭、同名冲突均有测试，GUI 延迟不超过 150ms。

#### Milestone P1-D：设置页外部命令（约 1 人日）

- PowerShell 快捷方式创建放入 QRunnable；执行期间只禁用对应按钮，完成后主线程提示。
- 复查所有设置按钮，确保无新的同步 subprocess/WorkerHost 回退路径。
- 完成标准：PowerShell 假实现阻塞/超时/失败时 UI 可响应，任务结束后按钮与提示状态正确。

#### Milestone P2-A：截图和大图路径（约 2–3 人日）

- 截图每块屏幕只抓取一次并复用；Qt 要求的 `grabWindow`、scene snapshot、QPixmap 最终赋值保留在 GUI。
- snapshot 后转 detached QImage/bytes，将多屏合成、PNG/JPEG 编码、临时文件和正式写盘迁入 worker。
- 统一三条图片打开入口：后台读取/解码 QImage，主线程只 `QPixmap.fromImage` 并校验 generation。
- 完成标准：4K/多屏、超大图片、快速重复打开、截图后立即关闭均无卡顿和迟到覆盖。

#### Milestone P2-B：结果渲染与批量列表（约 2–3 人日）

- 将 content blocks 遍历、图片 base64、HTML 拼接移入纯数据 worker；主线程只调用 WebEngine `setHtml()`。
- 批量文件去重改用 path set；大量新增切换为 model/view 或分帧批量插入，避免 O(N²)+逐行重绘。
- resize 缩放增加防抖和缩略图缓存，避免高频重复全尺寸重采样。
- 完成标准：复杂嵌图 OCR 结果、数千文件导入、连续 resize 均有耗时基准，事件延迟不超过 150ms。

#### Milestone P2-C：单实例、懒加载与退出体验（约 2–4 人日）

- 单实例服务端用 `readyRead/bytesWritten` 信号状态机替代 `newConnection` 回调中的同步 wait；第二实例退出端可保留短等待。
- 懒加载 Tab 先显示 skeleton；后台只做安全的模块/数据预热，QWidget 构造留在主线程并按事件循环切片。
- 退出体验若要优化，采用两阶段关闭：首次 close ignore + 冻结 UI，后台 coordinator 完成后真正 quit；不得删除安装/PDF 的安全 drain。
- 完成标准：异常单实例客户端、冷切换重型 Tab、后台任务未完成时退出均无冻结、重入或对象销毁竞态。

#### 发布批次与依赖关系

1. Gate 0 → P0，单独发布并观察首次 MinerU 使用。
2. P1-A 单独发布；它改动 PDF 状态机，禁止与其他 PDF 功能改动混批。
3. P1-B、P1-C、P1-D 可在独立分支并行，合并顺序建议 B → D → C。
4. P2-A、P2-B 可并行；P2-C 最后实施，退出状态机需基于前述 worker 生命周期稳定后再做。
5. 每个批次必须通过定向测试、扩展 UI 套件、Ruff、`git diff --check`；出现响应性回归时不得进入下一批。

#### 总体完成定义

- P0/P1/P2 所列 GUI 入口不再直接执行同步 subprocess、网络、WorkerHost RPC、大文件编码或有界 wait。
- 所有后台任务都具备 cancel、generation/closing、防迟到回写和可验证 drain。
- GUI 线程只保留 QWidget/QPixmap/clipboard/WebEngine 最终操作和短小本地状态更新。
- 慢调用注入下交互延迟稳定不超过 150ms，且现有功能回归全绿。

## 2026-07-20 全量异步改造执行

### Phase 12: Gate 0 与第一波 P0/P1 实现
**Status:** complete

- 主代理：新增只写新测试文件的响应性/架构护栏，负责集成与冲突检查。
- PDF 子代理：独占 `pdf_session_manager.py`、`pdf_tab.py` 及其新增/专属测试，完成 P0 + P1-A。
- 启动设置子代理：独占 `main_window.py`、`settings_page_controller.py`、`machine_cache.py`、必要的 `env_manager.py` 及专属测试，完成 P1-B + P1-D。
- 导出子代理：独占 `batch_recognition_tab.py`、`result_view_widget.py`、`qrcode_tab.py`、新增共享导出 worker 及专属测试，完成 P1-C。
- 所有子代理必须保留现有 dirty worktree 修改，不得重置、提交、安装依赖或继续派生子代理。
- 第一波实现已交付并完成首轮主审：PDF 定向 151 项、导出相关 108 项、启动/设置分组回归均通过；主审额外修正了导出控件关闭槽等待和 PDF 打开前同步 backend start。
- Gate 0 从初始 34 个违规收敛到 12 个，剩余项全部属于 P2-A/B/C；启动/设置 single-flight 交叉入口竞态正在补最后一项回归，完成后关闭本阶段。

### Phase 13: 第二波 P2 实现
**Status:** complete

- P2-A 已完成首轮实现：统一后台 QImage 解码、截图单次抓屏复用、后台多屏合成/编码/写盘，并覆盖 generation、关闭和事件循环响应性；等待独立审查。
- P2-B 已完成结果 HTML worker、批量列表 path set/分帧插入和 resize 防抖；主审发现 Preview/QR 选择图片仍同步解码，已退回原实现者补齐并扩展架构门禁。
- P2-C 单实例服务端信号状态机已完成并通过主代理专属回归；懒加载 skeleton/预热与主窗口两阶段退出正在最后实现。
- 当前并行边界：导出代理只改 Preview/QR 图片解码，PDF 代理独占 `main_window.py`/`main.py`/关闭协调器/PDF 页关闭；独立审查代理只读检查已稳定的启动设置与 P2-A 非主窗口部分。

### Phase 14: 集成与分层回归
**Status:** complete

- P0/P1/P2 定向组合回归均通过；最终全量串行回归为 3132 passed、7 skipped。
- Gate 0 架构守卫 8 项通过，Ruff、compileall 与 `git diff --check` 全部通过。

### Phase 15: P2-C Qt 亲和安全关闭返修
**Status:** complete

- 先新增线程亲和、超时保活、PDF drain 不创建线程及 preflight native-finished 写门红测。
- 将 MainWindow 关闭改为 GUI QTimer 轮询状态机；GUI owner 仅在自身线程 request/poll。
- GUI worker 全部结束后才启动非 Qt 后端清理，并保活至其自然结束后再 accept。
- PDF request 阶段创建 session close worker；poll/drain 阶段只观察，不创建或改写 GUI-owned 状态。
- 修复 MinerU preflight cancel 的业务终态，使 busy/ocr_done 与 native finished 对齐。
- 错误记录：首次 GUI poll 红测的 fake batch shutdown 不接受关键字 `timeout_ms`；已改为 `**_kwargs`，让失败聚焦目标状态机。
- 红测阶段完成：4 个最小用例分别覆盖 Qt 亲和 poll、预算超时保活、PDF request 命令边界、preflight native-finished 终态。

- 每个工作包先跑专属测试，主代理再串行跑合并后的 UI/PDF/设置/导出套件。
- 运行 Ruff、架构护栏、`git diff --check`，核对线程亲和性、取消、closing、generation 和 drain。

### Phase 16: 独立代码审查
**Status:** complete

- 使用未参与对应实现的子代理做只读审查，按正确性、竞态、Qt 线程规则、资源泄漏、测试缺口给出证据。
- 主代理逐项复核审查结论并修正；审查未通过不得完成目标。
- 首轮审查发现单图页独立使用退化、截图保存被后续 capture 取消、GPU 线程 close wait/析构竞态；单图页已由主代理恢复自有异步 loader 并通过 40 项回归，其余两项已退回原实现者。
- PDF/单实例独立只读审查与 P2-C 完成后的主窗口/退出交叉审查仍在进行。

### Phase 17: 最终清理与交付
**Status:** complete

- 删除临时测试目录/调试标记，确认没有覆盖用户既有修改。
- 完整回归、最终差异审查、计划完整性检查后才将目标标记完成。
- 第二次全量回归发现的唯一 MainWindow 游离 `singleShot` 关闭竞态已改为 GUI 事件内直接推进；80 项关闭分组及第三次全量回归均通过。
- 终审继续发现并关闭连续大结果编辑、ResultView 活模型/旧 DOM、PDF cancel ownership 三个 P1，以及 50k 快照/复制、稀疏 overlay、未知类型稳定比较和跨线程 timer probe 等 P2。
- 最终独立复审确认无 P0/P1/P2 交付阻塞；发布级全量回归 3156 passed、7 skipped，Ruff、compileall、Gate 0 和 `git diff --check` 全部通过。
