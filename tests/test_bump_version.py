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

    def test_changelog_created(self, tmp_path):
        """验证 CHANGELOG.md 在 bump 后被创建/更新"""
        result = _run_bump(tmp_path, ["patch", "--no-edit", "--no-build"])
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        changelog = tmp_path / "CHANGELOG.md"
        assert changelog.exists()
        content = changelog.read_text(encoding="utf-8")
        assert "0.1.1" in content

    def test_changelog_with_commits(self, tmp_path):
        """验证提交信息被正确分类到 CHANGELOG"""
        result = _run_bump_with_extra_commits(
            tmp_path,
            ["minor", "--no-edit", "--no-build"],
            [
                "feat: add new feature",
                "fix: fix a bug",
                "refactor: clean up code",
            ],
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        changelog = tmp_path / "CHANGELOG.md"
        content = changelog.read_text(encoding="utf-8")
        assert "0.2.0" in content
        assert "feat: add new feature" in content
        assert "fix: fix a bug" in content
        assert "refactor: clean up code" in content
        # 检查分类标题
        assert (
            "### Added" in content or "### Fixed" in content or "### Changed" in content
        )

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

    def test_changelog_inserts_before_existing(self, tmp_path):
        """验证新条目插入到现有 CHANGELOG 条目之前"""
        pyproject = tmp_path / "pyproject.toml"
        init_py = tmp_path / "__init__.py"
        main_py = tmp_path / "main.py"
        changelog = tmp_path / "CHANGELOG.md"

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
        # 用纯 ASCII 内容避免编码问题
        changelog.write_text(
            "# Changelog\n\n## [0.1.0] - 2025-01-01\n\n### Added\n- Initial version\n",
            encoding="utf-8",
        )

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

        env = os.environ.copy()
        env["PYPROJECT_TOML"] = str(pyproject)
        env["INIT_PY"] = str(init_py)
        env["MAIN_PY"] = str(main_py)
        env["CHANGELOG"] = str(changelog)
        # uv.lock 指向临时目录（不存在），避免命中真实仓库的 uv.lock
        env["UV_LOCK"] = str(tmp_path / "uv.lock")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "minor", "--no-edit", "--no-build"],
            cwd=tmp_path,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        content = changelog.read_text(encoding="utf-8")
        # 0.2.0 应该出现在 0.1.0 之前
        pos_020 = content.index("0.2.0")
        pos_010 = content.index("0.1.0")
        assert pos_020 < pos_010, "New version entry should appear before old entry"


class TestGitTagging:
    """测试 git 标签创建"""

    def test_git_tag_created(self, tmp_path):
        """验证 git tag vx.y.z 被创建"""
        result = _run_bump(tmp_path, ["patch", "--no-edit", "--no-build"])
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        tag_result = subprocess.run(
            ["git", "tag", "-l"],
            cwd=tmp_path,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        tags = tag_result.stdout.strip().split("\n")
        assert "v0.1.1" in tags

    def test_git_commit_created(self, tmp_path):
        """验证 git commit 被创建"""
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

    def test_major_tag(self, tmp_path):
        """验证 major 版本的 git tag"""
        result = _run_bump(tmp_path, ["major", "--no-edit", "--no-build"])
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        tag_result = subprocess.run(
            ["git", "tag", "-l"],
            cwd=tmp_path,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        tags = tag_result.stdout.strip().split("\n")
        assert "v1.0.0" in tags


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
