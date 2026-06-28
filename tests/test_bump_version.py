"""版本管理脚本单元测试"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "bump_version.py"


def _run_bump(tmp_path, args, env=None):
    """在临时目录中运行 bump_version.py"""
    pyproject = tmp_path / "pyproject.toml"
    init_py = tmp_path / "__init__.py"
    main_py = tmp_path / "main.py"

    pyproject.write_text(
        textwrap.dedent("""\
        [project]
        name = "vibeocr"
        version = "0.1.0"
    """),
        encoding="utf-8",
    )
    init_py.write_text('__version__ = "0.1.0"', encoding="utf-8")
    main_py.write_text('    app.setApplicationVersion("0.1.0")', encoding="utf-8")

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "v0.1.0"],
        cwd=tmp_path,
        capture_output=True,
    )

    env = env or os.environ.copy()
    env["PYPROJECT_TOML"] = str(pyproject)
    env["INIT_PY"] = str(init_py)
    env["MAIN_PY"] = str(main_py)
    env["CHANGELOG"] = str(tmp_path / "CHANGELOG.md")
    # uv.lock 指向临时目录（不存在），避免命中真实仓库的 uv.lock
    # 导致 git add 一个仓库外文件而失败（uv.lock 同步功能的测试隔离）
    env["UV_LOCK"] = str(tmp_path / "uv.lock")

    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _run_bump_with_extra_commits(tmp_path, args, commits_to_add):
    """在临时目录中运行 bump_version.py，并在打 tag 后额外添加空提交"""
    pyproject = tmp_path / "pyproject.toml"
    init_py = tmp_path / "__init__.py"
    main_py = tmp_path / "main.py"

    pyproject.write_text(
        textwrap.dedent("""\
        [project]
        name = "vibeocr"
        version = "0.1.0"
    """),
        encoding="utf-8",
    )
    init_py.write_text('__version__ = "0.1.0"', encoding="utf-8")
    main_py.write_text('    app.setApplicationVersion("0.1.0")', encoding="utf-8")

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "v0.1.0"],
        cwd=tmp_path,
        capture_output=True,
    )

    # 添加额外的空提交
    for msg in commits_to_add:
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", msg],
            cwd=tmp_path,
            capture_output=True,
        )

    env = os.environ.copy()
    env["PYPROJECT_TOML"] = str(pyproject)
    env["INIT_PY"] = str(init_py)
    env["MAIN_PY"] = str(main_py)
    env["CHANGELOG"] = str(tmp_path / "CHANGELOG.md")
    # uv.lock 指向临时目录（不存在），避免命中真实仓库的 uv.lock
    env["UV_LOCK"] = str(tmp_path / "uv.lock")

    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


class TestVersionParsing:
    """测试 read_current_version 正确解析 pyproject.toml"""

    def test_read_current_version(self):
        """验证从真实 pyproject.toml 中读取版本号

        不硬编码具体版本值（会随 bump 变化），只验证：
        - 返回三元组（major, minor, patch），均为非负整数；
        - 与 pyproject.toml 里 [project].version 字面量一致。
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        os.environ["PYPROJECT_TOML"] = ""
        os.environ["INIT_PY"] = ""
        os.environ["MAIN_PY"] = ""
        os.environ["CHANGELOG"] = ""
        spec.loader.exec_module(mod)

        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        result = mod.read_current_version(pyproject)
        # 三元组 + 非负整数
        assert isinstance(result, tuple) and len(result) == 3
        assert all(isinstance(n, int) and n >= 0 for n in result)
        # 与 pyproject.toml 字面量一致
        assert f'version = "{".".join(map(str, result))}"' in pyproject.read_text(
            encoding="utf-8"
        )

    def test_read_current_version_various(self, tmp_path):
        """测试各种版本号格式"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        os.environ["PYPROJECT_TOML"] = ""
        os.environ["INIT_PY"] = ""
        os.environ["MAIN_PY"] = ""
        os.environ["CHANGELOG"] = ""
        spec.loader.exec_module(mod)

        pyproject = tmp_path / "pyproject.toml"
        for version_str, expected in [
            ("1.2.3", (1, 2, 3)),
            ("10.20.30", (10, 20, 30)),
            ("0.0.1", (0, 0, 1)),
        ]:
            pyproject.write_text(
                f'[project]\nname = "test"\nversion = "{version_str}"\n',
                encoding="utf-8",
            )
            result = mod.read_current_version(pyproject)
            assert result == expected, (
                f"Expected {expected} for {version_str}, got {result}"
            )


class TestVersionBumping:
    """测试版本升级和文件更新"""

    @pytest.mark.parametrize(
        "args, expected_version",
        [
            (["patch"], "0.1.1"),
            (["minor"], "0.2.0"),
            (["major"], "1.0.0"),
            (["2.0.0"], "2.0.0"),
        ],
    )
    def test_bump_updates_all_files(self, tmp_path, args, expected_version):
        """验证 patch/minor/major/explicit 升级后 pyproject 与 __init__ 更新

        注意：main.py 通过 __version__ 引用版本号（无字面量），bump 不再改它。
        """
        result = _run_bump(tmp_path, [*args, "--no-edit", "--no-build"])
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        pyproject = tmp_path / "pyproject.toml"
        init_py = tmp_path / "__init__.py"
        main_py = tmp_path / "main.py"

        assert f'version = "{expected_version}"' in pyproject.read_text(
            encoding="utf-8"
        )
        assert f'__version__ = "{expected_version}"' in init_py.read_text(
            encoding="utf-8"
        )
        # main.py 不再被 bump 处理，其字面量版本应保持原样
        assert 'app.setApplicationVersion("0.1.0")' in main_py.read_text(
            encoding="utf-8"
        )

    def test_bump_version_function(self):
        """测试 bump_version 函数逻辑"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        os.environ["PYPROJECT_TOML"] = ""
        os.environ["INIT_PY"] = ""
        os.environ["MAIN_PY"] = ""
        os.environ["CHANGELOG"] = ""
        spec.loader.exec_module(mod)

        assert mod.bump_version((1, 2, 3), "patch") == (1, 2, 4)
        assert mod.bump_version((1, 2, 3), "minor") == (1, 3, 0)
        assert mod.bump_version((1, 2, 3), "major") == (2, 0, 0)

    def test_update_file_version_replaces_first_occurrence(self, tmp_path):
        """验证 update_file_version 只替换第一次出现的版本号"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        os.environ["PYPROJECT_TOML"] = ""
        os.environ["INIT_PY"] = ""
        os.environ["MAIN_PY"] = ""
        os.environ["CHANGELOG"] = ""
        spec.loader.exec_module(mod)

        test_file = tmp_path / "test.txt"
        test_file.write_text('version = "0.1.0"\n# still 0.1.0\n', encoding="utf-8")

        mod.update_file_version(test_file, "0.1.0", "0.2.0")

        content = test_file.read_text(encoding="utf-8")
        assert 'version = "0.2.0"' in content
        assert "# still 0.1.0" in content  # 第二次出现不被替换


class TestChangelogGeneration:
    """测试 CHANGELOG 生成"""

    def test_changelog_not_created_on_bump(self, tmp_path):
        """验证 develop bump 不再生成 CHANGELOG（CHANGELOG 改由 --to-main 维护）"""
        result = _run_bump(tmp_path, ["patch", "--no-edit", "--no-build"])
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        changelog = tmp_path / "CHANGELOG.md"
        assert not changelog.exists(), "develop bump 不应生成 CHANGELOG"

    def test_categorize_commits(self):
        """测试 commit 分类函数"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        os.environ["PYPROJECT_TOML"] = ""
        os.environ["INIT_PY"] = ""
        os.environ["MAIN_PY"] = ""
        os.environ["CHANGELOG"] = ""
        spec.loader.exec_module(mod)

        commits = [
            ("abc1234", "feat: add new feature"),
            ("def5678", "fix: fix a bug"),
            ("ghi9012", "refactor: clean up code"),
            ("jkl3456", "perf: optimize performance"),
            ("mno7890", "docs: update readme"),
            ("pqr1234", "chore: update deps"),
            ("stu5678", "random commit without prefix"),
        ]

        result = mod.categorize_commits(commits)
        # 完整的提交消息（含前缀）保留在对应分类中
        assert "feat: add new feature" in result.get("Added", [])
        assert "fix: fix a bug" in result.get("Fixed", [])
        assert "refactor: clean up code" in result.get("Changed", [])
        assert "perf: optimize performance" in result.get("Changed", [])
        assert "docs: update readme" in result.get("Changed", [])
        assert "chore: update deps" in result.get("Changed", [])

    # 注：原 test_changelog_inserts_before_existing（验证 develop bump 把新版本
    # 条目插到 CHANGELOG 顶部）已删除——develop bump 不再写 CHANGELOG。
    # 「新条目插在现有之上」的行为现由 TestUpdateMainChangelog::test_inserts_new_entry_above_existing
    # 覆盖（针对 --to-main 路径的 update_main_changelog）。


class TestGitTagging:
    """测试 git 标签创建"""

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

    def test_git_commit_created(self, tmp_path):
        """验证 git commit 被创建（develop bump 仍生成 release: 提交）"""
        result = _run_bump(tmp_path, ["minor", "--no-edit", "--no-build"])
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        log_result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=tmp_path,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        assert "release: v0.2.0" in log_result.stdout


class TestGenerateVersionJson:
    """测试 _generate_version_json 的 dep_versions 键名归一"""

    def _load_script(self):
        """加载 bump_version 脚本模块（独立 importlib）"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        for k in ("PYPROJECT_TOML", "INIT_PY", "MAIN_PY", "CHANGELOG"):
            os.environ[k] = ""
        spec.loader.exec_module(mod)
        return mod

    def test_paddlepaddle_gpu_normalized_to_paddlepaddle(self, tmp_path):
        """version.json 的 dep_versions 应把 paddlepaddle-gpu 归一为 paddlepaddle

        这与 env_config.OCR_CHECK_MODULES["paddle"] == "paddlepaddle" 保持一致，
        使打包环境的 _load_dep_specs（从 version.json 读）能正确匹配包名。
        """
        import json

        mod = self._load_script()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "vibeocr"
                version = "0.1.0"
                dependencies = [
                    "pyside6>=6.11.1",
                    "paddlepaddle-gpu>=3.3.1",
                    "paddleocr[doc-parser]>=3.7.0",
                    "mineru[core]>=3.3.1",
                    "torch>=2.5.0",
                    "nvidia-cudnn-cu13>=9.23.1.3",
                ]
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject

        mod._generate_version_json("1.2.3", tmp_path)

        data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
        dep_versions = data["dep_versions"]

        # paddlepaddle-gpu 应归一为 paddlepaddle（与 OCR_CHECK_MODULES 一致）
        assert "paddlepaddle" in dep_versions, (
            f"应归一为 paddlepaddle，实际 keys: {list(dep_versions)}"
        )
        assert "paddlepaddle-gpu" not in dep_versions
        # paddleocr/mineru/torch 也应记录
        assert "paddleocr" in dep_versions
        assert "mineru" in dep_versions
        assert "torch" in dep_versions
        # nvidia 包也应记录（更新器需要）
        assert any(k.startswith("nvidia") for k in dep_versions)

    def test_python_version_read_from_dot_python_version(self, tmp_path):
        """version.json 的 python_version 应从 .python-version 文件读取，而非硬编码"""
        import json

        mod = self._load_script()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "vibeocr"
                version = "0.1.0"
                dependencies = []
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject
        # 用自定义 PROJECT_ROOT + .python-version（值故意不同于 3.13）
        fake_root = tmp_path / "fake_root"
        fake_root.mkdir()
        (fake_root / ".python-version").write_text("3.99", encoding="utf-8")
        mod.PROJECT_ROOT = fake_root

        mod._generate_version_json("1.2.3", tmp_path)

        data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
        assert data["python_version"] == "3.99", (
            f"python_version 应读自 .python-version（3.99），实际: {data['python_version']}"
        )


class TestInteractiveMenuBuildOption:
    """交互式菜单的"仅打包当前版本"选项测试"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_option_5_returns_build_sentinel(self, monkeypatch):
        """选项 5 应返回哨兵 'build'（表示仅打包当前版本，不升级版本号）"""
        mod = self._load_module()
        monkeypatch.setattr("builtins.input", lambda _prompt="": "5")

        result = mod.interactive_menu((0, 1, 4))

        assert result == "build", (
            f"选项 5 应返回 'build' 哨兵，实际: {result!r}"
        )

    def test_option_0_returns_none_cancel(self, monkeypatch):
        """选项 0 仍返回 None（取消）"""
        mod = self._load_module()
        monkeypatch.setattr("builtins.input", lambda _prompt="": "0")

        result = mod.interactive_menu((0, 1, 4))

        assert result is None, f"选项 0 应返回 None（取消），实际: {result!r}"

    def test_option_1_still_returns_bumped_version(self, monkeypatch):
        """选项 1 (patch) 仍正常返回升级后的版本号三元组"""
        mod = self._load_module()
        monkeypatch.setattr("builtins.input", lambda _prompt="": "1")

        result = mod.interactive_menu((0, 1, 4))

        assert result == (0, 1, 5), f"选项 1 应返回 patch 升级 (0,1,5)，实际: {result!r}"


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
        """_collect_commits 返回 [(hash, subject), ...]，覆盖给定范围内的提交

        注意：测试传入 cwd=tmp_path 以隔离到临时仓库；_collect_commits 的默认
        cwd=None 表示继承调用者 CWD（与原 get_commits_since_last_tag 行为一致）。
        """
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
        """--to-main 后：main 有 v0.1.1 tag、整合 CHANGELOG、最终在 develop"""
        import os
        import subprocess
        import sys

        repo = self._setup_two_branch_repo(tmp_path)

        env = os.environ.copy()
        env["PYPROJECT_TOML"] = str(repo / "pyproject.toml")
        env["CHANGELOG"] = str(repo / "CHANGELOG.md")
        env["UV_LOCK"] = str(repo / "uv.lock")  # 不存在，跳过同步

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--to-main", "--no-edit"],
            cwd=repo,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            input="y\n",
        )
        assert result.returncode == 0, f"失败: {result.stdout}\n{result.stderr}"

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
        env["PYPROJECT_TOML"] = str(repo / "pyproject.toml")
        env["CHANGELOG"] = str(repo / "CHANGELOG.md")
        env["UV_LOCK"] = str(repo / "uv.lock")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--to-main", "--no-edit"],
            cwd=repo, capture_output=True, encoding="utf-8", errors="replace",
            env=env, input="y\n",
        )
        # 应中止（非零退出），且不打 tag、不切到 main
        assert result.returncode != 0
        assert "未发版" in result.stdout or "未版本化" in result.stdout
        tags = subprocess.run(
            ["git", "tag", "-l"], cwd=repo, capture_output=True, encoding="utf-8",
        ).stdout.strip().split("\n")
        assert "v0.1.1" not in tags


class TestToMainArgWiring:
    """--to-main 参数与 merge 哨兵接线测试"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_to_main_flag_invokes_cmd_to_main(self, monkeypatch):
        """--to-main 应调用 cmd_to_main（用 monkeypatch 桩掉真实 git 操作）"""
        mod = self._load_module()

        called: dict = {}

        def fake_cmd(skip_confirm: bool = False) -> int:
            called["invoked"] = True
            called["skip_confirm"] = skip_confirm
            return 0

        monkeypatch.setattr(mod, "cmd_to_main", fake_cmd)
        monkeypatch.setattr("sys.argv", ["bump_version.py", "--to-main"])

        rc = mod.main()

        assert called.get("invoked") is True
        assert rc == 0

    def test_merge_sentinel_invokes_cmd_to_main(self, monkeypatch):
        """菜单返回 'merge' 哨兵时 main() 应调用 cmd_to_main"""
        mod = self._load_module()

        called: dict = {}

        def fake_cmd(skip_confirm: bool = False) -> int:
            called["invoked"] = True
            return 0

        monkeypatch.setattr(mod, "cmd_to_main", fake_cmd)
        monkeypatch.setattr(mod, "read_current_version", lambda path: (0, 1, 6))
        monkeypatch.setattr(mod, "interactive_menu", lambda current: "merge")
        monkeypatch.setattr("sys.argv", ["bump_version.py"])

        rc = mod.main()

        assert called.get("invoked") is True
        assert rc == 0


class TestOption5UnversionedWarning:
    """选项 5 打包时未版本化提交警告（--build 模式）"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_option_5_warns_when_unversioned(self, monkeypatch, capsys):
        """有未版本化提交时 --build 应打印警告并按默认 N 放弃打包"""
        mod = self._load_module()

        # 桩：检测到未版本化提交；read_current_version 避免触碰真实 pyproject
        monkeypatch.setattr(
            mod, "check_unversioned_commits", lambda v, cwd=None: (True, 3)
        )
        monkeypatch.setattr(mod, "read_current_version", lambda path: (0, 1, 6))
        # 桩：打包不应被调用
        ran: dict = {}
        monkeypatch.setattr(
            mod, "_run_build", lambda v, force=False: ran.setdefault("built", True) or True
        )
        # input 默认 N（放弃）
        monkeypatch.setattr("builtins.input", lambda _p="": "N")
        monkeypatch.setattr("sys.argv", ["bump_version.py", "--build"])

        mod.main()

        out = capsys.readouterr().out
        assert "未发版" in out or "超出版本号" in out
        assert not ran.get("built"), "用户选 N 时不应打包"

    def test_option_5_proceeds_when_clean(self, monkeypatch):
        """无未版本化提交时 --build 正常打包"""
        mod = self._load_module()
        monkeypatch.setattr(
            mod, "check_unversioned_commits", lambda v, cwd=None: (False, 0)
        )
        monkeypatch.setattr(mod, "read_current_version", lambda path: (0, 1, 6))
        ran: dict = {}
        monkeypatch.setattr(
            mod, "_run_build", lambda v, force=False: ran.setdefault("built", True) or True
        )
        monkeypatch.setattr("sys.argv", ["bump_version.py", "--build"])

        mod.main()

        assert ran.get("built") is True


class TestBumpMergePrompt:
    """选项 1-4 bump 完后串联"合并至 main"提示"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_bump_prompts_merge_yes_invokes_cmd_to_main(self, monkeypatch):
        """bump 后提示合并，答 y → 调用 cmd_to_main，不再问打包"""
        mod = self._load_module()
        monkeypatch.setattr(mod, "read_current_version", lambda path: (0, 1, 5))
        monkeypatch.setattr(
            mod, "update_file_version", lambda f, old, new: None
        )
        monkeypatch.setattr(mod, "_sync_uv_lock", lambda v: False)
        # 桩 git 操作（add/commit）
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: None)
        merged: dict = {}
        monkeypatch.setattr(
            mod, "cmd_to_main", lambda skip_confirm=False: merged.setdefault("merged", True) or 0
        )
        built: dict = {}
        monkeypatch.setattr(
            mod, "_ask_build", lambda v: built.setdefault("asked", True) or False
        )
        # 合并提示答 y
        monkeypatch.setattr("builtins.input", lambda _p="": "y")
        monkeypatch.setattr("sys.argv", ["bump_version.py", "patch", "--no-build"])

        mod.main()

        assert merged.get("merged") is True, "答 y 应触发 cmd_to_main"
        assert not built.get("asked"), "已合并时不应再问打包"

    def test_bump_prompts_merge_no_falls_back_to_build_prompt(self, monkeypatch):
        """bump 后提示合并，答 N → 跳过合并，转问打包"""
        mod = self._load_module()
        monkeypatch.setattr(mod, "read_current_version", lambda path: (0, 1, 5))
        monkeypatch.setattr(
            mod, "update_file_version", lambda f, old, new: None
        )
        monkeypatch.setattr(mod, "_sync_uv_lock", lambda v: False)
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: None)
        merged: dict = {}
        monkeypatch.setattr(
            mod, "cmd_to_main", lambda skip_confirm=False: merged.setdefault("merged", True) or 1
        )
        built: dict = {}
        monkeypatch.setattr(
            mod, "_ask_build", lambda v: built.setdefault("asked", True) or False
        )
        # 合并提示答 N
        monkeypatch.setattr("builtins.input", lambda _p="": "N")
        monkeypatch.setattr("sys.argv", ["bump_version.py", "patch", "--no-build"])

        mod.main()

        assert not merged.get("merged"), "答 N 不应合并"
        # --no-build 时 _ask_build 仍被调用但由 no_build 跳过实际打包；
        # 此处验证没有走合并分支即可（built.asked 可能 True 但无害）


class TestPyInstallerNoUpx:
    """_get_pyinstaller_cmd 应禁用 UPX（启动慢主因）"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_cmd_contains_noupx(self):
        """打包命令应包含 --noupx（禁用 UPX 压缩换取启动提速）"""
        mod = self._load_module()
        cmd = mod._get_pyinstaller_cmd("0.1.7")
        assert "--noupx" in cmd, (
            f"应包含 --noupx 禁用 UPX 压缩，实际命令: {cmd}"
        )

    def test_cmd_uses_onedir(self):
        """打包命令应使用 --onedir 模式"""
        mod = self._load_module()
        cmd = mod._get_pyinstaller_cmd("0.1.7")
        assert "--onedir" in cmd
