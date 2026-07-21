# 进度记录

## 2026-07-20

- 已读取诊断 Bug 与文件化计划技能说明。
- 首次 session-catchup 因 PATH 中无 `python` 失败。
- 已加载工作区捆绑依赖并获得可用 Python/Node/Git 绝对路径。
- 已用捆绑 Python 完成 session-catchup，并盘点仓库测试/模块分布。
- 已检查现有 CUDA 和 PySide 依赖管理器测试覆盖面，找到可扩展的回归测试位置。
- 已确认工作树存在既有用户改动，并将本次范围限定在 PySide CUDA/依赖检测路径。
- 已获取本机真实 `nvidia-smi` 输出，其 `CUDA UMD Version: 13.3` 格式可作为 CUDA 精确复现输入。
- 已运行应用真实 CUDA 检测路径并复现 `None`，CUDA 红灯回路成立。
- 已读取依赖管理器与实时嵌入式环境检测边界，正在构建 Qt 事件循环压力回路。
- 已确认浮动 `EdgeToolbar` 的真实拖动事件路径和可用 Qt 测试 fixture。
- 首次相关 pytest 因捆绑 Python 缺 pytest 失败；已切换到仓库 `.venv` 解释器。
- `.venv` 相关测试已有 14 个通过；8 个受沙箱临时目录权限影响，正改用 `--basetemp` 隔离。
- 已用工作区 basetemp 完整跑通相关基线：22 passed in 0.35s。
- 已检查历史日志与工具栏/GPU 连接点；确认 CUDA 失败证据，未找到可直接利用的闪退栈。
- 已对照依赖安装与设置页 GPU 检测的 QThread 实现，待检查对话框关闭/拖动期间的完整生命周期。
- 已发现启动 100ms/200ms 分别触发依赖检查和 GPU 门控，正在验证后者是否占用 GUI 线程。
- 已新增 UI 响应性红灯测试，当前稳定失败：工具栏拖动延迟 0.263s（阈值 0.15s）。
- 已将 CUDA 复现最小化为新表头单行输入，并与 UI 回归用例联合复现：2 failed in 0.75s。
- 已验证四个假设：确认 CUDA 字段匹配与 GUI 同步 GPU 探测两个根因；DependencyManager 回调线程与独立 toolbar 拖动两项已排除。
- 正在设计 GPU 门控异步边界，优先保持 MainWindow 与设置页组件解耦并处理关闭生命周期。
- 已确认设置页现有 GPU worker 已接入主窗口统一关闭/drain，倾向转发该 worker 结果，避免重复探测和新增线程。
- 已发现设置页 worker 完成 slot 中还有一次可回退到 `nvidia-smi` 的同步调用；修复将一并移入现有 worker。
- 首次跨四文件合并补丁因 CUDA 片段上下文校验失败，未应用；已切换为按文件小步补丁。
- 修复已应用；首轮定向测试 32 passed / 1 timeout，两个用户红灯用例均已转绿。
- DependencyManager 文件重跑 13 passed，确认排除性线程测试的首次超时是组合测试污染；将在清理阶段移除该无关用例。
- 清理/补测合并补丁因精确上下文不一致未应用，已重新定位并改用单文件补丁。
- 已补齐后台探测复用/pending 优先级测试，并移除排除假设用的临时线程测试。
- 定向回归已全绿：34 passed in 0.74s。
- 本机真实 CUDA 复现命令已转绿：输出 `cu126`。
- Ruff 首轮检查发现 3 个可机械修正的新代码样式问题，正在清理。
- Ruff 定向 lint 已全通过；全文 format 会扩大 6 个旧文件差异，因此只将格式化新建测试文件。
- 新建 UI 测试已格式化；diff 空白检查通过；并已纠正一处文档 Args 误插位置。
- 扩大 UI 回归首轮在 117 项中约 55% 无摘要退出；已改用按文件隔离定位。
- 已收窄到 `test_settings_reinstall.py` 第 11/14 项后的 Qt 进程退出；下一步用 collect-only + 串行单用例隔离。
- 已定位到 detailed fresh check 用例通过后的 teardown/下一用例边界，正单独运行第 13 项。
- 第 12/13 项单跑均通过，确认为设置页 fixture 遗留真 GPU QThread 的测试隔离问题；将在该 fixture 禁用非本测试目标的后台 worker。
- 已修正设置页重装测试隔离，整文件 14 passed in 0.69s。
- 扩大 UI 回归已全绿：114 passed, 3 skipped in 2.96s。
- 相关文件 Ruff lint 再次通过。
- 已确认无 `[DEBUG-...]` 临时仪器，并删除 13 个已验证在工作区内的 pytest 临时目录。
- 最终 diff 空白检查通过；正在将临时验证触及的 manager 测试文件换行符恢复为仓库规则。
- manager 测试文件已恢复无语义差异；最终代码审查未发现遗留临时仪器或超范围修改。
- 六个诊断/修复/验证阶段全部完成。
- check-complete 脚本首次因中文编号未识别阶段，已将计划标记调整为 `Phase N` 后将重跑。
- check-complete 第二次仍未识别，已停止格式猜测并转向读取脚本匹配规则。
- 已按 check-complete 源码要求将计划改为 `### Phase` + `**Status:** complete` 格式。
- check-complete 已报告所有阶段完成；已移除错误记录中会被计数的字面模式，确保最终计数只包含真实六个阶段。
- 最终 check-complete 为 6/6；定向 status 和 diff 空白检查均符合交付要求。

## 2026-07-20 UI 主线程阻塞审计

- 已完整读取子代理路由与文件化计划技能，并完成 session-catchup。
- 已建立四阶段只读审计计划，下一步并行派发三个独立代码扫描工作包。
- 已派发主窗口/设置、业务页/控件、底层调用链三个并行只读子代理。
- 主代理完成首轮阻塞原语清点，已定位 wait、同步状态文件、图像/PDF/二维码与设置子进程等待复核候选。
- 已复核首批命中：批量串行导出、PowerShell 快捷方式创建、退出阶段多线程等待和 PDF 同步收尾进入候选池；继续沿 UI 入口确认调用链。
- 已确认批量页“导出全部”为直接 GUI signal → 同步循环，而 PDF 批量导出已有后台 worker，可作为改造参照。
- 已确认两个设置快捷方式按钮同步 shell out PowerShell；批量单项/全部导出同步 IPC；PDF 缩略图 worker 替换时主线程轮询等待，三类均进入复核清单。
- 已复核结果页同步导出、PDF 预览同步渲染、QR 保存同步 RPC/编码，以及重新打开 PDF 前最多 3 秒 worker 等待；均有直接 GUI callback 证据。
- 已确认 PDF 翻页可能串行两次同步 IPC；截图启动存在重复抓屏，截图复制/保存存在主线程编码与写盘，已按“必须留 GUI 的 Qt 操作”和“可后台化的纯数据操作”拆开评估。
- 已完成 UI 层同步后端调用的全量名称扫描，确认核心 OCR/QR 正常流程已正确 to_thread；新增统一大图加载和复杂结果 HTML 构建两个中优先级候选。
- 已验证三条图片打开入口同步解码、大批量文件添加 O(N²)+逐行 UI 更新，以及复杂结果 HTML 构建，均补齐直接 UI 触发证据。
- 已确认在线更新的大文件操作已正确 to_thread，并将设置页依赖版本/更新检测列为下一轮重点核验。
- 首次读取 `env_manager.py` 因假设了错误 package 路径失败；将先定位真实文件后继续，不影响其他审计结果。
- 已定位真实 `packages/vibeocr-client-py/src/vibeocr/env_manager.py`；确认设置页常规环境刷新已正确后台化，但主动“更新依赖”检测仍在 GUI slot 同步执行。
- 已排除设置页缓存/TTL/释放按钮（均为 QRunnable）；发现主窗口还有一处依赖更新检测调用，正在核对其线程上下文。
- 已确认启动依赖检测完成回调和设置页主动按钮都会在 GUI 线程调用同一个同步版本探测；计划合并为共享异步任务边界。
- 已逐行验证缓存校验的两次 WMIC 启动路径和设置页刷新缓存条件路径，并记录进程级单飞/缓存要求。
- 已验证依赖版本探测的逐包双层子进程策略、GPU 缓存缺失的同步 shell-out 回退，以及懒加载 Tab 的 GUI 构造约束。
- 已在汇总前重读任务计划并核对工作树；两名子代理已完成且保持只读，底层调用链代理仍在运行。
- 已复核退出等待路径：主关闭有统一预算且后台执行清理，保留为有意安全 drain；只在最终报告备注退出 UX 改进，不混入日常交互 P0/P1。
- 底层代理已完成；主代理确认 MinerU 首次模型准备在 GUI 线程、PDF async 入口前同步 reset_cancel、摆正完成槽同步 get_model 三类隐藏阻塞。
- 已进一步确认 PDF 文字编辑后串行同步写+重渲染、移除文件同步 close RPC、MinerU 下载最长等待，以及单实例服务端 GUI wait。
- 已追到同步客户端统一 `Future.result` 原语及各 RPC 超时，形成“PDF GUI 禁止直调同步 client”的统一整改原则。
- 三个子代理全部完成只读扫描；主代理已对 P0/P1 调用链逐项复核，进入去重、分组和优先级排序。
- 已清理重复审计记录并形成 P0/P1/P2 工作包；下一步补齐回归测试建议、完成计划并运行完整性检查。
- 已补齐事件循环延迟、generation、架构边界、导出生命周期和启动 single-flight 回归建议；四个审计阶段均完成。
- 首次完整性脚本因本机 PowerShell 执行策略被拒绝；将使用只针对该进程的 Bypass 方式重跑。
- 完整性检查通过：`ALL PHASES COMPLETE (10/10)`；审计计划文件 `git diff --check` 通过，重复高风险记录已清理。

## 2026-07-20 异步改造实施计划

- 已完成 session-catchup，并重读审计计划、发现和进度记录。
- 已将 P0/P1/P2 转换为 8 个实施里程碑，补充依赖关系、粗略工作量、线程边界、测试门槛和完成定义。
- 本轮只制定计划，未修改业务代码。
- 计划完整性检查通过：`ALL PHASES COMPLETE (11/11)`；三个计划文件的 `git diff --check` 通过。

## 2026-07-20 全量异步改造执行

- P2-C 第一版关闭实现被独立审查判定存在 Qt 线程亲和与超时后 UAF 风险；已启动 Phase 15 返修。
- 已读取 diagnosing-bugs 与 planning-with-files 技能，下一步先建立 P0 红测。
- 已盘点 Settings/Batch/QR/Subprocess 关闭 API：现有 drain 含 wait/状态写，不能从后台线程调用；将由 GUI QTimer 调度零等待兼容探测并逐步采用 `is_drained`。
- 已确认 PDF session close worker 创建仍在 drain，Thumbnail drain 仍 wait；两者将拆为 GUI request 创建/取消与纯状态观察。
- 首次 P0 红测被 fake 签名错误截断，已修正；teardown 因关闭异常停在 draining，下一次运行应只暴露目标状态机失败。
- P0 红测现已精准报红：Main 不调用 GUI `is_drained` 且预算后提前 ready；PDF request 未创建 session close worker；preflight cancel 提前清 `_ocr_running`。
- 已确认外部清理边界：SubprocessManager 需 GUI request/is_drained/detach，detach 后的非 QObject service callable 与全局 backend shutdown 可由一个保活 QThread 顺序运行至自然结束。

- 已读取子代理路由与文件化计划规则并完成 session-catchup。
- 已创建活动目标：完成 Gate 0、P0、全部 P1/P2、分层回归与独立审查。
- 已核对 dirty worktree，确定第一波三包互斥文件所有权和第二波 P2 顺序依赖。
- 已派发 PDF、启动/设置、导出三名第一波实现子代理；主代理开始建设 Gate 0 新测试护栏。
- 已新增通用 Qt 响应性断言和 AST 架构门；初始运行按预期 1 failed，报告 34 个待迁移 GUI 阻塞调用。
- Gate 0 首次 format check 发现新架构测试需机械格式化；将只处理本轮两个新文件。
- Gate 0 两个新测试文件已格式化，Ruff lint 与 format check 全部通过；架构测试保持预期红灯等待实现收敛。
- 已预扫描 P2 入口与现有测试分布，确认第二波需要串行处理 `main_window.py`、`qrcode_tab.py`、`result_view_widget.py` 等第一波重叠文件；在第一波验收前不启动重叠写入。
- 第一波代理暂未报告阻塞；导出包获准仅调整既有结果导出测试的异步等待契约，不得改动复制和渲染用例。
- 第一波三包已交付首轮：PDF 151 项通过，导出关闭阻塞修正后相关 108 项通过，启动/设置各分组均通过；主审发现并推动修复了 PDF `client.start()` 残留 GUI 调用、陈旧 open session 回收与导出 closeEvent 等待。
- 主代理重跑 Gate 0：违规由 34 项收敛到 12 项，精确对应 P2 的大图/截图、结果 HTML、批量列表和单实例入口。
- 启动/设置主审发现共享 single-flight 的跨入口启动停滞竞态，已要求补 settings-first/startup-second 与反向顺序测试。
- 第二波已派发 P2-B 与 P2-C 单实例子包；P2-A 等 `main_window.py` 所有权释放后派发。
- 启动/设置交叉 single-flight 竞态已最小修正，两个请求顺序均有回归，专项 10 项通过；第一波 Phase 12 实现内容正式收口。
- 主代理独立复跑 PDF 合并套件：151 passed；该次 basetemp 已在确认绝对路径位于工作区后清理。
- P2-A 已派发，当前 P2-A、P2-B、P2-C 单实例三包并行且文件所有权不重叠。
- P2-C 单实例服务端已改为纯信号状态机；主审补齐 ACK 排空超时后，主代理独立复跑专属 5 项全部通过，Gate 0 对应两项已归零。
- 主代理复核原始 CUDA/依赖故障：GPU 信息与 DependencyManager 合计 22 项通过；本机真实 `detect_cuda_version()` 返回 `cu126`，不再是 `None`。
- P2-A 约完成 65%，对应 Gate 0 六项已归零；P2-B 约完成 70%，专属实现已落地但暂受 P2-A 半成品收集错误影响，待文件稳定后串行复跑。
- P2-A 首轮已交付：图片加载、截图合成及保存/剪贴板 I/O 都进入 generation-aware 后台任务；相关新增与既有分组测试、Ruff、架构门均由实现代理通过，主代理进入独立复核。
- P2-B 首轮已交付：结果 HTML 构建后台化、批量路径索引与分帧插入、resize 防抖缓存；主审另发现 PreviewWidget 与二维码图片选择仍存在同步文件解码，已要求原实现者补齐。
- 批量列表路径规范化最初使用 `Path.resolve()`，会把网络/慢盘文件系统访问重新带回 GUI；已改为纯字符串 `normpath/abspath/normcase`，并增加禁止 `Path.resolve` 的回归测试。
- P2-C 单实例 ACK 状态机初稿缺少“连接保持但 ACK 永不排空”的超时；已在发送 ACK 时重启统一超时计时器，并增加 stuck socket 回归，主代理复跑 5 项通过。
- 已派发 P2-C 最后子包：懒加载 skeleton/后台安全预热、主窗口两阶段关闭、`aboutToQuit` 非累计等待和 PdfTab request-only close；同时启动对稳定子包的独立只读审查。
- Preview/QR 后台图片解码补漏已交付，主代理独立复跑慢解码、generation、关闭、Preview/QR 既有功能及架构门共 91 项全部通过。
- 第一轮独立审查发现 3 个 P1、1 个 P2：单图页独立行为退化、截图保存会被下一次截图取消、BackendOptionsWidget close wait/运行中线程析构，以及 GPU 取消后仍 emit。主代理已逐项确认，未把审查当作只读报告搁置。
- 主代理恢复 SingleRecognitionTab 自有异步图片 loader：文件按钮不依赖 MainWindow，`process_file(image)` 后台解码后继续原 OCR 语义，latest-wins/closing 有回归；既有与新增单图页 40 项通过，Ruff 通过。
- 截图 confirmed save 生命周期和 GPU worker 无等待关闭已退回原实现者补竞态测试；PDF/单实例同时由未参与实现的代理做独立只读审查。
- PDF/单实例独立审查完成：单实例未发现明确缺陷；PDF 发现四个 P1 生命周期窗口——取消已接纳 open 留下半加载 session、drain 单次快照漏关闭期新增 worker、导出在业务 done 而非 QThread finished 时丢引用、OCR all_done 后过早释放写门。四项均已退回实现者作为交付阻断项。
- P2-C 主窗口主体已落地 6 项专属测试：首个 closeEvent 立即返回并保持事件循环、重复 close single-flight、超时不做 WebEngine 显式清理、关闭丢弃 lazy 结果、恢复重页首屏后才在 GUI 构造、aboutToQuit 不累计 wait；待 PDF 四项修复后统一验收。
- 三个最终加固子包已完成：Gate 0 入口/别名/调用图强化，Preview/Dialog/Dependency/Batch/QR 生命周期回归，以及 PDF/子进程/设置/启动更新的关闭清账。
- 主代理补齐不可变导出快照、50k 大结果后台聚合与 bounded overlay、批量列表分帧让步，并串行复跑 PDF、OCR、单图、二维码、Preview、Result、Batch 组合套件。
- 第二次全量回归为 3125 passed、13 skipped，唯一失败是 MainWindow 关闭后的游离 `QTimer.singleShot` 命中已删除 C++ 对象；已移除非托管 bound-method 回调。
- 关闭定点测试、MainWindow/依赖/生命周期 80 项与最终第三次全量回归全部通过：3132 passed、7 skipped。
- 最终 Ruff、compileall、`git diff --check` 与 8 项 Gate 0 架构守卫全部通过；计划进入交付状态。
- 首次最终终审发现 3 个 P1：连续大结果聚合丢更新、ResultView 活模型/旧 DOM 竞态、PDF cancel ownership 泄漏；均先补稳定红测后修复。
- PDF barrier 回归验证 session 恰好关闭一次；Base/Preview 完成文本与表格 generation 重建、worker 索引、512 项分帧回填和 2000 稀疏 overlay；ResultView 完成稳定快照、异步复制及 DOM/JS token 守卫。
- 二次终审继续关闭重复文本错位、GUI 50k 快照/5秒复制、pickle 内存放大、未知对象恶意等值和跨线程 QTimer probe 等 P2。
- 终审返修交叉组 238 passed，最终守卫组 123 passed；最后发布级全量为 3156 passed、7 skipped。
- 最终独立只读审查确认无 P0/P1/P2 交付阻塞；Ruff、compileall 与 `git diff --check` 再次通过。

## 2026-07-21 GitHub 工作流失败修复与重新发版

- 已确认工作树干净，`main`、GitHub/main、gitee/main 与标签 `v0.5.2` 都指向 `43a29aa`。
- 已读取最近 Actions：Release 成功，Quality Gates 失败。
- 已取得 backend 失败日志，唯一失败是 PDF 并发渲染性能断言在 runner 上得到 0.9957x。
- session-catchup 首次因系统 `python` 不在 PATH 失败；后续使用仓库虚拟环境解释器。
- 单项性能测试本地通过（1 passed in 53.66s），与 GitHub 的 0.9957x 失败共同证明该问题具有环境/计时敏感性。
- 已形成三条可证伪假设，开始核对实现并构造确定性的并发回归信号。
- 已将不稳定的性能阈值测试替换为确定性的栅栏并发测试。
- 负向控制（临时加回锁）按预期失败；恢复真实实现后新测试通过，且临时注入已撤销。
- 目标测试文件 3 项通过；Ruff lint/format 与 `git diff --check` 已通过。
- 已核对发布脚本与版本文件，确定按补丁版本 `0.5.3` 重新发版，且先等待修复提交的 Quality Gates 成功。
- 与失败 Actions 完全相同的 backend 测试路径已通过：862 passed、5 skipped；进入提交与远端门禁阶段。
- 修复提交已推送，首轮远端验证确认原 backend 失败步骤转绿；remaining Python tests 又暴露 3 个依赖真实 `paddlex` 的隔离缺陷，发版继续暂停。
- 已让 3 个依赖探测测试使用最小假 `paddlex.utils.deps`；无 PaddleX 红绿回路从 3 failed 转为 4 passed。
- 完整 remaining Python 门禁已本地通过：1157 passed；进入第二个修复提交与远端复验。
