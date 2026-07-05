"""scripts/update_replacer.py 单元测试

覆盖共享替换逻辑的核心功能：zip 校验/解压、文件替换（含失败回滚）、SHA256 校验、
依赖同步标记、日志、运行中 exe 改名避让、就绪握手信号、统一入口 run_replacement。

该模块被两个调用方复用：updater.exe（首选替换器）与主程序 --self-update 兜底模式。
因此测试锚定在共享模块上（而非轻量入口 updater_main.py），保证两条路径行为一致。
通过 importlib 按文件路径加载脚本模块（与 test_bump_version.py 一致的做法）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "update_replacer.py"


@pytest.fixture(scope="module")
def updater():
    """按文件路径加载 update_replacer.py 为模块。"""
    spec = importlib.util.spec_from_file_location("update_replacer", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # 注入 sys.modules，使模块内顶层执行正常
    sys.modules["update_replacer"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("update_replacer", None)


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
    def test_missing_sha256_file_rejected(self, updater, tmp_path):
        """校验文件缺失时拒绝更新（返回 False），与下载阶段保持一致。"""
        zp = tmp_path / "pkg.zip"
        zp.write_bytes(b"data")
        # 不创建 .sha256 文件
        assert updater.verify_sha256(zp) is False

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
        (tmp_path / "pkg.zip.sha256").write_text("0" * 64, encoding="utf-8")
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
        original_version = (app_dir / "version.json").read_text(encoding="utf-8")

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
        assert (app_dir / "version.json").read_text(
            encoding="utf-8"
        ) == original_version
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


# ---------------------------------------------------------------------------
# _sync_dependencies（写 pending_sync.json 标记，而非直接 pip）
# ---------------------------------------------------------------------------


class TestSyncDependencies:
    """依赖版本同步：updater 不再直接 pip，改为写 pending_sync.json 交给新版程序。

    旧实现用裸 pip install pkg==ver 走 PyPI 默认源，会把 paddle/torch 装成
    CPU 版丢失 CUDA。新实现写标记文件，由覆盖后的 VibeOCR 用
    install_embedded_dependencies（含 GPU/CUDA tag/镜像）升级。

    dep_versions 值为 constraint 串（完整 PEP 440，如 ">=3.3.1" / "==3.3.1+cu126" /
    ">=2.6,<3"），归一化函数兼容三种历史格式（约束串 / {version,op} dict / 裸版本号）。

    新增字段（P1/P2/P4）：
    - dep_versions：变化的包 → constraint 串
    - dep_extras：变化的包的 extras 列表（透传）
    - attempts：失败重试计数（主程序递增，达阈值提示重装 Python）
    - removed：被移除的包名列表（主程序 pip uninstall 清理）
    """

    def test_writes_pending_sync_when_deps_changed(self, updater, tmp_path):
        """dep_versions 有变化时，应写入 pending_sync.json（含变更项 + constraint + attempts）。"""
        # 旧格式 str / 新格式 constraint 串混合，验证归一化比较
        old_deps = {"paddlepaddle": "3.3.0", "torch": "2.6.0"}
        new_data = {
            "version": "0.2.0",
            "dep_versions": {
                "paddlepaddle": ">=3.3.1",
                "torch": ">=2.6.0",
            },
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        assert pending.exists(), "应写入 pending_sync.json"
        import json

        data = json.loads(pending.read_text(encoding="utf-8"))
        # 只含变更项（torch 未变，不应出现）；值为 constraint 串
        assert data["dep_versions"] == {"paddlepaddle": ">=3.3.1"}
        assert data["version"] == "0.2.0"
        assert "written_at" in data
        # P2：初始 attempts 为 1
        assert data["attempts"] == 1

    def test_no_marker_when_deps_unchanged(self, updater, tmp_path):
        """dep_versions 无变化时，不应写入 pending_sync.json。"""
        # 旧裸 str "3.3.1" 归一化为 ">=3.3.1"，与新 constraint ">=3.3.1" 相等 → 不变
        old_deps = {"paddlepaddle": "3.3.1", "torch": "2.6.0"}
        new_data = {
            "version": "0.2.0",
            "dep_versions": {
                "paddlepaddle": ">=3.3.1",
                "torch": ">=2.6.0",
            },
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        assert not pending.exists(), "无变化时不应写标记"

    def test_marker_only_includes_changed_subset(self, updater, tmp_path):
        """多包变更时，标记应只含实际变更的包。"""
        old_deps = {"paddlepaddle": "3.3.0", "paddleocr": "3.7.0", "mineru": "3.4.0"}
        new_data = {
            "version": "0.3.0",
            "dep_versions": {
                "paddlepaddle": ">=3.3.1",  # 变
                "paddleocr": ">=3.7.0",  # 不变
                "mineru": ">=3.4.1",  # 变
            },
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)

        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert set(data["dep_versions"].keys()) == {"paddlepaddle", "mineru"}
        assert data["dep_versions"]["paddlepaddle"] == ">=3.3.1"
        assert data["dep_versions"]["mineru"] == ">=3.4.1"

    def test_does_not_call_pip(self, updater, tmp_path, monkeypatch):
        """同步不应再调用 subprocess（不直接 pip 安装）。"""
        import subprocess as _subprocess

        called = []
        monkeypatch.setattr(
            _subprocess, "run", lambda *a, **kw: called.append(a) or None
        )
        monkeypatch.setattr(
            _subprocess, "Popen", lambda *a, **kw: called.append(a) or None
        )

        old_deps = {"paddlepaddle": "3.3.0"}
        new_data = {
            "version": "0.2.0",
            "dep_versions": {"paddlepaddle": ">=3.3.1"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)

        assert called == [], "同步不应调用 subprocess（pip 安装交给新版程序）"

    def test_creates_settings_dir_if_missing(self, updater, tmp_path):
        """data/settings/ 目录不存在时应自动创建。"""
        old_deps = {"paddlepaddle": "3.3.0"}
        new_data = {
            "version": "0.2.0",
            "dep_versions": {"paddlepaddle": ">=3.3.1"},
        }

        # app_dir 是空的，data/settings 不存在
        updater._sync_dependencies(old_deps, new_data, tmp_path)

        assert (tmp_path / "data" / "settings" / "pending_sync.json").exists()

    # ------------------------------------------------------------------
    # P1：constraint 透传（PEP 440 全格式）
    # ------------------------------------------------------------------

    def test_sync_passes_equals_constraint(self, updater, tmp_path):
        """=='==' 时应原样透传，使主程序能精确锁定/降级版本。"""
        old_deps = {"paddlepaddle": ">=3.4.0"}
        new_data = {
            "version": "0.4.0",
            "dep_versions": {"paddlepaddle": "==3.3.1"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert data["dep_versions"]["paddlepaddle"] == "==3.3.1"

    def test_sync_passes_local_version(self, updater, tmp_path):
        """local version (+cu126) 应完整保留在 constraint 中。"""
        old_deps = {"paddlepaddle": ">=3.3.1"}
        new_data = {
            "version": "0.4.0",
            "dep_versions": {"paddlepaddle": "==3.3.1+cu126"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert data["dep_versions"]["paddlepaddle"] == "==3.3.1+cu126"

    def test_sync_passes_multi_segment_constraint(self, updater, tmp_path):
        """多段约束（>=2.6,<3）应完整透传，不丢失后半段。"""
        old_deps = {"torch": ">=2.6.0"}
        new_data = {
            "version": "0.4.0",
            "dep_versions": {"torch": ">=2.6.0,<3.0.0"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert data["dep_versions"]["torch"] == ">=2.6.0,<3.0.0"

    def test_sync_passes_compatible_release(self, updater, tmp_path):
        """~= 兼容发行操作符应正确透传。"""
        old_deps = {"torch": ">=2.5.0"}
        new_data = {
            "version": "0.4.0",
            "dep_versions": {"torch": "~=2.6.0"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert data["dep_versions"]["torch"] == "~=2.6.0"

    def test_constraint_change_triggers_sync(self, updater, tmp_path):
        """仅 constraint 变化(版本相同但操作符变)也应触发同步。"""
        old_deps = {"paddlepaddle": ">=3.3.1"}
        new_data = {
            "version": "0.4.0",
            "dep_versions": {"paddlepaddle": "==3.3.1"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert data["dep_versions"]["paddlepaddle"] == "==3.3.1"

    # ------------------------------------------------------------------
    # 三层向后兼容（归一化）
    # ------------------------------------------------------------------

    def test_normalizes_legacy_version_op_dict(self, updater, tmp_path):
        """曾用 {version, op} dict 格式应被归一化为 constraint 串比较。"""
        # 旧版 dict {version:3.3.0,op:>=} 与新版 constraint ">=3.3.0" 应判为相等
        old_deps = {"paddlepaddle": {"version": "3.3.0", "op": ">="}}
        new_data = {
            "version": "0.4.0",
            "dep_versions": {"paddlepaddle": ">=3.3.0"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        assert not pending.exists(), "归一化后相等不应写标记"

    def test_normalizes_legacy_bare_version(self, updater, tmp_path):
        """旧旧版裸版本号 str 应按 >=N 归一化比较。"""
        # 旧裸 "3.3.0" → ">=3.3.0"，与新版 ">=3.3.0" 相等
        old_deps = {"paddlepaddle": "3.3.0"}
        new_data = {
            "version": "0.4.0",
            "dep_versions": {"paddlepaddle": ">=3.3.0"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        assert not pending.exists(), "归一化后相等不应写标记"

    def test_writes_legacy_dict_format_as_constraint(self, updater, tmp_path):
        """新版若仍写 {version,op} dict（旧 bump_version 生成），应转成 constraint 写入。"""
        old_deps = {"paddlepaddle": "3.3.0"}
        new_data = {
            "version": "0.4.0",
            "dep_versions": {"paddlepaddle": {"version": "3.3.1", "op": "=="}},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        # dict 应被归一化为 constraint 串
        assert data["dep_versions"]["paddlepaddle"] == "==3.3.1"

    # ------------------------------------------------------------------
    # P1 extras：透传
    # ------------------------------------------------------------------

    def test_passes_extras_for_changed_pkg(self, updater, tmp_path):
        """变化的包带 extras 时，应透传 dep_extras。"""
        old_deps = {"paddleocr": ">=3.6.0"}
        new_data = {
            "version": "0.4.0",
            "dep_versions": {"paddleocr": ">=3.7.0"},
            "dep_extras": {"paddleocr": ["doc-parser"]},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert data.get("dep_extras") == {"paddleocr": ["doc-parser"]}

    def test_no_extras_field_when_none(self, updater, tmp_path):
        """无 extras 时不写 dep_extras 字段。"""
        old_deps = {"paddlepaddle": "3.3.0"}
        new_data = {
            "version": "0.2.0",
            "dep_versions": {"paddlepaddle": ">=3.3.1"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert "dep_extras" not in data

    # ------------------------------------------------------------------
    # P2：attempts 计数初始化
    # ------------------------------------------------------------------

    def test_attempts_initialized_to_1(self, updater, tmp_path):
        """pending_sync 首次写入时 attempts 应为 1。"""
        old_deps = {"paddlepaddle": "3.3.0"}
        new_data = {
            "version": "0.2.0",
            "dep_versions": {"paddlepaddle": ">=3.3.1"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert data["attempts"] == 1

    # ------------------------------------------------------------------
    # P4：removed 字段透传
    # ------------------------------------------------------------------

    def test_writes_removed_when_dep_dropped(self, updater, tmp_path):
        """旧版有、新版无的追踪包，应记入 removed 字段。"""
        old_deps = {
            "paddlepaddle": ">=3.3.1",
            "mineru": ">=3.4.0",  # 新版移除
        }
        new_data = {
            "version": "0.5.0",
            "dep_versions": {"paddlepaddle": ">=3.3.1"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert data.get("removed") == ["mineru"]

    def test_no_removed_field_when_nothing_dropped(self, updater, tmp_path):
        """无移除时不应写 removed 字段（旧读端兼容）。"""
        old_deps = {"paddlepaddle": "3.3.0"}
        new_data = {
            "version": "0.2.0",
            "dep_versions": {"paddlepaddle": ">=3.3.1"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert "removed" not in data

    def test_filters_non_tracked_from_removed(self, updater, tmp_path):
        """removed 只含 _TRACKED_PREFIXES 内的包，过滤掉非追踪残留。"""
        old_deps = {
            "paddlepaddle": ">=3.3.1",
            "numpy": ">=2.0.0",  # 非追踪包，不应进 removed
            "torch": ">=2.6.0",
        }
        new_data = {
            "version": "0.5.0",
            # 新版移除 torch，保留 paddlepaddle
            "dep_versions": {"paddlepaddle": ">=3.3.1"},
        }

        updater._sync_dependencies(old_deps, new_data, tmp_path)
        import json

        pending = tmp_path / "data" / "settings" / "pending_sync.json"
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert data.get("removed") == ["torch"]


# ---------------------------------------------------------------------------
# _setup_logging（写文件日志，确保 console=False 时仍有现场）
# ---------------------------------------------------------------------------


class TestSetupLogging:
    def test_writes_log_file(self, updater, tmp_path):
        """setup_logging 应在 data/logs/ 下创建指定日志文件并能记录日志。"""
        updater.setup_logging(tmp_path, "updater.log")

        updater.logger.info("test message from setup_logging")

        log_file = tmp_path / "data" / "logs" / "updater.log"
        assert log_file.exists(), "应创建 updater.log"
        content = log_file.read_text(encoding="utf-8")
        assert "test message from setup_logging" in content

    def test_self_update_log_uses_separate_filename(self, updater, tmp_path):
        """self-update 模式应能写入独立的 self_update.log，与 updater.log 区分。"""
        updater.setup_logging(tmp_path, "self_update.log")
        updater.logger.info("self-update path message")

        log_file = tmp_path / "data" / "logs" / "self_update.log"
        assert log_file.exists(), "应创建 self_update.log"
        assert "self-update path message" in log_file.read_text(encoding="utf-8")

    def test_logging_survives_missing_log_dir(self, updater, tmp_path, monkeypatch):
        """日志目录创建失败时不应抛异常（退化到 stdout）。"""
        def _boom(*a, **kw):
            raise OSError("no permission")

        monkeypatch.setattr("pathlib.Path.mkdir", _boom)
        # 不应抛异常
        updater.setup_logging(tmp_path, "updater.log")


# ---------------------------------------------------------------------------
# _rename_locked_self_exe（处理运行中的 updater.exe 无法删除/覆盖）
# ---------------------------------------------------------------------------


class TestRenameLockedSelfExe:
    def test_renames_self_exe_on_windows_only(self, updater, tmp_path, monkeypatch):
        """Windows 下应把 updater.exe 改名为 updater.exe.old。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "updater.exe").write_bytes(b"old running exe")

        monkeypatch.setattr(updater.os, "name", "nt")
        updater.rename_locked_self_exe(app_dir, "updater.exe")

        assert not (app_dir / "updater.exe").exists()
        assert (app_dir / "updater.exe.old").read_bytes() == b"old running exe"

    def test_renames_vibeocr_exe_for_self_update(self, updater, tmp_path, monkeypatch):
        """self-update 模式应能把 VibeOCR.exe 改名避让（通用化 self_name 参数）。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "VibeOCR.exe").write_bytes(b"running main exe")

        monkeypatch.setattr(updater.os, "name", "nt")
        updater.rename_locked_self_exe(app_dir, "VibeOCR.exe")

        assert not (app_dir / "VibeOCR.exe").exists()
        assert (app_dir / "VibeOCR.exe.old").read_bytes() == b"running main exe"

    def test_noop_on_non_windows(self, updater, tmp_path, monkeypatch):
        """非 Windows 下不做任何事。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "updater.exe").write_bytes(b"exe")

        monkeypatch.setattr(updater.os, "name", "posix")
        updater.rename_locked_self_exe(app_dir, "updater.exe")

        # 文件保持不变
        assert (app_dir / "updater.exe").read_bytes() == b"exe"
        assert not (app_dir / "updater.exe.old").exists()

    def test_noop_when_self_exe_absent(self, updater, tmp_path, monkeypatch):
        """目标 exe 不存在时不应抛异常。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()

        monkeypatch.setattr(updater.os, "name", "nt")
        updater.rename_locked_self_exe(app_dir, "updater.exe")  # 不应抛异常

    def test_removes_stale_old_before_rename(self, updater, tmp_path, monkeypatch):
        """应先清理上次更新残留的 .old 再改名。"""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "updater.exe").write_bytes(b"current")
        (app_dir / "updater.exe.old").write_bytes(b"stale from last update")

        monkeypatch.setattr(updater.os, "name", "nt")
        updater.rename_locked_self_exe(app_dir, "updater.exe")

        assert not (app_dir / "updater.exe").exists()
        assert (app_dir / "updater.exe.old").read_bytes() == b"current"


# ---------------------------------------------------------------------------
# run_replacement（统一入口：写就绪信号 + 顶层异常兜底写日志后返回 1）
# ---------------------------------------------------------------------------


class TestRunReplacementExceptionGuard:
    def test_writes_ready_signal_before_work(self, updater, tmp_path):
        """run_replacement 应在做任何替换前写出就绪信号文件（供主程序端握手）。"""
        zp = tmp_path / "pkg.zip"
        zp.write_bytes(b"data")
        # 校验会失败（缺 .sha256），但 ready 信号应已写出。
        app_dir = tmp_path / "app"
        app_dir.mkdir()

        updater.run_replacement(zp, app_dir, ready_filename="updater.ready")

        ready = app_dir / "data" / "cache" / "update" / "updater.ready"
        assert ready.exists(), "应在替换前写出就绪信号"

    def test_uncaught_exception_returns_1_and_logs(
        self, updater, tmp_path, monkeypatch
    ):
        """verify_zip 抛非预期异常时，run_replacement 应回退捕获并写日志、返回 1。

        与真实调用方（updater_main.main / main._run_self_update）一致：先 setup_logging
        再调 run_replacement，故异常现场会落到日志文件。
        """
        zp = tmp_path / "pkg.zip"
        zp.write_bytes(b"data")
        app_dir = tmp_path / "app"
        app_dir.mkdir()

        def _boom(_zip):
            raise RuntimeError("unexpected boom")

        monkeypatch.setattr(updater, "verify_zip", _boom)

        updater.setup_logging(app_dir, "updater.log")
        assert updater.run_replacement(zp, app_dir) == 1

        log_file = app_dir / "data" / "logs" / "updater.log"
        assert log_file.exists()
        assert "unexpected boom" in log_file.read_text(encoding="utf-8")
