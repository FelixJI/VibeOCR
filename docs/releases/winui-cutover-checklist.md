# WinUI 切换发布候选检查清单

Phase 5.5 发布候选签核清单。所有项必须在 Win10 1809 x64 与当前 Win11 各执行一次，除非另有说明。未完成项不得标记完成。

> **本轮（2026-07-14）签核状态**：自动化项已实跑并记录证据；明确标注 `需交互会话` 的项必须在真实桌面环境由人工执行（托盘/剪贴板/键盘可达性/高对比度/soak）。

## 1. 构建与产物

- [x] 审查分支构建发布候选 —— App、Bootstrapper、UI-free WorkerHost、独立 updater、manifest 均进入稳定布局。
- [x] `scripts/verify_winui_artifact.ps1` 同时验证目录和最终 ZIP；缺失/零字节入口、旧 UI、dev profile、output/cache 均拒绝。
- [x] 最终 0.4.28 ZIP 双重校验通过 —— 190 个文件、36,409,846 bytes（34.7 MiB），SHA-256 `ebb278201c08a4e4221ead7d29ca666f0ba0214b000f7d7994319d4547be16b4`。
- [ ] 连续两次干净构建逐文件 hash 可复现 —— 仍需在干净 CI checkout 保存两次 manifest 后签核。
- [x] `build_winui_release.ps1` 存在并可用（framework-dependent unpackaged，`--self-contained false -r win-x64`）。

## 2. 安装与解压

- [ ] **需交互会话**：Win11 解压、复用旧便携 Python runtime、runtime 缺失修复入口和首次启动健康握手。
- [ ] **需交互会话**：Win10 1809 x64 安装/解压、runtime 缺失修复、旧版升级（需 Win10 1809 机器）。
- [x] 用户数据/runtime/模型缓存/快捷键/历史输出不被旁路 —— winui-dev profile 隔离；migrator 原子替换 + 哈希备份保留原文件（Phase 5.1，9+5 测试）。
- [x] 卸载/删除目录无孤儿进程/共享内存 —— worker 走 JobObject 绑定 + parent-PID watchdog（Task 1.3/2.3）。

## 3. 功能对等（自动化）

- [x] `dotnet test src/dotnet/VibeOCR.slnx -c Release --no-restore` 全绿 —— App 99 / Contracts 14 / Platform 37 = 150 passed（最终门禁实跑）。
- [x] Python 全量回归全绿 —— 2692 passed / 7 skipped；仅有 Paddle 未安装可选 ccache 的第三方提示（最终门禁实跑）。
- [x] `npm test --prefix src/dotnet/VibeOCR.App/WebAssets` 全绿 —— 14 passed（本轮实跑）。
- [x] `python tests/parity/validate_matrix.py docs/quality/feature-parity.md --require-pass` 退出 0 —— 11 行全部通过（最终门禁实跑）。
- [x] 四个 E2E spec 全绿 —— batch 3/3、qrcode 3/3、pdf 3/3 PASS（本轮实跑）；single-recognition 在 main。

## 4. 后端与依赖

- [x] 后端切换协议已实现 —— `settings.switch_backend`（持久化 cpu/gpu，原子替换，不自动重试）+ `settings.install_dependency`（安装运行时/模型，协作取消）（commit d59e60f）。
- [x] SettingsViewModel 走真实 RPC（不再 protected hook）—— `SwitchBackendUsesWorkerRpcByDefault` 测试验证（本轮）。
- [x] **真实 GPU 后端切换已验证** —— 本机有 CUDA GPU（device count=1），`test_settings_backend_switch_integration.py` 驱动 `JsonSettingsAdapter.switch_backend` 完成 cpu→gpu→cpu 真实持久化往返 + 字段保留 + 非法目标拒绝（3 passed，本轮实跑）。
- [x] 切换失败只进修复页 —— 真实 updater 失败路径不再调用任何应用启动入口；成功只启动 Bootstrapper `production` 并等待 `startup.healthy`。
- [ ] **需交互会话**：真实 OCR 模型上的 GPU 预热/批量取消体感（切换协议已验证，但完整 OCR 管线预热需真实模型权重）。

## 5. 升级与切换

- [x] 真实 updater 链路已接入 —— SHA-256→替换→Bootstrapper production→T6 `startup.healthy`；发布 verifier 强制包含 `updater.exe`。
- [x] 迁移失败进修复页（数据保全）—— migrator 幂等 + 哈希备份 + 原子替换；9+5 测试（Phase 5.1）。
- [x] 文件占用/hash 错误/断电恢复均只进 bootstrapper repair mode —— cutover 7 步任一失败进 repair，legacy UI 永不启动（`test_launch_legacy_ui_is_never_called`）。
- [x] 发布布局不存在旧 UI executable/launcher —— `verify_winui_artifact.ps1` 拒绝 PySide6（本轮 rc=0）。

## 6. 性能与稳定性

- [ ] 性能门禁待用修复后的真实 T0/T3/T6 trace 各采集 30 个有效样本；旧 11.6 MB/542.6 ms 证据已因采集器造值而撤销。
- [ ] **需交互会话**：8 小时稳定性 soak（循环 OCR/PDF、worker crash 注入、休眠唤醒、网络中断）—— 需真实模型与长时间桌面会话。

## 7. 人工平台与可达性

- [ ] **需交互会话**：WinUI app 在 Win11 正常启动、WorkerHost ready、热键截图、托盘隐藏/恢复和更新健康握手。
- [ ] **需交互会话**：多显示器混合 DPI、真实托盘、Office 剪贴板、键盘可达性、高对比度、屏幕阅读器 —— 必须在真实桌面由人工执行。
- [ ] **需交互会话**：Win10 1809 与 Win11 各签核一次。

## 8. 审批

- [ ] 开发负责人签字（待人工）。
- [ ] 测试负责人签字（待人工）。
- [ ] 发布负责人签字（待人工）。

---

## 本轮结论

迁移审查已修复生产 profile、WorkerHost 发布路径、真实启动埋点、soak crash 恢复、桌面壳层、updater/健康握手、bump/release/CI 和质量门禁中的关键缺陷。自动化结果必须以本审查分支最终门禁输出为准；旧的性能数字和“全部闭环”结论不再有效。

**仍需真实桌面/物理机/人工**的硬性签核项（超出自动化 agent 可验证范围，必须由人工在相应环境完成）：
1. Win10 1809 x64 的安装/升级/解压验证 —— 本机为 Win11 (build 26220)，需 Win10 1809 机器。
2. 真实 OCR 模型上的 GPU 预热/批量取消体感 —— 切换协议已验证，完整 OCR 管线需真实模型权重。
3. 8 小时稳定性 soak —— 需 8 小时连续桌面会话。
4. 人工可达性签核（多显示器/DPI/真实托盘/Office 剪贴板/键盘/高对比度/屏幕阅读器）—— 需交互式人工操作。
5. 三方负责人签字（开发/测试/发布）—— 需实际人工签字。
6. 最终发布 tag 与对外发布 —— 必须等上述签核完成。

原迁移分支已合并到 `main` 且 refs 中不再存在；本次修复在独立审查分支完成后合回 `main`。
