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
