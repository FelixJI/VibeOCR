# Phase 0 质量门禁

## 目的

`scripts/run_phase0_gate.ps1` 在干净工作区执行 Phase 0 的完整质量门禁，并把
可验证的基准报告写到被忽略的 `reports/local/phase0-baseline.json`。

门禁包含：

1. `uv sync --frozen --group dev` —— 锁文件同步，确保依赖确定。
2. `uv run pytest -q` —— 全量回归。
3. `uv run ruff check src tests scripts` —— 静态风格检查。
4. `uv run pyright` —— 静态类型检查。

报告 schema：`tests/fixtures/startup/baseline.schema.json`。

## 用法

### 完整门禁（CI / 发布前）

```powershell
./scripts/run_phase0_gate.ps1
```

退出码 `0` 表示全部通过；报告写到 `reports/local/phase0-baseline.json`。

### 自检模式（不运行耗时测试）

```powershell
./scripts/run_phase0_gate.ps1 -ValidateOnly
```

只验证：

- 脚本结构完整；
- baseline schema 存在且是合法 JSON；
- 报告目录可写。

不执行 `uv sync` / `pytest` / `ruff` / `pyright`。

## 报告脱敏

报告不得包含本机绝对路径（用户目录、绝对盘符路径）。门禁脚本在写报告后
会自检：若报告文本中出现 `UserProfile` 路径，直接报错。

## 当前容差（Phase 0 已知技术债）

门禁目前对两项预先存在的问题采用容差模式，记录债务但不阻断：

| 步骤 | 容差 | 原因 | 清除计划 |
|---|---|---|---|
| `pytest -q` | ≤ 3 failed | 2-3 个 flaky 测试：GPU 检测线程 `subprocess.run("nvidia-smi")` 与 Qt 事件处理的 Windows RPC 竞态（`0x8001010d`） | Task 0.5/0.6 修复协作取消后消除 |
| `pyright` | ≤ 98 errors | 98 个遗留类型错误（测试 Qt mock 类型推断、env_manager tuple 解包、动态 importlib） | 独立类型清理任务清零后恢复阻断 |

容差只允许**减少**，不允许**增加**。若错误数超过基线，门禁立即 FAIL。

## 基准内容

每条步骤记录 `name`、`exit`（退出码）、`seconds`（耗时）、`ok`（是否通过）。
总结果 `result` 为 `PASS` 当且仅当所有步骤退出码为 0。

## 与 Phase 1+ 的关系

Phase 0 门禁是后续所有阶段的前提。Phase 1 的 WorkerHost 契约、Phase 2 的
WinUI 壳都建立在「当前正式版可复现、可测试、无生产 `QThread.terminate()`」的
基线之上。
