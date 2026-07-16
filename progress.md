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
