# CI / Release Workflow 整合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 4 个 GitHub Actions workflow 整合为 2 个（ci 测试 + release 发版构建发布），抽出 2 个 composite action 消除重复步骤，职责分明：release 的 build job 里不含任何测试代码。

**Architecture:** ci.yml 只做质量门（测试），通过 composite action 去重 setup 步骤并新增 parity job；release.yml 吸收 scheduled-release 的编排（detect/bump/原子推 tag），build 前 `workflow_call ci.yml` 做硬质量门，build job 删除全部与 ci 重复的测试步骤；删除 scheduled-release.yml 与 ttl-diagnostics.yml。

**Tech Stack:** GitHub Actions（workflow_call + reusable workflows）、composite actions（`actions/checkout@v5`、`actions/setup-python@v6`、`actions/setup-dotnet@v5`）、YAML、pwsh/bash。

## Global Constraints

- 平台不变：ci 测试 job 与 release build 用 `windows-latest`，新增的 detect/prepare-release 用 `ubuntu-latest`。
- Python 版本固定 `3.13`，.NET 固定 `10.0.302`。
- 发版产物（zip 名、内容、上传目标、SHA256）不变；发版行为（定时 UTC 07:30、原子推 tag、保留最近 5 个 release、CNB 镜像）不变。
- 依赖锁文件 `requirements/build-shell.lock` 用 `--require-hashes` 安装；不引入新依赖。
- composite action 放在 `.github/actions/<name>/action.yml`，遵循 composite runs 步骤规范（`using: "composite"`，每步 `shell` 显式）。
- 每个任务结束前用 `uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('...'))"` 校验 YAML 语法（本机无 actionlint / pyyaml 全局安装）。
- 不改任何 Python/.NET 测试代码、构建脚本（`bump_version.py` / `build_winui_release.ps1` / `verify_*` 等）。

**Spec:** `docs/superpowers/specs/2026-07-27-ci-release-workflow-consolidation-design.md`

---

## File Structure

| 文件 | 责任 |
|---|---|
| `.github/actions/setup-python-buildshell/action.yml`（新增） | composite：checkout + setup-python 3.13 + pip install build-shell.lock。供 ci 6 个 job 和 release build 复用。 |
| `.github/actions/build-workspace-wheels/action.yml`（新增） | composite：构建 5 个 workspace wheel 到 `dist/wheels` + `verify_workspace_wheels.py`。供 ci backend job 和 release build 复用。 |
| `.github/workflows/ci.yml`（修改） | 6 个 job 切到 setup composite；backend job 切到 wheels composite（smoke 步骤留 job 内）；新增 `parity` job。 |
| `.github/workflows/release.yml`（修改） | 吸收 scheduled-release 编排；build job 删 6 个重复测试步骤、切 2 个 composite；concurrency/触发器合并。 |
| `.github/workflows/scheduled-release.yml`（删除） | 编排并入 release.yml。 |
| `.github/workflows/ttl-diagnostics.yml`（删除） | 特性分支遗留诊断。 |
| `README.md`（修改） | 第 532–541 行定时发版段落更新指向。 |

---

## Task 1: 新建 setup-python-buildshell composite action

**Files:**
- Create: `.github/actions/setup-python-buildshell/action.yml`

**Interfaces:**
- Consumes: `requirements/build-shell.lock`（仓库根）
- Produces: 仓库已 checkout + Python 3.13 在 PATH + build-shell.lock 依赖已装，供后续步骤直接用 `python`。输入 `fetch-depth`（int，默认 1）。

- [ ] **Step 1: 创建 composite action 文件**

创建 `.github/actions/setup-python-buildshell/action.yml`：

```yaml
name: Setup Python + build-shell.lock
description: Checkout + setup Python 3.13 + install hash-pinned build-shell dependencies.

inputs:
  fetch-depth:
    description: actions/checkout fetch-depth
    required: false
    default: "1"

runs:
  using: "composite"
  steps:
    - uses: actions/checkout@v5
      with:
        fetch-depth: ${{ inputs.fetch-depth }}
    - uses: actions/setup-python@v6
      with:
        python-version: "3.13"
    - shell: bash
      run: pip install --require-hashes -r requirements/build-shell.lock
```

- [ ] **Step 2: 校验 YAML 语法**

Run:
```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/actions/setup-python-buildshell/action.yml'))"
```
Expected: 无输出，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add .github/actions/setup-python-buildshell/action.yml
git commit -m "ci: add setup-python-buildshell composite action"
```

---

## Task 2: 新建 build-workspace-wheels composite action

**Files:**
- Create: `.github/actions/build-workspace-wheels/action.yml`

**Interfaces:**
- Consumes: 调用方须先装好 `build` + `hatchling`（ci 和 release 各自已装，不进 composite 以免重复装/版本漂移）；仓库已 checkout。
- Produces: `dist/wheels/` 下 5 个 workspace wheel，且 `verify_workspace_wheels.py` 已通过。

- [ ] **Step 1: 创建 composite action 文件**

创建 `.github/actions/build-workspace-wheels/action.yml`：

```yaml
name: Build workspace wheels
description: Build the 5 workspace wheels into dist/wheels and verify them.

runs:
  using: "composite"
  steps:
    - shell: bash
      run: |
        python -m build --wheel packages/vibeocr-contracts-py --outdir dist/wheels
        python -m build --wheel packages/vibeocr-client-py --outdir dist/wheels
        python -m build --wheel packages/vibeocr-backend --outdir dist/wheels
        python -m build --wheel apps/vibeocr-pyside --outdir dist/wheels
        python -m build --wheel . --outdir dist/wheels
        python scripts/verify_workspace_wheels.py dist/wheels
```

- [ ] **Step 2: 校验 YAML 语法**

Run:
```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/actions/build-workspace-wheels/action.yml'))"
```
Expected: 无输出，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add .github/actions/build-workspace-wheels/action.yml
git commit -m "ci: add build-workspace-wheels composite action"
```

---

## Task 3: ci.yml 切到 setup composite（6 个 job）

**Files:**
- Modify: `.github/workflows/ci.yml`（替换 6 个 job 各自的前 3 个 step）

**Interfaces:**
- Consumes: Task 1 的 `setup-python-buildshell` action。
- Produces: ci.yml 的 table-contract / table-regression / backend / pyside / coverage 5 个 job 用 composite 替换 setup；contracts、winui 不动（前者只装 pytest+jsonschema，后者不装 Python 包）。

- [ ] **Step 1: table-contract job（第 21–24 行）切到 composite**

把 `.github/workflows/ci.yml` 第 21–24 行：

```yaml
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with: { python-version: "3.13" }
      - run: pip install --require-hashes -r requirements/build-shell.lock
```

替换为：

```yaml
      - uses: ./.github/actions/setup-python-buildshell
```

- [ ] **Step 2: table-regression job（第 46–49 行）切到 composite**

同样的替换：把第 46–49 行的 checkout + setup-python + pip install 三步替换为：

```yaml
      - uses: ./.github/actions/setup-python-buildshell
```

- [ ] **Step 3: backend job（第 86–89 行）切到 composite**

把第 86–89 行的 checkout + setup-python + pip install 三步替换为：

```yaml
      - uses: ./.github/actions/setup-python-buildshell
```

- [ ] **Step 4: pyside job（第 130–133 行）切到 composite**

把第 130–133 行的 checkout + setup-python + pip install 三步替换为：

```yaml
      - uses: ./.github/actions/setup-python-buildshell
```

- [ ] **Step 5: coverage job（第 168–171 行）切到 composite**

把第 168–171 行的 checkout + setup-python + pip install 三步替换为：

```yaml
      - uses: ./.github/actions/setup-python-buildshell
```

- [ ] **Step 6: 校验 YAML 语法**

Run:
```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```
Expected: 无输出，退出码 0。

- [ ] **Step 7: 抽查替换后无残留旧 setup**

Run:
```bash
grep -n "actions/setup-python@v6" .github/workflows/ci.yml
```
Expected: 仅 `contracts` job（约第 73 行）一处保留（contracts 不用 composite），其余 5 处已删。

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: use setup-python-buildshell composite in 5 jobs"
```

---

## Task 4: ci.yml backend job 切到 wheels composite

**Files:**
- Modify: `.github/workflows/ci.yml`（backend job 第 104–110 行的 5 build + verify 命令）

**Interfaces:**
- Consumes: Task 2 的 `build-workspace-wheels` action。
- Produces: backend job 用 composite 构建 wheel；其后的 `pip install build==1.3.0 hatchling==1.27.0`（第 104 行）保留（composite 不装 build/hatchling）；clean-venv smoke 步骤（第 111–123 行）保留不动。

- [ ] **Step 1: 替换 backend job 的 build+verify 命令**

把 `.github/workflows/ci.yml` 第 105–110 行（5 个 `python -m build --wheel ...` + `python scripts/verify_workspace_wheels.py dist/wheels`）：

```yaml
      - run: python -m build --wheel packages/vibeocr-contracts-py --outdir dist/wheels
      - run: python -m build --wheel packages/vibeocr-client-py --outdir dist/wheels
      - run: python -m build --wheel packages/vibeocr-backend --outdir dist/wheels
      - run: python -m build --wheel apps/vibeocr-pyside --outdir dist/wheels
      - run: python -m build --wheel . --outdir dist/wheels
      - run: python scripts/verify_workspace_wheels.py dist/wheels
```

替换为：

```yaml
      - uses: ./.github/actions/build-workspace-wheels
```

注意：保留第 104 行 `- run: pip install build==1.3.0 hatchling==1.27.0`（composite 前置依赖）。

- [ ] **Step 2: 校验 YAML 语法**

Run:
```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```
Expected: 无输出，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: use build-workspace-wheels composite in backend job"
```

---

## Task 5: ci.yml 新增 parity job（硬门 --require-pass）

**Files:**
- Modify: `.github/workflows/ci.yml`（在 coverage job 后追加 parity job）

**Interfaces:**
- Consumes: Task 1 的 `setup-python-buildshell` action；`tests/parity/validate_matrix.py`、`docs/quality/feature-parity.md`、`tests/parity/` 测试目录。
- Produces: ci.yml 新增 `parity` job，把 feature-parity 矩阵校验从 release 迁过来，作为主干硬门（总跑 `--require-pass`）。

- [ ] **Step 1: 先验证主干 parity 当前是否全绿（前置风险检查）**

Run:
```bash
uv run python tests/parity/validate_matrix.py docs/quality/feature-parity.md --require-pass
```
Expected: 退出码 0（矩阵全绿，可安全加硬门）。若非 0：**停止本任务**，先修矩阵或代码（在 feature-parity.md 标注 wip 或补齐 WinUI 实现），绿后再继续。记录退出码到任务执行结果。

- [ ] **Step 2: 在 coverage job 后追加 parity job**

在 `.github/workflows/ci.yml` 文件末尾（coverage job 的最后一个 step 之后）追加：

```yaml

  parity:
    runs-on: windows-latest
    # feature-parity 矩阵是主干硬门：Classic + WinUI 双前端实现必须与
    # docs/quality/feature-parity.md 声明的状态一致。从 release 迁来，
    # 取消原 release 里依赖 build_winui 的条件放宽——日常 PR 与发版一视同仁。
    # 若 WinUI 某 feature 暂未实现，应在矩阵文档标注 wip，而非靠开关放宽门禁。
    timeout-minutes: 15
    steps:
      - uses: ./.github/actions/setup-python-buildshell
      - name: Validate feature-parity matrix (hard gate)
        shell: bash
        run: |
          python tests/parity/validate_matrix.py docs/quality/feature-parity.md
          python tests/parity/validate_matrix.py docs/quality/feature-parity.md --require-pass
          python -m pytest tests/parity -q
```

说明：第一行无参调用验证矩阵可解析；第二行 `--require-pass` 是硬门；第三行跑 parity 测试目录。三行都跑（与原 release 行为对齐，但无条件加 `--require-pass`）。

- [ ] **Step 3: 校验 YAML 语法**

Run:
```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```
Expected: 无输出，退出码 0。

- [ ] **Step 4: 本地跑 parity 测试目录确认通过**

Run:
```bash
uv run python -m pytest tests/parity -q
```
Expected: 全绿（这一步不含 `--require-pass`，但确认 parity 测试本身在本机能跑通）。

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add parity job as hard gate (migrated from release)"
```

---

## Task 6: release.yml 合并触发器 + concurrency + permissions

**Files:**
- Modify: `.github/workflows/release.yml`（第 12–46 行的 `on:`、`concurrency:`、`permissions:` 块）

**Interfaces:**
- Consumes: scheduled-release.yml 的 `on:` 触发器（schedule + workflow_dispatch.version_bump）和 concurrency 策略。
- Produces: release.yml 触发器合并 schedule + push tag + workflow_dispatch（双 input）+ workflow_call；concurrency 按 event 区分；permissions 含 contents: write。

- [ ] **Step 1: 替换 `on:` 块（第 12–38 行）**

把 `.github/workflows/release.yml` 第 12–38 行整个 `on:` 块：

```yaml
on:
  push:
    tags:
      - "v*"
  workflow_dispatch:
    inputs:
      build_variants:
        description: "生成哪些安装包：pyside6（Classic 主力，PyInstaller）/ winui（Next 开发预览，dotnet）/ all（两者）。默认 pyside6。"
        required: false
        default: "pyside6"
        type: choice
        options:
          - pyside6
          - winui
          - all
  workflow_call:
    inputs:
      version:
        description: "要发布的 x.y.z 版本；调用方须先创建对应 vX.Y.Z tag。"
        required: true
        type: string
      build_variants:
        description: "生成 pyside6、winui 或 all。"
        required: false
        default: "pyside6"
        type: string
```

替换为（吸收 scheduled-release 的 schedule + version_bump）：

```yaml
on:
  schedule:
    # GitHub Actions cron 使用 UTC；北京时间 15:30 = UTC 07:30。
    - cron: "30 7 * * *"
  push:
    tags:
      - "v*"
  workflow_dispatch:
    inputs:
      version_bump:
        description: "选择要增加的版本号：patch（x.y.Z）/ minor（x.Y.0）/ major（X.0.0）。仅 schedule/dispatch 路径生效。"
        required: true
        default: "patch"
        type: choice
        options:
          - patch
          - minor
          - major
      build_variants:
        description: "生成哪些安装包：pyside6（Classic 主力，PyInstaller）/ winui（Next 开发预览，dotnet）/ all（两者）。默认 pyside6。"
        required: false
        default: "pyside6"
        type: choice
        options:
          - pyside6
          - winui
          - all
  workflow_call:
    inputs:
      version:
        description: "要发布的 x.y.z 版本；调用方须先创建对应 vX.Y.Z tag。"
        required: true
        type: string
      build_variants:
        description: "生成 pyside6、winui 或 all。"
        required: false
        default: "pyside6"
        type: string
```

- [ ] **Step 2: 替换 concurrency 块（第 39–42 行）**

把第 39–42 行：

```yaml
concurrency:
  group: release-${{ inputs.version || github.ref }}
  cancel-in-progress: true
```

替换为（定时独占不取消，其余取消旧 run）：

```yaml
concurrency:
  # 定时发版用固定 group 且不取消（避免漏发版）；tag push / 手动 / workflow_call
  # 按版本或 ref 分组并取消旧 run（同 tag 并发只保留最新）。
  group: release-${{ github.event_name == 'schedule' && 'scheduled' || (inputs.version || github.ref) }}
  cancel-in-progress: ${{ github.event_name != 'schedule' }}
```

- [ ] **Step 3: permissions 块（第 44–45 行）保持不动**

确认第 44–45 行仍为 `permissions: contents: write`（detect/prepare-release job 内会按需收紧，见 Task 7/8）。无需改动，跳过。

- [ ] **Step 4: 校验 YAML 语法**

Run:
```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```
Expected: 无输出，退出码 0。

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci(release): merge triggers + concurrency from scheduled-release"
```

---

## Task 7: release.yml 新增 detect job（迁自 scheduled-release）

**Files:**
- Modify: `.github/workflows/release.yml`（在 `jobs:` 下、`build:` job 之前插入 detect job）

**Interfaces:**
- Consumes: scheduled-release.yml 的 detect job 逻辑（读 pyproject.toml 版本、算 `release_commit..HEAD` 提交数）。
- Produces: release.yml 新增 `detect` job，输出 `should_release` / `current_version` / `commit_count`；仅 schedule / workflow_dispatch 路径运行；tag push / workflow_call 跳过。

- [ ] **Step 1: 在 build job 之前插入 detect job**

在 `.github/workflows/release.yml` 的 `jobs:` 行之后、`  build:` 行之前插入：

```yaml
  detect:
    name: Detect unreleased main commits
    if: >-
      github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    outputs:
      should_release: ${{ steps.unreleased.outputs.should_release }}
      current_version: ${{ steps.unreleased.outputs.current_version }}
      commit_count: ${{ steps.unreleased.outputs.commit_count }}
    steps:
      - name: Require main
        shell: bash
        run: |
          if [[ "$GITHUB_REF" != "refs/heads/main" ]]; then
            echo "::error::请在 GitHub Actions 手动触发页面选择 main 分支"
            exit 1
          fi

      - name: Checkout triggering main commit
        uses: actions/checkout@v5
        with:
          fetch-depth: 0
          fetch-tags: true

      - name: Detect commits after current release point
        id: unreleased
        shell: bash
        run: |
          version="$(
            sed -n 's/^version = "\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)"/\1/p' \
              pyproject.toml | head -n 1
          )"
          if [[ -z "$version" ]]; then
            echo "::error::无法从 pyproject.toml 解析当前版本"
            exit 1
          fi

          release_commit="$(git rev-parse "refs/tags/v${version}^{commit}" 2>/dev/null || true)"
          if [[ -z "$release_commit" ]]; then
            count="$(git rev-list --count HEAD)"
          else
            count="$(git rev-list --count "${release_commit}..HEAD")"
          fi

          should_release=false
          if (( count > 0 )); then
            should_release=true
          fi

          echo "current_version=$version" >> "$GITHUB_OUTPUT"
          echo "commit_count=$count" >> "$GITHUB_OUTPUT"
          echo "should_release=$should_release" >> "$GITHUB_OUTPUT"
          echo "当前版本 v${version} 之后有 ${count} 个 main 提交"

```

- [ ] **Step 2: 校验 YAML 语法**

Run:
```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```
Expected: 无输出，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci(release): add detect job (migrated from scheduled-release)"
```

---

## Task 8: release.yml 新增 quality-gates job（workflow_call ci.yml）

**Files:**
- Modify: `.github/workflows/release.yml`（在 detect job 之后插入 quality-gates job）

**Interfaces:**
- Consumes: `./.github/workflows/ci.yml`（workflow_call）；detect 的 `should_release` 输出。
- Produces: release.yml 新增 `quality-gates` job，调 ci.yml 跑完整测试质量门。schedule/dispatch 路径依赖 detect；tag push / workflow_call 路径无 detect 仍需跑（用 `if` 兼容两条路径）。

- [ ] **Step 1: 在 detect job 之后插入 quality-gates job**

在 `.github/workflows/release.yml` 的 detect job 末尾（`echo "当前版本..."` 那一步之后、`  build:` 之前）插入：

```yaml
  quality-gates:
    name: Quality gates (reuse CI)
    needs: detect
    # schedule/dispatch 路径：detect 先判断 should_release；
    # tag push / workflow_call 路径无 detect（needs detect 被跳过 → result 'skipped'），
    # 此 job 仍需运行，故同时放行 should_release == 'true' 与 detect 被跳过两种情况。
    if: >-
      needs.detect.result == 'skipped' ||
      needs.detect.outputs.should_release == 'true'
    uses: ./.github/workflows/ci.yml
    permissions:
      contents: read

```

- [ ] **Step 2: 校验 YAML 语法**

Run:
```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```
Expected: 无输出，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci(release): add quality-gates job reusing ci.yml"
```

---

## Task 9: release.yml 新增 prepare-release job（迁自 scheduled-release）

**Files:**
- Modify: `.github/workflows/release.yml`（在 quality-gates job 之后插入 prepare-release job）

**Interfaces:**
- Consumes: detect 的 `current_version` / `commit_count` 输出；quality-gates 的 `result`；`scripts/bump_version.py`；`inputs.version_bump`（默认 patch）。
- Produces: release.yml 新增 `prepare-release` job，输出 `version`（新版本号），完成 bump + 原子推 main+tag。仅 schedule/dispatch 路径运行；tag push / workflow_call 跳过。

- [ ] **Step 1: 在 quality-gates job 之后插入 prepare-release job**

在 `.github/workflows/release.yml` 的 quality-gates job 末尾（`contents: read` 那一步之后、`  build:` 之前）插入：

```yaml
  prepare-release:
    name: Prepare and trigger release
    needs: [detect, quality-gates]
    if: >-
      needs.detect.outputs.should_release == 'true' &&
      needs.quality-gates.result == 'success'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    outputs:
      version: ${{ steps.release.outputs.version }}
    steps:
      - name: Checkout the quality-gated commit
        uses: actions/checkout@v5
        with:
          fetch-depth: 0
          fetch-tags: true

      - name: Set up Python 3.13
        uses: actions/setup-python@v6
        with:
          python-version: "3.13"

      - name: Install pinned uv
        run: python -m pip install uv==0.11.28

      - name: Configure release identity
        shell: bash
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

      - name: Prepare next release
        id: release
        shell: bash
        env:
          EXPECTED_VERSION: ${{ needs.detect.outputs.current_version }}
          VERSION_BUMP: ${{ inputs.version_bump || 'patch' }}
        run: |
          actual_version="$(
            sed -n 's/^version = "\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)"/\1/p' \
              pyproject.toml | head -n 1
          )"
          if [[ "$actual_version" != "$EXPECTED_VERSION" ]]; then
            echo "::error::质量门前后版本不一致：expected=$EXPECTED_VERSION actual=$actual_version"
            exit 1
          fi

          case "$VERSION_BUMP" in
            patch|minor|major) ;;
            *)
              echo "::error::未知版本增量：$VERSION_BUMP"
              exit 1
              ;;
          esac

          python scripts/bump_version.py "$VERSION_BUMP" --no-edit --no-push --no-build

          next_version="$(
            sed -n 's/^version = "\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)"/\1/p' \
              pyproject.toml | head -n 1
          )"
          git rev-parse --verify "refs/tags/v${next_version}^{commit}"
          echo "version=$next_version" >> "$GITHUB_OUTPUT"

      - name: Atomically push main and release tag
        shell: bash
        env:
          VERSION: ${{ steps.release.outputs.version }}
        run: |
          git push --atomic origin \
            HEAD:refs/heads/main \
            "refs/tags/v${VERSION}:refs/tags/v${VERSION}"

      - name: Write release summary
        shell: bash
        env:
          VERSION: ${{ steps.release.outputs.version }}
          COMMIT_COUNT: ${{ needs.detect.outputs.commit_count }}
          VERSION_BUMP: ${{ inputs.version_bump || 'patch' }}
        run: |
          {
            echo "### 已触发自动发版"
            echo
            echo "- 待发布 main 提交：${COMMIT_COUNT}"
            echo "- 版本增量：${VERSION_BUMP}"
            echo "- 新版本：v${VERSION}"
            echo "- 后续构建：下方 build job"
          } >> "$GITHUB_STEP_SUMMARY"

```

- [ ] **Step 2: 校验 YAML 语法**

Run:
```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```
Expected: 无输出，退出码 0。

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci(release): add prepare-release job (migrated from scheduled-release)"
```

---

## Task 10: release.yml 改造 build job（needs + 版本解析 + 删重复测试步骤）

**Files:**
- Modify: `.github/workflows/release.yml`（build job：第 62–68 行 needs/版本来源；删除第 147–194 行的 6 个重复测试步骤）

**Interfaces:**
- Consumes: detect（commit_count，可选）、quality-gates（result）、prepare-release（version，可选）。build job 需兼容三条路径：schedule/dispatch（有 prepare-release.version）、tag push（从 tag 解析）、workflow_call（inputs.version）。
- Produces: build job 依赖前置 job 完成，版本号来源统一解析；删除与 ci 重复的 6 个测试步骤，保留 release 独有的构建发布步骤。

- [ ] **Step 1: 改 build job 头部：加 needs + if + 兼容三路径版本来源**

把 `.github/workflows/release.yml` 的 build job 头部（当前约第 62–77 行，从 `  build:` 到 Checkout 步骤结束）：

```yaml
  build:
    name: Build & Release (Windows)
    runs-on: windows-latest
    # 发版流程含 .NET 契约测试 + Classic(PyInstaller) 打包 + 可选 WinUI(dotnet)
    # 打包 + 上传 + 镜像，正常 ~5-10min；45min 宽裕上限。dotnet test 同样可能
    # 因 App.Tests 挂起（见 ci.yml winui job 注释），超时护栏避免烧满 6h 默认配额。
    timeout-minutes: 45

    steps:
      - name: Checkout
        uses: actions/checkout@v5
        with:
          # workflow_call 的 github.ref 仍是调用方 main；显式 checkout 新 tag，
          # 保证构建的是含 release commit 的版本。tag push 时 inputs.version 为空。
          ref: ${{ inputs.version != '' && format('refs/tags/v{0}', inputs.version) || github.ref }}
```

替换为：

```yaml
  build:
    name: Build & Release (Windows)
    needs: [detect, quality-gates, prepare-release]
    # 三条路径汇聚到 build：
    #   schedule/dispatch：detect→quality-gates→prepare-release 全跑，build 取 prepare-release.version
    #   tag push / workflow_call：detect/prepare-release 被跳过（result 'skipped'），quality-gates 已过，
    #     build 从 tag（github.ref_name）或 inputs.version 解析版本。
    if: >-
      (needs.prepare-release.result == 'success') ||
      (needs.detect.result == 'skipped' && needs.quality-gates.result == 'success')
    runs-on: windows-latest
    # 发版构建发布：构建 wheel + Classic(PyInstaller)/可选 WinUI(dotnet) 打包 +
    # 上传 + 镜像，正常 ~5-10min；45min 宽裕上限。测试归 ci.yml（quality-gates 已过）。
    timeout-minutes: 45

    steps:
      - name: Checkout
        uses: actions/checkout@v5
        with:
          # 三路径版本来源不同，统一解析后 checkout 对应 tag：
          #   prepare-release.outputs.version（schedule/dispatch，含 bump）
          #   inputs.version（workflow_call，调用方已建 tag）
          #   github.ref_name（tag push，去掉前导 v）
          ref: ${{ format('refs/tags/v{0}', needs.prepare-release.outputs.version || inputs.version || github.ref_name) }}
```

- [ ] **Step 2: 改 Parse version 步骤，兼容三路径版本来源**

把 `Parse version from tag` 步骤（原第 88–100 行）：

```yaml
      - name: Parse version from tag
        id: version
        shell: bash
        run: |
          # tag 形如 refs/tags/v0.1.7 → version=0.1.7
          input="${{ inputs.version }}"
          tag="${input:-${GITHUB_REF_NAME#v}}"
          if ! [[ "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "::error::版本 '$tag' 不符合 X.Y.Z 格式"
            exit 1
          fi
          echo "version=$tag" >> "$GITHUB_OUTPUT"
          echo "解析到版本号: $tag"
```

替换为：

```yaml
      - name: Parse version from tag
        id: version
        shell: bash
        run: |
          # 三路径版本来源优先级：prepare-release.outputs.version（schedule/dispatch）>
          # inputs.version（workflow_call）> GITHUB_REF_NAME（tag push，去掉前导 v）。
          prep="${{ needs.prepare-release.outputs.version }}"
          input="${{ inputs.version }}"
          tag="${prep:-${input:-${GITHUB_REF_NAME#v}}}"
          if ! [[ "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "::error::版本 '$tag' 不符合 X.Y.Z 格式"
            exit 1
          fi
          echo "version=$tag" >> "$GITHUB_OUTPUT"
          echo "解析到版本号: $tag"
```

- [ ] **Step 3: 删除 build job 里与 ci 重复的 6 个测试步骤**

删除 `.github/workflows/release.yml` 中以下 6 个连续步骤（当前约第 147–194 行，从 `Validate Python migration and release gates` 到 `Validate feature-parity matrix` 结束）：

1. `Validate Python migration and release gates`（pytest，迁自 ci backend）
2. `Validate Python style`（ruff，重复 ci pyside）
3. `Validate complete .NET solution`（dotnet build + 2 个 test，重复 ci winui）
4. `Validate Web assets`（node test，重复 ci winui）
5. `Run offline table provider contract gate`（重复 ci table-contract）
6. `Validate feature-parity matrix`（迁到 ci parity job，Task 5）

即把这一整段（6 个 `- name: ...` 及其 `shell:`/`run:` 块）整块删除。删除后 `Restore .NET solution` 步骤之后直接接 `Build physical Python workspace wheels`（下一步会把后者换成 composite）。

注意：`Restore .NET solution`（第 141–145 行）**保留**——它是 WinUI publish 的前置 restore（不是测试）。

- [ ] **Step 4: 校验 YAML 语法**

Run:
```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```
Expected: 无输出，退出码 0。

- [ ] **Step 5: 抽查 build job 无残留测试命令**

Run:
```bash
grep -nE "pytest|ruff|node --test|validate_matrix" .github/workflows/release.yml
```
Expected: 无输出（build job 已无任何测试命令；parity/pytest/ruff/node 全在 ci.yml）。

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci(release): build job depends on quality-gates, drop 6 duplicate test steps"
```

---

## Task 11: release.yml build job 切到 2 个 composite

**Files:**
- Modify: `.github/workflows/release.yml`（build job 的 setup 与 wheel 构建步骤）

**Interfaces:**
- Consumes: Task 1 的 `setup-python-buildshell`、Task 2 的 `build-workspace-wheels`。
- Produces: build job 用 composite 替换 setup 三步和 wheel 构建步骤。

- [ ] **Step 1: 替换 setup 三步为 composite**

把 `.github/workflows/release.yml` build job 里的 `Set up Python 3.13` + `Install minimal build dependencies (hash-pinned)` 两步（原第 78–81 行 + 第 127–139 行）。

注意：release build 还需要 `.NET`，且 `Install minimal build dependencies` 步骤额外装了 `build==1.3.0 hatchling==1.27.0`。保留 `.NET` 步骤不动，把 Python setup + build-shell 安装换成 composite，再单独保留 build/hatchling 安装。

把原第 78–81 行：

```yaml
      - name: Set up Python 3.13
        uses: actions/setup-python@v6
        with:
          python-version: "3.13"
```

替换为：

```yaml
      - uses: ./.github/actions/setup-python-buildshell
```

然后把原 `Install minimal build dependencies (hash-pinned)` 步骤（第 127–139 行，含 `pip install --require-hashes -r requirements/build-shell.lock` 和 `pip install build==1.3.0 hatchling==1.27.0`）整个替换为（composite 已装 build-shell.lock，这里只留 build/hatchling）：

```yaml
      - name: Install build frontends
        shell: bash
        run: pip install build==1.3.0 hatchling==1.27.0
```

`Set up .NET 10.0.302` 步骤（原第 83–86 行）保持不动。

- [ ] **Step 2: 替换 wheel 构建步骤为 composite**

把 `Build physical Python workspace wheels` 步骤（原第 195–204 行）：

```yaml
      - name: Build physical Python workspace wheels
        shell: pwsh
        run: |
          python -m build --wheel packages/vibeocr-contracts-py --outdir dist/wheels
          python -m build --wheel packages/vibeocr-client-py --outdir dist/wheels
          python -m build --wheel packages/vibeocr-backend --outdir dist/wheels
          python -m build --wheel apps/vibeocr-pyside --outdir dist/wheels
          python -m build --wheel . --outdir dist/wheels
          python scripts/verify_workspace_wheels.py dist/wheels
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

替换为：

```yaml
      - uses: ./.github/actions/build-workspace-wheels
```

- [ ] **Step 3: 校验 YAML 语法**

Run:
```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```
Expected: 无输出，退出码 0。

- [ ] **Step 4: 抽查 build job 步骤顺序合理**

Run:
```bash
grep -nE "^      - (name:|uses:)" .github/workflows/release.yml
```
Expected: build job 步骤顺序大致为：Checkout → setup-python-buildshell → setup-dotnet → Parse version → Resolve variants → Install build frontends → Restore .NET → build-workspace-wheels → Build PySide6 Classic → …（构建发布链路），无 setup-python@v6 / build-shell.lock 直接安装残留（除 composite 内部）。

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci(release): use setup + wheels composite actions in build job"
```

---

## Task 12: 删除 scheduled-release.yml 和 ttl-diagnostics.yml

**Files:**
- Delete: `.github/workflows/scheduled-release.yml`
- Delete: `.github/workflows/ttl-diagnostics.yml`

**Interfaces:**
- Consumes: Task 6–11 已把 scheduled-release 的全部逻辑（detect/prepare-release/quality-gates）并入 release.yml。
- Produces: 仓库仅剩 ci.yml + release.yml 两个 workflow。

- [ ] **Step 1: 删除两个 workflow 文件**

Run:
```bash
git rm .github/workflows/scheduled-release.yml .github/workflows/ttl-diagnostics.yml
```
Expected: 输出 `rm '.github/workflows/scheduled-release.yml'` 和 `rm '.github/workflows/ttl-diagnostics.yml'`。

- [ ] **Step 2: 确认仓库内无对已删文件的引用**

Run:
```bash
grep -rn "scheduled-release.yml\|ttl-diagnostics.yml" .github/ docs/ README.md 2>/dev/null
```
Expected: README.md:532 仍引用 scheduled-release.yml（下一步 Task 13 修），其余无引用。若 .github/ 内有引用则说明有遗漏，需修正。

- [ ] **Step 3: 校验剩余 workflow YAML 语法**

Run:
```bash
uv run --with pyyaml python -c "import yaml; [yaml.safe_load(open(f'.github/workflows/{f}')) for f in ('ci.yml','release.yml')]"
```
Expected: 无输出，退出码 0。

- [ ] **Step 4: Commit**

```bash
git commit -m "ci: remove scheduled-release.yml and ttl-diagnostics.yml (consolidated into release.yml)"
```

---

## Task 13: 更新 README.md 定时发版段落

**Files:**
- Modify: `README.md`（第 532–541 行）

**Interfaces:**
- Consumes: Task 6–11 的 release.yml 新结构（detect/prepare-release/quality-gates/build job）。
- Produces: README 定时发版段落指向 release.yml，描述与新结构一致。

- [ ] **Step 1: 替换 README 第 532–541 行**

把 `README.md` 第 532–541 行：

```markdown
仓库还提供 [Scheduled Release](.github/workflows/scheduled-release.yml)：

- 每天北京时间 15:30（GitHub cron 为 UTC 07:30）检查 `main`。
- 若当前版本标签之后没有新提交，直接结束，不消耗完整发版门禁。
- 若有新提交，先复用完整 Quality Gates；全部通过后自动升级 patch 版本，
  创建 release commit/tag，原子推送二者，再调用 Release workflow。
- 若质量门或后续 Release 失败，先把修复补丁合入 `main`，再到 GitHub
  **Actions → Scheduled Release → Run workflow**，选择 `main` 手动重试。
  手动触发时可选择 `patch`、`minor` 或 `major`；流水线会重新执行质量门，
  并按所选增量生成新版本。每日定时发版固定使用 `patch`。
```

替换为：

```markdown
仓库的 [Release](.github/workflows/release.yml) workflow 同时承担定时发版编排：

- 每天北京时间 15:30（GitHub cron 为 UTC 07:30）检查 `main`。
- 若当前版本标签之后没有新提交，直接结束，不消耗完整发版门禁。
- 若有新提交，先复用完整 Quality Gates（调用 ci.yml）；全部通过后自动升级
  patch 版本，创建 release commit/tag，原子推送二者，再进入 build job 构建发布。
- 若质量门或后续 build 失败，先把修复补丁合入 `main`，再到 GitHub
  **Actions → Release → Run workflow**，选择 `main` 手动重试。
  手动触发时可选择 `patch`、`minor` 或 `major`；流水线会重新执行质量门，
  并按所选增量生成新版本。每日定时发版固定使用 `patch`。
- 手动推 `vX.Y.Z` tag 也会触发 Release：跳过版本号编排，直接复用 Quality Gates
  后从该 tag 构建发布。
```

- [ ] **Step 2: 确认 README 无残留旧链接**

Run:
```bash
grep -n "scheduled-release.yml\|ttl-diagnostics.yml" README.md
```
Expected: 无输出。

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: update release workflow references in README"
```

---

## Task 14: 全量校验与发版链路演练

**Files:**
- 无文件改动（只读校验）。

**Interfaces:**
- Consumes: Task 1–13 的全部产物。
- Produces: 确认两个 workflow YAML 合法、job 依赖图自洽、无残留重复/测试代码、触发器覆盖三条路径。

- [ ] **Step 1: 全量 YAML 语法校验**

Run:
```bash
uv run --with pyyaml python -c "
import yaml, glob
for f in glob.glob('.github/workflows/*.yml') + glob.glob('.github/actions/*/action.yml'):
    yaml.safe_load(open(f)); print('OK', f)
"
```
Expected: 列出 `ci.yml`、`release.yml`、两个 action.yml，全 OK，退出码 0。

- [ ] **Step 2: 校验 release.yml job 依赖图自洽**

Run（人工核对，无脚本）：
```bash
grep -nE "^  [a-z-]+:|^    needs:|^    if:|^    uses: \./" .github/workflows/release.yml
```
Expected（人工核对）：
- `detect`：无 needs，`if` 限 schedule/dispatch。
- `quality-gates`：`needs: detect`，`uses: ./.github/workflows/ci.yml`，`if` 放行 skipped 或 should_release。
- `prepare-release`：`needs: [detect, quality-gates]`，`if` 限 should_release + quality-gates success。
- `build`：`needs: [detect, quality-gates, prepare-release]`，`if` 放行 prepare success 或 (detect skipped + quality-gates success)。

- [ ] **Step 3: 校验 build job 零测试命令**

Run:
```bash
awk '/^  build:/{f=1} f&&/^  [a-z]/{if(NR>1&&prev!~/^  build:/)exit} {print} /^jobs:/{next}' .github/workflows/release.yml | grep -nE "pytest|ruff check|node --test|validate_matrix|run_table_contract_gate" || echo "PASS: build job has no test commands"
```
Expected: `PASS: build job has no test commands`。

- [ ] **Step 4: 校验 ci.yml 含 parity job 且无 workflow_call 自调用**

Run:
```bash
grep -nE "^  parity:|workflow_call" .github/workflows/ci.yml
```
Expected: 有 `  parity:` 行；`workflow_call` 仅在 `on:` 块出现一次（触发器声明），无 job 内自调用。

- [ ] **Step 5: 模拟三条触发路径的 job 流转（人工推理核对）**

针对三条路径，逐条确认哪些 job 会运行（基于 Step 2 的 needs/if）：

- **schedule / workflow_dispatch（main 有新提交）**：detect(runs, should_release=true) → quality-gates(runs) → prepare-release(runs) → build(runs, version=prepare-release.version)。
- **schedule / workflow_dispatch（main 无新提交）**：detect(runs, should_release=false) → quality-gates(skipped, if 不满足) → prepare-release(skipped) → build(skipped)。✓ 不空发版。
- **tag push（push v1.2.3）**：detect(skipped, if 限 schedule/dispatch) → quality-gates(runs, needs.detect.result=='skipped') → prepare-release(skipped, needs.detect.outputs.should_release 为空) → build(runs, version=GITHUB_REF_NAME)。
- **workflow_call（外部传 version）**：同 tag push 路径，detect/prepare-release skipped，version=inputs.version。

若任一路径流转不符预期，回到对应 Task 修正 needs/if。

- [ ] **Step 6: 推送分支触发真实 CI 验证**

把本分支推到 GitHub（或先开 PR），观察：
- ci.yml 在 PR/push 时全 job 绿（含新增 parity job）。
- 若条件允许，手动 workflow_dispatch 触发一次 Release（选 main + patch）观察 detect→quality-gates→prepare-release→build 全链路；或在低风险时段等一次定时触发。

Expected: ci 全绿；Release 链路按 Step 5 流转，发版产物正常（zip + sha256 上传到 Release）。

- [ ] **Step 7: 最终提交（若有 Step 6 的小修）**

若 Step 6 暴露问题并就地修复，提交修复；否则无操作。

```bash
git status   # 确认工作区干净
```
Expected: nothing to commit, working tree clean（或仅含本任务的小修）。

---

## Self-Review 记录

- **Spec 覆盖**：§3.1 文件变化 → Task 1/2（新增 action）、Task 12/13（删文件+README）；§3.2 composite → Task 1/2；§3.3 ci 变化（setup composite + parity job + wheels composite）→ Task 3/4/5；§3.4 release 变化（触发器/concurrency/三 job/build 改造）→ Task 6/7/8/9/10/11；§3.5 parity --require-pass → Task 5 Step 1 前置检查 + 硬门；§3.6 concurrency → Task 6 Step 2；§6 风险验证 → Task 5 Step 1（parity 红）、Task 14（全量校验 + 真实 CI）。全覆盖。
- **Placeholder 扫描**：无 TBD/TODO；每步含具体代码或命令；行号基于探索期读取的文件状态，Task 10 Step 3 因前置删除会导致行号偏移，已用"约第 147–194 行 + 步骤名定位"双重锚定。
- **类型/命名一致**：composite 名 `setup-python-buildshell` / `build-workspace-wheels` 在所有 Task 一致；job 名 `detect` / `quality-gates` / `prepare-release` / `build` / `parity` 在 needs/if 引用处一致；输出名 `should_release` / `current_version` / `commit_count` / `version` 在 produce/consume 处一致。
