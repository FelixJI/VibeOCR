"""scripts/updater_main.py 单元测试

覆盖更新助手的核心逻辑：zip 校验/解压、文件替换（含失败回滚）、SHA256 校验。
通过 importlib 按文件路径加载脚本模块（与 test_bump_version.py 一致的做法）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "updater_main.py"


@pytest.fixture(scope="module")
def updater():
    """按文件路径加载 updater_main.py 为模块。"""
    spec = importlib.util.spec_from_file_location("updater_main", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # 注入 sys.modules，使模块内 if __name__ 之外的顶层执行正常
    sys.modules["updater_main"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("updater_main", None)


# ---------------------------------------------------------------------------
# verify_zip
# ---------------------------------------------------------------------------


class TestVerifyZip:
    def test_missing_file(self, updater, tmp_path):
        assert updater.verify_zip(tmp_path / "nope.zip") is False

    def test_bad_zip(self, updater, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        assert updater.verify_zip(bad) is False

    def test_valid_zip(self, updater, tmp_path):
        zp = tmp_path / "ok.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("a.txt", "hello")
        assert updater.verify_zip(zp) is True


# ---------------------------------------------------------------------------
# verify_sha256
# ---------------------------------------------------------------------------


class TestVerifySha256:
    def test_missing_sha256_file_skipped(self, updater, tmp_path):
        """校验文件缺失时按当前约定返回 True（跳过校验）。"""
        zp = tmp_path / "pkg.zip"
        zp.write_bytes(b"data")
        # 不创建 .sha256 文件
        assert updater.verify_sha256(zp) is True

    def test_matching_hash(self, updater, tmp_path):
        zp = tmp_path / "pkg.zip"
        content = b"package content"
        zp.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        (tmp_path / "pkg.zip.sha256").write_text(digest, encoding="utf-8")
        assert updater.verify_sha256(zp) is True

    def test_mismatched_hash(self, updater, tmp_path):
        zp = tmp_path / "pkg.zip"
        zp.write_bytes(b"package content")
        (tmp_path / "pkg.zip.sha256").write_text(
            "0" * 64, encoding="utf-8"
        )
        assert updater.verify_sha256(zp) is False


# ---------------------------------------------------------------------------
# extract_zip
# ---------------------------------------------------------------------------


class TestExtractZip:
    def test_single_top_dir_unwrapped(self, updater, tmp_path):
        """zip 内仅一层目录时应剥掉，返回该目录。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        zp = tmp_path / "pkg.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("VibeOCR-v0.2.0-win64/VibeOCR.exe", "exe")
            zf.writestr("VibeOCR-v0.2.0-win64/version.json", "{}")

        new_dir = updater.extract_zip(zp, app_dir)
        # 应返回剥掉一层后的目录
        assert (new_dir / "VibeOCR.exe").exists()
        assert (new_dir / "version.json").exists()

    def test_flat_files_return_tmp(self, updater, tmp_path):
        """zip 内是平铺文件（无单层目录）时返回 tmp 目录本身。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        zp = tmp_path / "pkg.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("VibeOCR.exe", "exe")
            zf.writestr("version.json", "{}")

        new_dir = updater.extract_zip(zp, app_dir)
        assert (new_dir / "VibeOCR.exe").exists()


# ---------------------------------------------------------------------------
# replace_app_files
# ---------------------------------------------------------------------------


def _make_app_dir(app_dir: Path) -> None:
    """构造一个模拟的已安装 app 目录。"""
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "VibeOCR.exe").write_bytes(b"old exe")
    (app_dir / "version.json").write_text(
        '{"version": "0.1.0", "dep_versions": {}}', encoding="utf-8"
    )
    # 保留目录：应跨更新保留
    (app_dir / "config").mkdir()
    (app_dir / "config" / "settings.json").write_text("{}", encoding="utf-8")
    (app_dir / "data").mkdir()
    (app_dir / "python").mkdir()


def _make_new_files(new_dir: Path) -> None:
    """构造新版本文件。"""
    new_dir.mkdir(parents=True, exist_ok=True)
    (new_dir / "VibeOCR.exe").write_bytes(b"new exe")
    (new_dir / "version.json").write_text(
        '{"version": "0.2.0", "dep_versions": {}}', encoding="utf-8"
    )


class TestReplaceAppFiles:
    def test_successful_replace_preserves_dirs(self, updater, tmp_path):
        app_dir = tmp_path / "app"
        _make_app_dir(app_dir)
        new_dir = tmp_path / "new"
        _make_new_files(new_dir)

        assert updater.replace_app_files(new_dir, app_dir) is True

        assert (app_dir / "VibeOCR.exe").read_bytes() == b"new exe"
        assert (
            app_dir / "config" / "settings.json"
        ).read_text() == "{}"  # 保留目录未动
        assert (app_dir / "data").exists()
        assert (app_dir / "python").exists()

    def test_replace_failure_restores_app(self, updater, tmp_path, monkeypatch):
        """替换过程中出错时，app_dir 必须回滚到更新前状态。

        这是任务3 的核心：避免半残状态导致应用无法启动。
        """
        app_dir = tmp_path / "app"
        _make_app_dir(app_dir)
        new_dir = tmp_path / "new"
        _make_new_files(new_dir)

        original_exe = (app_dir / "VibeOCR.exe").read_bytes()
        original_version = (app_dir / "version.json").read_text(
            encoding="utf-8"
        )

        # 让复制阶段抛错（在第一次 copytree/copy2 时失败）
        import shutil as _shutil

        def _boom(*args, **kwargs):
            raise OSError("simulated disk full")

        monkeypatch.setattr(_shutil, "copy2", _boom)
        monkeypatch.setattr(_shutil, "copytree", _boom)

        result = updater.replace_app_files(new_dir, app_dir)

        # 失败应返回 False
        assert result is False
        # app_dir 必须回滚：旧 exe 和 version.json 都还在、内容不变
        assert (app_dir / "VibeOCR.exe").exists()
        assert (app_dir / "VibeOCR.exe").read_bytes() == original_exe
        assert (app_dir / "version.json").read_text(encoding="utf-8") == original_version
        # 保留目录仍在
        assert (app_dir / "config" / "settings.json").exists()

    def test_replace_cleans_old_files(self, updater, tmp_path):
        """成功替换后，旧版本独有的文件应被清除。"""
        app_dir = tmp_path / "app"
        _make_app_dir(app_dir)
        # 旧版本独有文件
        (app_dir / "old_unused.dll").write_bytes(b"garbage")
        new_dir = tmp_path / "new"
        _make_new_files(new_dir)

        assert updater.replace_app_files(new_dir, app_dir) is True
        # 旧文件被清除
        assert not (app_dir / "old_unused.dll").exists()
        assert (app_dir / "VibeOCR.exe").read_bytes() == b"new exe"
