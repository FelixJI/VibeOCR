# CI / Release Workflow 整合设计

- 日期：2026-07-27
- 状态：待评审
- 相关文件：`.github/workflows/ci.yml`、`.github/workflows/release.yml`、`.github/workflows/scheduled-release.yml`、`.github/workflows/ttl-diagnostics.yml`、新增 `.github/actions/*/action.yml`

## 1. 背景与问题

当前 `.github/workflows/` 下有 4 个 workflow，存在两类重复：

### 1.1 真正的重复（ci ↔ release）

release.yml 的 build job 里逐字复制了 ci.yml 的多处校验步骤，发版一次会把这些步骤跑两遍（一遍在 scheduled-release 复用的 ci.yml 里，一遍在 release.yml 自身）：

| 步骤 | ci.yml 位置 | release.yml 重复位置 |
|---|---|---|
| `python -m ruff check apps packages tests scripts` | `pyside` job | `Validate Python style` |
| `dotnet restore` + `build VibeOCR.slnx` + `test Contracts.Tests` + `test Platform.Tests` | `winui` job | `Validate complete .NET solution` |
| `node --test tests/web/*.test.ts` | `winui` job | `Validate Web assets` |
| `pytest tests/contracts/v2 tests/supervisor tests/migration ...` | `backend` job | `Validate Python migration and release gates` |
| 构 5 个 workspace wheel + `verify_workspace_wheels.py` | `backend` job | `Build physical Python workspace wheels` |
| `./scripts/run_table_contract_gate.ps1` | `table-contract` job | `Run offline table provider contract gate` |

两处的注释（如 "App.Tests 在 Session 0 会挂起"）也完全一致。

### 1.2 编排冗余

`scheduled-release.yml` 是个薄编排层：detect 未发版提交 → `workflow_call ci.yml` → bump 版本 + 原子推 tag → `workflow_call release.yml`。它自身逻辑不多，但作为独立文件让发版链路分散在两个文件里。

### 1.3 特性分支遗留

`ttl-diagnostics.yml` 绑在 `agent/fix-persistent-model-residency` 分支，是该分支 PR 期间的临时诊断工作流。最近一次相关 commit (`03aa977f ci: remove stale TTL diagnostic paths`) 已在清理其残留。该分支逻辑已合入主干，诊断 workflow 不再服务主流程。

## 2. 设计目标

1. **职责分明**：ci.yml 只做"测什么"，release.yml 只做"怎么发"。release 的 build job 里不含任何测试代码（无 pytest / ruff / dotnet test / node test）。
2. **消除重复**：ci 与 release 共享的 setup 步骤和构建命令通过 composite action 复用，不复制粘贴。
3. **文件收敛**：4 个 workflow → 2 个（ci + release）。
4. **发版链路内聚**：定时发版编排并入 release.yml，发版逻辑集中在一处。
5. **不引入风险**：质量门依旧强制执行（通过 `workflow_call ci.yml`），发版产物不变。

## 3. 设计

### 3.1 文件结构变化

| 文件 | 动作 |
|---|---|
| `ci.yml` | 保留。内部用 composite action 去重 setup 步骤。新增 `parity` job。 |
| `release.yml` | 保留并吸收 scheduled-release 的编排。build job 删除所有重复校验步骤。 |
| `scheduled-release.yml` | **删除**。detect / bump / 原子推 tag 逻辑并入 release.yml。 |
| `ttl-diagnostics.yml` | **删除**。 |
| `README.md` | **更新**。第 532–541 行的 "Scheduled Release" 段落：链接由 `scheduled-release.yml` 改为 `release.yml`，描述从"调用 Release workflow"改为"由 release.yml 的 detect/prepare-release job 完成编排"，行为说明（定时检查、复用 Quality Gates、原子推送、手动重试路径）保持不变。 |
| `.github/actions/setup-python-buildshell/action.yml` | **新增** composite。 |
| `.github/actions/build-workspace-wheels/action.yml` | **新增** composite。 |

### 3.2 composite actions

#### `.github/actions/setup-python-buildshell/action.yml`

封装：`actions/checkout@v5` + `actions/setup-python@v6 (3.13)` + `pip install --require-hashes -r requirements/build-shell.lock`。

输入：
- `fetch-depth`（默认 1，release 的 detect/prepare 调用时传 0）

复用点：
- ci.yml：`table-contract`、`table-regression`、`backend`、`pyside`、`coverage`、`parity`（6 个 job，每个省 3 行）
- release.yml：`build` job

注意：ci 的 `contracts` job 只装 `pytest + jsonschema`（不装 build-shell.lock），`winui` job 不装 Python 包，二者**不**用此 composite，保持现状。

#### `.github/actions/build-workspace-wheels/action.yml`

封装：构建 5 个 workspace wheel (`vibeocr-contracts-py` / `vibeocr-client-py` / `vibeocr-backend` / `vibeocr-pyside` / root) 到 `dist/wheels` + `python scripts/verify_workspace_wheels.py dist/wheels`。

输出：无（产物落在调用方工作区 `dist/wheels`）。

复用点：
- ci.yml `backend` job（验证可构建 + clean-venv smoke，smoke 步骤留在 job 内不进 composite）
- release.yml `build` job（拿 wheel 去 bind artifact）

### 3.3 ci.yml 变化

1. 6 个 job 改用 `setup-python-buildshell` composite（替换各自前 3 个 step）。
2. `backend` job 的"构 5 wheel + verify"步骤改用 `build-workspace-wheels` composite；其后的 clean-venv smoke 步骤保留在 job 内（smoke 是 ci 独有，不进 composite）。
3. **新增 `parity` job**：从 release.yml 迁来 `Validate feature-parity matrix` 步骤。条件分支 `--require-pass`（原依赖 release 的 `build_winui`）改为读 ci 独立的 job 输入或固定逻辑（见 3.5）。parity 由"CI 外"改为纳入 CI 质量门。
4. 触发器不变：`pull_request` / `push: [main]` / `workflow_call`。

### 3.4 release.yml 变化

#### 触发器合并

```yaml
on:
  schedule:
    - cron: "30 7 * * *"          # 北京 15:30 = UTC 07:30（来自 scheduled-release）
  push:
    tags: ["v*"]
  workflow_dispatch:
    inputs:
      version_bump: { ... }       # 来自 scheduled-release
      build_variants: { ... }     # 原有
  workflow_call:                  # 保留，向后兼容（若有外部调用）
    inputs: { version, build_variants }
```

`workflow_call` 保留以兼容，但项目内主路径改为 release.yml 自包含（不再被 scheduled-release 调用）。

#### job 结构

```
detect (ubuntu-latest)            ← 仅 schedule / workflow_dispatch：检测未发版提交
  → quality-gates                 ← workflow_call ci.yml（完整测试质量门）
    → prepare-release (ubuntu)    ← 仅 schedule / workflow_dispatch：bump + 原子推 tag
      → build (windows-latest)    ← 纯构建发布
```

- **schedule / workflow_dispatch 路径**：detect → quality-gates → prepare-release → build。完整链路，自动 bump。
- **tag push 路径**：跳过 detect 和 prepare-release（tag 已存在），直接 quality-gates → build。推 tag 发版也保证过 CI。
- **workflow_call 路径**：跳过 detect 和 prepare-release，直接 build（保持向后兼容行为）。

#### build job 删除的步骤（测试归 ci）

- `Validate Python style`（ruff）— 删
- `Validate complete .NET solution`（dotnet build + 2 个 dotnet test）— 删
- `Validate Web assets`（node test）— 删
- `Run offline table provider contract gate`— 删
- `Validate Python migration and release gates`（pytest）— 删
- `Validate feature-parity matrix`— 删（迁到 ci.yml 新 parity job）

#### build job 保留的步骤（release 独有）

- 版本解析（Parse version from tag）
- variants 解析（Resolve which installers to build）
- `setup-python-buildshell` composite + `setup-dotnet` + 安装 build==1.3.0 hatchling==1.27.0
- `dotnet restore`（仅 restore，不 build/test；为 WinUI publish 准备）— 注：dotnet restore 本身不是测试，是 WinUI 构建的前置，保留。
- `build-workspace-wheels` composite（构建 wheel 供 bind）
- Build PySide6 Classic (PyInstaller) + Bind backend wheel + verify
- Build WinUI Next (dotnet publish) + verify
- Verify artifacts / table semantics in release artifacts
- Upload table artifact diagnostics
- Package Python wheelhouse
- Extract release notes + Upload to GitHub Release
- Cleanup old releases (keep 5)
- Mirror code to CNB

### 3.5 parity job 的 `--require-pass` 条件处理

release.yml 原逻辑：`build_winui == "true"` 时加 `--require-pass`（WinUI 发版强制 parity 通过）。

迁到 ci.yml 后，ci 不感知"本次发版是否构建 WinUI"。两种处理：

- **方案 A（推荐）**：ci 的 parity job **总是**跑 `--require-pass`。parity 矩阵本应是主干始终绿的硬门，发版与日常 PR 一视同仁。若主干 WinUI parity 暂不过，应通过矩阵文档标注 `wip` 而非靠 release 的 build_winui 开关放宽。
- **方案 B**：保留宽松，ci 的 parity job 不加 `--require-pass`（只验证矩阵可解析），WinUI 发版时由 release build job 补一次 `--require-pass`。但这会让 release build job 又出现一次 parity 调用，违背"build job 零测试"原则。

选 A。代价：若当前 WinUI parity 矩阵未全绿，需先把矩阵状态修正（标注 wip 或修代码），否则 ci 会红。**这是必须验证的前置条件**（见 §6 风险）。

### 3.6 concurrency 合并

scheduled-release 用 `scheduled-release`（独占、cancel-in-progress: false），release 用 `release-${{ version }}`（cancel-in-progress: true）。

合并后统一：

```yaml
concurrency:
  group: release-${{ github.event_name == 'schedule' && 'scheduled' || (inputs.version || github.ref) }}
  cancel-in-progress: ${{ github.event_name != 'schedule' }}
```

- 定时发版（schedule）：group 固定 `release-scheduled`，cancel-in-progress: false（不被手动/tag 触发取消，避免漏发版）。
- tag push / workflow_dispatch / workflow_call：group 含 version 或 ref，cancel-in-progress: true（同 tag 并发取消旧 run）。

### 3.7 发版链路时序（整合后）

**定时发版（每天 UTC 07:30）：**
1. `detect`：读 pyproject.toml 版本，算 `release_commit..HEAD` 提交数，>0 则 `should_release=true`。
2. `quality-gates`：`workflow_call ci.yml`（7+1 个测试 job 全跑）。
3. `prepare-release`：校验版本未漂移 → `bump_version.py` → 原子推 main + tag。
4. `build`：拉新 tag checkout → 构建 wheel → PyInstaller/dotnet publish → verify → 上传 → 清理 → 镜像。

**手动推 tag (`git push origin v1.2.3`)：**
1. （detect / prepare-release 跳过）
2. `quality-gates`：`workflow_call ci.yml`。
3. `build`：从 tag checkout → 构建 → 发布。

**workflow_dispatch（手动触发）：** 同定时，但 version_bump 可选 patch/minor/major。

## 4. 职责边界小结

| 关注点 | 归属 |
|---|---|
| Python 单测（core/managers/services/...） | ci |
| 契约测试（contracts v2 / .NET Contracts+Platform / web） | ci |
| table 契约门 + table 回归 | ci |
| 风格检查（ruff） | ci |
| coverage 门 | ci |
| feature-parity 矩阵 | ci（新增 job） |
| workspace wheel 可构建性 + smoke | ci |
| 版本解析 / variants / bump / 推 tag | release |
| 构建 wheel（供 bind） | release（composite 复用命令） |
| PyInstaller / dotnet publish 打包 | release |
| artifact 验证（pyside/winui/table） | release |
| 上传 Release / 清理旧 release / CNB 镜像 | release |

## 5. 非目标

- 不改测试本身、不改构建脚本（`bump_version.py` / `build_winui_release.ps1` / `verify_*` 等）。
- 不改发版产物（zip 名、内容、上传目标）。
- 不动 cnb / gitee 镜像策略。
- 不引入新依赖或新 runner 镜像（仍是 windows-latest / ubuntu-latest）。

## 6. 风险与验证

| 风险 | 验证 |
|---|---|
| parity job `--require-pass` 让主干变红（WinUI parity 未全绿） | 实施前本地跑 `python tests/parity/validate_matrix.py docs/quality/feature-parity.md --require-pass`，红则先修矩阵或代码 |
| composite action 在 windows-latest 上 step 顺序/环境变量传递出错 | 实施后 push 触发一次 PR，观察 ci 全 job 绿 |
| release build job 删除测试步骤后，发版前质量门缺失 | 确认 `workflow_call ci.yml` 在 build 之前且为 hard gate（build `needs: quality-gates` 且 `if: success()`） |
| 定时发版 concurrency 与手动撞版本 | 检查 group 表达式，手动测试一次 workflow_dispatch |
| scheduled-release.yml 删除后，外部若有引用失效 | grep 仓库确认无 `uses: ./.github/workflows/scheduled-release.yml` 引用（已确认仅 release.yml 被 scheduled-release 调用） |

## 7. 实施顺序（概要，详细见后续 plan）

1. 新建两个 composite action。
2. ci.yml 切到 composite + 新增 parity job。
3. release.yml 吸收 scheduled-release 编排 + 删重复步骤 + 切 composite。
4. 删除 scheduled-release.yml、ttl-diagnostics.yml。
5. 更新 README.md 第 532–541 行定时发版段落（指向 release.yml，重述编排归属）。
6. 本地/分支验证后合并。
