"""scripts/update_replacer.py 单元测试 —— 架构重构新增逻辑。

加载方式遵循仓库脚本测试惯例（参考 tests/test_updater_main.py）：
importlib 按路径加载，避免依赖 src/vibeocr 包。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "update_replacer.py"


@pytest.fixture(scope="module")
def replacer():
    """按路径加载 scripts/update_replacer.py（纯 stdlib 模块）。"""
    spec = importlib.util.spec_from_file_location("update_replacer_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["update_replacer_test"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("update_replacer_test", None)


class TestDetectSelfExeNames:
    """_detect_self_exe_names：根据 updater 自身位置判断是否需避让 updater.exe。

    新架构下 updater 从暂存目录（data/cache/update/）运行，不在 app_dir，
    故 app_dir/updater.exe(旧) 无人运行、可直接覆盖，无需避让。
    旧路径（过渡期）updater 自身在 app_dir，仍需避让自己。
    """

    def test_updater_in_app_dir_needs_avoidance(self, replacer, monkeypatch, tmp_path):
        """旧路径：updater 自身在 app_dir → 返回 ('updater.exe',)。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        updater_exe = app_dir / "updater.exe"
        updater_exe.write_bytes(b"fake")
        monkeypatch.setattr("sys.argv", [str(updater_exe)])
        result = replacer._detect_self_exe_names(app_dir)
        assert result == ("updater.exe",)

    def test_updater_in_staging_no_avoidance(self, replacer, monkeypatch, tmp_path):
        """新路径：updater 在暂存目录（不在 app_dir）→ 返回 ()。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        staging = tmp_path / "data" / "cache" / "update"
        staging.mkdir(parents=True)
        updater_exe = staging / "updater.exe"
        updater_exe.write_bytes(b"fake")
        monkeypatch.setattr("sys.argv", [str(updater_exe)])
        result = replacer._detect_self_exe_names(app_dir)
        assert result == ()

    def test_no_argv0_fallback_to_avoidance(self, replacer, monkeypatch, tmp_path):
        """sys.argv[0] 无法解析为 app_dir 内文件时，保守返回旧路径（需避让）。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        monkeypatch.setattr("sys.argv", [""])  # 空 argv[0]
        result = replacer._detect_self_exe_names(app_dir)
        assert result == ("updater.exe",)

    def test_non_windows_no_avoidance(self, replacer, monkeypatch, tmp_path):
        """非 Windows：无 PE 映射锁问题，直接返回 ()（不避让）。

        新旧路径判定仅 Windows 有意义（PE 锁是 Windows 独有）。
        """
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        updater_exe = app_dir / "updater.exe"
        updater_exe.write_bytes(b"fake")
        monkeypatch.setattr("sys.argv", [str(updater_exe)])
        monkeypatch.setattr("os.name", "posix")
        result = replacer._detect_self_exe_names(app_dir)
        assert result == ()


# ---------------------------------------------------------------------------
# run_replacement 成功路径：不再 cleanup / 不再 verify_zip（新架构）
# ---------------------------------------------------------------------------


def _make_app_dir(app_dir: Path) -> None:
    """造一个最小可用的 app_dir（含保留目录 + version.json + 旧 exe）。"""
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "python").mkdir(exist_ok=True)
    (app_dir / "data").mkdir(exist_ok=True)
    (app_dir / "config").mkdir(exist_ok=True)
    (app_dir / "version.json").write_text(
        '{"version": "1.0.0", "dep_versions": {}}', encoding="utf-8"
    )
    (app_dir / "VibeOCR.exe").write_bytes(b"old main")
    (app_dir / "updater.exe").write_bytes(b"old updater")


def _make_new_files(new_dir: Path) -> None:
    """造新版文件目录（模拟解压结果）。"""
    new_dir.mkdir(parents=True, exist_ok=True)
    (new_dir / "version.json").write_text(
        '{"version": "9.9.9", "dep_versions": {}}', encoding="utf-8"
    )
    (new_dir / "VibeOCR.exe").write_bytes(b"new main")
    (new_dir / "updater.exe").write_bytes(b"new updater")


class TestRunReplacementNoCleanup:
    """run_replacement 成功路径不再调用 cleanup（清理移交给新主程序后台线程）。

    验证：成功路径下，tmp/zip/sha256 残留仍在（未被 cleanup 删除），
    因为 updater 启动主程序后立即退出，清理由新主程序负责。
    """

    def test_successful_path_leaves_artifacts(self, replacer, monkeypatch, tmp_path):
        """成功更新后，tmp/zip/sha256 仍在（等新主程序后台清理）。"""
        import zipfile

        app_dir = tmp_path / "app"
        _make_app_dir(app_dir)
        cache_dir = app_dir / "data" / "cache" / "update"
        cache_dir.mkdir(parents=True, exist_ok=True)

        # 造假 zip + sha256
        zip_path = cache_dir / "VibeOCR-v9.9.9-win64.zip"
        new_dir_content = tmp_path / "new_src"
        _make_new_files(new_dir_content)
        with zipfile.ZipFile(zip_path, "w") as zf:
            for f in new_dir_content.rglob("*"):
                if f.is_file():
                    zf.write(f, f"VibeOCR/{f.relative_to(new_dir_content)}")
        # updater 在 zip 里
        (new_dir_content / "updater.exe").write_bytes(b"new updater")
        # 重写 zip 确保含 updater.exe
        zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("VibeOCR/version.json", '{"version": "9.9.9", "dep_versions": {}}')
            zf.writestr("VibeOCR/updater.exe", b"new updater")
            zf.writestr("VibeOCR/VibeOCR.exe", b"new main")
        sha_path = cache_dir / "VibeOCR-v9.9.9-win64.zip.sha256"
        import hashlib

        sha_path.write_text(hashlib.sha256(zip_path.read_bytes()).hexdigest())

        # mock launch_app 和 os._exit（避免真启动进程/真退出测试进程）
        monkeypatch.setattr(replacer, "launch_app", lambda *a, **k: None)
        monkeypatch.setattr(
            replacer.os,
            "_exit",
            lambda code=0: (_ for _ in ()).throw(SystemExit(code)),
        )

        # updater 自身在暂存目录（新路径）→ 无需避让
        staging_updater = cache_dir / "updater.exe"
        staging_updater.write_bytes(b"staging updater")
        monkeypatch.setattr("sys.argv", [str(staging_updater)])

        with pytest.raises(SystemExit):
            replacer.run_replacement(
                zip_path,
                app_dir,
                self_exe_names=("VibeOCR.exe",),  # 仅避让主程序（新路径）
                ready_filename="updater.ready",
                launch_entry="VibeOCR.exe",
            )

        # 关键断言：cleanup 未被调用，tmp/zip/sha256 仍在
        assert zip_path.exists(), "成功路径不应删 zip（交给新主程序后台清理）"
        assert sha_path.exists(), "成功路径不应删 sha256"
        tmp_dir = cache_dir / "tmp"
        assert tmp_dir.exists(), "成功路径不应删 tmp（交给新主程序后台清理）"


class TestRunReplacementNoVerifyZip:
    """run_replacement 不再调用 verify_zip（主程序端已 testzip + SHA256 更强）。

    updater 端省略 testzip：主程序递送时已做 testzip 确保 zip 可读，
    SHA256（更强）由 updater 自己做。重复 testzip 无价值，从关键路径移除。
    """

    def test_verify_zip_not_called(self, replacer, monkeypatch, tmp_path):
        """run_replacement 不应调用 verify_zip。"""
        import zipfile

        app_dir = tmp_path / "app"
        _make_app_dir(app_dir)
        cache_dir = app_dir / "data" / "cache" / "update"
        cache_dir.mkdir(parents=True, exist_ok=True)

        zip_path = cache_dir / "VibeOCR-v9.9.9-win64.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("VibeOCR/version.json", '{"version": "9.9.9", "dep_versions": {}}')
            zf.writestr("VibeOCR/updater.exe", b"new updater")
            zf.writestr("VibeOCR/VibeOCR.exe", b"new main")
        sha_path = cache_dir / "VibeOCR-v9.9.9-win64.zip.sha256"
        import hashlib

        sha_path.write_text(hashlib.sha256(zip_path.read_bytes()).hexdigest())

        # 标记 verify_zip 是否被调用
        verify_zip_called = []
        monkeypatch.setattr(
            replacer, "verify_zip", lambda p: verify_zip_called.append(1) or True
        )
        monkeypatch.setattr(replacer, "launch_app", lambda *a, **k: None)
        monkeypatch.setattr(
            replacer.os,
            "_exit",
            lambda code=0: (_ for _ in ()).throw(SystemExit(code)),
        )

        staging_updater = cache_dir / "updater.exe"
        staging_updater.write_bytes(b"staging updater")
        monkeypatch.setattr("sys.argv", [str(staging_updater)])

        with pytest.raises(SystemExit):
            replacer.run_replacement(
                zip_path,
                app_dir,
                self_exe_names=("VibeOCR.exe",),
                launch_entry="VibeOCR.exe",
            )

        assert not verify_zip_called, "run_replacement 不应调用 verify_zip"


class TestUpdaterMainSelfExeNames:
    """updater_main.main 只避让 updater 自身与调用产品的显式入口。"""

    def test_staging_path_excludes_updater_exe(self, replacer, monkeypatch, tmp_path):
        """新路径无需避让 updater 自身，但要覆盖新旧应用入口。"""
        import importlib.util

        # 加载 updater_main 模块（它 import update_replacer，需先注入 sys.modules）
        updater_main_script = Path(__file__).parent.parent / "scripts" / "updater_main.py"
        monkeypatch.setitem(sys.modules, "update_replacer", replacer)
        spec = importlib.util.spec_from_file_location("updater_main_test", updater_main_script)
        assert spec is not None and spec.loader is not None
        updater_main = importlib.util.module_from_spec(spec)
        sys.modules["updater_main_test"] = updater_main
        spec.loader.exec_module(updater_main)

        app_dir = tmp_path / "app"
        app_dir.mkdir()
        staging = tmp_path / "data" / "cache" / "update"
        staging.mkdir(parents=True)
        monkeypatch.setattr("sys.argv", [str(staging / "updater.exe"),
                                          "--update", str(tmp_path / "x.zip"),
                                          "--app-dir", str(app_dir),
                                          "--entry", "VibeOCR.exe"])

        captured = {}
        def fake_run_replacement(zip_p, app_d, **kwargs):
            captured["self_exe_names"] = kwargs.get("self_exe_names")
            captured["launch_entry"] = kwargs.get("launch_entry")
            captured["launch_args"] = kwargs.get("launch_args")
            return 0
        # 注意：updater_main 内 ``run_replacement``/``setup_logging`` 是
        # ``from update_replacer import ...`` 绑入的模块全局名，import 时已固定引用，
        # 后续 patch ``replacer.run_replacement`` 不会影响 updater_main 内的裸调用。
        # 必须 patch updater_main 模块自身的全局绑定，main() 里的裸调用才会命中 mock。
        monkeypatch.setattr(updater_main, "run_replacement", fake_run_replacement)
        monkeypatch.setattr(updater_main, "setup_logging", lambda *a, **k: None)

        updater_main.main()

        assert captured["self_exe_names"] == ("VibeOCR.exe",)
        assert captured["launch_entry"] == "VibeOCR.exe"
        assert captured["launch_args"] == ()

    def test_old_path_includes_updater_exe(self, replacer, monkeypatch, tmp_path):
        """旧路径还需避让 app_dir 中正在运行的 updater。"""
        import importlib.util

        updater_main_script = Path(__file__).parent.parent / "scripts" / "updater_main.py"
        monkeypatch.setitem(sys.modules, "update_replacer", replacer)
        spec = importlib.util.spec_from_file_location("updater_main_test2", updater_main_script)
        assert spec is not None and spec.loader is not None
        updater_main = importlib.util.module_from_spec(spec)
        sys.modules["updater_main_test2"] = updater_main
        spec.loader.exec_module(updater_main)

        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "updater.exe").write_bytes(b"old")
        monkeypatch.setattr("sys.argv", [str(app_dir / "updater.exe"),
                                          "--update", str(tmp_path / "x.zip"),
                                          "--app-dir", str(app_dir),
                                          "--entry", "VibeOCR.Bootstrapper.exe",
                                          "--entry-arg=--profile",
                                          "--entry-arg=production"])

        captured = {}
        def fake_run_replacement(zip_p, app_d, **kwargs):
            captured["self_exe_names"] = kwargs.get("self_exe_names")
            captured["launch_entry"] = kwargs.get("launch_entry")
            captured["launch_args"] = kwargs.get("launch_args")
            return 0
        # 同上：patch updater_main 模块全局（from-import 绑定于 import 时已固定）。
        monkeypatch.setattr(updater_main, "run_replacement", fake_run_replacement)
        monkeypatch.setattr(updater_main, "setup_logging", lambda *a, **k: None)

        updater_main.main()

        assert captured["self_exe_names"] == (
            "updater.exe",
            "VibeOCR.Bootstrapper.exe",
        )
        assert captured["launch_entry"] == "VibeOCR.Bootstrapper.exe"
        assert captured["launch_args"] == ("--profile", "production")


class TestProductionRelaunch:
    def test_launch_uses_bootstrapper_production_profile(
        self, replacer, monkeypatch, tmp_path
    ):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        bootstrapper = app_dir / "VibeOCR.Bootstrapper.exe"
        bootstrapper.write_bytes(b"entry")
        (app_dir / "VibeOCR.exe").write_bytes(b"classic entry")
        calls = []

        def fake_popen(*args, **kwargs):
            calls.append((args, kwargs))
            health_file = Path(args[0][-1])
            health_file.parent.mkdir(parents=True, exist_ok=True)
            health_file.write_text('{"status":"healthy"}', encoding="utf-8")

        monkeypatch.setattr(replacer.subprocess, "Popen", fake_popen)

        health_file = app_dir / "data/cache/update/startup.healthy"
        replacer.launch_app(
            app_dir,
            "VibeOCR.Bootstrapper.exe",
            entry_args=(
                "--profile",
                "production",
                "--health-file",
                str(health_file),
            ),
            health_file=health_file,
        )

        assert calls[0][0][0] == [
            str(bootstrapper),
            "--profile",
            "production",
            "--health-file",
            str(app_dir / "data/cache/update/startup.healthy"),
        ]

    def test_launches_explicit_classic_without_winui_arguments(
        self, replacer, monkeypatch, tmp_path
    ):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        classic = app_dir / "VibeOCR.exe"
        classic.write_bytes(b"classic entry")
        calls = []
        monkeypatch.setattr(
            replacer.subprocess,
            "Popen",
            lambda *args, **kwargs: calls.append((args, kwargs)),
        )

        replacer.launch_app(app_dir, "VibeOCR.exe")

        assert calls == [
            (
                ([str(classic)],),
                {"creationflags": 0x8, "cwd": str(app_dir)},
            )
        ]
        assert not (app_dir / "data/cache/update/startup.healthy").exists()

    def test_missing_explicit_entrypoint_does_not_fall_back(
        self, replacer, tmp_path
    ):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "VibeOCR.WinUI.exe").write_bytes(b"not a formal entry")

        with pytest.raises(FileNotFoundError) as exc_info:
            replacer.launch_app(app_dir, "VibeOCR.Bootstrapper.exe")

        message = str(exc_info.value)
        assert "VibeOCR.Bootstrapper.exe" in message
        assert "VibeOCR.exe" not in message

    def test_failure_never_relaunches_legacy_ui(
        self, replacer, monkeypatch, tmp_path
    ):
        zip_path = tmp_path / "missing.zip"
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        launches = []
        notices = []
        monkeypatch.setattr(replacer, "launch_app", lambda *a, **k: launches.append(a))

        assert replacer.run_replacement(
            zip_path,
            app_dir,
            launch_entry="VibeOCR.exe",
            on_failure=notices.append,
        ) == 1
        assert launches == []
        assert notices


class TestSyncDependenciesLockBump:
    """_sync_dependencies 必须能检出 uv.lock 锁定版的升级（constraint 不变场景）。

    场景：mineru 的 pyproject 约束 ``>=3.4.0`` 跨版本未变，但 uv.lock 锁定版从
    3.4.0 升到 3.4.2。仅比对 dep_versions（约束串）会判"无变化"而漏同步，
    导致便携环境永久停留在旧锁定版 3.4.0。

    dep_locked_versions 正是为捕获这类下界内升级而引入的字段，替换器必须一并比对。
    """

    @staticmethod
    def _read_pending(app_dir: Path) -> dict:
        """读取待同步标记（不存在则空 dict）。"""
        import json

        pending = app_dir / "data" / "settings" / "pending_sync.json"
        if not pending.exists():
            return {}
        return json.loads(pending.read_text(encoding="utf-8"))

    def test_constraint_unchanged_but_lock_bumped_triggers_sync(
        self, replacer, tmp_path
    ):
        """约束不变、仅锁定版升级（3.4.0→3.4.2）应写入待同步标记。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir(parents=True)
        # 模拟 run_replacement 实际传入：old_deps 是旧 dep_versions，
        # old_locked 是旧 dep_locked_versions
        old_deps = {"mineru": ">=3.4.0"}
        old_locked = {"mineru": "3.4.0"}
        new_data = {
            "version": "0.4.19",
            "dep_versions": {"mineru": ">=3.4.0"},  # 约束不变
            "dep_locked_versions": {"mineru": "3.4.2"},  # 锁定版升级
        }

        replacer._sync_dependencies(old_deps, new_data, app_dir, old_locked)

        pending = self._read_pending(app_dir)
        changed = pending.get("dep_versions", {})
        assert "mineru" in changed, (
            "锁定版升级（3.4.0→3.4.2）即便约束不变也应触发同步，"
            f"实际 changed={changed}"
        )

    def test_old_lock_absent_new_lock_present_triggers_sync(
        self, replacer, tmp_path
    ):
        """旧版无 dep_locked_versions（兼容旧 version.json），新版有 → 视为变化触发同步。

        旧便携版可能在 dep_locked_versions 引入前发布（字段缺失），新版首次携带，
        全部追踪包都应同步以确保便携环境与新版 lock 对齐。
        """
        app_dir = tmp_path / "app"
        app_dir.mkdir(parents=True)
        old_deps = {"mineru": ">=3.4.0"}
        old_locked: dict = {}  # 旧版无此字段
        new_data = {
            "version": "0.4.19",
            "dep_versions": {"mineru": ">=3.4.0"},
            "dep_locked_versions": {"mineru": "3.4.2"},
        }

        replacer._sync_dependencies(old_deps, new_data, app_dir, old_locked)

        pending = self._read_pending(app_dir)
        assert "mineru" in pending.get("dep_versions", {})

    def test_lock_unchanged_no_sync(self, replacer, tmp_path):
        """约束与锁定版都不变时不写标记（回归：不误报）。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir(parents=True)
        old_deps = {"mineru": ">=3.4.0"}
        old_locked = {"mineru": "3.4.2"}
        new_data = {
            "version": "0.4.19",
            "dep_versions": {"mineru": ">=3.4.0"},
            "dep_locked_versions": {"mineru": "3.4.2"},
        }

        replacer._sync_dependencies(old_deps, new_data, app_dir, old_locked)

        pending = self._read_pending(app_dir)
        assert pending == {}, f"锁定版未变不应触发同步，实际 pending={pending}"
