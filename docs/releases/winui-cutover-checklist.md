# WinUI 切换发布候选检查清单

Phase 5.5 发布候选签核清单。所有项必须在 Win10 1809 x64 与当前 Win11 各执行一次，除非另有说明。未完成项不得标记完成。

## 1. 构建与产物

- [ ] 干净签名 worktree 构建发布候选（`scripts/build_winui_release.ps1`）。
- [ ] `scripts/verify_winui_artifact.ps1` 退出 0（无 self-contained runtime、无重复 WebView2 SDK、无 PySide6 UI、无 winui-dev profile、无 output/test/cache）。
- [ ] 连续两次干净构建，解压文件清单与每文件 hash 一致（版本/签名时间戳除外）。

## 2. 安装与解压

- [ ] Win10 1809 x64 与 Win11：安装/解压、runtime 缺失修复、旧版升级。
- [ ] 用户数据、runtime、模型缓存、快捷键、历史输出可见且原始历史文件未被改写。
- [ ] 卸载/删除目录场景不残留孤儿进程或共享内存。

## 3. 功能对等（自动化）

- [ ] `dotnet test src/dotnet/VibeOCR.slnx -c Release` 全绿。
- [ ] `uv run pytest -q`（或等价 Python 全量）全绿。
- [ ] `npm test --prefix src/dotnet/VibeOCR.App/WebAssets` 全绿。
- [ ] `python tests/parity/validate_matrix.py docs/quality/feature-parity.md --require-pass` 退出 0（矩阵 100% PASS）。
- [ ] 四个 E2E spec 全绿：`single-recognition.spec.ps1`、`batch.spec.ps1`、`qrcode.spec.ps1`、`pdf.spec.ps1`。

## 4. 后端与依赖

- [ ] CPU-only 与受支持 GPU 环境各执行：依赖安装、后端切换、预热、OCR/PDF/批量取消。
- [ ] 切换失败只进修复页，不启动旧 UI。

## 5. 升级与切换

- [ ] 旧版升级到切换版：`cutover_sequence` 全步骤通过；WinUI 健康握手成功。
- [ ] 迁移失败进入修复页（数据保全）。
- [ ] 文件占用、hash 错误、断电恢复均只进 bootstrapper repair mode。
- [ ] 发布布局不存在旧 UI executable/launcher。

## 6. 性能与稳定性

- [ ] `scripts/compare_release_metrics.py --require-gate` 退出 0（ZIP 或冷启动 T0–T3 p95 改善 ≥30%，另一项无 >10% 未批准回退）。
- [ ] 8 小时稳定性 soak：循环 OCR/PDF、worker crash 注入、休眠唤醒、网络中断；无孤儿进程/共享内存/句柄持续增长。

## 7. 人工平台与可达性

- [ ] 多显示器混合 DPI、真实托盘、Office 剪贴板、键盘可达性、高对比度、屏幕阅读器。
- [ ] Win10 1809 与 Win11 各签核一次。

## 8. 审批

- [ ] 开发负责人签字。
- [ ] 测试负责人签字。
- [ ] 发布负责人签字。
