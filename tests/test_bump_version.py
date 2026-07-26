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
        # bump 完成后会交互式询问“是否推送发版”和“是否本地打包”。
        # 测试以子进程跑、无 TTY，传一个换行（=回车=N）让两者都走“否”，
        # 避免 input() 读到 EOF 崩溃。
        input="\n",
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
        input="\n",
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

    def test_workspace_version_files_match_root_version(self, monkeypatch):
        """真实 workspace 的包版本和内部精确依赖不得再次漂移"""
        import importlib.util
        import re

        for key in ("PYPROJECT_TOML", "INIT_PY", "MAIN_PY", "CHANGELOG"):
            monkeypatch.delenv(key, raising=False)
        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        current = ".".join(map(str, mod.read_current_version(mod.PYPROJECT_TOML)))
        for path in mod._discover_version_files():
            content = path.read_text(encoding="utf-8")
            if path.name == "pyproject.toml":
                assert f'version = "{current}"' in content, path
                internal_pins = re.findall(
                    r'"vibeocr-[a-z0-9-]+==(\d+\.\d+\.\d+)"', content
                )
                assert all(version == current for version in internal_pins), path
            else:
                assert f'__version__ = "{current}"' in content, path

    def test_update_file_version_replaces_all_occurrences(self, tmp_path):
        """项目版本与内部包精确约束中的旧版本都应被替换"""
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
        assert "0.1.0" not in content
        assert "# still 0.2.0" in content

    def test_update_project_versions_discovers_workspace_members(
        self, tmp_path, monkeypatch
    ):
        """workspace 子包版本、内部包约束与 __version__ 一次性同步"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        pyproject = tmp_path / "pyproject.toml"
        init_py = tmp_path / "src" / "vibeocr" / "__init__.py"
        member = tmp_path / "packages" / "vibeocr-client-py"
        member_init = member / "src" / "vibeocr_client_py" / "__init__.py"
        init_py.parent.mkdir(parents=True)
        member_init.parent.mkdir(parents=True)
        pyproject.write_text(
            textwrap.dedent("""\
            [project]
            name = "vibeocr"
            version = "0.1.0"

            [tool.uv.workspace]
            members = ["packages/*"]
            """),
            encoding="utf-8",
        )
        init_py.write_text('__version__ = "0.1.0"', encoding="utf-8")
        (member / "pyproject.toml").write_text(
            textwrap.dedent("""\
            [project]
            name = "vibeocr-client-py"
            version = "0.1.0"
            dependencies = ["vibeocr-contracts-py==0.1.0"]
            """),
            encoding="utf-8",
        )
        member_init.write_text('__version__ = "0.1.0"', encoding="utf-8")
        monkeypatch.setattr(mod, "PYPROJECT_TOML", pyproject)
        monkeypatch.setattr(mod, "INIT_PY", init_py)

        changed = mod.update_project_versions("0.1.0", "0.2.0")

        assert set(changed) == {
            pyproject,
            init_py,
            member / "pyproject.toml",
            member_init,
        }
        for path in changed:
            assert "0.1.0" not in path.read_text(encoding="utf-8")
        assert "vibeocr-contracts-py==0.2.0" in (member / "pyproject.toml").read_text(
            encoding="utf-8"
        )


class TestChangelogGeneration:
    """测试 CHANGELOG 生成"""

    def test_changelog_created_on_bump(self, tmp_path):
        """验证 bump 时生成 CHANGELOG 条目并纳入 release 提交"""
        result = _run_bump(tmp_path, ["patch", "--no-edit", "--no-build"])
        assert result.returncode == 0, f"Script failed: {result.stderr}"

        changelog = tmp_path / "CHANGELOG.md"
        assert changelog.exists(), "bump 应生成 CHANGELOG"

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


class TestGitTagging:
    """测试 git 标签创建"""

    def test_tag_created_on_bump(self, tmp_path):
        """验证 bump 后直接打 tag（新模型：main 上 bump → commit → tag）"""
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
        # bump 到 0.1.1 后应创建 v0.1.1 tag，连同夹具里的 v0.1.0
        assert "v0.1.1" in tags, "bump 后应打 tag v0.1.1"
        assert "v0.1.0" in tags

    def test_git_commit_created(self, tmp_path):
        """验证 git commit 被创建（bump 生成 release: 提交）"""
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

    def test_portable_installed_deps_all_tracked(self, tmp_path):
        """便携 Python 安装的依赖（EXCLUDED_PACKAGES 且 OCR 检测）必须全部被追踪。

        回归防护：markdown 曾因未加入 _TRACKED_PREFIXES 而漏写 version.json，
        导致便携环境 _load_dep_specs 取不到约束 → 裸包名安装（丢失 >=3.10.2 约束）、
        detect_dependency_updates 漏报更新、dep_locked_versions 缺基准。

        不变量：EXCLUDED_PACKAGES ∩ 便携检测集 ⊆ _TRACKED_PREFIXES 覆盖的包名。
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
                    "markdown>=3.10.2",
                    "paddleocr[doc-parser]>=3.7.0",
                    "mineru[core]>=3.3.1",
                    "torch>=2.5.0",
                    "pymupdf>=1.27.2.3",
                    "fastapi>=0.115.0",
                    "uvicorn>=0.34.0",
                    "pydantic>=2.11.0",
                    "fonttools>=4.61.1",
                ]
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject

        mod._generate_version_json("1.2.3", tmp_path)

        data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
        dep_versions = data["dep_versions"]

        # 这些包均已从主 exe 排除、由便携 Python 安装，必须出现在 dep_versions
        required_portable = {
            "markdown", "paddleocr", "mineru", "torch",
            "pymupdf", "fastapi", "uvicorn", "pydantic", "fonttools",
        }
        missing = required_portable - set(dep_versions)
        assert not missing, (
            f"便携安装的依赖未写入 version.json：{missing}，"
            f"实际 keys: {sorted(dep_versions)}"
        )

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

    def test_dep_versions_use_constraint_string(self, tmp_path):
        """P1：dep_versions 值应为 constraint 串（完整 PEP 440），保留操作符。

        形如 ">=3.3.1" / "==3.3.1+cu126" / ">=2.6,<3"，读端拼接 {pkg}{constraint}
        即得合法 pip requirement。extras 单独存于 dep_extras。
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
                    "paddlepaddle-gpu>=3.3.1",
                    "paddleocr[doc-parser]>=3.7.0",
                    "torch>=2.6.0",
                ]
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject

        mod._generate_version_json("1.2.3", tmp_path)

        data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
        dep_versions = data["dep_versions"]
        # constraint 串（含操作符）
        assert dep_versions["paddlepaddle"] == ">=3.3.1"
        assert dep_versions["torch"] == ">=2.6.0"
        # extras 单独存放
        assert data.get("dep_extras") == {"paddleocr": ["doc-parser"]}

    def test_dep_versions_preserves_local_version(self, tmp_path):
        """P1：local version (+cu126) 应完整保留在 constraint 中。"""
        import json

        mod = self._load_script()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "vibeocr"
                version = "0.1.0"
                dependencies = [
                    "paddlepaddle-gpu==3.3.1+cu126",
                ]
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject

        mod._generate_version_json("1.2.3", tmp_path)

        data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
        # +cu126 必须保留（不被截断成 3.3.1）
        assert data["dep_versions"]["paddlepaddle"] == "==3.3.1+cu126"

    def test_dep_versions_preserves_multi_segment_constraint(self, tmp_path):
        """P1：多段约束 (>=2.6,<3) 应完整保留，不丢失后半段。"""
        import json

        mod = self._load_script()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "vibeocr"
                version = "0.1.0"
                dependencies = [
                    "torch>=2.6.0,<3.0.0",
                ]
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject

        mod._generate_version_json("1.2.3", tmp_path)

        data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
        assert data["dep_versions"]["torch"] == ">=2.6.0,<3.0.0"

    def test_dep_versions_preserves_compatible_release(self, tmp_path):
        """P1：~= 兼容发行操作符应正确记录。"""
        import json

        mod = self._load_script()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "vibeocr"
                version = "0.1.0"
                dependencies = [
                    "torch~=2.6.0",
                ]
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject

        mod._generate_version_json("1.2.3", tmp_path)

        data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
        assert data["dep_versions"]["torch"] == "~=2.6.0"

    def test_dep_versions_handles_not_equal_operator(self, tmp_path):
        """P1：!= 操作符应被识别（不漏掉包）。"""
        import json

        mod = self._load_script()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "vibeocr"
                version = "0.1.0"
                dependencies = [
                    "torch!=2.7.0",
                ]
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject

        mod._generate_version_json("1.2.3", tmp_path)

        data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
        # != 应被识别，torch 不应丢失
        assert "torch" in data["dep_versions"]
        assert data["dep_versions"]["torch"] == "!=2.7.0"

    def test_dep_versions_multi_extras(self, tmp_path):
        """P1：多 extras（[a,b]）应正确拆分列表。"""
        import json

        mod = self._load_script()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "vibeocr"
                version = "0.1.0"
                dependencies = [
                    "paddleocr[doc-parser,rapid-table]>=3.7.0",
                ]
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject

        mod._generate_version_json("1.2.3", tmp_path)

        data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
        assert data["dep_versions"]["paddleocr"] == ">=3.7.0"
        assert data["dep_extras"]["paddleocr"] == ["doc-parser", "rapid-table"]

    def test_removed_field_when_dep_dropped(self, tmp_path):
        """P4：新版移除某依赖时，version.json 应含 removed 字段。

        通过 git 历史：先 commit 旧 pyproject（含 mineru）→ tag → 改 pyproject
        移除 mineru → _generate_version_json 应把 mineru 记入 removed。
        """
        import json

        mod = self._load_script()
        # 初始化 git 仓库（_get_last_release_pyproject_deps 依赖 git history）
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True
        )
        old_pyproject = tmp_path / "pyproject.toml"
        old_pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "vibeocr"
                version = "0.1.0"
                dependencies = [
                    "paddlepaddle-gpu>=3.3.1",
                    "mineru[core]>=3.4.0",
                ]
            """),
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True
        )
        subprocess.run(
            ["git", "tag", "v0.1.0"], cwd=tmp_path, capture_output=True
        )

        # 新版 pyproject：移除 mineru
        new_pyproject_text = textwrap.dedent("""\
            [project]
            name = "vibeocr"
            version = "0.2.0"
            dependencies = [
                "paddlepaddle-gpu>=3.3.1",
            ]
        """)
        old_pyproject.write_text(new_pyproject_text, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        # 注意：不 commit，让 _get_last_release_pyproject_deps 从 v0.1.0 tag 读旧版

        mod.PYPROJECT_TOML = old_pyproject
        mod.PROJECT_ROOT = tmp_path

        mod._generate_version_json("0.2.0", tmp_path)

        data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
        assert data.get("removed") == ["mineru"], (
            f"应记入 removed=['mineru']，实际: {data.get('removed')}"
        )

    def test_no_removed_field_when_nothing_dropped(self, tmp_path):
        """P4：无移除时不应写 removed 字段（旧读端兼容）。"""
        import json

        mod = self._load_script()
        # 无 git 历史 → _get_last_release_pyproject_deps 返回空 → removed 为空
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "vibeocr"
                version = "0.1.0"
                dependencies = [
                    "paddlepaddle-gpu>=3.3.1",
                ]
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject
        mod.PROJECT_ROOT = tmp_path

        mod._generate_version_json("1.2.3", tmp_path)

        data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
        assert "removed" not in data, "无移除时不应写 removed 字段"

    def test_dep_locked_versions_from_uv_lock(self, tmp_path):
        """version.json 的 dep_locked_versions 应从 uv.lock 取锁定版本。

        pyproject 的 ``>=3.4.0`` 只是下界，无法表达"实际锁定 3.4.2"。便携环境已装
        3.4.0 满足下界会被误判为最新。打包时应从 uv.lock 解析锁定版写入新字段
        ``dep_locked_versions``，运行时据此比较（见 env_manager.detect_dependency_updates）。

        本测试构造 uv.lock fixture，验证：
        - mineru 锁定版 3.4.2 正确写入；
        - paddlepaddle-gpu 归一为 paddlepaddle key；
        - torch 的 local label（+cu126）原样保留；
        - 锁里缺失的追踪包被省略（运行时回退下界）。
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
                    "paddlepaddle-gpu>=3.3.1",
                    "paddleocr[doc-parser]>=3.7.0",
                    "mineru[core]>=3.4.0",
                    "torch>=2.6.0",
                ]
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject

        # 构造 uv.lock：锁定 mineru 3.4.2、paddlepaddle-gpu 3.3.1、torch 2.12.1+cu126。
        # 故意不含 paddleocr（模拟某个追踪包在 lock 里查不到 → 应从 dep_locked_versions 省略）。
        uv_lock = tmp_path / "uv.lock"
        uv_lock.write_text(
            textwrap.dedent("""\
                version = 1

                [[package]]
                name = "mineru"
                version = "3.4.2"

                [[package]]
                name = "paddlepaddle-gpu"
                version = "3.3.1"

                [[package]]
                name = "torch"
                version = "2.12.1+cu126"

                [[package]]
                name = "nvidia-cudnn-cu13"
                version = "9.23.1.3"
            """),
            encoding="utf-8",
        )
        mod.UV_LOCK = uv_lock

        mod._generate_version_json("1.2.3", tmp_path)

        data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
        # dep_locked_versions 应存在且含 lock 中找到的追踪包
        assert "dep_locked_versions" in data, "有锁定版时应写 dep_locked_versions 字段"
        locked = data["dep_locked_versions"]

        # mineru：锁定版 3.4.2（本 Bug 的核心：下界 3.4.0 漏掉 3.4.0→3.4.2 的升级）
        assert locked.get("mineru") == "3.4.2", (
            f"mineru 应取 uv.lock 锁定版 3.4.2，实际: {locked.get('mineru')}"
        )
        # paddlepaddle-gpu → paddlepaddle（与 _KEY_ALIASES_LOCK 归一一致）
        assert locked.get("paddlepaddle") == "3.3.1", (
            "paddlepaddle-gpu 应归一为 paddlepaddle key 并取其锁定版"
        )
        assert "paddlepaddle-gpu" not in locked
        # local label（+cu126）原样保留
        assert locked.get("torch") == "2.12.1+cu126", (
            f"local label 应原样保留，实际: {locked.get('torch')}"
        )
        # lock 中无对应追踪包（paddleocr）→ 应省略（运行时回退下界）
        assert "paddleocr" not in locked, (
            "lock 中缺失的追踪包不应写入 dep_locked_versions（运行时回退下界）"
        )
        # dep_versions 仍应含全部追踪包（不受 lock 影响）
        assert set(data["dep_versions"]).issuperset(
            {"paddlepaddle", "paddleocr", "mineru", "torch"}
        )


class TestChangelogDepDiff:
    """P3：CHANGELOG 应附带依赖变更说明（升级/新增/移除）。"""

    @staticmethod
    def _load_script():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        for k in ("PYPROJECT_TOML", "INIT_PY", "MAIN_PY", "CHANGELOG"):
            os.environ[k] = ""
        spec.loader.exec_module(mod)
        return mod

    def test_changelog_includes_upgrade(self):
        """dep_versions 升级时 CHANGELOG 条目应含 '### Dependencies' 段与升级文案。"""
        mod = self._load_script()
        dep_diff = {
            "upgraded": ["升级 paddlepaddle-gpu 3.3.1 → 3.4.0"],
            "added": [],
            "removed": [],
        }
        entry = mod.generate_changelog_entry("1.0.0", [], dep_diff)
        assert "### Dependencies" in entry
        assert "升级 paddlepaddle-gpu 3.3.1 → 3.4.0" in entry

    def test_changelog_includes_added_and_removed(self):
        """新增 + 移除依赖时 CHANGELOG 应同时列出。"""
        mod = self._load_script()
        dep_diff = {
            "upgraded": [],
            "added": ["新增 scipy>=1.14.0"],
            "removed": ["移除 mineru[core]>=3.4.0"],
        }
        entry = mod.generate_changelog_entry("1.0.0", [], dep_diff)
        assert "新增 scipy>=1.14.0" in entry
        assert "移除 mineru[core]>=3.4.0" in entry

    def test_changelog_omits_dependencies_when_no_diff(self):
        """无依赖变更时 CHANGELOG 不应含 '### Dependencies' 段。"""
        mod = self._load_script()
        entry = mod.generate_changelog_entry("1.0.0", [], None)
        assert "### Dependencies" not in entry

        # 全空 dict 也不应出现
        entry2 = mod.generate_changelog_entry(
            "1.0.0", [], {"upgraded": [], "added": [], "removed": []}
        )
        assert "### Dependencies" not in entry2

    def test_compute_dep_diff_detects_upgrade(self):
        """_compute_dep_diff 应识别版本升级。"""
        mod = self._load_script()
        old = {"paddlepaddle-gpu": "paddlepaddle-gpu>=3.3.1"}
        new = {"paddlepaddle-gpu": "paddlepaddle-gpu>=3.4.0"}
        diff = mod._compute_dep_diff(old, new)
        assert len(diff["upgraded"]) == 1
        assert "3.3.1" in diff["upgraded"][0]
        assert "3.4.0" in diff["upgraded"][0]
        assert diff["added"] == []
        assert diff["removed"] == []

    def test_compute_dep_diff_detects_add_and_remove(self):
        """_compute_dep_diff 应识别新增与移除。"""
        mod = self._load_script()
        old = {"mineru": "mineru[core]>=3.4.0"}
        new = {"scipy": "scipy>=1.14.0"}
        diff = mod._compute_dep_diff(old, new)
        assert len(diff["added"]) == 1
        assert "scipy" in diff["added"][0]
        assert len(diff["removed"]) == 1
        assert "mineru" in diff["removed"][0]
        assert diff["upgraded"] == []


class TestInteractiveMenuBuildOption:
    """交互式菜单的"仅打包当前版本"选项测试"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
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
        assert spec is not None and spec.loader is not None
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

    def test_collect_commits_excludes_merge_commits(self, tmp_path):
        """_collect_commits 必须剔除 merge commit，避免 CHANGELOG 充斥合并噪音

        团队规范要求功能分支合并到 main，git log 默认会列出 "Merge branch 'fix/xxx'"
        这类 merge commit，它们不是真实的变更语义，必须过滤掉。
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
        # main 上一个基线提交并打 tag
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "feat: baseline"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(["git", "tag", "v0.1.0"], cwd=tmp_path, capture_output=True)

        # 建功能分支、提交一条真实改动，再 --no-ff 合并回 main（产生 merge commit）
        subprocess.run(
            ["git", "checkout", "-b", "fix/branch"], cwd=tmp_path, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "fix: real change"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "merge", "--no-ff", "fix/branch", "-m", "Merge branch 'fix/branch'"],
            cwd=tmp_path,
            capture_output=True,
        )

        commits = mod._collect_commits("v0.1.0..HEAD", cwd=tmp_path)
        subjects = [s for _, s in commits]
        assert "fix: real change" in subjects
        assert not any(s.startswith("Merge branch") for s in subjects)

    def test_release_commit_is_boundary_when_current_tag_is_missing(self, tmp_path):
        """缺少当前版本 tag 时，不应从更老 tag 重复收集提交"""
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
        subprocess.run(["git", "tag", "v0.1.0"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "feat: already archived"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "release: v0.1.1"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "fix: only new change"],
            cwd=tmp_path,
            capture_output=True,
        )

        commits = mod.get_commits_since_last_tag("0.1.1", cwd=tmp_path)
        subjects = [subject for _, subject in commits]

        assert subjects == ["fix: only new change"]


class TestCheckUnversionedCommits:
    """测试 check_unversioned_commits 发版前未版本化提交检测"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
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


class TestBumpPushConfirm:
    """bump 后推送确认接线测试（答 y/答 N/--yes）"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _stub_bump_deps(self, mod, monkeypatch):
        """桩掉 bump 流程里的文件/git/changelog 副作用"""
        monkeypatch.setattr(mod, "read_current_version", lambda path: (0, 1, 5))
        monkeypatch.setattr(mod, "update_project_versions", lambda old, new: [])
        monkeypatch.setattr(mod, "_sync_uv_lock", lambda v: False)
        monkeypatch.setattr(mod, "get_commits_since_last_tag", lambda *_args: [])
        monkeypatch.setattr(mod, "update_changelog", lambda v, c, *a, **k: None)
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: None)

    def test_push_yes_invokes_push_release(self, monkeypatch):
        """推送确认答 y → 调用 _push_release，不再问本地打包"""
        mod = self._load_module()
        self._stub_bump_deps(mod, monkeypatch)
        pushed: dict = {}
        monkeypatch.setattr(
            mod, "_push_release", lambda v: pushed.setdefault("v", v) or True
        )
        built: dict = {}
        monkeypatch.setattr(
            mod, "_ask_build", lambda v: built.setdefault("asked", True) or False
        )
        # 推送确认答 y
        monkeypatch.setattr("builtins.input", lambda _p="": "y")
        monkeypatch.setattr("sys.argv", ["bump_version.py", "patch", "--no-build"])

        mod.main()

        assert pushed.get("v") == "0.1.6", "答 y 应触发 _push_release(0.1.6)"
        assert not built.get("asked"), "已推送时不应再问本地打包"

    def test_push_no_skips_push(self, monkeypatch):
        """推送确认答 N（默认）→ 不推送"""
        mod = self._load_module()
        self._stub_bump_deps(mod, monkeypatch)
        pushed: dict = {}
        monkeypatch.setattr(
            mod, "_push_release", lambda v: pushed.setdefault("pushed", True) or False
        )
        # 推送确认答 N；--no-build 跳过打包提示避免再触发 input
        monkeypatch.setattr("builtins.input", lambda _p="": "N")
        monkeypatch.setattr("sys.argv", ["bump_version.py", "patch", "--no-build"])

        mod.main()

        assert not pushed.get("pushed"), "答 N 不应推送"

    def test_yes_flag_auto_pushes(self, monkeypatch):
        """--yes 跳过推送确认直接 _push_release"""
        mod = self._load_module()
        self._stub_bump_deps(mod, monkeypatch)
        pushed: dict = {}
        monkeypatch.setattr(
            mod, "_push_release", lambda v: pushed.setdefault("pushed", True) or True
        )
        monkeypatch.setattr("sys.argv", ["bump_version.py", "patch", "--yes", "--no-build"])

        mod.main()

        assert pushed.get("pushed") is True, "--yes 应直接推送"


class TestOption5UnversionedWarning:
    """选项 5 打包时未版本化提交警告（--build 模式）"""

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
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
            mod, "_run_build",
            lambda v, force=False, frontend="pyside": ran.setdefault("built", True) or True,
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
            mod, "_run_build",
            lambda v, force=False, frontend="pyside": ran.setdefault("built", True) or True,
        )
        monkeypatch.setattr("sys.argv", ["bump_version.py", "--build"])

        mod.main()

        assert ran.get("built") is True


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

    def test_cmd_collects_all_physical_workspace_modules(self):
        """Classic namespace 的四个物理分片必须确定性进入 hidden imports。"""
        mod = self._load_module()
        cmd = mod._get_pyinstaller_cmd("0.5.0")
        hidden = {
            cmd[index + 1]
            for index, value in enumerate(cmd[:-1])
            if value == "--hidden-import"
        }

        assert {
            "vibeocr.startup_metrics",
            "vibeocr.env_manager",
            "vibeocr.views.main_window",
            "vibeocr.supervisor.main",
            "vibeocr.contracts.pipelines",
        }.issubset(hidden)
        assert "--collect-submodules" not in cmd
        assert len(subprocess.list2cmdline(cmd)) < 30_000

    def test_build_stages_namespace_as_one_physical_package(
        self, monkeypatch, tmp_path
    ):
        """PyInstaller pathex 必须优先使用合并后的完整 workspace 包。"""
        mod = self._load_module()
        monkeypatch.setattr(mod, "DIST_BASE_DIR", tmp_path / "dist")

        stage = mod._prepare_workspace_source("0.5.0")
        cmd = mod._get_pyinstaller_cmd("0.5.0", workspace_source=stage)

        assert (stage / "vibeocr/startup_metrics.py").is_file()
        assert (stage / "vibeocr/env_manager.py").is_file()
        assert (stage / "vibeocr/supervisor/main.py").is_file()
        assert not list(stage.rglob("*.pyc"))
        first_paths = cmd.index("--paths")
        assert cmd[first_paths + 1] == str(stage)
        assert f"{stage / 'vibeocr'}{';' if mod.os.name == 'nt' else ':'}vibeocr" in cmd


class TestVersionInfoFileDescription:
    """VibeOCR.exe 的 FileDescription 必须是 'VibeOCR'（任务管理器/属性页显示名）。

    背景：曾误用 pyproject.toml 的 description（'A screenshot OCR application'），
    导致系统里进程名显示为英文描述而非产品名。硬编码为 'VibeOCR' 与 ProductName 一致。
    """

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        for k in ("PYPROJECT_TOML", "INIT_PY", "MAIN_PY", "CHANGELOG"):
            os.environ[k] = ""
        spec.loader.exec_module(mod)
        return mod

    def test_main_file_description_is_vibeocr(self, tmp_path):
        # 即使 pyproject.toml 的 description 是英文描述，
        # 主程序的 FileDescription 也必须固定为 'VibeOCR'。
        mod = self._load_module()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "vibeocr"
                version = "0.1.0"
                description = "A screenshot OCR application"
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject

        version_file = mod._generate_version_file(
            "1.2.3", tmp_path, target="main"
        )
        content = version_file.read_text(encoding="utf-8")
        assert "StringStruct('FileDescription', 'VibeOCR')" in content

    def test_updater_file_description_unchanged(self, tmp_path):
        # updater.exe 的 FileDescription 保持 'VibeOCR auto-updater' 不变。
        mod = self._load_module()
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "vibeocr"
                version = "0.1.0"
                description = "A screenshot OCR application"
            """),
            encoding="utf-8",
        )
        mod.PYPROJECT_TOML = pyproject

        version_file = mod._generate_version_file(
            "1.2.3", tmp_path, target="updater"
        )
        content = version_file.read_text(encoding="utf-8")
        assert "StringStruct('FileDescription', 'VibeOCR auto-updater')" in content


class TestBuildManifestIntegration:
    """_package_zip 应内嵌 artifact-manifest.json 并在打包后自检通过。

    覆盖：
    - manifest 写入 ZIP
    - staging 不含 output/（即使 dist 目录下存在）
    - manifest 校验通过后 _package_zip 返回路径
    """

    @staticmethod
    def _load_module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _make_fake_dist(self, dist_dir: Path) -> None:
        """造一个最小 dist 目录（模拟 PyInstaller 产物）。"""
        (dist_dir / "_internal").mkdir(parents=True)
        (dist_dir / "VibeOCR.exe").write_bytes(b"fake exe")
        (dist_dir / "_internal" / "python313.dll").write_bytes(b"fake dll")
        (dist_dir / "_internal" / "config.json").write_text("{}", encoding="utf-8")

    def test_package_zip_embeds_manifest(self, tmp_path, monkeypatch):
        """_package_zip 应在 ZIP 内写入 artifact-manifest.json。"""
        import json
        import zipfile

        mod = self._load_module()
        # 把 DIST_BASE_DIR 指向临时目录，避免污染真实 dist
        monkeypatch.setattr(mod, "DIST_BASE_DIR", tmp_path / "dist")
        mod.DIST_BASE_DIR.mkdir(parents=True, exist_ok=True)

        dist_dir = mod.DIST_BASE_DIR / "VibeOCR"
        self._make_fake_dist(dist_dir)

        zip_path = mod._package_zip(dist_dir, "9.9.9")
        assert zip_path is not None
        assert zip_path.exists()

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            manifest_members = [n for n in names if n.endswith("artifact-manifest.json")]
            assert len(manifest_members) == 1, f"expected 1 manifest, got {manifest_members}"

            raw = zf.read(manifest_members[0]).decode("utf-8")
            manifest = json.loads(raw)
            assert manifest["entry_count"] > 0
            assert manifest["version"] == 1

    def test_package_zip_rejects_output_in_dist(self, tmp_path, monkeypatch):
        """dist 目录下若混入 output/（本地脏构建），_package_zip 应失败。"""
        mod = self._load_module()
        monkeypatch.setattr(mod, "DIST_BASE_DIR", tmp_path / "dist")
        mod.DIST_BASE_DIR.mkdir(parents=True, exist_ok=True)

        dist_dir = mod.DIST_BASE_DIR / "VibeOCR"
        self._make_fake_dist(dist_dir)
        # 注入 output（模拟脏工作区泄漏）
        (dist_dir / "output").mkdir()
        (dist_dir / "output" / "leaked.pdf").write_bytes(b"user secret data")

        zip_path = mod._package_zip(dist_dir, "9.9.9")
        # manifest 校验应检测到禁止路径，返回 None
        assert zip_path is None

    def test_cleanup_dist_removes_pycache_under_vibeocr(self, tmp_path):
        """_cleanup_dist 应删除 _internal/vibeocr/**/__pycache__。

        回归：src/vibeocr 经 --add-data 收集为 datas 时，源码树下的
        __pycache__/*.pyc 会被 PyInstaller 一并复制进 bundle。manifest 校验
        将 __pycache__ 视为禁止路径，导致 CI 打包失败（见 v0.4.26 release）。
        _cleanup_dist 在打包前清掉这些目录，保证 manifest 校验通过。
        """
        mod = self._load_module()
        dist_dir = tmp_path / "VibeOCR"
        self._make_fake_dist(dist_dir)
        # 模拟 PyInstaller --add-data src/vibeocr:vibeocr 带入的字节码缓存
        pycache = (
            dist_dir
            / "_internal"
            / "vibeocr"
            / "supervisor"
            / "__pycache__"
        )
        pycache.mkdir(parents=True)
        (pycache / "composition.cpython-313.pyc").write_bytes(b"fake pyc")
        (pycache / "__init__.cpython-313.pyc").write_bytes(b"fake pyc")
        # 同时植入嵌套子目录，确认递归清理
        nested = (
            dist_dir
            / "_internal"
            / "vibeocr"
            / "supervisor"
            / "handlers"
            / "__pycache__"
        )
        nested.mkdir(parents=True)
        (nested / "ocr.cpython-313.pyc").write_bytes(b"fake pyc")

        assert pycache.exists() and nested.exists()

        mod._cleanup_dist(dist_dir)

        assert not pycache.exists(), "__pycache__ under _internal/vibeocr should be removed"
        assert not nested.exists(), "nested __pycache__ should be removed recursively"
        # 非 pycache 文件不受影响
        assert (dist_dir / "_internal" / "config.json").exists()
        assert (dist_dir / "VibeOCR.exe").exists()

    def test_package_zip_passes_with_pycache_then_cleanup(self, tmp_path, monkeypatch):
        """端到端：注入 __pycache__ → _cleanup_dist 清理 → _package_zip 通过。

        复刻 v0.4.26 CI 失败的完整链路，验证修复后 manifest 校验 OK。
        """
        mod = self._load_module()
        monkeypatch.setattr(mod, "DIST_BASE_DIR", tmp_path / "dist")
        mod.DIST_BASE_DIR.mkdir(parents=True, exist_ok=True)

        dist_dir = mod.DIST_BASE_DIR / "VibeOCR"
        self._make_fake_dist(dist_dir)
        # 复刻 CI 中导致失败的精确路径
        pycache = (
            dist_dir
            / "_internal"
            / "vibeocr"
            / "supervisor"
            / "__pycache__"
        )
        pycache.mkdir(parents=True)
        (pycache / "composition.cpython-313.pyc").write_bytes(b"fake pyc")

        mod._cleanup_dist(dist_dir)
        zip_path = mod._package_zip(dist_dir, "9.9.9")
        # 清理后 manifest 校验应通过，返回有效路径
        assert zip_path is not None
        assert zip_path.exists()

    def test_lock_file_uses_exact_pins(self):
        """build-shell.lock 每行包约束必须是精确 == 锁定（无 >= / ~>）。"""
        import re

        lock = (
            Path(__file__).parent.parent / "requirements" / "build-shell.lock"
        )
        assert lock.exists(), "build-shell.lock must exist"
        text = lock.read_text(encoding="utf-8")
        # 每个包定义行：name==version \
        pin_lines = re.findall(r"^([a-zA-Z0-9_\-\.\[\]]+)==([^\s\\]+)", text, re.MULTILINE)
        assert len(pin_lines) > 0, "lock file should have pinned packages"
        # 所有包定义行必须是 == 形式（pip-compile --generate-hashes 保证）
        # 此处验证没有 >= / ~= / > 形式的版本约束出现在包定义行
        bad = re.findall(r"^[a-zA-Z0-9_\-\.\[\]]+(>=|~=|>|<)", text, re.MULTILINE)
        assert bad == [], f"lock file should use == only, found: {bad}"
