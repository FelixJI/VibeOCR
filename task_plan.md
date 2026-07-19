# 任务计划：优化 PDF 文字层与批量识别流程

## 目标

梳理 PDF 批量识别、工作进程通信和文字层写入的完整链路；修复 stderr 的 GBK/UTF-8 解码崩溃及其导致的后端状态异常；在测试约束下实施可验证的吞吐性能优化，并记录后续可量化的优化方向。

## 当前阶段

已完成

## 阶段

### Phase 1：代码与调用链审计

- [x] 定位批量 PDF 识别、任务调度、进程通信和文字层写入实现
- [x] 定位现有测试、性能参数和潜在串行瓶颈
- [x] 复现/解释 stderr 解码崩溃与 `invalid state` 的因果链
- **Status:** complete

### Phase 2：设计与实现

- [x] 修复跨平台子进程文本解码和读取线程稳定性
- [x] 新增 WorkerHost 真批量 OCR RPC，恢复 Paddle 批推理
- [x] 消除每批增量保存的整文件备份，并增加 OCR 快速收尾路径
- [x] 评估并实现安全的跨批渲染预取
- [x] 补充针对并发、批量和异常输出的回归测试
- **Status:** complete

### Phase 3：验证与总结

- [x] 运行相关单元/集成测试和静态检查
- [x] 对比关键路径、吞吐或调用次数
- [x] 审核最终差异并形成流程与后续建议
- **Status:** complete

## 决策记录

- 优先修复 stderr drain 线程解码异常；该线程崩溃会丢失真实后端诊断信息，并可能放大进程终止时的状态机问题。
- 性能改造以现有架构和测试证据为依据，不预设必须替换 IPC。
- 保留 named pipe + 共享内存，新增批量 RPC；控制通道只传描述符和 JSON 结果。
- 普通“保存”仍保留全文档文字层重写；只有 OCR 编排末尾使用“无需重写”的快速 finalize。
- incremental save 利用 PDF 增量保存只追加的性质，用失败时截断到原长度代替每批完整 `.bak`。

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| 把 `vibeocr --help` 当成无副作用 CLI，实际启动 GUI 并等待事件循环 | 1 | 终止该进程；复用已安装环境，以直接模块导入和物理模块遍历完成单包验证 |
| 新增运行态路径测试保留未使用的 `Path` 导入，定向 Ruff 失败 | 1 | 删除未使用导入；测试本身 10 项已全部通过 |
| 系统找不到全局 `python` 命令，首次 session-catchup 失败 | 1 | 改用项目 `.vibeocr/uv-python/.../python.exe` 后成功运行 |
| `.venv` 定向 pytest 在收集合约测试时无法加载 `rpds` DLL（WinError 5），且环境缺少 pytest-asyncio | 1 | 记录为测试环境问题；后续改用项目 uv/隔离依赖组运行，不重复相同命令 |
| 用 `rg --files` 在隐藏运行时目录查找 `uv.exe` 无结果 | 1 | 改用 PowerShell 定向枚举 |
| PowerShell `Get-ChildItem -Filter` 误传多个模式导致参数错误 | 1 | 后续分开查询，不重复数组 Filter 写法 |
| 沙箱内无法加载 `rpds` 原生 DLL | 2 | 按权限规则在沙箱外重跑同一定向测试，170 项全部通过 |
| 新增流水线测试 monkeypatch 了兼容重导出模块，找不到 `mirror_to_doc` | 1 | 改为 patch 实现类函数实际所属的 `vibeocr.pyside.pdf_session_manager` |
| Ruff 首轮发现 3 处 import 排序问题 | 1 | 对涉及文件运行 Ruff 自动排序后复检 |
| Pyright 沙箱内无法读取 editable `.pth` | 1 | 按权限规则在沙箱外重跑 |
| Pyright 发现 OCR finalize 新参数未穿过 `client.pdf` WorkerHost command 层 | 1 | 补齐实际运行时调用链及测试后复检；另有 1 个 composition 既有 asdict 类型错误一并收紧 |
| 首次 `rg` 测试检索的 PowerShell/正则引号组合不完整 | 1 | 改用单引号完整正则后成功定位；不重复原命令 |
| 第二次组合 `rg` 在 JSON/PowerShell 双层引号下再次形成不完整正则 | 2 | 停止组合复杂模式，后续只用简单单模式检索 |
| 批量补丁假设 `method_validation.py` import 块次序与实际不符 | 1 | 读取精确文件头后拆分补丁，不重复原上下文 |
| 无法运行 .NET Contracts 测试：未安装 `global.json` 要求的 .NET SDK 10.0.302 | 1 | 记录环境限制；已通过 Python 侧 JSON Schema、golden、C# `RpcMethods` 集合一致性测试 |
| 直接运行 planning-with-files 完成检查被 PowerShell ExecutionPolicy 拒绝 | 1 | 按技能脚本说明改用 `powershell -ExecutionPolicy Bypass -File` |
| 当前任务首次运行 session-catchup 时全局 `python` 不可用 | 1 | 改用仓库 `.vibeocr/uv-python/.../python.exe` 成功执行 |

---

# 任务计划：修复 0.5.0 Classic 打包漏收 startup_metrics

## 目标

修复重打包后的 `VibeOCR.exe` 启动时报 `ModuleNotFoundError: vibeocr.startup_metrics`；补充能在 CI 打包阶段发现缺失模块的门禁，并重新发布可启动的 0.5.0 Classic 资产。

## 阶段

### Phase 1：定位打包根因
- [x] 核对入口 import、物理 workspace 包归属与 PyInstaller 分析结果
- [x] 复现/确认产物中缺失模块及现有门禁盲点
- **Status:** complete

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| 并行检查中 `rg startup_metrics dist` 无匹配返回 1，使同组目录列表输出被工具折叠 | 1 | 将目录/分析文件读取拆成独立命令；不重复组合无匹配检索 |
| 并行打印 PyInstaller 命令的一侧非零退出，工具只保留入口常量输出 | 1 | 已确认 `MAIN_PY` 正确；下一步直接运行正式构建获取 Analysis，不重复该拼接探针 |
| 读取 startup_metrics 后附带的复合 `rg` 未匹配，整组命令返回 1 | 1 | 已获得所需模块内容；后续只使用单一确定模式检索测试位置 |
| 第一版逐项 hidden import 在真实 PyInstaller Analysis 中仍无法跨 contracts 根解析 namespace 分片 | 1 | 保留确定性模块清单，但增加合并后的单一 workspace staging 作为最高优先 pathex |
| `uv build` 默认用户缓存 `AppData/Local/uv/cache` 初始化失败（os error 183） | 1 | 改用仓库既有可写 `.uv-cache`，不重复使用全局缓存 |

### Phase 2：修复与回归
- [x] 修正 PyInstaller 的 workspace namespace 收集策略
- [x] 增加 Classic 冻结产物启动/import smoke 门禁
- [x] 运行定向测试与静态检查
- **Status:** complete

### Phase 3：重新发布验收
- [x] 提交推送并更新 v0.5.0 tag 触发打包
- [ ] 修复 CI 启动 smoke 因继承 PIPE 导致的假超时并重新触发
- [ ] 跟踪工作流并验证新 Classic 产物可启动
- **Status:** in_progress

---

# 任务计划：重新打包 0.5.0 更新修复

## 目标

将 Classic 更新启动入口修复提交到远端，并触发包含该修复的 Windows Classic 打包流程；确认工作流成功且发布产物已更新。

## 阶段

### Phase 1：确认发布策略
- [x] 核对 workflow_dispatch/tag 的代码引用、0.5.0 Release 与现有运行记录
- [x] 选择不会误用旧 tag 代码且可安全更新产物的触发方式
- **Status:** complete

### Phase 2：提交并触发
- [x] 最终复核并提交当前修复
- [x] 推送修复并触发 Classic 打包
- **Status:** complete

### Phase 3：跟踪与验收
- [x] 跟踪 GitHub Actions 到完成
- [x] 核对 0.5.0 Release 产物、SHA256 与时间
- **Status:** complete

## 风险记录

- 当前 `v0.5.0` tag 指向修复前提交；单纯 rerun 旧 workflow 或在该 tag 上 dispatch 都不会包含本地修复。
- 采用仓库已有的同 tag 修复重推流程：先推 `main`，再把 `v0.5.0` 更新到新提交并 force-with-lease 推送；远端历史显示 0.5.0 已按此方式恢复过一次。

---

# 任务计划：修复版本升级遗漏与 CHANGELOG 重复归档

## 目标

让 `bump_version.py` 一次性更新 monorepo 中所有应用/内部包的版本声明和内部包精确依赖；即使当前版本 tag 缺失，也能从对应 release commit 正确截取新提交，避免 CHANGELOG 重复归档，并修正当前重复条目。

## 当前阶段

已完成

## 阶段

### Phase 1：定位根因

- [x] 审计版本文件、依赖声明与替换入口
- [x] 检查 0.4.29/0.4.30 的 tag、release commit 和 CHANGELOG 差异
- **Status:** complete

### Phase 2：实现修复

- [x] 扩展版本目标发现与全量精确替换
- [x] 让提交收集优先使用当前版本 release commit 作为边界
- [x] 去除当前 0.4.30 中从 0.4.29 重复归档的内容
- [x] 增加回归测试
- **Status:** complete

### Phase 3：验证

- [x] 尝试运行 bump_version 定向 pytest，并对受限项目完成直接函数级替代验证
- [x] 运行 Ruff、AST/锁文件一致性检查和 diff 审核
- **Status:** complete

## 决策记录

- 不对仓库做无边界的版本字符串全局替换；只更新 workspace 的 `pyproject.toml` 与其包 `__init__.py`，避免改写 README 示例和历史 CHANGELOG。
- CHANGELOG 边界优先取 `release: v{current_version}` commit；找不到时兼容回退到最近 tag。

## 验证限制

| 限制 | 处理 |
|---|---|
| pytest 在沙箱内无法创建用户临时目录；沙箱外授权又被环境额度策略拒绝 | 已收集 53 项，19 项通过、34 项在 setup 阶段因权限错误未运行；用真实 0.4.29 缺 tag 历史和版本文件发现函数完成直接验证 |
| Pyright 无法读取 `.venv/Lib/site-packages/_editable_impl_vibeocr.pth`（EPERM） | 记录为环境限制；Ruff、AST 解析、直接导入执行和 `git diff --check` 均通过 |

---

# 任务计划：PySide6 架构与运行治理审计

## 目标

以只读方式核查 PySide6 前端的模块边界、信号/槽与依赖注入接线、界面功能落地、异步/线程/进程和批处理模型、日志结构、依赖管理、模型缓存及超时治理；通过代码、配置和测试证据判断正确性，并输出按优先级排序的优化决策。本任务不修改产品代码。

## 当前阶段

已完成

## 阶段

### Phase 1：资产与入口盘点

- [x] 识别 PySide6 应用入口、composition root、窗口/页面/服务/worker 边界
- [x] 盘点配置、依赖声明、缓存目录、日志与超时常量
- **Status:** complete

### Phase 2：接线与功能正确性审计

- [x] 逐项核对界面控件、action、信号/槽、状态更新和后端调用
- [x] 对照测试与实现，标出完整、占位、失配和无覆盖功能
- **Status:** complete

### Phase 3：异步、批处理与生命周期审计

- [x] 核查 UI 线程安全、任务取消、并发上限、背压、批次策略和资源释放
- [x] 核查依赖装配、模型缓存命中/失效/容量管理和多进程复用效果
- **Status:** complete

### Phase 4：日志与超时治理审计

- [x] 核查日志上下文、分层、轮转、敏感信息、UI 展示和异常链
- [x] 汇总所有超时入口，评估默认值、覆盖层级、取消语义与集中配置
- **Status:** complete

### Phase 5：验证与决策

- [x] 运行适度的静态检查和定向测试
- [x] 形成问题清单、风险级别、证据位置与分阶段优化路线
- **Status:** complete

## 审计判定标准

- 正确性：界面操作能到达预期用例，状态和异常能回到 UI，停止/关闭可回收资源。
- 性能：UI 线程不做重 CPU/I/O；批处理减少固定开销且有内存/并发上限；缓存能观测到命中并有失效策略。
- 可维护性：composition root 集中、依赖方向稳定、日志和超时由语义化策略治理而非散落魔数。

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| 当前任务首次执行 session-catchup 时全局 `python` 不可用 | 1 | 使用 Codex 工作区依赖中提供的 Python 后成功执行 |
| 二维码联合检索引用了不存在的 `src/vibeocr/client/qrcode.py`，导致该组命令返回 1 | 1 | 已确认 typed API 位于 `worker_host/backend_client.py`，后续改用现有路径并拆开读取 |

---

# 任务计划：PySide6 三阶段治理实施

## 实施错误记录

| 日期 | 环节 | 现象 | 处理 |
|---|---|---|---|
| 2026-07-19 | Phase 1 子代理测试 | 沙箱内 pytest 收集时 `rpds` 原生 DLL 被拒绝访问 | 不绕过权限；由主线使用已授权的项目虚拟环境在沙箱外统一验证 |
| 2026-07-19 | PySide 定向回归退出 | 39 项均通过、进程返回 0，但 pytest-qt 清理事件时报告 Windows `0x8001010d`，后台 GPU 检测线程仍在 `subprocess.communicate()` | 记入 Phase 3 退出治理，集中收拢环境检测线程；当前不把非失败退出误判为功能失败 |
| 2026-07-19 | 协议一致性回归 | 350 项通过、1 项失败；C# 方法解析正则不允许域名含下划线，误报 5 个 `pipeline_cache.*` 方法缺失 | 将测试解析器域名从 `[a-z]+` 修正为 `[a-z_]+`，随后重跑 |
| 2026-07-19 | WorkerHost 异步启动首轮回归 | 后台 task 的 ready 信号在测试 teardown 后迟到，manager 已清空 task，主窗口递归进入失败提示并触发 Windows 堆异常 | manager 忽略无活动 task 的迟到 started，MainWindow 忽略 closing 后 ready；主窗口单元 fixture 隔离真实 WorkerHost，专项真实线程测试覆盖响应性；重跑 56 passed |
| 2026-07-19 | workspace wheel 离线 smoke | 首次沙箱内 uv cache 初始化失败；按规则在沙箱外重跑后，两个 wheel 均因当前 venv 未安装 hatchling、offline 无法建立隔离构建环境而未构建 | 不联网安装；记录为构建环境限制。manifest/包内容静态核查仍确认子包只有 marker，不能把依赖隔离判为已生效 |
| 2026-07-19 | Phase 3 首轮扩展回归 | UI 新增代码直接 import `core` 破坏架构门禁；按类型桩将 `QImage.save` 格式改为 bytes 又触发真实运行时错误 | 增加 `pyside` 壳层转发，恢复 PySide 运行时要求的字符串格式并局部注明类型桩偏差；相关 72 项与最终 681 项均通过 |
| 2026-07-19 | 静态检查 | 全仓 Pyright 仍包含生成 UI 与既有模块的存量类型错误；pytest-qt 在 Windows 退出时偶发打印 `0x8001010d`，但测试进程返回 0 | 以本次变更源码为门禁：0 errors/8 warnings；Ruff 与 diff check 全通过。MainWindow 单测隔离真实 GPU 子进程，保留专项取消测试；不把存量/非失败诊断包装成已清零 |

## 目标

将 2026-07-18 的只读审计结论落实为三个可验收阶段，修复生产接线、异步/批处理生命周期、日志与超时治理，并以真实 PySide6 → typed client → WorkerHost 路径测试证明功能有效。保留用户已有规划文件改动，不覆盖无关工作。

## 当前阶段

已完成

## 阶段

### Phase 1：正确性与生产接线止血

- [x] typed client 使用统一端到端 deadline，消除外层 300/1800 秒被内层 30 秒截断
- [x] 增加真实 pipeline cache status/set_ttl/release/preload RPC，并让生产 `BatchBackendAdapter` 可读回验证（设置页可见区在 Phase 3 落地）
- [x] 修复批量 QThread cancel/error/finished 状态机、禁止重入并纳入 MainWindow 退出 drain
- [x] 补充生产适配器、协议契约、真实 QThread 生命周期定向测试
- **Status:** complete

### Phase 2：交互与异步生命周期

- [x] PDF 保存成功后再切换/继续 OCR，mutate 采用明确 busy gate/排队且不在 GUI 线程阻塞等待
- [x] WorkerHost 启动、二维码生成/识别、单图大文件读取与编码移出 GUI 线程
- [x] 统一取消语义为 running/cancelling/cancelled/completed/partial_failed，并清理迟到信号
- [x] 补充 UI 响应性、continuation、取消与关闭回归测试
- **Status:** complete

### Phase 3：可观测性、性能与工程边界

- [x] 主进程/WorkerHost 统一结构化日志上下文与级别转发，状态栏脱离日志关键词
- [x] 统一 connect/queue/execution/stall/cancel/shutdown budget，并从单一总预算扣减
- [x] 批大小同时受数量、总字节/像素与资源预算约束；缓存状态显示命中、常驻、TTL 与释放结果
- [x] 验证依赖缓存/模型磁盘缓存语义、workspace 包边界与可安装/启动 smoke test（构建环境限制与 marker 包结论已记录）
- [x] 运行完整相关测试、Ruff、Pyright、协议一致性与 diff 审核
- **Status:** complete

## 子代理路由

- Phase 1 使用两个互斥写入工作包：A 负责 deadline + pipeline cache RPC/契约；B 负责批量 QThread 生命周期与测试。
- 主代理负责规划文件、跨工作包复核、MainWindow 组合根接线、冲突处理和阶段验收。
- 子代理不得继续生成下一层代理；所有结论必须由主代理用源码与测试复核。

## 验收标准

- 所有长任务只有一个真正生效的端到端 deadline，测试检查 envelope deadline 而非仅外层 wait。
- 设置页 TTL/释放/状态必须命中当前 `BatchBackendAdapter(SyncBackendClient)` 生产路径并可读回。
- 取消后不得释放仍运行的 QThread 引用、不得重新启用开始按钮、不得把取消显示为 100% 完成。
- GUI 线程不得同步等待 WorkerHost 启动、模型推理或 5 秒 QThread wait。
- 日志可按 request/task/session/pipeline/page/batch 关联，且 WorkerHost severity/traceback 不丢失。
- 每阶段通过定向测试；最终通过相关全量测试、静态检查和完成审计。

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| 定向 pytest 在沙箱内无法创建用户临时目录/pytest cache，批量出现 WinError 5 | 1 | 按权限规则在沙箱外重跑相同定向集，225 项全部通过 |
| 尝试把 pytest basetemp 改到 `C:\tmp` 仍被当前命令沙箱拒绝创建目录 | 1 | 不再重复绕过；使用已批准的沙箱外 pytest 前缀完成验证 |
| 本地 `.venv` 在沙箱内未加载 pytest-asyncio，单个 sync_client async 用例被误报失败 | 1 | 沙箱外完整环境已加载 pytest-asyncio；本次产品审计验证集排除环境误报，未将其计入产品缺陷 |
| PDF 测试联合检索假设存在 `tests/pyside` 目录，导致命令返回 1 | 1 | 改为先用 `rg --files tests` 发现实际 PDF 测试路径，再按现有文件读取 |
| 超时审计预设 `src/vibeocr/constants.py`/`config.py` 存在，联合命令因路径不存在返回 1 | 1 | 改为先按 `class Constants`/`Timeout` 全仓发现定义，再读取真实路径 |
| 二维码联合检索引用了不存在的 `src/vibeocr/client/qrcode.py`，导致该组命令返回 1 | 1 | 已确认 typed API 位于 `worker_host/backend_client.py`，后续改用现有路径并拆开读取 |

---

# 任务计划：四包物理拆分与联网重依赖安装

## 目标

把现有 workspace 的 marker 包落实为四个包含真实生产代码、可独立构建和安装的 wheel：`vibeocr-contracts`、`vibeocr-client`、`vibeocr-backend`、`vibeocr-pyside`；根 `vibeocr` 作为兼容 meta package 保留现有安装和导入体验。CI 允许联网构建，最终用户安装时允许联网下载 Paddle/Torch/MinerU 等重依赖，并验证不会破坏现有 WorkerHost、GPU/CPU 选择和应用启动链。

## 当前阶段

已完成

## 阶段

### Phase 1：包归属与构建拓扑

- [x] 盘点根 `src/vibeocr` 的模块依赖、入口点、动态导入和资源文件
- [x] 确定四个 wheel 的真实文件归属、依赖方向和兼容 namespace 策略
- [x] 核查现有 workspace/build hook/CI/重依赖来源，形成不破坏用户联网安装的迁移方案
- **Status:** completed

### Phase 2：真实代码与元数据拆分

- [x] 将生产模块/资源物理归入四个 workspace 包，并保留 `vibeocr.*` 兼容导入
- [x] 更新四包与根 meta package 的 build-system、依赖、入口点和版本同步
- [x] 更新源码/架构守卫，禁止 marker wheel 或跨层直接依赖回归
- **Status:** completed

### Phase 3：用户联网安装与 CI 构建链

- [x] 确保 backend wheel 声明并正确解析 CPU/GPU/平台相关重依赖与自定义 index
- [x] 更新 CI 构建顺序、wheelhouse/安装 smoke 和全新环境启动验证
- [x] 验证根 meta package、PySide-only 开发安装、完整用户安装和 WorkerHost 启动
- **Status:** completed

### Phase 4：回归、文档与交付

- [x] 运行可执行的协议/架构/PySide/WorkerHost 回归、Ruff 与 wheel 内容审核
- [x] 更新安装/开发/发布文档，明确联网、GPU/CPU 与兼容周期
- [x] 记录剩余平台限制并完成规划检查
- **Status:** completed

## 关键约束

- 不通过复制同一份生产代码到多个 wheel 实现“拆包”；同一模块只有一个真实归属。
- 四包继续组成 `vibeocr` namespace，现有 `vibeocr.*` 导入在兼容期保持可用。
- `contracts → client → pyside` 不得反向依赖 backend；backend 可依赖 contracts，WorkerHost 不得导入 Qt。
- 重依赖必须由用户机标准联网安装链正确解析；CI 成功不能依赖开发机已有 `.venv`。
- 保留当前未提交的三阶段治理改动，不回滚或覆盖。

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| `uv build` 默认用户缓存目录不可写 | 1 | 显式使用工作区 `.uv-cache`，五个 wheel 全部构建成功 |
| 完整 pytest 收集被既有 venv 的 `pytest_asyncio`/`rpds` 文件 ACL 拒绝 | 2 | 不绕过权限；保留 CI 完整回归，沙箱内完成架构、重依赖关键链、版本发布等可执行测试 |
| Pyright 读取既有 editable `.pth` 时被 ACL 拒绝 | 1 | 记录为环境限制；Ruff 与 `git diff --check` 均通过，CI 保留 Pyright |
| 首个 Classic 绑定 smoke 误用旧 WinUI ZIP，校验器报告缺少 `VibeOCR.exe` | 1 | 改用最小 Classic 夹具，五 wheel 绑定、manifest 和哈希校验通过 |

---

# 任务计划：拆包变更终审、提交与合并

## 目标

对当前“四包 + 根兼容包”及此前同一工作区内的 PySide6 治理改动进行提交前终审；修复确认的问题，完成足量测试和发布制品复核；把当前未提交工作转移到特性分支提交，合并回 `main`，并仅在成功合并后删除特性分支。

## 当前阶段

已完成

## 阶段

### Phase 1：变更与分支审计

- [x] 核实当前分支、远端基线、工作区变更归属与未跟踪文件
- [x] 审查真实物理拆包、namespace、依赖、CI/release 与用户安装链
- [x] 识别并修复 P0/P1 问题
- **Status:** completed

### Phase 2：验证与提交

- [x] 运行 wheel 构建/安装、架构、依赖、发布、静态检查与适用回归
- [x] 检查 diff、版本、资源归属和 Git 变更完整性
- [x] 在特性分支创建提交
- **Status:** completed

### Phase 3：合并与清理

- [x] 切回 `main` 并以非快进合并特性分支
- [x] 验证合并提交、工作区清洁和 main 指向
- [x] 删除已合并的本地特性分支
- **Status:** completed

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| 把 `vibeocr --help` 当成无副作用 CLI，实际启动 GUI并等待事件循环 | 1 | 终止进程；改用直接模块导入和物理模块遍历 |
| `C:\tmp` 与权限声明不一致，创建隔离 venv 被拒绝 | 1 | 改用任务明确可写的可视化工作区，保持仓库外隔离条件 |
| 首次根 wheel smoke 的内部四包被 uv 同版本缓存替换 | 1 | 确认 wheel 本身含新代码；改为显式安装五个本地 wheel，CI/README 同步采用该方式 |
| Pyright 即使指定干净解释器仍读取旧 `.venv` editable `.pth` 并被 ACL 拒绝 | 2 | 停止重试；Ruff、全模块导入、wheel smoke 和分组测试通过，CI 保留 Pyright |
| UI 回归出现 WMIC/nvidia-smi 输出编码导致的 reader thread warning | 2 | 所有环境探测文本子进程增加 `errors="replace"`；复测 warning 消失 |

---

# 任务计划：修复 GitHub 工作流并发布 0.5.0

## 目标

使用 GitHub CLI 定位当前失败的 GitHub Actions 工作流，按“四包 + 根兼容包”物理拆分后的真实架构修复 CI/release 链；同步项目版本到 0.5.0，完成本地构建、安装和关键回归验证后提交、推送、创建并验证 0.5.0 发布。

## 当前阶段

已完成

## 阶段

### Phase 1：远端失败诊断

- [x] 核实本地分支、远端、标签和工作区状态
- [x] 使用 GitHub CLI 读取失败工作流、失败步骤与日志
- [x] 将失败原因映射到新物理拆包和发布架构
- **Status:** completed

### Phase 2：修复与版本升级

- [x] 修复 CI/release、构建或测试问题
- [x] 将所有发布版本源、内部依赖 pin 与锁文件升级到 0.5.0
- [x] 更新必要的发行说明与架构文档
- **Status:** completed

### Phase 3：本地发布验证

- [x] 运行静态检查、架构与关键回归
- [x] 构建并校验五个 0.5.0 wheel
- [x] 在隔离环境验证根兼容包、PySide-only 和 WorkerHost 安装/启动链
- **Status:** completed

### Phase 4：提交与远端发布

- [x] 提交并推送修复到 GitHub
- [x] 观察修复后的 GitHub Actions 通过
- [x] 创建 0.5.0 标签/发行版并验证发布工作流和制品
- **Status:** completed

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| `py -3` 无可用全局 Python，planning session-catchup 未运行 | 1 | 后续改用仓库内可用解释器；先依据现有规划文件和 Git 状态人工恢复上下文 |
| `.venv` 未安装 `build` 模块，首次五-wheel 构建命令不可用 | 1 | 改用仓库已有 `uv.exe` 与工作区 `.uv-cache`，五个 0.5.0 wheel 构建和所有权验证通过 |
| 定向 pytest 默认使用用户临时目录与仓库 `.pytest_cache`，均被沙箱 ACL 拒绝 | 1 | 改用明确可写的可视化工作区作为 `TEMP/TMP/--basetemp`，并禁用 pytest cacheprovider |
| 定向测试调用 `powershell.exe -File` 受本机 ExecutionPolicy 阻止，10 项均未进入校验逻辑 | 1 | 测试调用显式增加 `-ExecutionPolicy Bypass`，与 CI/项目脚本的非交互执行方式一致 |
| 本地完整 release-gate 收集读取既有 `.venv` 的 `rpds` DLL 被 ACL 拒绝，且缺少 pytest-asyncio 插件 | 1 | 不重复绕过；本次修改相关 10 项已全部通过，原 GitHub 同提交 release-gate 除旧夹具外为 `464 passed`，推送后用干净 GitHub runner 复核全量 |
| 隔离 venv 位于较深的可视化目录，pip 解压 PySide6 QML 调试对象时超过 Windows 路径长度 | 1 | 下载和依赖解析均成功；改用明确可写且更短的 `C:\tmp\v050` 重试，避免把操作系统长路径限制误判为包问题 |
# 任务计划：修复升级到 0.5.0 后无法启动

## 目标

修复更新替换器在完成文件替换后只查找 `VibeOCR.Bootstrapper.exe`、导致 0.5.0 实际产物缺少该入口时更新失败的问题；补充回归测试并验证兼容旧版与 0.5.0 的启动布局。

## 阶段

### Phase 1：定位根因
- [x] 核对替换器启动入口、更新包布局和打包配置
- [x] 核对现有启动测试与 0.5.0 迁移要求
- **Status:** complete

### Phase 2：实现修复
- [x] 设计兼容且不会启动错误程序的入口选择策略
- [x] 修改替换器并补充回归测试
- **Status:** complete

### Phase 3：验证
- [x] 运行定向测试和静态检查
- [x] 审核最终差异与回滚/失败语义
- **Status:** complete

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| 当前 PowerShell 无全局 `python`，首次 session-catchup 失败 | 1 | 改用 Codex 工作区依赖中的 Python，已成功运行 |
| Ruff 语义检查通过，但首次 `ruff format --check` 报两个修改文件需格式化 | 1 | 使用仓库 Ruff 执行机械格式化后重新验证 |
| 沙箱内 GitHub CLI 无权读取用户配置 | 1 | 按权限规则使用已批准的 `gh` 前缀在沙箱外读取远端状态 |

---
