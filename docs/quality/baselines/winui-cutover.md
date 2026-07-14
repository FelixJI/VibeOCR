# WinUI 切换性能/体积门禁基线

此基线记录 Phase 5 切换前 Python/PySide6 正式版与 WinUI 候选版的对比指标。门禁由 `scripts/compare_release_metrics.py --require-gate` 强制。

## 门禁规则（全部满足才通过）

1. 双方各至少 30 个样本。
2. 双方机器 fingerprint 一致（`COMPUTERNAME|PROCESSOR_ARCHITECTURE`）。
3. 双方均含 ZIP 体积、解压体积、T0–T3 与 T0–T6 p95。
4. ZIP 体积或冷启动 T0–T3 p95 至少改善 30%。
5. 另一项不得出现 >10% 的未批准回退。

## 指标定义

- **T0–T3**：进程入口到首窗可见（`StartupEvent.FIRST_WINDOW`）。
- **T0–T6**：进程入口到首次可交互（`StartupEvent.INTERACTIVE`）。
- **ZIP 体积**：release 构建产物的压缩包字节数。
- **RSS/handle**：冷启动后空闲态的驻留内存与句柄数（诊断用，非硬门禁）。

## 采样方法

同机、重启后、冷缓存条件下分别采集旧/新各至少 30 次：
```powershell
# 旧（Python）
.\.venv\Scripts\python.exe scripts\profile_startup.py --runs 30 --output reports\local\python-startup.json
# 新（WinUI）
powershell -File scripts\benchmark_winui_startup.ps1 -AppPath <published-exe> -Runs 30 -Output reports\local\winui-startup.json
# 对比门禁
uv run python scripts\compare_release_metrics.py --old reports\local\python-startup.json --new reports\local\winui-startup.json --require-gate
```

## 现状（迁移审查，2026-07-14）

此前记录的 11.6 MB、542.6 ms 与“门禁已通过”结论已撤销。审查发现旧采集脚本把 T3/T6 写成硬编码代理值，并把同一个进程退出耗时同时填入两个里程碑；这些数据不能作为发布证据。

修复后的采集器只接受应用写出的真实 T0/T3/T6 JSONL，缺失、非数值或非单调样本会失败，成功样本数不再等于请求次数。WinUI `t6` smoke 会等待 WorkerHost handshake 后写入 T6；`compare_release_metrics.py` 同时拒绝 T6、RSS 和 handle 的未批准回退。

审查期间已真实构建并验证 0.4.28 WinUI ZIP：`36,409,846` bytes（34.7 MiB）、190 个文件，SHA-256 `ebb278201c08a4e4221ead7d29ca666f0ba0214b000f7d7994319d4547be16b4`，包含正式 Bootstrapper、UI-free WorkerHost source、manifest 和独立 updater。该数值仅是新候选包的实际体积，不代表对旧版的改善结论；冷启动与旧版体积仍必须在同一基准口径下采集后才能签核。目前性能门禁状态为 **待重测**。

采集命令（可复现）：
```powershell
.\.venv\Scripts\python.exe scripts\collect_startup_metrics.py --target python --runs 30 --name python --zip-bytes 167772160 --output reports\local\python-startup.json
.\.venv\Scripts\python.exe scripts\collect_startup_metrics.py --target <winui-exe> --runs 30 --name winui --zip-bytes <final-zip-bytes> --output reports\local\winui-startup.json
.\.venv\Scripts\python.exe scripts\compare_release_metrics.py --old reports\local\python-startup.json --new reports\local\winui-startup.json --require-gate
```

注：T3 是首窗里程碑，T6 是 WorkerHost ready 后首次可交互里程碑。双方必须使用修复后的真实 trace；不得再用进程退出耗时或常量替代。门禁逻辑由 `tests/test_collect_startup_metrics.py` 与 `tests/test_compare_release_metrics.py` 固化。
