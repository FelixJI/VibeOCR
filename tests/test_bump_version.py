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

    pyproject.write_text(textwrap.dedent("""\
        [project]
        name = "vibeocr"
        version = "0.1.0"
    """), encoding="utf-8")
    init_py.write_text('__version__ = "0.1.0"', encoding="utf-8")
    main_py.write_text('    app.setApplicationVersion("0.1.0")', encoding="utf-8")

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "v0.1.0"], cwd=tmp_path, capture_output=True,
    )

    env = env or os.environ.copy()
    env["PYPROJECT_TOML"] = str(pyproject)
    env["INIT_PY"] = str(init_py)
    env["MAIN_PY"] = str(main_py)
    env["CHANGELOG"] = str(tmp_path / "CHANGELOG.md")

    result = subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )
    return result


def _run_bump_with_extra_commits(tmp_path, args, commits_to_add):
    """在临时目录中运行 bump_version.py，并在打 tag 后额外添加空提交"""
    pyproject = tmp_path / "pyproject.toml"
    init_py = tmp_path / "__init__.py"
    main_py = tmp_path / "main.py"

    pyproject.write_text(textwrap.dedent("""\
        [project]
        name = "vibeocr"
        version = "0.1.0"
    """), encoding="utf-8")
    init_py.write_text('__version__ = "0.1.0"', encoding="utf-8")
    main_py.write_text('    app.setApplicationVersion("0.1.0")', encoding="utf-8")

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "v0.1.0"], cwd=tmp_path, capture_output=True,
    )

    # 添加额外的空提交
    for msg in commits_to_add:
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", msg],
            cwd=tmp_path, capture_output=True,
        )

    env = os.environ.copy()
    env["PYPROJECT_TOML"] = str(pyproject)
    env["INIT_PY"] = str(init_py)
    env["MAIN_PY"] = str(main_py)
    env["CHANGELOG"] = str(tmp_path / "CHANGELOG.md")

    result = subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )
    return result


class TestVersionParsing:
    """测试 read_current_version 正确解析 pyproject.toml"""

    def test_read_current_version(self):
        """验证从 pyproject.toml 中读取版本号"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        os.environ["PYPROJECT_TOML"] = ""
        os.environ["INIT_PY"] = ""
        os.environ["MAIN_PY"] = ""
        os.environ["CHANGELOG"] = ""
        spec.loader.exec_module(mod)

        result = mod.read_current_version(Path(__file__).parent.parent / "pyproject.toml")
        assert result == (0, 1, 0)

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
            assert result == expected, f"Expected {expected} for {version_str}, got {result}"


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
        """验证 patch/minor/major/explicit 升级后三个文件都更新"""
        result = _run_bump(tmp_path, args + ["--no-edit", "--no-build"])
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        pyproject = tmp_path / "pyproject.toml"
        init_py = tmp_path / "__init__.py"
        main_py = tmp_path / "main.py"

        assert f'version = "{expected_version}"' in pyproject.read_text(encoding="utf-8")
        assert f'__version__ = "{expected_version}"' in init_py.read_text(encoding="utf-8")
        assert f'app.setApplicationVersion("{expected_version}")' in main_py.read_text(encoding="utf-8")

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
        assert "### Added" in content or "### Fixed" in content or "### Changed" in content

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

        pyproject.write_text(textwrap.dedent("""\
            [project]
            name = "vibeocr"
            version = "0.1.0"
        """), encoding="utf-8")
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
            cwd=tmp_path, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path, capture_output=True,
        )
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True,
        )
        subprocess.run(
            ["git", "tag", "v0.1.0"], cwd=tmp_path, capture_output=True,
        )

        env = os.environ.copy()
        env["PYPROJECT_TOML"] = str(pyproject)
        env["INIT_PY"] = str(init_py)
        env["MAIN_PY"] = str(main_py)
        env["CHANGELOG"] = str(changelog)

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "minor", "--no-edit", "--no-build"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
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
            text=True,
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
            text=True,
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
            text=True,
        )
        tags = tag_result.stdout.strip().split("\n")
        assert "v1.0.0" in tags
