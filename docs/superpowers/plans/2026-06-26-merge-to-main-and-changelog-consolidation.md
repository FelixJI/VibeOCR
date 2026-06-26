# develop → main 合并发版与 CHANGELOG 整合 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `scripts/bump_version.py` 中新增「合并至 main」操作（交互式菜单选项 6 + `--to-main` 参数），在 squash 合并 develop→main 的同时重建 main 的整合 CHANGELOG 并打 tag；同时让 develop 的 bump 流程瘦身（不再生成 CHANGELOG、不再打 tag）。

**Architecture:** 单脚本扩展。develop 的 bump 路径删除 CHANGELOG 生成与 tag；新增 `cmd_to_main()` 负责完整合并流程（预检→未版本化检测→squash merge→重建 CHANGELOG→commit→tag→同步回 develop）。CHANGELOG 整合边界靠 git 历史（`main..develop`），不依赖外部状态文件。新增未版本化提交检测函数作为发版安全闸。

**Tech Stack:** Python 3.13、stdlib only（subprocess/git、argparse、pathlib、re）；pytest（既有测试在 `tests/test_bump_version.py`，用 importlib 加载模块 + 子进程跑真实 git 仓库两种模式）。

**设计依据：** `docs/superpowers/specs/2026-06-26-merge-to-main-and-changelog-consolidation-design.md`（已确认）。

---

## 重要背景（必读）

1. **既有测试会因本次改动而失败**。以下 5 个测试断言 develop bump 会生成 CHANGELOG / 打 tag，本计划改完后它们必须更新（Task 5、Task 6）：
   - `TestChangelogGeneration::test_changelog_created`
   - `TestChangelogGeneration::test_changelog_with_commits`
   - `TestGitTagging::test_git_tag_created`
   - `TestGitTagging::test_major_tag`
   - `TestGitTagging::test_git_commit_created`（这个仍应通过，只验证 commit）

2. **测试隔离约定**：所有测试通过 `importlib` 加载 `bump_version.py` 为模块，加载前把 `PYPROJECT_TOML/INIT_PY/MAIN_PY/CHANGELOG` 环境变量置空（见 `tests/test_bump_version.py` 的 `_load_module` 模式）。涉及 git 的端到端测试用子进程在 `tmp_path` 里建真实 git 仓库（见 `_run_bump`）。

3. **CHANGELOG 路径环境变量**：脚本通过 `os.environ.get("CHANGELOG", ...)` 读取 CHANGELOG 路径常量。测试可覆盖它指向 `tmp_path`。新增的 main CHANGELOG 操作也必须通过环境变量可覆盖。

4. **菜单哨兵约定**：`interactive_menu()` 返回 `"build"`（仅打包）/ `None`（取消）/ 版本元组。新增 `"merge"` 哨兵表示合并至 main。

5. **模块级常量**：脚本顶部有 `CHANGELOG = Path(os.environ.get("CHANGELOG", ...))`。`cmd_to_main` 涉及 main 分支的 CHANGELOG，仍是同一个文件路径（main 上 checkout 后工作区里的 CHANGELOG.md），无需新增常量。

---

## 文件结构

- **修改** `scripts/bump_version.py`：
  - `get_commits_since_last_tag()` → 重构抽出 `_collect_commits(rev_range: str)`。
  - 新增 `_filter_release_commits()`、`generate_consolidated_entry()`、`check_unversioned_commits()`、`cmd_to_main()` 及预检/重建子函数。
  - 修改 `interactive_menu()`：菜单加选项 6，返回 `"merge"` 哨兵。
  - 修改 `main()`：develop bump 路径删 CHANGELOG/tag；新增 `--to-main` 分支与 `"merge"` 哨兵处理；bump 后串联合并提示。
  - `_Args` 类加字段。
- **修改** `tests/test_bump_version.py`：更新 5 个因瘦身而失败的测试；新增多个测试类覆盖新函数。

每个 Task 产出可独立提交、可独立测试的改动。

---

## Task 1: 重构 `_collect_commits(rev_range)` 并新增 `_filter_release_commits`

把 `get_commits_since_last_tag` 的「取提交」逻辑抽成可接受任意范围的形式，为 `cmd_to_main` 复用铺路。`get_commits_since_last_tag` 保留为对 `_collect_commits` 的薄封装（develop 路径 Task 5 才删除其调用，这里先保证向后兼容）。

**Files:**
- Modify: `scripts/bump_version.py:256-295`（`get_commits_since_last_tag`）
- Test: `tests/test_bump_version.py`

- [ ] **Step 1: 写失败测试 —— `_collect_commits` 接受任意范围**

在 `tests/test_bump_version.py` 末尾新增测试类：

```python
class TestCollectCommits:
    """测试 _collect_commits 按任意 git 范围收集提交"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_collect_commits_returns_hash_subject_tuples(self, tmp_path):
        """_collect_commits 返回 [(hash, subject), ...]，覆盖给定范围内的提交"""
        mod = self._load_module()
        import subprocess

        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "feat: one"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(["git", "tag", "v0.1.0"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "fix: two"],
            cwd=tmp_path,
            capture_output=True,
        )

        commits = mod._collect_commits("v0.1.0..HEAD", cwd=tmp_path)
        subjects = [s for _, s in commits]
        assert "fix: two" in subjects
        assert "feat: one" not in subjects  # 在范围之外（tag 之前）

    def test_filter_release_commits_drops_release_prefix(self):
        """_filter_release_commits 过滤掉 release: 前缀提交"""
        mod = self._load_module()
        commits = [
            ("aaa1111", "feat: a"),
            ("bbb2222", "release: v0.1.5"),
            ("ccc3333", "fix: b"),
            ("ddd4444", "release: v0.1.6"),
        ]
        filtered = mod._filter_release_commits(commits)
        subjects = [s for _, s in filtered]
        assert "feat: a" in subjects
        assert "fix: b" in subjects
        assert not any("release:" in s for s in subjects)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_bump_version.py::TestCollectCommits -v`
Expected: FAIL —— `AttributeError: module 'bump_version' has no attribute '_collect_commits'`

- [ ] **Step 3: 重构实现 —— 抽出 `_collect_commits` 并新增 `_filter_release_commits`**

把 `scripts/bump_version.py:256-295` 的 `get_commits_since_last_tag` 替换为下面两个函数。注意 `_collect_commits` 新增可选 `cwd` 参数（测试在 `tmp_path` 跑 git，默认 `PROJECT_ROOT` 兼容生产）：

```python
def _collect_commits(
    rev_range: str, cwd: Path | None = None
) -> list[tuple[str, str]]:
    """收集给定 git 范围内的提交

    Args:
        rev_range: git 修订范围，如 "v0.1.0..HEAD" 或 "main..develop"
        cwd: git 仓库目录，默认 PROJECT_ROOT

    Returns:
        [(hash, subject), ...] 列表
    """
    cmd = ["git", "log", "--pretty=format:%h %s", rev_range]
    work_dir = str(cwd) if cwd else str(PROJECT_ROOT)

    try:
        result = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    commits: list[tuple[str, str]] = []
    stdout = result.stdout or ""
    for line in stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2:
            commits.append((parts[0], parts[1]))
        else:
            commits.append((parts[0], ""))
    return commits


def _filter_release_commits(
    commits: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """过滤掉 ``release: vX.Y.Z`` 提交（版本边界，非功能性改动）

    Args:
        commits: [(hash, subject), ...]

    Returns:
        不含 release: 前缀提交的列表
    """
    return [(h, s) for h, s in commits if not s.startswith("release: ")]


def get_commits_since_last_tag() -> list[tuple[str, str]]:
    """获取自上次 tag 以来的 git 提交（develop bump 路径用，保留向后兼容）"""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        last_tag = result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        last_tag = ""

    if last_tag:
        return _collect_commits(f"{last_tag}..HEAD")
    return _collect_commits("HEAD")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_bump_version.py::TestCollectCommits -v`
Expected: PASS（2 个测试通过）

- [ ] **Step 5: 回归现有测试**

Run: `python -m pytest tests/test_bump_version.py -v`
Expected: 全部通过（`get_commits_since_last_tag` 行为不变，仅内部委托 `_collect_commits`）

- [ ] **Step 6: 提交**

```bash
git add scripts/bump_version.py tests/test_bump_version.py
git commit -m "refactor(bump): 抽出 _collect_commits/_filter_release_commits 供合并复用"
```

---

## Task 2: 新增 `generate_consolidated_entry()` —— 整合后的单条 CHANGELOG 生成

封装「分类 + 过滤 release + 去重 + 生成单条」逻辑，`cmd_to_main` 直接调用。与既有 `generate_changelog_entry` 区分（后者接原始 commits）。

**Files:**
- Modify: `scripts/bump_version.py`（在 `generate_changelog_entry` 之后新增）
- Test: `tests/test_bump_version.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_bump_version.py` 新增测试类：

```python
class TestGenerateConsolidatedEntry:
    """测试 generate_consolidated_entry 整合生成单条 CHANGELOG 条目"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_filters_release_commits_and_categorizes(self):
        """自动过滤 release: 提交，剩余按 Added/Fixed/Changed 分类"""
        mod = self._load_module()
        commits = [
            ("h1", "feat: add X"),
            ("h2", "release: v0.1.5"),
            ("h3", "fix: bug Y"),
            ("h4", "chore: cleanup"),
            ("h5", "release: v0.1.6"),
        ]
        entry = mod.generate_consolidated_entry("0.1.6", commits)
        assert "## [0.1.6]" in entry
        assert "feat: add X" in entry
        assert "fix: bug Y" in entry
        assert "chore: cleanup" in entry
        assert "release: v0.1.5" not in entry
        assert "release: v0.1.6" not in entry

    def test_dedupes_identical_subjects(self):
        """相同 subject 只保留一条"""
        mod = self._load_module()
        commits = [
            ("h1", "feat: add X"),
            ("h2", "feat: add X"),  # 重复
            ("h3", "feat: add X"),  # 重复
        ]
        entry = mod.generate_consolidated_entry("0.1.6", commits)
        assert entry.count("feat: add X") == 1

    def test_omits_empty_categories(self):
        """空分类不出现在条目中"""
        mod = self._load_module()
        commits = [("h1", "feat: only added")]
        entry = mod.generate_consolidated_entry("0.1.6", commits)
        assert "### Added" in entry
        assert "### Fixed" not in entry
        assert "### Changed" not in entry
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_bump_version.py::TestGenerateConsolidatedEntry -v`
Expected: FAIL —— `AttributeError: module 'bump_version' has no attribute 'generate_consolidated_entry'`

- [ ] **Step 3: 实现 `generate_consolidated_entry`**

在 `scripts/bump_version.py` 的 `generate_changelog_entry` 函数之后（约 `update_changelog` 之前）新增：

```python
def generate_consolidated_entry(
    version: str, commits: list[tuple[str, str]]
) -> str:
    """生成合并至 main 用的单条整合 CHANGELOG 条目

    与 generate_changelog_entry 区别：自动过滤 release: 提交、对 subject 去重。

    Args:
        version: 整合后的目标版本号字符串（如 "0.1.6"）
        commits: 范围内的原始提交列表（含 release: 提交，会被过滤）

    Returns:
        格式化的单条 CHANGELOG 条目（## [version] - 日期 + 分类）
    """
    today = date.today().isoformat()
    lines: list[str] = [f"## [{version}] - {today}", ""]

    filtered = _filter_release_commits(commits)
    # 按 subject 去重，保留首次出现顺序
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for h, s in filtered:
        if s not in seen:
            seen.add(s)
            deduped.append((h, s))

    categories = categorize_commits(deduped)
    for cat_name, cat_commits in categories.items():
        if not cat_commits:
            continue
        lines.append(f"### {cat_name}")
        for commit_msg in cat_commits:
            lines.append(f"- {commit_msg}")
        lines.append("")

    return "\n".join(lines)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_bump_version.py::TestGenerateConsolidatedEntry -v`
Expected: PASS（3 个测试通过）

- [ ] **Step 5: 提交**

```bash
git add scripts/bump_version.py tests/test_bump_version.py
git commit -m "feat(bump): 新增 generate_consolidated_entry 整合生成单条 CHANGELOG"
```

---

## Task 3: 新增 `check_unversioned_commits()` —— 未版本化提交检测

发版安全闸。判断 develop HEAD 是否等于 `release: v{版本}` 提交，并统计其后的提交数。

**Files:**
- Modify: `scripts/bump_version.py`
- Test: `tests/test_bump_version.py`

- [ ] **Step 1: 写失败测试**

新增测试类：

```python
class TestCheckUnversionedCommits:
    """测试 check_unversioned_commits 发版前未版本化提交检测"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_clean_head_is_release_point(self, tmp_path):
        """HEAD 恰为 release: v0.1.0 提交 → 无未版本化提交"""
        import subprocess

        mod = self._load_module()
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "release: v0.1.0"],
            cwd=tmp_path,
            capture_output=True,
        )

        has, n = mod.check_unversioned_commits("0.1.0", cwd=tmp_path)
        assert has is False
        assert n == 0

    def test_commits_after_release_point_detected(self, tmp_path):
        """release 之后还有 2 个提交 → 检测到，n=2"""
        import subprocess

        mod = self._load_module()
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "release: v0.1.0"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "feat: a"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "fix: b"],
            cwd=tmp_path,
            capture_output=True,
        )

        has, n = mod.check_unversioned_commits("0.1.0", cwd=tmp_path)
        assert has is True
        assert n == 2

    def test_no_release_commit_found_treats_all_as_unversioned(self, tmp_path):
        """找不到 release: vX 提交 → 视为全部未版本化（保守策略）"""
        import subprocess

        mod = self._load_module()
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "feat: a"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "feat: b"],
            cwd=tmp_path,
            capture_output=True,
        )

        has, n = mod.check_unversioned_commits("9.9.9", cwd=tmp_path)
        assert has is True
        # 找不到 release 点，n 取全部提交数（>=2）
        assert n >= 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_bump_version.py::TestCheckUnversionedCommits -v`
Expected: FAIL —— `AttributeError: module 'bump_version' has no attribute 'check_unversioned_commits'`

- [ ] **Step 3: 实现 `check_unversioned_commits`**

在 `scripts/bump_version.py` 新增（放在 `_filter_release_commits` 之后）：

```python
def check_unversioned_commits(
    version: str, cwd: Path | None = None
) -> tuple[bool, int]:
    """检测当前版本号之后是否有未版本化提交（发版安全闸）

    找 ``release: v{version}`` 提交，比较它与 HEAD：
    - 相等 → HEAD 即 release 点，干净，返回 (False, 0)。
    - 不等 → 用 rev-list 统计其后提交数，返回 (True, N)。
    - 找不到 release 提交 → 保守视为全部未版本化，
      N 取该仓库全部提交数。

    Args:
        version: 当前版本号字符串（如 "0.1.6"）
        cwd: git 仓库目录，默认 PROJECT_ROOT

    Returns:
        (是否有未版本化提交, 未版本化提交数)
    """
    work_dir = str(cwd) if cwd else str(PROJECT_ROOT)

    # 找 release 提交的完整 hash
    try:
        result = subprocess.run(
            ["git", "log", "--grep", f"^release: v{version}$", "--pretty=%H", "-1"],
            cwd=work_dir,
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        release_hash = (result.stdout or "").strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        release_hash = ""

    if not release_hash:
        # 找不到 release 点：保守统计全部提交数
        try:
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=work_dir,
                capture_output=True,
                encoding="utf-8",
                check=True,
            )
            total = int((result.stdout or "0").strip() or "0")
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            total = 0
        return (True, total)

    # release 点之后有多少提交
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{release_hash}..HEAD"],
            cwd=work_dir,
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        count = int((result.stdout or "0").strip() or "0")
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        count = 0

    return (count > 0, count)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_bump_version.py::TestCheckUnversionedCommits -v`
Expected: PASS（3 个测试通过）

- [ ] **Step 5: 提交**

```bash
git add scripts/bump_version.py tests/test_bump_version.py
git commit -m "feat(bump): 新增 check_unversioned_commits 发版安全闸"
```

---

## Task 4: 新增 `update_main_changelog()` —— 在 main CHANGELOG 顶部插入整合条目

封装「读 main CHANGELOG → 顶部插入 → 写回」。复用既有 `update_changelog` 的插入位置逻辑，但接受已生成好的 entry 文本。

**Files:**
- Modify: `scripts/bump_version.py`
- Test: `tests/test_bump_version.py`

- [ ] **Step 1: 写失败测试**

新增测试类：

```python
class TestUpdateMainChangelog:
    """测试 update_main_changelog 在 main CHANGELOG 顶部插入整合条目"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_creates_changelog_when_absent(self, tmp_path):
        """main 无 CHANGELOG 时创建（# Changelog + 首条）"""
        mod = self._load_module()
        changelog = tmp_path / "CHANGELOG.md"
        mod.CHANGELOG = changelog
        entry = "## [0.2.0] - 2026-06-26\n\n### Added\n- feat: X\n"

        mod.update_main_changelog(entry)

        content = changelog.read_text(encoding="utf-8")
        assert content.startswith("# Changelog")
        assert "0.2.0" in content

    def test_inserts_new_entry_above_existing(self, tmp_path):
        """新条目插在现有最新条目之上（顶部）"""
        mod = self._load_module()
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(
            "# Changelog\n\n## [0.1.0] - 2025-01-01\n\n### Added\n- init\n",
            encoding="utf-8",
        )
        mod.CHANGELOG = changelog
        entry = "## [0.2.0] - 2026-06-26\n\n### Added\n- feat: X\n"

        mod.update_main_changelog(entry)

        content = changelog.read_text(encoding="utf-8")
        pos_new = content.index("0.2.0")
        pos_old = content.index("0.1.0")
        assert pos_new < pos_old
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_bump_version.py::TestUpdateMainChangelog -v`
Expected: FAIL —— `AttributeError: module 'bump_version' has no attribute 'update_main_changelog'`

- [ ] **Step 3: 实现 `update_main_changelog`**

在 `scripts/bump_version.py` 的 `update_changelog` 函数之后新增：

```python
def update_main_changelog(entry: str) -> None:
    """把整合条目插入 main 的 CHANGELOG.md 顶部

    与 update_changelog 区别：接受已生成好的 entry 文本（由
    generate_consolidated_entry 产出），不复算分类。

    Args:
        entry: 单条 CHANGELOG 条目文本（含 ``## [version]`` 标题）
    """
    if CHANGELOG.exists():
        content = CHANGELOG.read_text(encoding="utf-8")
    else:
        content = "# Changelog\n"

    # 在第一个 ## 标题前插入（与 update_changelog 同逻辑）
    idx = content.find("\n## ")
    if idx >= 0:
        insert_pos = idx + 1
        content = content[:insert_pos] + entry + "\n" + content[insert_pos:]
    else:
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + entry

    CHANGELOG.write_text(content, encoding="utf-8")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_bump_version.py::TestUpdateMainChangelog -v`
Expected: PASS（2 个测试通过）

- [ ] **Step 5: 提交**

```bash
git add scripts/bump_version.py tests/test_bump_version.py
git commit -m "feat(bump): 新增 update_main_changelog 顶部插入整合条目"
```

---

## Task 5: develop 的 bump 路径瘦身 —— 删除 CHANGELOG 生成与 tag

修改 `main()` 的版本升级路径：不再调 `get_commits_since_last_tag` / `update_changelog` / `_open_editor` / `git tag`。同时更新因之失败的既有测试。

**Files:**
- Modify: `scripts/bump_version.py:1074-1122`（`main()` 版本升级路径）
- Modify: `tests/test_bump_version.py`（更新失败测试）

- [ ] **Step 1: 更新会失败的测试（先红后绿）**

注意：此 Task 改完后旧测试会失败，故先改测试断言为新预期行为，跑一次确认它们因实现未改而失败，再改实现。

在 `tests/test_bump_version.py`：

1. `TestChangelogGeneration::test_changelog_created`（277-285 行）—— 改为断言 CHANGELOG **不再被创建**：

```python
    def test_changelog_not_created_on_bump(self, tmp_path):
        """验证 develop bump 不再生成 CHANGELOG（CHANGELOG 改由 --to-main 维护）"""
        result = _run_bump(tmp_path, ["patch", "--no-edit", "--no-build"])
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        changelog = tmp_path / "CHANGELOG.md"
        assert not changelog.exists(), "develop bump 不应生成 CHANGELOG"
```

（把原方法名 `test_changelog_created` 改为 `test_changelog_not_created_on_bump`。）

2. `TestChangelogGeneration::test_changelog_with_commits`（287-309 行）—— 删除整个方法（它测的是 bump 生成 CHANGELOG 的分类，已不适用）。删除后该类仅保留 `test_categorize_commits` 和 `test_changelog_inserts_before_existing`。

3. `TestGitTagging::test_git_tag_created`（417-430 行）—— 改为断言 develop bump **不再打 tag**：

```python
    def test_no_tag_created_on_develop_bump(self, tmp_path):
        """验证 develop bump 不再打 tag（tag 改由 --to-main 在 main 上打）"""
        result = _run_bump(tmp_path, ["patch", "--no-edit", "--no-build"])
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        tag_result = subprocess.run(
            ["git", "tag", "-l"],
            cwd=tmp_path,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        tags = [t for t in tag_result.stdout.strip().split("\n") if t]
        assert "v0.1.1" not in tags, "develop bump 不应打 tag"
        # 仅保留测试夹具里打的 v0.1.0
        assert tags == ["v0.1.0"]
```

4. `TestGitTagging::test_major_tag`（446-459 行）—— 删除整个方法（同理不再适用）。

5. `TestGitTagging::test_git_commit_created`（432-444 行）—— 保留不变（commit 仍生成，断言 `release: v0.2.0` 仍成立）。

- [ ] **Step 2: 运行测试确认它们按预期失败**

Run: `python -m pytest tests/test_bump_version.py::TestChangelogGeneration tests/test_bump_version.py::TestGitTagging -v`
Expected: FAIL —— `test_changelog_not_created_on_bump` 失败（CHANGELOG 仍被创建）、`test_no_tag_created_on_develop_bump` 失败（tag 仍被打）。`test_git_commit_created` 应仍通过。

- [ ] **Step 3: 改实现 —— develop bump 路径删除 CHANGELOG/tag**

把 `scripts/bump_version.py:1074-1122`（从 `new_str = ...` 到函数结尾）替换为：

```python
    new_str = ".".join(map(str, new_version))
    print(f"版本升级: {current_str} → {new_str}")

    # 更新版本号文件（develop bump 只前进版本号 + release 提交；
    # CHANGELOG 与 tag 改由 --to-main 在 main 上维护）
    update_file_version(PYPROJECT_TOML, current_str, new_str)
    print(f"  已更新 {PYPROJECT_TOML}")

    if INIT_PY.exists():
        update_file_version(INIT_PY, current_str, new_str)
        print(f"  已更新 {INIT_PY}")

    # 注意：main.py 通过 __version__ 引用版本号（无字面量），无需在此更新。

    # 同步 uv.lock（pyproject 版本号已变，锁文件需刷新避免滞后漂移）
    _sync_uv_lock(new_str)

    # Git 操作（develop 不打 tag —— tag 只在 main 上由 --to-main 创建）
    try:
        subprocess.run(["git", "add", str(PYPROJECT_TOML)], check=True)
        if INIT_PY.exists():
            subprocess.run(["git", "add", str(INIT_PY)], check=True)
        if UV_LOCK.exists():
            subprocess.run(["git", "add", str(UV_LOCK)], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"release: v{new_str}"], check=True
        )
        print(f"  已创建 git commit release: v{new_str}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"警告: git 操作失败: {e}")
        return 1

    # 询问是否打包
    if not args.no_build and _ask_build(new_str):
        _run_build(new_str)

    print(f"\n完成! 版本已升级到 {new_str}")
    return 0
```

- [ ] **Step 4: 运行全套 bump 测试确认通过**

Run: `python -m pytest tests/test_bump_version.py -v`
Expected: 全部 PASS（含更新后的 Changelog/Tagging 测试）

- [ ] **Step 5: 提交**

```bash
git add scripts/bump_version.py tests/test_bump_version.py
git commit -m "refactor(bump): develop bump 瘦身，不再生成 CHANGELOG/打 tag"
```

---

## Task 6: `interactive_menu()` 新增选项 6 + 返回 `"merge"` 哨兵

菜单加「合并至 main」选项，返回 `"merge"` 哨兵。`main()` 在交互式分支识别该哨兵调用 `cmd_to_main`（Task 7 实现函数体，Task 8 接线）。

**Files:**
- Modify: `scripts/bump_version.py:395-440`（`interactive_menu`）
- Test: `tests/test_bump_version.py`

- [ ] **Step 1: 写失败测试**

新增测试类：

```python
class TestInteractiveMenuMergeOption:
    """交互式菜单的"合并至 main"选项 6 测试"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_option_6_returns_merge_sentinel(self, monkeypatch):
        """选项 6 应返回哨兵 'merge'"""
        mod = self._load_module()
        monkeypatch.setattr("builtins.input", lambda _prompt="": "6")

        result = mod.interactive_menu((0, 1, 6))

        assert result == "merge", f"选项 6 应返回 'merge'，实际: {result!r}"

    def test_menu_lists_option_6(self, capsys, monkeypatch):
        """菜单输出应包含"合并至 main"描述"""
        mod = self._load_module()
        monkeypatch.setattr("builtins.input", lambda _prompt="": "0")
        mod.interactive_menu((0, 1, 6))
        captured = capsys.readouterr()
        assert "合并至 main" in captured.out
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_bump_version.py::TestInteractiveMenuMergeOption -v`
Expected: FAIL —— 选项 6 未识别（返回 None 或被当作取消）

- [ ] **Step 3: 修改 `interactive_menu`**

把 `scripts/bump_version.py:395-440` 的 `interactive_menu` 中菜单打印与返回部分扩展。修改 print 块，在选项 5 之后加选项 6；在返回逻辑加哨兵：

找到这段：
```python
    print("  4) 自定义版本号")
    print(f"  5) 仅打包当前版本（{current_str}，不升级版本号）")
    print("  0) 取消")
    print("请输入选项 [0-5]: ", end="", flush=True)
```
替换为：
```python
    print("  4) 自定义版本号")
    print(f"  5) 仅打包当前版本（{current_str}，不升级版本号）")
    print("  6) 合并至 main（squash + 整合 CHANGELOG + 打 tag）")
    print("  0) 取消")
    print("请输入选项 [0-6]: ", end="", flush=True)
```

找到返回逻辑末尾（`if choice == "5":` 块之后、`return None` 之前），插入：
```python
    if choice == "6":
        # 合并至 main：由 main() 识别 'merge' 哨兵后调用 cmd_to_main
        return "merge"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_bump_version.py::TestInteractiveMenuMergeOption -v`
Expected: PASS（2 个测试通过）

- [ ] **Step 5: 回归既有菜单测试**

Run: `python -m pytest tests/test_bump_version.py::TestInteractiveMenuBuildOption -v`
Expected: PASS（选项 0/1/5 行为不变）

- [ ] **Step 6: 提交**

```bash
git add scripts/bump_version.py tests/test_bump_version.py
git commit -m "feat(bump): 交互式菜单新增选项 6 合并至 main"
```

---

## Task 7: 实现 `cmd_to_main()` —— 完整合并流程

整合 Task 1-4 的函数 + git 操作，实现 9 步合并流程。这是核心，但因前几个 Task 已把可测函数抽好，本 Task 主要是编排 + git 端到端测试。

**Files:**
- Modify: `scripts/bump_version.py`
- Test: `tests/test_bump_version.py`

- [ ] **Step 1: 写端到端失败测试 —— 双分支 git 仓库**

新增测试类，用子进程在 `tmp_path` 建一个含 main/develop 两分支的真实仓库，跑 `--to-main`，断言合并结果：

```python
class TestCmdToMain:
    """测试 cmd_to_main 端到端合并流程（双分支真实 git 仓库）"""

    def _setup_two_branch_repo(self, tmp_path):
        """建一个含 main + develop 两分支、develop 领先的仓库"""
        import subprocess

        def git(*args):
            subprocess.run(["git", *args], cwd=tmp_path, capture_output=True)

        git("init", "-b", "main")
        git("config", "user.email", "t@t.com")
        git("config", "user.name", "T")
        # main 初始提交 + CHANGELOG
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "vibeocr"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (tmp_path / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [0.1.0] - 2025-01-01\n\n### Added\n- init\n",
            encoding="utf-8",
        )
        git("add", ".")
        git("commit", "-m", "init main")
        git("tag", "v0.1.0")
        # develop 分支 + 领先若干提交 + release
        git("checkout", "-b", "develop")
        git("commit", "--allow-empty", "-m", "feat: feature A")
        git("commit", "--allow-empty", "-m", "fix: bug B")
        # bump 到 0.1.1（develop 路径：版本号前进 + release 提交，无 tag）
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "vibeocr"\nversion = "0.1.1"\n', encoding="utf-8"
        )
        git("add", "pyproject.toml")
        git("commit", "-m", "release: v0.1.1")
        return tmp_path

    def test_merge_consolidates_and_tags_on_main(self, tmp_path):
        """--to-main 后：main 有 release commit、v0.1.1 tag、整合 CHANGELOG"""
        import os
        import subprocess
        import sys

        repo = self._setup_two_branch_repo(tmp_path)

        env = os.environ.copy()
        env["CHANGELOG"] = str(repo / "CHANGELOG.md")
        env["UV_LOCK"] = str(repo / "uv.lock")  # 不存在，跳过同步

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--to-main", "--no-edit"],
            cwd=repo,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            # 用管道喂入确认提示的 "y"
            input="y\n",
        )
        assert result.returncode == 0, f"失败: {result.stdout}\n{result.stderr}"

        # 当前应在 develop
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo, capture_output=True, encoding="utf-8",
        ).stdout.strip()

        # main 上应有 v0.1.1 tag
        tags = subprocess.run(
            ["git", "tag", "-l"], cwd=repo, capture_output=True, encoding="utf-8",
        ).stdout.strip().split("\n")
        assert "v0.1.1" in tags

        # CHANGELOG 顶部应有 0.1.1 整合条目，含 feature A / bug B
        content = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
        assert "0.1.1" in content
        assert "feat: feature A" in content
        assert "fix: bug B" in content
        # 0.1.1 整合条目在 0.1.0 之前
        assert content.index("0.1.1") < content.index("0.1.0")

    def test_blocks_when_unversioned_commits_exist(self, tmp_path):
        """develop release 之后还有未版本化提交 → --to-main 阻止并引导先 bump"""
        import os
        import subprocess
        import sys

        repo = self._setup_two_branch_repo(tmp_path)
        # 在 release v0.1.1 之后再加提交（未版本化）
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "feat: unversioned"],
            cwd=repo, capture_output=True,
        )

        env = os.environ.copy()
        env["CHANGELOG"] = str(repo / "CHANGELOG.md")
        env["UV_LOCK"] = str(repo / "uv.lock")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--to-main", "--no-edit"],
            cwd=repo, capture_output=True, encoding="utf-8", errors="replace",
            env=env, input="y\n",
        )
        # 应中止（非零退出），且不切到 main、不打 tag
        assert result.returncode != 0
        assert "未发版" in result.stdout or "未版本化" in result.stdout
        tags = subprocess.run(
            ["git", "tag", "-l"], cwd=repo, capture_output=True, encoding="utf-8",
        ).stdout.strip().split("\n")
        assert "v0.1.1" not in tags
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_bump_version.py::TestCmdToMain -v`
Expected: FAIL —— `--to-main` 参数未识别（argparse 报错）

- [ ] **Step 3: 实现 `cmd_to_main` 与预检子函数**

在 `scripts/bump_version.py` 新增（放在 `_ask_build` 之后、`_Args` 之前）：

```python
def _current_branch() -> str:
    """返回当前 git 分支名"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _working_tree_clean() -> bool:
    """工作区是否干净（无未提交改动）"""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        return result.stdout.strip() == ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def cmd_to_main(skip_confirm: bool = False) -> int:
    """合并 develop → main：squash + 整合 CHANGELOG + 打 tag + 同步回 develop

    完整流程见设计文档 §「cmd_to_main() 流程」。返回退出码。

    Args:
        skip_confirm: True 跳过确认提示（用于已在上游确认的场景）

    Returns:
        0=成功, 1=失败/中止
    """
    # 1. 预检
    if _current_branch() != "develop":
        print("错误: 必须在 develop 分支上执行 --to-main")
        return 1
    if not _working_tree_clean():
        print("错误: 工作区不干净，请先提交或暂存改动")
        return 1

    try:
        ahead = subprocess.run(
            ["git", "rev-list", "--count", "main..develop"],
            capture_output=True, encoding="utf-8", check=True,
        )
        n_ahead = int(ahead.stdout.strip() or "0")
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        print("错误: 无法确定 develop 相对 main 的领先提交数（main 分支是否存在？）")
        return 1
    if n_ahead == 0:
        print("develop 与 main 无差异，无需合并")
        return 1

    # 2. 未版本化提交检测（发版安全闸）
    current = read_current_version(PYPROJECT_TOML)
    current_str = ".".join(map(str, current))
    has_unversioned, n_unversioned = check_unversioned_commits(current_str)
    if has_unversioned:
        print(
            f"错误: 检测到 v{current_str} 之后有 {n_unversioned} 个未发版提交。"
            "发版前需先升级版本号（选 1-4 bump），再合并。"
        )
        return 1

    # 3-4. 取版本 + 确认
    print(
        f"将执行：develop → main squash 合并 + 整合 CHANGELOG + 打 tag v{current_str}"
    )
    print("这会切换分支并创建提交。确认继续？[y/N]: ", end="", flush=True)
    if not skip_confirm:
        choice = input().strip().lower()
        if choice not in ("y", "yes"):
            print("已取消")
            return 1

    v_new = current_str

    # 5. checkout main
    try:
        subprocess.run(["git", "checkout", "main"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"错误: 切换到 main 失败: {e}")
        return 1

    # 6. squash merge
    try:
        subprocess.run(["git", "merge", "--squash", "develop"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"错误: squash 合并失败: {e}")
        return 1

    # 7. 重建 main CHANGELOG
    commits = _collect_commits("main..develop")
    entry = generate_consolidated_entry(v_new, commits)
    update_main_changelog(entry)
    print(f"  已整合 CHANGELOG（v{v_new}，{len(commits)} 个提交）")

    # 8. commit
    try:
        subprocess.run(["git", "add", str(CHANGELOG)], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"release: v{v_new}"], check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"错误: 提交失败: {e}")
        return 1

    # 9. tag
    try:
        subprocess.run(["git", "tag", f"v{v_new}"], check=True)
        print(f"  已在 main 打 tag v{v_new}")
    except subprocess.CalledProcessError as e:
        print(f"警告: 打 tag 失败: {e}")

    # 10. 同步回 develop
    try:
        subprocess.run(["git", "checkout", "develop"], check=True)
        subprocess.run(
            ["git", "merge", "main", "--no-edit"], check=True
        )
        print("  已同步整合 CHANGELOG 回 develop")
    except subprocess.CalledProcessError as e:
        print(f"警告: 同步回 develop 失败: {e}")

    print(f"\n完成! main 已更新到 v{v_new}，可用 --release 发布")
    return 0
```

- [ ] **Step 4: 运行端到端测试确认通过**

Run: `python -m pytest tests/test_bump_version.py::TestCmdToMain -v`
Expected: PASS（2 个测试通过）

- [ ] **Step 5: 提交**

```bash
git add scripts/bump_version.py tests/test_bump_version.py
git commit -m "feat(bump): 实现 cmd_to_main 合并至 main 完整流程"
```

---

## Task 8: 接线 `--to-main` 参数 + `"merge"` 哨兵到 `main()`

让命令行 `--to-main` 和菜单选项 6 都能触发 `cmd_to_main`；同时为选项 5（打包）加未版本化检测警告、为选项 1-4 加串联合并提示。

**Files:**
- Modify: `scripts/bump_version.py`（`_Args` 类 + `main()` 入口分支 + 选项 5 警告 + 选项 1-4 串联）
- Test: `tests/test_bump_version.py`

- [ ] **Step 1: 写失败测试 —— `--to-main` 参数被识别并触发合并**

新增测试（复用 `TestCmdToMain._setup_two_branch_repo` 的建仓逻辑；若该 helper 已在同类中可直接用）：

```python
class TestToMainArg:
    """--to-main 命令行参数接线测试"""

    def test_to_main_flag_invokes_cmd_to_main(self, monkeypatch):
        """--to-main 应调用 cmd_to_main（用 monkeypatch 桩掉真实 git 操作）"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        called: dict = {}
        def fake_cmd(skip_confirm: bool = False) -> int:
            called["invoked"] = True
            called["skip_confirm"] = skip_confirm
            return 0
        monkeypatch.setattr(mod, "cmd_to_main", fake_cmd)

        rc = mod.main(["--to-main"])

        assert called.get("invoked") is True
        assert rc == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_bump_version.py::TestToMainArg -v`
Expected: FAIL —— `--to-main` 未被 argparse 识别

- [ ] **Step 3: 加 `--to-main` 到 `_Args` 与 argparse，并在 `main()` 最早分支处理**

在 `_Args` 类（`scripts/bump_version.py:945-952`）加字段：
```python
    to_main: bool
```

在 `main()` 的 argparse 部分（`--release` 附近）加参数定义：
```python
    parser.add_argument(
        "--to-main",
        action="store_true",
        dest="to_main",
        help="合并 develop → main（squash + 整合 CHANGELOG + 打 tag）",
    )
```

在 `main()` 函数体最开头（现有「模式0: 构建并发布」`if args.release:` 之前）加分支：
```python
    # 模式: 合并至 main
    if args.to_main:
        return cmd_to_main(skip_confirm=args.no_edit)
```

注意：`--no-edit` 在此复用为「跳过确认提示」的信号（`skip_confirm`）。这样 `--to-main --no-edit` 可用于非交互场景。

- [ ] **Step 4: 接线 `"merge"` 哨兵**

在 `main()` 交互式分支（`scripts/bump_version.py:1061-1063`，`if new_version == "build":` 块之后）加：
```python
        if new_version == "merge":
            return cmd_to_main(skip_confirm=args.no_edit)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_bump_version.py::TestToMainArg tests/test_bump_version.py::TestCmdToMain -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add scripts/bump_version.py tests/test_bump_version.py
git commit -m "feat(bump): 接线 --to-main 参数与菜单选项 6 到 cmd_to_main"
```

---

## Task 9: 选项 5 打包警告 + 选项 1-4 串联合并提示

完成交互体验的最后两块：打包前的未版本化警告、bump 后的合并串联。这两块是 `main()` 内的提示逻辑，用 monkeypatch 桩测。

**Files:**
- Modify: `scripts/bump_version.py`（`main()` 的选项 5 与选项 1-4 尾部）
- Test: `tests/test_bump_version.py`

- [ ] **Step 1: 写失败测试 —— 选项 5 有未版本化提交时警告**

新增测试类：

```python
class TestOption5UnversionedWarning:
    """选项 5 打包时未版本化提交警告"""

    def test_option_5_warns_when_unversioned(self, monkeypatch, tmp_path, capsys):
        """有未版本化提交时选项 5 应打印警告并按默认 N 放弃打包"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # 桩：检测到未版本化提交
        monkeypatch.setattr(
            mod, "check_unversioned_commits", lambda v, cwd=None: (True, 3)
        )
        # 桩：打包不应被调用
        ran: dict = {}
        monkeypatch.setattr(
            mod, "_run_build", lambda v: ran.setdefault("built", True) or True
        )
        # input 默认 N（放弃）
        inputs = iter(["N"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(inputs))

        rc = mod.main(["--build"])

        out = capsys.readouterr().out
        assert "未发版" in out or "超出版本号" in out
        assert not ran.get("built"), "用户选 N 时不应打包"

    def test_option_5_proceeds_when_clean(self, monkeypatch, tmp_path):
        """无未版本化提交时选项 5 正常打包"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.PYPROJECT_TOML = tmp_path / "pyproject.toml"
        mod.PYPROJECT_TOML.write_text(
            '[project]\nname="x"\nversion="0.1.0"\n', encoding="utf-8"
        )
        monkeypatch.setattr(
            mod, "check_unversioned_commits", lambda v, cwd=None: (False, 0)
        )
        ran: dict = {}
        monkeypatch.setattr(
            mod, "_run_build", lambda v: ran.setdefault("built", True) or True
        )

        mod.main(["--build", "--no-build"])

        assert ran.get("built") is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_bump_version.py::TestOption5UnversionedWarning -v`
Expected: FAIL（检测/警告逻辑尚未加入）

- [ ] **Step 3: 在选项 5（`--build`）分支加未版本化警告**

在 `main()` 的「模式1: 仅打包当前版本」分支（`scripts/bump_version.py:1029-1036`）替换为：

```python
    # 模式1: 仅打包当前版本
    if args.build:
        try:
            current = read_current_version(PYPROJECT_TOML)
        except (FileNotFoundError, ValueError) as e:
            print(f"错误: {e}")
            return 1
        current_str = ".".join(map(str, current))

        # 未版本化提交警告（打包内容可能超出版本号标注）
        has_unversioned, n = check_unversioned_commits(current_str)
        if has_unversioned:
            print(
                f"警告: 当前版本 {current_str} 之后有 {n} 个未发版提交，"
                "打包内容将超出版本号标注。仍要打包？[y/N]: ",
                end="",
                flush=True,
            )
            if input().strip().lower() not in ("y", "yes"):
                print("已取消打包")
                return 0

        return 0 if _run_build(current_str) else 1
```

- [ ] **Step 4: 在选项 1-4 bump 尾部加串联合并提示**

在 `main()` 版本升级路径末尾（`print(f"\\n完成! 版本已升级到 {new_str}")` 之前），把当前的「询问是否打包」段替换为「先问合并、否决后再问打包」：

找到：
```python
    # 询问是否打包
    if not args.no_build and _ask_build(new_str):
        _run_build(new_str)
```
替换为：
```python
    # 先问是否合并至 main（更重操作优先）；否决后再问打包
    merged = False
    if not args.no_edit:
        print(f"\n是否立即合并至 main 并发版？[y/N]: ", end="", flush=True)
        if input().strip().lower() in ("y", "yes"):
            rc_merge = cmd_to_main(skip_confirm=True)
            merged = rc_merge == 0

    if not merged and not args.no_build and _ask_build(new_str):
        _run_build(new_str)
```

说明：`--no-edit` 复用为「跳过交互提示」（与 Task 8 的 `skip_confirm` 语义一致），便于 CI。菜单交互式时 `args.no_edit=False`，会问合并。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_bump_version.py::TestOption5UnversionedWarning -v`
Expected: PASS

- [ ] **Step 6: 回归 Task 5 的打包测试**

Task 5 的 `test_bump_updates_all_files` 等用了 `--no-edit --no-build`，应不受影响（合并提示被 `--no-edit` 跳过、打包被 `--no-build` 跳过）。

Run: `python -m pytest tests/test_bump_version.py -v`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add scripts/bump_version.py tests/test_bump_version.py
git commit -m "feat(bump): 选项 5 未版本化警告 + 选项 1-4 串联合并提示"
```

---

## Task 10: 全量回归 + 静态检查 + 手动验证清单

收尾：跑全量测试、静态检查，给出首次手动初始化的步骤清单（设计文档要求首次手工）。

**Files:** 无代码改动，仅验证 + 文档。

- [ ] **Step 1: 全量测试**

Run: `python -m pytest tests/test_bump_version.py -v`
Expected: 全部 PASS

Run: `python -m pytest tests/ -x -q`
Expected: 无新增失败（与改动前基线一致）

- [ ] **Step 2: 静态检查**

Run: `python -m ruff check scripts/bump_version.py tests/test_bump_version.py`
Expected: 无 error

Run: `python -m pyright scripts/bump_version.py`
Expected: 无新增 error

- [ ] **Step 3: 菜单冒烟（交互式）**

Run: `python scripts/bump_version.py`（不传参）
Expected: 菜单显示选项 0-6，含「合并至 main」。输入 0 取消退出，无副作用。

- [ ] **Step 4: 首次手动初始化清单（写入设计文档或 README）**

在 `docs/superpowers/specs/2026-06-26-merge-to-main-and-changelog-consolidation-design.md` 的「一次性初始化」节，补充操作步骤（这是人工执行项，非脚本）：

> **首次初始化步骤：**
> 1. `git checkout main`
> 2. 手工整理 develop 现有 `CHANGELOG.md`（0.1.0~0.1.6 各条）为 main 的整合基线（归并成少数正式版条目），写入 main 的 `CHANGELOG.md`。
> 3. 从 develop 同步当前版本号到 main 的 `pyproject.toml`（version = "0.1.6"）。
> 4. `git add CHANGELOG.md pyproject.toml && git commit -m "release: v0.1.6"`
> 5. `git tag v0.1.6`
> 6. `git checkout develop && git merge main --no-edit`（让 develop 拿到整合 CHANGELOG）
> 7. 之后每次发版跑 `python scripts/bump_version.py --to-main`（或菜单选 6）。

- [ ] **Step 5: 最终提交**

```bash
git add docs/superpowers/specs/2026-06-26-merge-to-main-and-changelog-consolidation-design.md
git commit -m "docs(spec): 补充 main CHANGELOG 首次手动初始化步骤"
```

---

## 完成标准

- [ ] `--to-main` 与菜单选项 6 都能完成 squash 合并 + 整合 CHANGELOG + 打 tag + 同步回 develop
- [ ] develop bump 不再生成 CHANGELOG、不打 tag（仅 release 提交 + 版本号前进）
- [ ] 选项 5 有未版本化提交时警告，选项 6 阻止并引导先 bump
- [ ] 选项 1-4 后串联合并提示
- [ ] 全量 `tests/test_bump_version.py` 通过；ruff/pyright 无新增问题
- [ ] 首次手动初始化步骤已记录

---

## Self-Review（计划作者自检）

**1. Spec 覆盖：**
- 模型总表（develop 不维护 CHANGELOG/tag）→ Task 5。✓
- develop bump 瘦身 → Task 5。✓
- `cmd_to_main` 9 步流程 → Task 7。✓
- CHANGELOG 重建算法（过滤 release + 去重 + 分类）→ Task 2 + Task 4。✓
- 未版本化检测 → Task 3，接选项 5/6 → Task 9/Task 8。✓
- 交互菜单选项 6 → Task 6。✓
- `--to-main` 参数 → Task 8。✓
- 选项 1-4 串联合并 → Task 9。✓
- 首次手动初始化 → Task 10 Step 4。✓

**2. 占位符扫描：** 无 TBD/TODO；所有代码步骤含完整代码。✓

**3. 类型一致性：**
- `_collect_commits(rev_range, cwd=None) -> list[tuple[str,str]]` —— Task 1 定义，Task 7 调用一致。✓
- `_filter_release_commits(commits) -> list[tuple[str,str]]` —— Task 1 定义，Task 2 内部使用。✓
- `generate_consolidated_entry(version, commits) -> str` —— Task 2 定义，Task 7 调用一致。✓
- `check_unversioned_commits(version, cwd=None) -> tuple[bool,int]` —— Task 3 定义，Task 7/Task 9 调用一致。✓
- `update_main_changelog(entry) -> None` —— Task 4 定义，Task 7 调用一致。✓
- `cmd_to_main(skip_confirm=False) -> int` —— Task 7 定义，Task 8/Task 9 调用一致。✓
- `"merge"` 哨兵 —— Task 6 返回，Task 8 识别。✓
- `_Args.to_main` —— Task 8 加字段，与 argparse dest 一致。✓
- `--no-edit` 复用为 `skip_confirm` —— Task 8（cmd_to_main）与 Task 9（合并提示）一致。✓

**4. 潜在风险（执行时注意）：**
- Task 7 端到端测试用 `input="y\n"` 喂确认提示；`--no-edit` 被复用为 `skip_confirm`，端到端测试传了 `--no-edit` 故实际跳过 input —— 测试里 `input="y\n"` 是冗余保险，无害。执行者若发现 input 未被消费属预期。
- Task 9 选项 1-4 串联改动会影响 Task 5 的 `test_bump_updates_all_files`（用 `--no-edit --no-build`，合并提示与打包提示都被跳过，应仍 PASS）。已在 Task 9 Step 6 回归。
- main 分支当前在真实仓库可能落后较多；首次手动初始化（Task 10 Step 4）是前置依赖，自动化流程跑通后第一次真实使用前必须先做。
