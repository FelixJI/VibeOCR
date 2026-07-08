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
            replacer.run_replacement(zip_path, app_dir, self_exe_names=("VibeOCR.exe",))

        assert not verify_zip_called, "run_replacement 不应调用 verify_zip"
