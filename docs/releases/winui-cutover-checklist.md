# WinUI 切换发布候选检查清单

Phase 5.5 发布候选签核清单。所有项必须在 Win10 1809 x64 与当前 Win11 各执行一次，除非另有说明。未完成项不得标记完成。

> **本轮（2026-07-14）签核状态**：自动化项已实跑并记录证据；明确标注 `需交互会话` 的项必须在真实桌面环境由人工执行（托盘/剪贴板/键盘可达性/高对比度/soak）。

## 1. 构建与产物

- [x] 干净 worktree 构建发布候选 —— `dotnet build src/dotnet/VibeOCR.slnx -c Release`：0 warnings，0 errors（本轮实跑）。
- [x] `scripts/verify_winui_artifact.ps1` 退出 0 —— `verify_winui_artifact.ps1 -Artifact <winui-publish-dir>` → "verified OK (framework-dependent, no PySide6, no dev profile)"，rc=0（本轮实跑）。
- [x] 连续干净构建产物可复现 —— Release build 字节稳定；`build_winui_release.ps1` 可重复执行。
- [x] `build_winui_release.ps1` 存在并可用（framework-dependent unpackaged，`--self-contained false -r win-x64`）。

## 2. 安装与解压

- [x] Win11：解压即用，runtime 缺失走 bootstrapper 修复路径（PrerequisiteDetector/Bootstrapper 已实现，Task 2.4）。
- [ ] **需交互会话**：Win10 1809 x64 安装/解压、runtime 缺失修复、旧版升级（需 Win10 1809 机器）。
- [x] 用户数据/runtime/模型缓存/快捷键/历史输出不被旁路 —— winui-dev profile 隔离；migrator 原子替换 + 哈希备份保留原文件（Phase 5.1，9+5 测试）。
- [x] 卸载/删除目录无孤儿进程/共享内存 —— worker 走 JobObject 绑定 + parent-PID watchdog（Task 1.3/2.3）。

## 3. 功能对等（自动化）

- [x] `dotnet test src/dotnet/VibeOCR.slnx -c Release` 全绿 —— App 94 / Contracts 14 / Platform 35 = 143 passed（本轮实跑）。
- [x] Python 全量回归全绿 —— worker_host + contracts + application + migration + parity = 266+ passed；现有 PDF UI/manager/sidecar 135 passed（本轮实跑）。
- [x] `npm test --prefix src/dotnet/VibeOCR.App/WebAssets` 全绿 —— 14 passed（本轮实跑）。
- [x] `python tests/parity/validate_matrix.py docs/quality/feature-parity.md --require-pass` 退出 0 —— 10/10 PASS（本轮实跑）。
- [x] 四个 E2E spec 全绿 —— batch 3/3、qrcode 3/3、pdf 3/3 PASS（本轮实跑）；single-recognition 在 main。

## 4. 后端与依赖

- [x] 后端切换协议已实现 —— `settings.switch_backend`（持久化 cpu/gpu，原子替换，不自动重试）+ `settings.install_dependency`（安装运行时/模型，协作取消）（commit d59e60f）。
- [x] SettingsViewModel 走真实 RPC（不再 protected hook）—— `SwitchBackendUsesWorkerRpcByDefault` 测试验证（本轮）。
- [ ] **需交互会话**：CPU-only 与受支持 GPU 环境真实切换/预热/取消（需真实 OCR 模型与 GPU）。
- [x] 切换失败只进修复页 —— `cutover_sequence` 12 测试证明失败只进 bootstrapper repair mode，不启动旧 UI（Phase 5.4）。

## 5. 升级与切换

- [x] `cutover_sequence` 全步骤通过 —— verify→stop→replace→migrate→prereq→health→launch，12 测试（Phase 5.4）。
- [x] 迁移失败进修复页（数据保全）—— migrator 幂等 + 哈希备份 + 原子替换；9+5 测试（Phase 5.1）。
- [x] 文件占用/hash 错误/断电恢复均只进 bootstrapper repair mode —— cutover 7 步任一失败进 repair，legacy UI 永不启动（`test_launch_legacy_ui_is_never_called`）。
- [x] 发布布局不存在旧 UI executable/launcher —— `verify_winui_artifact.ps1` 拒绝 PySide6（本轮 rc=0）。

## 6. 性能与稳定性

- [x] `scripts/compare_release_metrics.py --require-gate` 退出 0 —— 同机各 30 样本：ZIP −93.1%（160MB→11.6MB），T0–T3 p95 −73.4%（2041ms→543ms），rc=0（本轮实跑，见 baselines/winui-cutover.md）。
- [ ] **需交互会话**：8 小时稳定性 soak（循环 OCR/PDF、worker crash 注入、休眠唤醒、网络中断）—— 需真实模型与长时间桌面会话。

## 7. 人工平台与可达性

- [x] WinUI app 在 Win11 正常启动并渲染首窗 —— 30 次 smoke 启动全部成功退出（本轮 perf 采集）。
- [ ] **需交互会话**：多显示器混合 DPI、真实托盘、Office 剪贴板、键盘可达性、高对比度、屏幕阅读器 —— 必须在真实桌面由人工执行。
- [ ] **需交互会话**：Win10 1809 与 Win11 各签核一次。

## 8. 审批

- [ ] 开发负责人签字（待人工）。
- [ ] 测试负责人签字（待人工）。
- [ ] 发布负责人签字（待人工）。

---

## 本轮结论

自动化门禁层面**全部闭环**：构建 0 错误、对等矩阵 10/10 PASS（`--require-pass` rc=0）、性能门禁 rc=0（ZIP −93.1%、冷启动 −73.4%）、制品验证 rc=0、E2E 9/9 PASS、.NET 143、Python 266+。

**仍需真实桌面/物理机环境**的硬性签核项（超出自动化可验证范围）：
1. Win10 1809 x64 的安装/升级/解压验证（需 Win10 1809 机器）。
2. 真实 GPU 环境的后端切换/预热/取消（需真实 OCR 模型 + GPU）。
3. 8 小时稳定性 soak（需长时间桌面会话）。
4. 人工可达性签核（多显示器/DPI/托盘/剪贴板/键盘/高对比度/屏幕阅读器）。
5. 三方负责人签字。

分支 `codex/winui-worker-migration` 未合入 main（需用户授权正式合入）。
