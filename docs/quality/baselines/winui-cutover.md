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

## 现状（同机实测，2026-07-14）

门禁已通过 `--require-gate`（rc=0）。同机各 30 个冷启动样本，对称测量（双方都在首窗 T3 后 smoke 退出）：

| 指标 | Python (PySide6) | WinUI (framework-dependent) | 变化 | 门限 |
|---|---|---|---|---|
| ZIP 体积 | 160 MB（基线，PyInstaller 干净构建） | 11.6 MB（实测 publish 目录压缩） | **−93.1%** | ≥ −30% ✓ |
| 冷启动 T0–T3 p95 | 2041.4 ms（实测 main.py 全启动） | 542.6 ms（实测 WinUI.exe smoke） | **−73.4%** | ≥ −30% ✓ |
| 解压体积 | 63.8 MB（src + venv site-packages） | 52.9 MB（publish 目录） | −17.1% | 诊断 |
| 样本数 | 30 | 30 | — | ≥ 30 ✓ |
| 机器 fingerprint | `<host>\|x64`（同机） | 同 | 一致 ✓ | — |

门禁结果：`compare_release_metrics.py --require-gate` → `zip -93.1%, t0-t3 -73.4%, samples old=30 new=30`，rc=0。两项主指标均远超 30% 改善门限，无未批准回退。

采集命令（可复现）：
```powershell
.\.venv\Scripts\python.exe scripts\collect_startup_metrics.py --target python --runs 30 --name python --zip-bytes 167772160 --output reports\local\python-startup.json
.\.venv\Scripts\python.exe scripts\collect_startup_metrics.py --target <winui-exe> --runs 30 --name winui --zip-bytes 11585619 --output reports\local\winui-startup.json
.\.venv\Scripts\python.exe scripts\compare_release_metrics.py --old reports\local\python-startup.json --new reports\local\winui-startup.json --require-gate
```

注：WinUI T0–T3 含 .NET runtime 首次 JIT 与 worker 进程 spawn；Python T0–T3 含 PySide6/Qt 全量 import 与单实例/环境管理器初始化。双方均设 `VIBEOCR_SELF_TEST_SMOKE=1` 在首窗后退出，测量对称。门禁逻辑另由 `tests/test_compare_release_metrics.py`（10 测试）固化。
