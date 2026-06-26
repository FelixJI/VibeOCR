# develop → main 合并发版与 CHANGELOG 整合 设计

日期：2026-06-26
分支：develop
状态：已确认，待实施

## 背景与问题

项目当前双分支模型：

- `develop`：私有开发分支。
- `main`：开源发布分支，是发版锚点（`_create_release` 已用 `target_commitish="main"`）。

但现状与意图脱节，存在三处不一致：

1. **main 几乎是空的发版分支**：`main` 上没有 `version`、没有 `CHANGELOG.md`、不可达任何 tag；所有版本号（0.1.6）、完整 CHANGELOG（0.1.0~0.1.6 各一条）、所有 tag（v0.1.0~v0.1.6）都在 `develop` 上。develop 实际成了发版分支。
2. **打 tag 位置与发版目标不一致**：`bump_version.py` 在 develop 上打 tag，但发布 API 以 main 为锚点——同一个版本号在 develop 打了 tag，main 上却没有。
3. **CHANGELOG 冗余**：develop 的 CHANGELOG 为每个小版本（0.1.1、0.1.2…）各记一条，而这些细粒度提交 git 历史里本就有记录。CHANGELOG 重复了 git 提交，且这些稀碎条目若原样带到开源 main，会让公开变更史很嘈杂。

### 目标

- develop 继续细粒度开发与版本号前进；main 作为干净的开源发版分支。
- 借 `bump_version.py` 新增一个「合并至 main」操作，在合并的同时把 develop 上若干小版本的提交整合成 main 的一条正式版 CHANGELOG 条目。
- 修复 tag 打错分支的问题：正式 tag 只在 main 上。

## 决策汇总（来自逐项确认）

| 决策点 | 选择 |
|---|---|
| 版本号关系 | 两分支同步（合并后 main 版本号 = develop 最新） |
| 合并方式 | squash 合并（稀碎提交压成一条，开源历史干净） |
| CHANGELOG 整合粒度 | 按分类（Added/Changed/Fixed）合并全部提交，develop 中间小版本在 main 不单独出现 |
| 官方 tag 位置 | 只在 main（develop 不打 tag） |
| develop CHANGELOG | 不再维护细粒度记录（git 提交即记录），CHANGELOG 只在 main 维护 |
| 现有 develop CHANGELOG | 整理成 main 的初始版（开源首版完整变更史基线） |
| 交互方式 | 菜单可视化选择 + 命令行参数双入口，共用同一流程 |
| bump 后串联 | 选 1-4 bump 完后提示「是否合并至 main」，默认否 |
| 未版本化提交检测 | 选 5 打包时警告；选 6 合并发版时阻止并引导先 bump |

## 模型总表

| | develop（私有） | main（开源 / 发版） |
|---|---|---|
| 版本号 | 细粒度 bump（0.1.1 → 0.1.2 → …） | squash 合并后 = develop 最新 |
| git tag | **不打** | **唯一打 tag 处** `vX.Y.Z` |
| CHANGELOG.md | **不维护**（git 提交即记录） | **唯一维护处**，`--to-main` 时重建 |
| 提交历史 | 正常提交 + `release:` 提交 | 每次合并 = 1 条 squash 提交 |

核心洞察：CHANGELOG 本就该只在发版点生成，细粒度提交 git 已记录，CHANGELOG 里重复每个小版本是冗余。因此 develop 不再维护 CHANGELOG，CHANGELOG 只在 main 上、由 `--to-main` 在合并时重建。

## 一次性初始化（main 初始 CHANGELOG，手工）

main 当前 CHANGELOG 为空。首次开源发版需要一份完整的、可读的变更史作为基线。

- **手工**整理 develop 现有 `CHANGELOG.md`（0.1.0~0.1.6 各条）→ 归并成少数几条正式版条目，提交到 main，作为开源首版基线。
- 只做这一次。之后所有 main CHANGELOG 增长都由 `--to-main` 自动完成。
- 为何手工：历史归并需要人工判断归类与重点，不适合自动脚本。
- develop 上现有的 CHANGELOG.md 在此次初始化后从 develop 删除（develop 不再维护 CHANGELOG）。

### 首次初始化步骤（人工执行，一次性）

> **这是自动化 `--to-main` 跑通后、第一次真实发版前必须先做的手工步骤。**
> 之后所有 main CHANGELOG 增长都由 `python scripts/bump_version.py --to-main` 自动完成。

1. `git checkout main`
2. 手工整理 develop 现有 `CHANGELOG.md`（0.1.0~0.1.6 各条）为 main 的整合基线（归并成少数正式版条目），写入 main 的 `CHANGELOG.md`。
3. 从 develop 同步当前版本号到 main 的 `pyproject.toml`（`version = "0.1.6"`）与 `src/vibeocr/__init__.py`（`__version__ = "0.1.6"`）。
4. `git add CHANGELOG.md pyproject.toml src/vibeocr/__init__.py && git commit -m "release: v0.1.6"`
5. `git tag v0.1.6`
6. `git checkout develop && git merge main --no-edit`（让 develop 拿到整合 CHANGELOG）。
7. 之后每次发版跑 `python scripts/bump_version.py --to-main`（或菜单选 6）。

**注意**：首次执行前 develop 的 HEAD 必须是干净的 release 点（即 `release: v0.1.6` 提交 = HEAD），否则 `--to-main` 的未版本化检测会阻止。

## develop 的 bump 流程改动（瘦身）

现有 `main()` 版本升级路径（`bump_version.py:1078-1112`）做如下删减：

**删除：**
- `get_commits_since_last_tag()` 调用
- `update_changelog()` 调用
- `_open_editor(CHANGELOG)` 调用
- `git tag vX.Y.Z`（develop 不再打 tag）

**保留：**
- pyproject.toml / `__init__.py` / uv.lock 版本号更新
- `git commit -m "release: v{new_str}"`

develop 的 bump 变成纯「版本号前进 + release 提交」，不再碰 CHANGELOG 和 tag。

## 新增操作：合并至 main（`--to-main` / 菜单选项 6）

### 入口

两种入口共用同一个 `cmd_to_main()` 函数：

- **命令行参数**：`python scripts/bump_version.py --to-main`（适合脚本/CI）
- **交互式菜单选项 6**（见下文菜单）

### `cmd_to_main()` 流程

1. **预检**：当前分支 = develop；工作区干净；`git rev-list --count main..develop > 0`。
2. **未版本化提交检测**（关键防护）：找 `release: v{develop当前版本号}` 提交，若不等于 HEAD → 说明当前版本号之后还有未发版提交。
   - 检测到 → **阻止**，提示「检测到 v0.1.6 之后有 N 个未发版提交。发版前需先升级版本号。请先选 1-4 bump，再合并。」并中止。
   - 这是合并发版（打 tag + 推开源 main）的前置安全闸，版本号必须准确，不允许跳过。
3. **取版本**：读 develop pyproject 的 `V_new`。
4. **确认提示**（不可逆 git 操作前）：
   ```
   将执行：develop → main squash 合并 + 整合 CHANGELOG + 打 tag v{V_new}
   这会切换分支并创建提交。确认继续？[y/N]:
   ```
   默认 N，避免误触发。
5. **checkout main**（若失败，提示并中止，不回滚——操作显式可见）。
6. **`git merge --squash develop`**：代码改动 + 版本号入暂存区。
7. **重建 main CHANGELOG**：
   - `git log main..develop --pretty="%h %s"`，过滤 `release:` 前缀提交。
   - 复用 `categorize_commits` 分类成 Added/Changed/Fixed，去重 bullets。
   - 读 main 现有 CHANGELOG，在顶部首个 `## [` 之前插入一条 `## [V_new] - <date>`。main 无 CHANGELOG 则创建（`# Changelog\n` + 首条）。
8. **`git add CHANGELOG.md` + `git commit -m "release: v{V_new}"`**（含 squash 的代码改动 + 整合 CHANGELOG + 版本号）。
9. **`git tag v{V_new}`**（main 唯一 tag 来源）。
10. **切回 develop + 同步**：`git checkout develop && git merge main --no-edit`，让 develop 拿到 main 的整合 CHANGELOG 和版本号快照。
    - 此时 develop pyproject 版本号与 main 已一致（都 = V_new），merge 平顺。
    - CHANGELOG 以 main 的整合版为准（符合「develop 不维护细粒度 CHANGELOG」的决策）。
11. **提示**：可继续 `--release` 发布。

（编号 1-11 为叙述方便；"9 步" 仅作概称，以本列表为准。）

### CHANGELOG 重建算法说明

- **整合边界靠 git 历史，不靠外部状态文件**：`main..develop` 这个范围天然是「自上次整合以来 develop 新增的全部提交」，main 的 HEAD（= 上次 `--to-main` 的 squash commit）就是上次整合点。
- **逐次累积，不覆盖历史**：每次 merge 在 main CHANGELOG 顶部加一条，只往上加。
- **多次合并语义**：假设 main 在 0.1.0，develop 已到 0.1.6，`main..develop` 含 0.1.1~0.1.6 全部提交 → 全部揉进 main 的一条 `## [0.1.6]`。
- **前提**：每次 `--to-main` 都先 checkout main 再 squash merge，让 main HEAD 始终代表上次整合点。

## 交互式菜单集成

无参数运行 `bump_version.py` 进入 `interactive_menu()`，现有菜单 0-5，新增选项 6，并在选 1-4 后串联合并提示。

### 新菜单结构（建议分两组显示）

```
当前版本: 0.1.6
请选择操作:

  版本升级
    1) Patch  (修订号)  0.1.6 → 0.1.7
    2) Minor  (次版本)  0.1.6 → 0.2.0
    3) Major  (主版本)  0.1.6 → 1.0.0
    4) 自定义版本号

  打包 / 发版
    5) 仅打包当前版本（0.1.6，不升级版本号）
    6) 合并至 main（squash + 整合 CHANGELOG + 打 tag）
    0) 取消
请输入选项 [0-6]:
```

（分组是体验细节，单组列表亦可，不影响功能。）

### 各选项行为

**选 1-4（bump）：**
- 完成 develop 版本号前进 + `release:` 提交（不再生成 CHANGELOG、不打 tag）。
- 提示串联：`是否立即合并至 main 并发版？[y/N]:`（默认 N）。
- 选 y → 进入 `cmd_to_main()`（此时 HEAD 刚好 = 新的 release 点，未版本化检测通过）；合并流程结束后再按其内部提示决定是否打包/发布，**跳过**下方独立的打包询问。
- 选 N → 维持现有行为：询问「是否立即执行 PyInstaller 打包？」（`_ask_build`）。

即 bump 后的询问顺序为：先问「合并至 main？」（更重的操作优先），否决后再问「仅打包？」，避免两个询问冲突或让用户被重复打扰。

**选 5（仅打包当前版本）：**
- 未版本化提交检测：HEAD ≠ `release: v{当前版本}` 时 → **警告**：
  `当前版本 0.1.6 之后有 N 个未发版提交，打包内容将超出版本号标注。仍要打包？[y/N]:`
  （默认 N。打包可能是内部测试用途，版本号不严苛，故警告而非阻止。）
- 检测通过或用户确认 → 现有 `_run_build()` 流程不变。

**选 6（合并至 main）：**
- 未版本化提交检测：有未发版提交 → **阻止**，引导「请先选 1-4 bump，再合并」，中止。
- 检测通过 → 确认提示 → `cmd_to_main()` 完整流程。

## 未版本化提交检测算法

检测「当前版本号之后是否有未版本化提交」：

1. 读 develop 当前版本 `V_cur`（来自 pyproject）。
2. 找 `release: v{V_cur}` 提交：`git log --grep="^release: v{V_cur}$" --pretty=%H -1`。
3. 取 develop HEAD。
4. 两者相等 → 干净（HEAD 即 release 点）；不等 → 用 `git rev-list --count {release提交}..HEAD` 得到未版本化提交数 N。

此检测用于选 5（警告）与选 6（阻止），确保发版/打包时版本号与内容一致。

## 代码复用与新增

**复用（原样）：**
- `read_current_version`
- `categorize_commits`

**重构：**
- `get_commits_since_last_tag` → 抽出 `_collect_commits(rev_range: str)` 接受任意范围。develop bump 路径不再调用它；`--to-main` 用 `_collect_commits("main..develop")`。
- `generate_changelog_entry` → 改为接受分类后的 dict（避免在 develop 路径误用），或新增独立的整合版生成函数。

**新增：**
- `cmd_to_main()` 主函数（9 步流程）。
- `check_unversioned_commits(version: str) -> tuple[bool, int]`：返回（是否有未版本化提交, 数量 N）。
- `rebuild_main_changelog(version: str, rev_range: str) -> None`：重建 main CHANGELOG 条目。
- argparse `--to-main` 选项。
- `interactive_menu()` 选项 6 + 选 1-4 的串联提示 + 选 5/6 的未版本化检测分流。

**删除（develop bump 路径）：**
- `main()` 中 `get_commits_since_last_tag` / `update_changelog` / `_open_editor(CHANGELOG)` / `git tag` 调用。

## 发布路径

`_create_release` 已是 `target_commitish="main"`，与「tag 只在 main」完全自洽，无需改动。

## 与用户三个判断的对照

1. ✅ 稀碎提交 squash 成一条（开源历史干净）。
2. ✅ 单脚本统一管理（`bump_version.py` 新增 `--to-main` / 菜单选项 6）。
3. ✅ 合并时同步整合 changelog（`cmd_to_main` 第 7 步，与 commit 绑定，时序正确）。

## 边界与注意

- **main CHANGELOG 与 develop 不同**：main 是整合版，develop 不维护。这是有意设计，非 bug。
- **首次初始化是手工的一次性工作**，不进入自动流程。
- **未版本化检测是发版安全闸**：选 6 阻止、选 5 警告，避免版本号与内容脱节（尤其推到开源 main 的正式版）。
- **merge 回 develop 会改 develop 的 CHANGELOG**：develop 拿到 main 的整合版，这是「develop 不维护细粒度 CHANGELOG」决策的自然结果。
