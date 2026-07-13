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

## 现状

正式对比数据待 Phase 5.5 发布候选阶段在同机采集后填入。门禁逻辑已由 `tests/test_compare_release_metrics.py`（10 测试）固化。
