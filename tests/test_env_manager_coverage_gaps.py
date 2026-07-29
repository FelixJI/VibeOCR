"""Branch-coverage tests for env_manager gaps not covered by the focused suites.

Existing suites (test_env_manager_install.py, _gpu_info.py, _bundled_paths.py,
_runtime_paths.py, test_download_artifact_multi_source.py) cover the main
behaviors. This module fills the remaining branch gaps:
- get_environment_mode / get_embedded_python_* path functions (None-default,
  portable, non-nt branches).
- download_file_with_progress: resume, retry, exception paths.
- download_artifact_multi_source: ImportError fallback for update_service.
- _parse_uv_lock / _load_locked_versions: file-missing / parse-error / packaged.
- check_dependencies / check_current_environment_dependencies / is_production_environment_ready.
- detect_dependency_updates: version comparison branches.
- install_embedded_python: symlink skip, network_type ordering, pip self-check.
- switch_paddle_backend: invalid target, missing python, cancel paths.
- detect_gpu / detect_cuda_version: generic-exception branches.
"""

from __future__ import annotations

import io
import logging
import subprocess
import tarfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import vibeocr.backend.env_manager as em
from vibeocr.backend.env_manager import (
    CUDA_VERSION_MAP,
    _dedup_preserve_order,
    _is_gpu_requirement,
    _load_locked_versions,
    _parse_uv_lock,
    _pkg_in_force_reinstall_set,
    check_current_environment_dependencies,
    check_dependencies,
    download_artifact_multi_source,
    download_file_with_progress,
    get_embedded_python_executable,
    get_embedded_python_info,
    get_embedded_python_path,
    get_embedded_venv_python,
    get_environment_mode,
    install_embedded_python,
    is_embedded_python_installed,
    is_production_environment_ready,
)

# ---------------------------------------------------------------------------
# Path / mode functions
# ---------------------------------------------------------------------------


class TestEnvironmentModeAndPaths:
    def test_environment_mode_portable(self, tmp_path):
        """python/ 存在 → portable 模式。"""
        (tmp_path / "python").mkdir()
        assert get_environment_mode(tmp_path) == "portable"

    def test_environment_mode_none(self, tmp_path):
        """无 .venv / python → none。"""
        assert get_environment_mode(tmp_path) == "none"

    def test_environment_mode_venv_dir(self, tmp_path):
        """仅 .venv 目录存在（非当前解释器）→ venv。"""
        (tmp_path / ".venv").mkdir()
        assert get_environment_mode(tmp_path) == "venv"

    def test_get_embedded_python_path_portable(self, tmp_path):
        """portable 模式返回 python/ 目录。"""
        assert get_embedded_python_path(tmp_path) == tmp_path / "python"

    def test_get_embedded_python_path_venv(self, tmp_path):
        """.venv/Scripts 存在时返回其父目录。"""
        scripts = tmp_path / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        # create python.exe so .parent is returned
        exe = scripts / "python.exe"
        exe.touch()
        result = get_embedded_python_path(tmp_path)
        assert result == scripts

    def test_get_embedded_python_executable_portable(self, tmp_path):
        """portable 模式返回 python/python.exe。"""
        result = get_embedded_python_executable(tmp_path)
        assert result == tmp_path / "python" / "python.exe"

    def test_get_embedded_venv_python_default_project_root(self, monkeypatch):
        """project_root=None 时用 get_project_root()（line 403）。"""
        with patch("vibeocr.backend.env_manager.get_project_root", return_value=Path("/fake")):
            result = get_embedded_venv_python()
        assert "venv" in str(result).lower() or ".venv" in str(result)

    def test_get_embedded_venv_python_active_env(self, tmp_path, monkeypatch):
        """_is_active_python_environment_root True 时返回 sys.executable（line 405-406）。"""
        monkeypatch.setattr(
            "vibeocr.backend.env_manager._is_active_python_environment_root",
            lambda project_root: True,
        )
        result = get_embedded_venv_python(tmp_path)
        assert result == Path(em.sys.executable).resolve()

    def test_get_embedded_python_info_none_project_root(self, tmp_path, monkeypatch):
        """project_root=None 走 get_project_root()（line 439-440）。"""
        monkeypatch.setattr("vibeocr.backend.env_manager.get_project_root", lambda: tmp_path)
        info = get_embedded_python_info()
        assert info["mode"] == "none"
        assert info["ready"] is False
        assert "path" in info

    def test_get_embedded_python_info_ready_portable(self, tmp_path):
        """portable + python.exe 存在 → ready=True。"""
        python_exe = tmp_path / "python" / "python.exe"
        python_exe.parent.mkdir(parents=True)
        python_exe.touch()
        info = get_embedded_python_info(tmp_path)
        assert info["mode"] == "portable"
        assert info["ready"] is True

    def test_is_embedded_python_installed_portable(self, tmp_path):
        """portable + python.exe 存在 → True。"""
        python_exe = tmp_path / "python" / "python.exe"
        python_exe.parent.mkdir(parents=True)
        python_exe.touch()
        assert is_embedded_python_installed(tmp_path) is True

    def test_is_embedded_python_installed_none_mode(self, tmp_path):
        """none 模式 → False。"""
        assert is_embedded_python_installed(tmp_path) is False


class TestNonWindowsPaths:
    """覆盖 os.name == 'posix' 的分支（line 370-371）。

    注意：Windows 上 ``Path`` 是 ``WindowsPath``，monkeypatch os.name='posix'
    会让 ``_is_active_python_environment_root`` 内部的 ``Path.resolve()`` 尝试
    构造 ``PosixPath`` 而抛 ``UnsupportedOperation``。故同时 patch 该函数返回
    False，绕过 resolve() 路径。
    """

    def test_get_embedded_python_executable_posix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(em.os, "name", "posix")
        monkeypatch.setattr(
            "vibeocr.backend.env_manager._is_active_python_environment_root",
            lambda project_root: False,
        )
        result = get_embedded_python_executable(tmp_path)
        assert "bin" in str(result)
        assert "python.exe" not in str(result)

    def test_get_embedded_venv_python_posix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(em.os, "name", "posix")
        monkeypatch.setattr(
            "vibeocr.backend.env_manager._is_active_python_environment_root",
            lambda project_root: False,
        )
        result = get_embedded_venv_python(tmp_path)
        assert result == tmp_path / ".venv" / "bin" / "python"

    def test_get_embedded_python_path_posix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(em.os, "name", "posix")
        monkeypatch.setattr(
            "vibeocr.backend.env_manager._is_active_python_environment_root",
            lambda project_root: False,
        )
        # 无 venv → 返回 portable python/
        result = get_embedded_python_path(tmp_path)
        assert result == tmp_path / "python"


# ---------------------------------------------------------------------------
# download_file_with_progress
# ---------------------------------------------------------------------------


class TestDownloadFileWithProgress:
    def _make_cm(self, chunks_iter, status=200, content_length=None):
        """Build an object usable as `with urlopen(...) as resp:`.

        Returns a callable that, when called, returns a context manager whose
        ``read`` pops from ``chunks_iter``.
        """
        import contextlib

        class _Resp:
            def __init__(self):
                self.status = status
                total = content_length
                if total is None:
                    # consume a copy to compute length without draining
                    pass
                self._content_length = total

            def read(self, size=65536):
                try:
                    return next(chunks_iter)
                except StopIteration:
                    return b""

        class _Headers:
            def __init__(self, length):
                self._length = length

            def get(self, key, default=None):
                if key.lower() == "content-length":
                    return self._length if self._length is not None else "0"
                return default

        resp = _Resp()
        # Pre-compute content length from a peek of chunks if not given
        resp.headers = _Headers(content_length)

        @contextlib.contextmanager
        def _cm(*args, **kwargs):
            yield resp

        return _cm

    def test_successful_download_with_progress_logging(
        self, tmp_path, caplog
    ):
        """正常下载 + 进度日志（覆盖 line 495/519/529/532/550-551）。"""
        dest = tmp_path / "out.zip"
        # 构造大数据触发进度日志（每 10% 一条）
        chunk = b"x" * (65536 * 20)  # ~1.3MB
        total_len = len(chunk) * 3
        cm = self._make_cm(
            iter([chunk, chunk, chunk, b""]), content_length=str(total_len)
        )

        with (
            patch("vibeocr.backend.env_manager.urlopen", side_effect=cm),
            caplog.at_level(logging.INFO, logger="vibeocr.backend.env_manager"),
        ):
            ok = download_file_with_progress(
                "http://x", dest, description="测试", max_retries=1
            )

        assert ok is True
        assert dest.exists()
        # 进度日志或下载完成日志应被记录
        assert any(
            "进度" in r.message or "下载完成" in r.message for r in caplog.records
        )

    def test_resume_download_appends(self, tmp_path):
        """断点续传：dest 已有内容时用 Range 头追加（line 494-495）。"""
        dest = tmp_path / "out.zip"
        dest.write_bytes(b"existing")
        # 206 状态：续传成功
        reads = iter([b"data", b""])

        import contextlib

        class _Resp:
            status = 206

            class _H:
                @staticmethod
                def get(key, default=None):
                    return "4" if key.lower() == "content-length" else default

            headers = _H()

            def read(self, size=65536):
                return next(reads)

        resp = _Resp()

        @contextlib.contextmanager
        def cm(*a, **kw):
            yield resp

        with patch("vibeocr.backend.env_manager.urlopen", side_effect=cm):
            ok = download_file_with_progress("http://x", dest, max_retries=1)

        assert ok is True
        # 内容应是 existing + data（追加模式）
        assert dest.read_bytes() == b"existingdata"

    def test_download_failure_returns_false(self, tmp_path):
        """异常时重试 max_retries 次后返回 False（line 545-550）。"""
        dest = tmp_path / "out.zip"
        with patch("vibeocr.backend.env_manager.urlopen", side_effect=OSError("network")):
            ok = download_file_with_progress("http://x", dest, max_retries=2)
        assert ok is False

    def test_resume_unlinks_on_non_resume_response(self, tmp_path):
        """非续传响应（200）且 dest 已有内容时清空重写（line 518-519）。"""
        dest = tmp_path / "out.zip"
        dest.write_bytes(b"old_partial")
        reads = iter([b"new!", b""])

        import contextlib

        class _Resp:
            status = 200  # 非 206 → 整体重写

            class _H:
                @staticmethod
                def get(key, default=None):
                    return "4" if key.lower() == "content-length" else default

            headers = _H()

            def read(self, size=65536):
                return next(reads)

        resp = _Resp()

        @contextlib.contextmanager
        def cm(*a, **kw):
            yield resp

        with patch("vibeocr.backend.env_manager.urlopen", side_effect=cm):
            ok = download_file_with_progress("http://x", dest, max_retries=1)

        assert ok is True
        # 应被 new! 覆盖（而非追加）
        assert dest.read_bytes() == b"new!"


# ---------------------------------------------------------------------------
# download_artifact_multi_source: ImportError fallback (line 603-627)
# ---------------------------------------------------------------------------


class TestDownloadArtifactMultiSourceFallback:
    def test_import_error_fallback_constants(self, tmp_path, monkeypatch):
        """update_service import 失败时回退到本模块常量（line 603-627）。"""
        # 让 update_service import 抛 ImportError
        import sys

        monkeypatch.setitem(
            sys.modules, "vibeocr.classic.services.update_service", None
        )
        dest = tmp_path / "out.zip"

        # download_file_with_progress 成功 → use_sha=False 路径直接成功
        with patch(
            "vibeocr.backend.env_manager.download_file_with_progress", return_value=True
        ):
            ok, reason = download_artifact_multi_source(
                ["https://github.com/x/y/z.tar.gz"], dest
            )

        assert ok is True
        assert reason == "ok"

    def test_import_error_fallback_sha_mismatch(self, tmp_path, monkeypatch):
        """ImportError 回退 + sha 校验失败 → 换源（line 620-627 verify_sha256）。"""
        import sys

        monkeypatch.setitem(
            sys.modules, "vibeocr.classic.services.update_service", None
        )
        dest = tmp_path / "out.zip"
        sha_dest = tmp_path / "out.sha256"

        call = {"n": 0}

        def fake_dl(url, path, **kw):
            call["n"] += 1
            path.write_bytes(b"data")
            return True

        with (
            patch(
                "vibeocr.backend.env_manager.download_file_with_progress",
                side_effect=fake_dl,
            ),
            # sha 文件内容与实际 hash 不匹配
        ):
            # 写一个 sha 文件让 verify 返回 False（实际 hash != 期望）
            sha_dest.write_text("0" * 64, encoding="utf-8")
            ok, reason = download_artifact_multi_source(
                ["https://github.com/x/y/z.zip"],
                dest,
                sha_candidates=["https://github.com/x/y/z.sha256"],
                sha_dest_path=sha_dest,
            )

        # 单源 sha mismatch → 失败
        assert ok is False
        assert reason == "sha_mismatch"

    def test_import_error_fallback_sha_missing_file(self, tmp_path, monkeypatch):
        """ImportError 回退 + sha 文件下载失败 → sha_missing（line 659-666）。"""
        import sys

        monkeypatch.setitem(
            sys.modules, "vibeocr.classic.services.update_service", None
        )
        dest = tmp_path / "out.zip"
        sha_dest = tmp_path / "out.sha256"

        call = {"n": 0}

        def fake_dl(url, path, **kw):
            call["n"] += 1
            if call["n"] == 1:
                # 主文件下载成功
                path.write_bytes(b"data")
                return True
            # sha 文件下载失败 → sha_missing
            return False

        with patch(
            "vibeocr.backend.env_manager.download_file_with_progress", side_effect=fake_dl
        ):
            ok, reason = download_artifact_multi_source(
                ["https://github.com/x/y/z.zip"],
                dest,
                sha_candidates=["https://x/y/z.sha256"],
                sha_dest_path=sha_dest,
            )

        assert ok is False
        assert reason == "sha_missing"

    def test_download_exception_path(self, tmp_path):
        """download_file_with_progress 抛异常 → exception reason（line 678-685）。"""
        dest = tmp_path / "out.zip"
        with patch(
            "vibeocr.backend.env_manager.download_file_with_progress",
            side_effect=RuntimeError("boom"),
        ):
            ok, reason = download_artifact_multi_source(
                ["https://x/y/z"], dest
            )
        assert ok is False
        assert reason == "exception"

    def test_source_switch_fn_called_on_failure(self, tmp_path):
        """失败时 source_switch_fn 被回调（line 649-650/664-665/672-673/684-685）。"""
        dest = tmp_path / "out.zip"
        switches = []

        def switch(label, reason):
            switches.append((label, reason))

        with patch(
            "vibeocr.backend.env_manager.download_file_with_progress", return_value=False
        ):
            download_artifact_multi_source(
                ["https://github.com/a", "https://ghproxy.com/b"],
                dest,
                source_switch_fn=switch,
            )

        assert len(switches) >= 1

    def test_sha_candidates_length_mismatch_raises(self, tmp_path):
        """sha_candidates 长度 ≠ url_candidates → ValueError（line 630-633）。"""
        dest = tmp_path / "out.zip"
        sha_dest = tmp_path / "out.sha256"
        with pytest.raises(ValueError, match="长度必须与"):
            download_artifact_multi_source(
                ["url1", "url2"],
                dest,
                sha_candidates=["sha1"],
                sha_dest_path=sha_dest,
            )


# ---------------------------------------------------------------------------
# _parse_uv_lock / _load_locked_versions
# ---------------------------------------------------------------------------


class TestParseUvLock:
    def test_missing_file_returns_empty(self, tmp_path):
        """uv.lock 不存在 → 空 dict（line 272-273）。"""
        assert _parse_uv_lock(tmp_path / "missing.lock") == {}

    def test_parse_error_returns_empty(self, tmp_path):
        """uv.lock 解析失败 → 空 dict（line 278-279）。"""
        bad = tmp_path / "uv.lock"
        bad.write_text("not valid toml {{{", encoding="utf-8")
        assert _parse_uv_lock(bad) == {}

    def test_parses_packages_and_aliases(self, tmp_path):
        """正常解析 + paddlepaddle-gpu 归一为 paddlepaddle（line 280-287）。"""
        lock = tmp_path / "uv.lock"
        lock.write_text(
            '[[package]]\nname = "paddlepaddle-gpu"\nversion = "3.3.1"\n\n'
            '[[package]]\nname = "torch"\nversion = "2.6.0+cu126"\n\n'
            '[[package]]\nname = ""\nversion = ""\n',
            encoding="utf-8",
        )
        result = _parse_uv_lock(lock)
        assert result["paddlepaddle"] == "3.3.1"
        assert result["torch"] == "2.6.0+cu126"
        # 空 name/version 不进 dict
        assert "" not in result


class TestLoadLockedVersions:
    def test_version_json_corrupt_returns_empty(self, tmp_path, monkeypatch):
        """version.json 解析失败 → 空 dict（line 241-243）。"""
        em._locked_versions_cache = None
        (tmp_path / "version.json").write_text("not json{", encoding="utf-8")
        monkeypatch.setattr(em, "get_project_root", lambda: tmp_path)
        # 无 pyproject.toml → 走 version.json 分支
        assert _load_locked_versions() == {}

    def test_version_json_not_dict_returns_empty(self, tmp_path, monkeypatch):
        """dep_locked_versions 非 dict → 空 dict（line 245）。"""
        em._locked_versions_cache = None
        import json

        (tmp_path / "version.json").write_text(
            json.dumps({"dep_locked_versions": "not-a-dict"}), encoding="utf-8"
        )
        monkeypatch.setattr(em, "get_project_root", lambda: tmp_path)
        assert _load_locked_versions() == {}

    def test_packaged_profile_locked_versions(self, tmp_path, monkeypatch):
        """无 pyproject/version.json → 读 packaged profile（line 249-252）。"""
        em._locked_versions_cache = None
        monkeypatch.setattr(em, "get_project_root", lambda: tmp_path)
        with patch(
            "vibeocr.backend.env_manager._load_packaged_dependency_profiles",
            return_value={"locked_versions": {"torch": "2.6.0"}},
        ):
            result = _load_locked_versions()
        assert result == {"torch": "2.6.0"}

    def test_packaged_profile_not_dict_returns_empty(self, tmp_path, monkeypatch):
        """packaged profile locked_versions 非 dict → 空（line 250 条件不满足）。"""
        em._locked_versions_cache = None
        monkeypatch.setattr(em, "get_project_root", lambda: tmp_path)
        with patch(
            "vibeocr.backend.env_manager._load_packaged_dependency_profiles",
            return_value={"locked_versions": "not-a-dict"},
        ):
            assert _load_locked_versions() == {}


# ---------------------------------------------------------------------------
# check_dependencies / check_current_environment_dependencies / is_production_environment_ready
# ---------------------------------------------------------------------------


class TestCheckCurrentEnvironment:
    def test_returns_dict_with_bools(self):
        """check_current_environment_dependencies 返回 dict[str, bool]。"""
        result = check_current_environment_dependencies()
        assert "PySide6" in result
        assert "PIL" in result
        assert all(isinstance(v, bool) for v in result.values())

    def test_is_production_environment_ready(self):
        """is_production_environment_ready 返回 (bool, list)。"""
        ready, missing = is_production_environment_ready()
        assert isinstance(ready, bool)
        assert isinstance(missing, list)


class TestCheckDependencies:
    def test_python_not_exists_returns_empty(self, tmp_path):
        """python.exe 不存在 → 空 dict（line 1018-1019）。"""
        with patch(
            "vibeocr.backend.env_manager.get_embedded_python_executable",
            return_value=tmp_path / "nope.exe",
        ):
            assert check_dependencies(tmp_path) == {}

    def test_returns_merged_deps(self, tmp_path):
        """返回 OCR + 生产依赖（line 1022-1037）。"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0  # import 成功
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.backend.env_manager.subprocess.run", side_effect=mock_run),
        ):
            deps = check_dependencies(tmp_path)

        assert "paddlepaddle" in deps
        assert "PySide6" in deps
        assert "PIL" in deps

    def test_production_dep_subprocess_exception_returns_false(self, tmp_path):
        """生产依赖 subprocess 异常 → 该依赖 False（line 1036-1037）。"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if "metadata" in code or code.startswith(("import paddle", "import mineru")):
                r.returncode = 0
                r.stderr = ""
                return r
            if code.startswith(("import PySide6", "import PIL")):
                raise OSError("boom")
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.backend.env_manager.subprocess.run", side_effect=mock_run),
        ):
            deps = check_dependencies(tmp_path)

        assert deps["PySide6"] is False
        assert deps["PIL"] is False


# ---------------------------------------------------------------------------
# _is_gpu_requirement / _pkg_in_force_reinstall_set / _dedup_preserve_order
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_is_gpu_requirement_gpu_name(self):
        assert _is_gpu_requirement("PaddlePaddle GPU (cu126)") is True

    def test_is_gpu_requirement_cuda_name(self):
        assert _is_gpu_requirement("PyTorch CUDA (cu126)") is True

    def test_is_gpu_requirement_cpu_name(self):
        assert _is_gpu_requirement("PaddlePaddle CPU") is False

    def test_is_gpu_requirement_plain_pkg(self):
        assert _is_gpu_requirement("PaddleOCR") is False

    def test_pkg_in_force_reinstall_empty_set(self):
        assert _pkg_in_force_reinstall_set("FontTools", set()) is False

    def test_pkg_in_force_reinstall_none(self):
        assert _pkg_in_force_reinstall_set("FontTools", None) is False

    def test_pkg_in_force_reinstall_match(self):
        assert _pkg_in_force_reinstall_set("FontTools", {"fonttools"}) is True

    def test_pkg_in_force_reinstall_substring_match(self):
        """展示名含后缀时子串匹配（line 1670）。"""
        assert _pkg_in_force_reinstall_set(
            "PaddleOCR GPU (cu126)", {"paddleocr"}
        ) is True

    def test_dedup_preserve_order(self):
        assert _dedup_preserve_order(["a", "b", "a", "", "c"]) == ["a", "b", "c"]

    def test_dedup_preserve_order_empty(self):
        assert _dedup_preserve_order([]) == []


# ---------------------------------------------------------------------------
# install_embedded_python: symlink skip, network_type ordering, pip self-check
# ---------------------------------------------------------------------------


class TestInstallEmbeddedPythonBranches:
    @staticmethod
    def _make_tar_with_symlink() -> bytes:
        """构造含符号链接的 tar.gz。"""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for d in ("install_only/python",):
                info = tarfile.TarInfo(name=d)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            exe = b"fake"
            info = tarfile.TarInfo(name="install_only/python/python.exe")
            info.size = len(exe)
            tar.addfile(info, io.BytesIO(exe))
            # 符号链接
            link = tarfile.TarInfo(name="install_only/python/python3")
            link.type = tarfile.SYMTYPE
            link.linkname = "python.exe"
            tar.addfile(link)
        return buf.getvalue()

    def test_skips_symlink_members(self, tmp_path):
        """含符号链接的成员应被跳过（line 802-804）。"""
        with (
            patch("vibeocr.backend.env_manager.get_environment_mode", return_value="none"),
            patch(
                "vibeocr.backend.env_manager.download_file_with_progress", return_value=True
            ) as mock_dl,
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
            patch("vibeocr.backend.env_manager.subprocess.run"),
        ):
            def _fake_dl(url, dest, *a, **kw):
                dest.write_bytes(self._make_tar_with_symlink())
                return True

            mock_dl.side_effect = _fake_dl
            ok, _msg = install_embedded_python(tmp_path)

        assert ok
        # python.exe 应存在，符号链接 python3 应被跳过（不存在）
        assert (tmp_path / "python" / "python.exe").exists()
        assert not (tmp_path / "python" / "python3").exists()

    def test_international_network_orders_github_first(self, tmp_path):
        """international 网络时 GitHub 直链在前（line 745）。"""
        from vibeocr.backend.env_manager import install_embedded_python

        captured_urls = []

        def fake_dl(url, dest, *a, **kw):
            captured_urls.append(url)
            dest.write_bytes(self._make_tar_with_symlink())
            return True

        with (
            patch("vibeocr.backend.env_manager.get_environment_mode", return_value="none"),
            patch(
                "vibeocr.backend.env_manager.download_file_with_progress", side_effect=fake_dl
            ),
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
            patch("vibeocr.backend.env_manager.subprocess.run"),
        ):
            install_embedded_python(tmp_path, network_type="international")

        # GitHub 直链应在前
        assert "github.com" in captured_urls[0]

    def test_pip_self_check_fails_returns_warning(self, tmp_path, caplog):
        """pip 自检 returncode != 0 时记 warning（line 833-837）。"""
        from vibeocr.backend.env_manager import install_embedded_python

        def fake_run(cmd, **kw):
            r = MagicMock()
            if "pip" in " ".join(cmd) and "--version" in cmd:
                r.returncode = 1
                r.stderr = "pip broken"
                r.stdout = ""
            else:
                r.returncode = 0
                r.stderr = ""
                r.stdout = ""
            return r

        with (
            patch("vibeocr.backend.env_manager.get_environment_mode", return_value="none"),
            patch(
                "vibeocr.backend.env_manager.download_file_with_progress", return_value=True
            ) as mock_dl,
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
            patch("vibeocr.backend.env_manager.subprocess.run", side_effect=fake_run),
            caplog.at_level(logging.WARNING, logger="vibeocr.backend.env_manager"),
        ):
            def _fake_dl(url, dest, *a, **kw):
                dest.write_bytes(self._make_tar_with_symlink())
                return True

            mock_dl.side_effect = _fake_dl
            ok, _msg = install_embedded_python(tmp_path)

        assert ok
        assert any("pip 自检失败" in r.message for r in caplog.records)

    def test_pip_self_check_exception_logs_warning(self, tmp_path, caplog):
        """pip 自检 subprocess 异常时记 warning（line 838-839）。"""
        from vibeocr.backend.env_manager import install_embedded_python

        def fake_run(cmd, **kw):
            if "pip" in " ".join(cmd) and "--version" in cmd:
                raise OSError("subprocess crashed")
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            r.stdout = ""
            return r

        with (
            patch("vibeocr.backend.env_manager.get_environment_mode", return_value="none"),
            patch(
                "vibeocr.backend.env_manager.download_file_with_progress", return_value=True
            ) as mock_dl,
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
            patch("vibeocr.backend.env_manager.subprocess.run", side_effect=fake_run),
            caplog.at_level(logging.WARNING, logger="vibeocr.backend.env_manager"),
        ):
            def _fake_dl(url, dest, *a, **kw):
                dest.write_bytes(self._make_tar_with_symlink())
                return True

            mock_dl.side_effect = _fake_dl
            ok, _msg = install_embedded_python(tmp_path)

        assert ok
        assert any("pip 自检异常" in r.message for r in caplog.records)

    def test_extract_fails_returns_false(self, tmp_path):
        """解压抛异常时返回失败并清理（line 806-810）。"""
        from vibeocr.backend.env_manager import install_embedded_python

        with (
            patch("vibeocr.backend.env_manager.get_environment_mode", return_value="none"),
            patch(
                "vibeocr.backend.env_manager.download_file_with_progress", return_value=True
            ) as mock_dl,
            patch("tarfile.open", side_effect=OSError("corrupt tar")),
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
            patch("vibeocr.backend.env_manager.subprocess.run"),
            patch("vibeocr.backend.env_manager.shutil.rmtree"),
        ):
            def _fake_dl(url, dest, *a, **kw):
                dest.write_bytes(b"not a real tar")
                return True

            mock_dl.side_effect = _fake_dl
            ok, msg = install_embedded_python(tmp_path)

        assert ok is False
        assert "解压失败" in msg

    def test_python_exe_missing_after_extract_returns_false(self, tmp_path):
        """解压后 python.exe 不存在 → 失败（line 813-819）。"""
        from vibeocr.backend.env_manager import install_embedded_python

        with (
            patch("vibeocr.backend.env_manager.get_environment_mode", return_value="none"),
            patch(
                "vibeocr.backend.env_manager.download_file_with_progress", return_value=True
            ) as mock_dl,
            # 解压后 get_embedded_python_executable 返回的路径不存在
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "missing.exe",
            ),
            patch("vibeocr.backend.env_manager.subprocess.run"),
            patch("vibeocr.backend.env_manager.shutil.rmtree"),
        ):
            # 用真实 tar（解压成功但不含 missing.exe）
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                info = tarfile.TarInfo(name="install_only/python")
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
                exe = b"x"
                info = tarfile.TarInfo(name="install_only/python/python.exe")
                info.size = 1
                tar.addfile(info, io.BytesIO(exe))

            def _fake_dl(url, dest, *a, **kw):
                dest.write_bytes(buf.getvalue())
                return True

            mock_dl.side_effect = _fake_dl
            ok, msg = install_embedded_python(tmp_path)

        assert ok is False
        assert "解压后未找到" in msg

    def test_venv_mode_incomplete_returns_false(self, tmp_path):
        """venv 模式但 python.exe 不存在 → 失败（line 716-717）。"""
        from vibeocr.backend.env_manager import install_embedded_python

        with (
            patch("vibeocr.backend.env_manager.get_environment_mode", return_value="venv"),
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "missing.exe",
            ),
        ):
            ok, msg = install_embedded_python(tmp_path)
        assert ok is False
        assert "虚拟环境不完整" in msg


class TestReinstallEmbeddedPython:
    def test_reinstall_reports_progress(self, tmp_path):
        """reinstall_embedded_python 调用 progress_callback（line 865-877）。"""
        from vibeocr.backend.env_manager import reinstall_embedded_python

        messages = []
        with patch(
            "vibeocr.backend.env_manager.install_embedded_python",
            return_value=(True, "ok"),
        ) as mock_install:
            ok, _msg = reinstall_embedded_python(
                tmp_path,
                progress_callback=lambda s, m: messages.append((s, m)),
            )

        assert ok is True
        assert mock_install.called
        # 应有清理 + 重装两段进度
        assert len(messages) >= 2


# ---------------------------------------------------------------------------
# switch_paddle_backend branches
# ---------------------------------------------------------------------------


class TestSwitchPaddleBackendBranches:
    def test_invalid_target_returns_false(self, tmp_path):
        """无效 target → 失败（line 3083-3084）。"""
        from vibeocr.backend.env_manager import switch_paddle_backend

        ok, msg = switch_paddle_backend(tmp_path, "tpu")
        assert ok is False
        assert "无效" in msg

    def test_python_missing_returns_false(self, tmp_path):
        """python.exe 不存在 → 失败（line 3087-3088）。"""
        from vibeocr.backend.env_manager import switch_paddle_backend

        with patch(
            "vibeocr.backend.env_manager.get_embedded_python_executable",
            return_value=tmp_path / "nope.exe",
        ):
            ok, msg = switch_paddle_backend(tmp_path, "gpu")
        assert ok is False
        assert "Python 运行时未安装" in msg

    def test_cancel_during_uninstall(self, tmp_path):
        """uninstall 后 cancel_event 被置位 → 取消（line 3126-3127）。"""
        from vibeocr.backend.env_manager import switch_paddle_backend

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        cancel = threading.Event()

        proc = MagicMock()
        proc.wait.return_value = 0

        # cancel 在 on_proc 回调中设置（uninstall 进程启动后）
        def on_proc(p):
            cancel.set()

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.backend.env_manager.subprocess.Popen", return_value=proc),
        ):
            ok, msg = switch_paddle_backend(
                tmp_path, "gpu", cancel_event=cancel, on_proc=on_proc
            )
        assert ok is False
        assert "取消" in msg

    def test_install_failure_returns_false(self, tmp_path):
        """install_embedded_dependencies 失败 → 失败（line 3139-3140）。"""
        from vibeocr.backend.env_manager import switch_paddle_backend

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        proc = MagicMock()
        proc.wait.return_value = 0

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.backend.env_manager.subprocess.Popen", return_value=proc),
            patch(
                "vibeocr.backend.env_manager.install_embedded_dependencies",
                return_value=(False, "install failed"),
            ),
        ):
            ok, msg = switch_paddle_backend(tmp_path, "gpu")
        assert ok is False
        assert "GPU 安装失败" in msg

    def test_uninstall_timeout_kills_proc(self, tmp_path):
        """uninstall 超时 → kill 进程（line 3121-3123）。"""
        from vibeocr.backend.env_manager import switch_paddle_backend

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        proc = MagicMock()
        # 第一次 wait(timeout=300) 超时；kill 后第二次 wait(timeout=10) 正常返回
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="x", timeout=300),
            None,
        ]

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.backend.env_manager.subprocess.Popen", return_value=proc),
            patch(
                "vibeocr.backend.env_manager.install_embedded_dependencies",
                return_value=(True, "ok"),
            ),
            patch("vibeocr.backend.env_manager.update_cache_field", return_value=True),
        ):
            ok, _msg = switch_paddle_backend(tmp_path, "cpu")
        # 应 kill 并继续
        proc.kill.assert_called()
        assert ok is True

    def test_success_cpu_writes_pending_backend(self, tmp_path):
        """成功切换 cpu → 写 pending_backend（line 3146-3150）。"""
        from vibeocr.backend.env_manager import switch_paddle_backend

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        proc = MagicMock()
        proc.wait.return_value = 0

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.backend.env_manager.subprocess.Popen", return_value=proc),
            patch(
                "vibeocr.backend.env_manager.install_embedded_dependencies",
                return_value=(True, "ok"),
            ),
            patch("vibeocr.backend.env_manager.update_cache_field", return_value=True) as mock_update,
        ):
            ok, _msg = switch_paddle_backend(tmp_path, "cpu")
        assert ok is True
        mock_update.assert_called_once_with(tmp_path, "pending_backend", "cpu")

    def test_cache_update_failure_warns(self, tmp_path, caplog):
        """update_cache_field 失败 → warning（line 3146-3147）。"""
        from vibeocr.backend.env_manager import switch_paddle_backend

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        proc = MagicMock()
        proc.wait.return_value = 0

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.backend.env_manager.subprocess.Popen", return_value=proc),
            patch(
                "vibeocr.backend.env_manager.install_embedded_dependencies",
                return_value=(True, "ok"),
            ),
            patch("vibeocr.backend.env_manager.update_cache_field", return_value=False),
            caplog.at_level(logging.INFO, logger="vibeocr.backend.env_manager"),
        ):
            ok, _msg = switch_paddle_backend(tmp_path, "cpu")
        assert ok is True
        assert any("缓存更新失败" in r.message for r in caplog.records)

    def test_generic_exception_returns_false(self, tmp_path):
        """install 抛通用异常 → 失败（line 3154-3155）。"""
        from vibeocr.backend.env_manager import switch_paddle_backend

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.backend.env_manager.subprocess.Popen",
                side_effect=RuntimeError("unexpected"),
            ),
        ):
            ok, msg = switch_paddle_backend(tmp_path, "cpu")
        assert ok is False
        assert "异常" in msg


# ---------------------------------------------------------------------------
# detect_dependency_updates version comparison
# ---------------------------------------------------------------------------


class TestDetectDependencyUpdatesBranches:
    def test_venv_mode_returns_empty(self, tmp_path):
        """venv 模式 → 空 dict（line 2584-2585）。"""
        from vibeocr.backend.env_manager import detect_dependency_updates

        with patch("vibeocr.backend.env_manager.get_environment_mode", return_value="venv"):
            assert detect_dependency_updates(tmp_path) == {}

    def test_python_missing_returns_empty(self, tmp_path):
        """portable 但 python.exe 不存在 → 空（line 2587-2589）。"""
        from vibeocr.backend.env_manager import detect_dependency_updates

        with (
            patch("vibeocr.backend.env_manager.get_environment_mode", return_value="portable"),
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "nope.exe",
            ),
        ):
            assert detect_dependency_updates(tmp_path) == {}

    def test_outdated_version_reported(self, tmp_path):
        """已装 < 锁定版 → 报告更新（line 2643-2644/2651-2652）。"""
        from vibeocr.backend.env_manager import detect_dependency_updates

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        with (
            patch("vibeocr.backend.env_manager.get_environment_mode", return_value="portable"),
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.backend.env_manager._load_dep_specs",
                return_value={"paddleocr": "paddleocr>=3.7.0"},
            ),
            patch(
                "vibeocr.backend.env_manager.get_dependency_versions",
                return_value={"paddleocr": "3.6.0"},  # 旧版
            ),
            patch(
                "vibeocr.backend.env_manager._load_locked_versions",
                return_value={"paddleocr": "3.7.0"},
            ),
        ):
            updates = detect_dependency_updates(tmp_path)
        assert "paddleocr" in updates

    def test_not_installed_reported(self, tmp_path):
        """未安装（空版本串）→ 报告更新（line 2635-2636）。"""
        from vibeocr.backend.env_manager import detect_dependency_updates

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        with (
            patch("vibeocr.backend.env_manager.get_environment_mode", return_value="portable"),
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.backend.env_manager._load_dep_specs",
                return_value={"paddleocr": "paddleocr>=3.7.0"},
            ),
            patch(
                "vibeocr.backend.env_manager.get_dependency_versions",
                return_value={"paddleocr": ""},  # 未安装
            ),
            patch(
                "vibeocr.backend.env_manager._load_locked_versions",
                return_value={"paddleocr": "3.7.0"},
            ),
        ):
            updates = detect_dependency_updates(tmp_path)
        assert "paddleocr" in updates

    def test_up_to_date_not_reported(self, tmp_path):
        """已装 == 锁定版 → 不报告（line 2651 条件不满足）。"""
        from vibeocr.backend.env_manager import detect_dependency_updates

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        with (
            patch("vibeocr.backend.env_manager.get_environment_mode", return_value="portable"),
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.backend.env_manager._load_dep_specs",
                return_value={"paddleocr": "paddleocr>=3.7.0"},
            ),
            patch(
                "vibeocr.backend.env_manager.get_dependency_versions",
                return_value={"paddleocr": "3.7.0"},  # 最新
            ),
            patch(
                "vibeocr.backend.env_manager._load_locked_versions",
                return_value={"paddleocr": "3.7.0"},
            ),
        ):
            updates = detect_dependency_updates(tmp_path)
        assert "paddleocr" not in updates

    def test_no_lower_bound_skipped(self, tmp_path):
        """约束无版本（纯包名）→ 跳过（line 2629-2630）。"""
        from vibeocr.backend.env_manager import detect_dependency_updates

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        with (
            patch("vibeocr.backend.env_manager.get_environment_mode", return_value="portable"),
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.backend.env_manager._load_dep_specs",
                return_value={"paddleocr": "paddleocr"},  # 无版本约束
            ),
            patch(
                "vibeocr.backend.env_manager.get_dependency_versions", return_value={}
            ),
            patch(
                "vibeocr.backend.env_manager._load_locked_versions", return_value={}
            ),
        ):
            updates = detect_dependency_updates(tmp_path)
        assert "paddleocr" not in updates


# ---------------------------------------------------------------------------
# install_backend_dependencies (wheel-installer entrypoint)
# ---------------------------------------------------------------------------


class TestInstallBackendDependencies:
    def test_missing_python_returns_false(self, tmp_path):
        """python_exe 不存在 → 失败（line 2089-2090）。"""
        from vibeocr.backend.env_manager import install_backend_dependencies

        ok, msg = install_backend_dependencies(python_exe=tmp_path / "nope.exe")
        assert ok is False
        assert "不存在" in msg

    def test_cpu_profile_success(self, tmp_path):
        """cpu profile → 调 _install_paddle_stack（line 2092-2111）。"""
        from vibeocr.backend.env_manager import install_backend_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        captured = {}

        def fake_stack(**kw):
            captured.update(kw)
            return True, "ok"

        with (
            patch("vibeocr.backend.env_manager._load_dep_specs", return_value={}),
            patch(
                "vibeocr.backend.env_manager._install_paddle_stack", side_effect=fake_stack
            ),
        ):
            ok, _msg = install_backend_dependencies(python_exe=python_exe, profile="cpu")
        assert ok is True
        assert captured["use_gpu"] is False
        assert captured["cuda_version"] is None

    def test_gpu_profile_success(self, tmp_path):
        """gpu-cu126 profile → use_gpu=True + cu126（line 2092-2093）。"""
        from vibeocr.backend.env_manager import install_backend_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        captured = {}

        def fake_stack(**kw):
            captured.update(kw)
            return True, "ok"

        with (
            patch("vibeocr.backend.env_manager._load_dep_specs", return_value={}),
            patch(
                "vibeocr.backend.env_manager._install_paddle_stack", side_effect=fake_stack
            ),
        ):
            ok, _msg = install_backend_dependencies(
                python_exe=python_exe, profile="gpu-cu126"
            )
        assert ok is True
        assert captured["use_gpu"] is True
        assert captured["cuda_version"] == "cu126"


# ---------------------------------------------------------------------------
# get_workspace_source_paths
# ---------------------------------------------------------------------------


class TestGetWorkspaceSourcePaths:
    def test_dev_mode_returns_four_roots(self):
        """开发态返回四个 source root（line 3196-3207）。"""
        from vibeocr.backend.env_manager import get_workspace_source_paths

        result = get_workspace_source_paths()
        # 开发态应有四个 roots
        assert len(result) == 4

    def test_non_workspace_returns_empty(self, tmp_path, monkeypatch):
        """非工作区 → 空元组（line 3207）。"""
        from vibeocr.backend.env_manager import get_workspace_source_paths

        monkeypatch.setattr(
            "vibeocr.backend.env_manager.get_project_root", lambda: tmp_path
        )
        assert get_workspace_source_paths() == ()


# ---------------------------------------------------------------------------
# CUDA_VERSION_MAP sanity (documented mapping)
# ---------------------------------------------------------------------------


class TestCudaVersionMap:
    def test_12_x_all_map_to_cu126(self):
        """所有 12.x 全部归并到 cu126。"""
        for v in ("12.0", "12.1", "12.6", "12.9"):
            assert CUDA_VERSION_MAP[v] == "cu126"

    def test_13_x_map_to_cu126(self):
        for v in ("13.0", "13.1", "13.2"):
            assert CUDA_VERSION_MAP[v] == "cu126"

    def test_11_8_maps_to_cu118(self):
        assert CUDA_VERSION_MAP["11.8"] == "cu118"


# ---------------------------------------------------------------------------
# uninstall_removed_deps: cancel + timeout + generic-exception + refresh-fail
# ---------------------------------------------------------------------------


class TestUninstallRemovedDepsBranches:
    def test_cancel_event_aborts(self, tmp_path):
        """cancel_event 在循环中被检测 → 取消（line 2704-2706）。"""
        from vibeocr.backend.env_manager import uninstall_removed_deps

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        cancel = threading.Event()
        cancel.set()

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
        ):
            ok, msg = uninstall_removed_deps(
                tmp_path, ["mineru"], cancel_event=cancel
            )
        assert ok is False
        assert "取消" in msg

    def test_uninstall_timeout_recorded_as_failed(self, tmp_path):
        """pip uninstall 超时 → 记入 failed（line 2727-2729）。"""
        from vibeocr.backend.env_manager import uninstall_removed_deps

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            if "uninstall" in " ".join(cmd):
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)
            r.returncode = 0
            r.stderr = ""
            r.stdout = ""
            return r

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.backend.env_manager._check_imports", return_value={}
            ),
            patch("vibeocr.backend.env_manager.update_cache_field"),
            patch(
                "vibeocr.backend.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
        ):
            ok, msg = uninstall_removed_deps(tmp_path, ["mineru"])
        assert ok is False
        assert "超时" in msg or "失败" in msg

    def test_uninstall_generic_exception_recorded(self, tmp_path):
        """pip uninstall 抛通用异常 → 记入 failed（line 2731-2733）。"""
        from vibeocr.backend.env_manager import uninstall_removed_deps

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            if "uninstall" in " ".join(cmd):
                raise RuntimeError("unexpected")
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            r.stdout = ""
            return r

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.backend.env_manager._check_imports", return_value={}
            ),
            patch("vibeocr.backend.env_manager.update_cache_field"),
            patch(
                "vibeocr.backend.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
        ):
            ok, msg = uninstall_removed_deps(tmp_path, ["mineru"])
        assert ok is False
        assert "失败" in msg

    def test_refresh_cache_failure_does_not_fail(self, tmp_path):
        """卸载成功后刷新缓存失败不影响整体成功（line 2737-2742）。"""
        from vibeocr.backend.env_manager import uninstall_removed_deps

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = "Successfully uninstalled"
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.backend.env_manager._check_imports",
                side_effect=RuntimeError("cache fail"),
            ),
            patch("vibeocr.backend.env_manager.update_cache_field"),
            patch(
                "vibeocr.backend.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
        ):
            ok, _msg = uninstall_removed_deps(tmp_path, ["mineru"])
        # 卸载本身成功，刷新缓存失败只记 warning
        assert ok is True


# ---------------------------------------------------------------------------
# _run_pip helper for the uninstall tests (mirrors test_env_manager_install)
# ---------------------------------------------------------------------------


def _popen_side_effect(mock_run):
    """Bridge a mock_run(cmd, **kw) -> result into a Popen factory."""

    def factory(cmd, **kw):
        result = mock_run(cmd, **kw)
        proc = MagicMock()
        proc.returncode = result.returncode
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        proc.communicate.return_value = (stdout, stderr)
        proc.poll.side_effect = [None, result.returncode]
        return proc

    return factory


# ---------------------------------------------------------------------------
# detect_gpu_info: exception branch
# ---------------------------------------------------------------------------


class TestDetectGpuInfoException:
    def test_parse_exception_falls_back_to_detect_gpu(self, monkeypatch):
        """解析异常时回退到 detect_gpu（line 2953-2962）。"""
        import vibeocr.backend.env_manager as em

        # _run_pip 抛通用异常（非 InstallCancelled）
        def boom(*a, **kw):
            raise RuntimeError("parse fail")

        monkeypatch.setattr(em, "_run_pip", boom)
        monkeypatch.setattr(em, "detect_gpu", lambda *a, **kw: (True, "cu126"))
        info = em.detect_gpu_info()
        # 异常分支回退到 detect_gpu
        assert info["has_gpu"] is True
        assert info["cuda"] == "cu126"


# ---------------------------------------------------------------------------
# _probe_module: import-layer subprocess exception
# ---------------------------------------------------------------------------


class TestProbeModuleSubprocessException:
    def test_import_subprocess_exception(self, tmp_path):
        """metadata 成功但 import subprocess 抛异常 → usable=False（line 1149-1151）。"""
        from vibeocr.backend.env_manager import _probe_module

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if "metadata" in code:
                r.returncode = 0  # 发行版存在
                r.stdout = "1.0.0"
                r.stderr = ""
                return r
            # import 子进程抛异常
            raise OSError("subprocess crashed")

        with patch("vibeocr.backend.env_manager.subprocess.run", side_effect=mock_run):
            installed, usable, _missing = _probe_module(
                python_exe, "mineru", "mineru"
            )
        assert installed is True
        assert usable is False


# ---------------------------------------------------------------------------
# get_direct_dependencies: packaging ImportError fallback
# ---------------------------------------------------------------------------


class TestGetDirectDependenciesPackagingFallback:
    def test_packaging_import_error_falls_back(self, tmp_path, monkeypatch):
        """packaging 不可用时退化为纯名解析（line 2379-2383）。"""
        from vibeocr.backend.env_manager import get_direct_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = '["numpy>=1.0", "scipy"]'
            return r

        # 让 packaging.requirements import 失败
        import sys

        monkeypatch.setitem(sys.modules, "packaging.requirements", None)
        with patch("vibeocr.backend.env_manager.subprocess.run", side_effect=mock_run):
            deps = get_direct_dependencies(python_exe, "mineru")
        # 退化为纯名解析（无 marker 过滤）
        assert "numpy" in deps
        assert "scipy" in deps

    def test_subprocess_returns_empty_on_nonzero(self, tmp_path):
        """returncode != 0 → 空 list（line 2368-2369）。"""
        from vibeocr.backend.env_manager import get_direct_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            return r

        with patch("vibeocr.backend.env_manager.subprocess.run", side_effect=mock_run):
            deps = get_direct_dependencies(python_exe, "mineru")
        assert deps == []

    def test_subprocess_exception_returns_empty(self, tmp_path):
        """subprocess 抛异常 → 空 list（line 2373-2374）。"""
        from vibeocr.backend.env_manager import get_direct_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        with patch(
            "vibeocr.backend.env_manager.subprocess.run", side_effect=OSError("boom")
        ):
            deps = get_direct_dependencies(python_exe, "mineru")
        assert deps == []

    def test_invalid_requirement_skipped(self, tmp_path):
        """无效 requirement 串被跳过（line 2389-2390）。"""
        import json

        from vibeocr.backend.env_manager import get_direct_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stdout = json.dumps(["!!!invalid", "numpy"])
            return r

        with patch("vibeocr.backend.env_manager.subprocess.run", side_effect=mock_run):
            deps = get_direct_dependencies(python_exe, "mineru")
        # 无效串跳过，保留 numpy
        assert "numpy" in deps
        assert "!!!invalid" not in deps


# ---------------------------------------------------------------------------
# install_missing_dependencies: all-already-installed path
# ---------------------------------------------------------------------------


class TestInstallMissingAllInstalled:
    def test_all_installed_refreshes_cache(self, tmp_path):
        """全部已装时刷新缓存并返回成功（line 2287-2296）。"""
        from vibeocr.backend.env_manager import install_missing_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        from vibeocr.backend.services.env_config import (
            OCR_CHECK_LEAF_MODULES,
            OCR_CHECK_MODULES,
        )

        detailed = {}
        for _mod, pkg in OCR_CHECK_MODULES.items():
            detailed[pkg] = (True, True)
        for _mod, pkg in OCR_CHECK_LEAF_MODULES.items():
            detailed[pkg] = (True, True)

        with (
            patch(
                "vibeocr.backend.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.backend.env_manager._check_imports_detailed",
                return_value=detailed,
            ),
            patch("vibeocr.backend.env_manager.update_cache_field") as mock_update,
            patch("vibeocr.backend.env_manager.detect_gpu", return_value=(False, None)),
        ):
            ok, msg = install_missing_dependencies(tmp_path)

        assert ok is True
        assert "已安装" in msg
        mock_update.assert_called_once()


# ---------------------------------------------------------------------------
# check_embedded_environment_dependencies: cache revalidation
# ---------------------------------------------------------------------------


class TestCheckEmbeddedDepsCacheRevalidation:
    def test_cache_valid_with_deps_uses_cache(self, tmp_path):
        """有效缓存含 dependencies → 直接返回（line 931-975 简化路径）。"""
        from vibeocr.backend.env_manager import check_embedded_environment_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        cached_deps = {
            "paddlepaddle": True,
            "torch": False,  # False 项会复核
        }

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.backend.env_manager.is_cache_valid",
                return_value=(True, {"dependencies": dict(cached_deps)}),
            ),
            patch(
                "vibeocr.backend.runtime_state.get_cache_age_seconds", return_value=100
            ),
            # 复核 False 项
            patch(
                "vibeocr.backend.env_manager._quick_verify_deps",
                return_value={"paddlepaddle": True, "torch": False},
            ),
            patch("vibeocr.backend.env_manager.detect_gpu", return_value=(False, None)),
            patch("vibeocr.backend.env_manager.create_cache_entry"),
        ):
            deps = check_embedded_environment_dependencies(tmp_path)

        # torch 复核后仍 False
        assert deps.get("torch") is False

    def test_cache_empty_deps_falls_to_live_check(self, tmp_path):
        """缓存有效但 dependencies 为空 → 实时检测（line 976-996）。"""
        from vibeocr.backend.env_manager import check_embedded_environment_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        live_deps = {"paddlepaddle": True, "torch": True}

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.backend.env_manager.is_cache_valid",
                return_value=(True, {"dependencies": {}}),  # 空 dependencies
            ),
            patch("vibeocr.backend.env_manager._check_imports", return_value=live_deps),
            patch("vibeocr.backend.env_manager.detect_gpu", return_value=(False, None)),
            patch("vibeocr.backend.env_manager.create_cache_entry"),
        ):
            deps = check_embedded_environment_dependencies(tmp_path)

        assert deps == live_deps

    def test_python_missing_returns_empty(self, tmp_path):
        """python.exe 不存在 → 空 dict（line 981-982）。"""
        from vibeocr.backend.env_manager import check_embedded_environment_dependencies

        with patch(
            "vibeocr.backend.env_manager.get_embedded_python_executable",
            return_value=tmp_path / "nope.exe",
        ), patch("vibeocr.backend.env_manager.is_cache_valid", return_value=(False, None)):
            deps = check_embedded_environment_dependencies(tmp_path)
        assert deps == {}

    def test_ttl_expired_rechecks_true_items(self, tmp_path):
        """缓存超 TTL 时复核 True 项（line 944-949）。"""
        from vibeocr.backend.env_manager import check_embedded_environment_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        cached_deps = {"paddlepaddle": True, "torch": True}

        with (
            patch(
                "vibeocr.backend.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.backend.env_manager.is_cache_valid",
                return_value=(True, {"dependencies": dict(cached_deps)}),
            ),
            # TTL 已过
            patch(
                "vibeocr.backend.runtime_state.get_cache_age_seconds", return_value=9999999
            ),
            # 复核发现 torch 实际缺失
            patch(
                "vibeocr.backend.env_manager._quick_verify_deps",
                return_value={"paddlepaddle": True, "torch": False},
            ),
            patch("vibeocr.backend.env_manager.detect_gpu", return_value=(False, None)),
            patch("vibeocr.backend.env_manager.create_cache_entry") as mock_create,
        ):
            deps = check_embedded_environment_dependencies(tmp_path)

        # torch 复核后变 False，触发缓存重建
        assert deps.get("torch") is False
        mock_create.assert_called_once()


# ---------------------------------------------------------------------------
# get_dependency_versions: metadata failure + dunder fallback
# ---------------------------------------------------------------------------


class TestGetDependencyVersionsFallback:
    def test_metadata_subprocess_exception(self, tmp_path):
        """metadata 子进程异常 → 走 dunder 回退（line 1444-1445）。"""
        from vibeocr.backend.env_manager import get_dependency_versions

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        call = {"n": 0}

        def mock_run(cmd, **kw):
            call["n"] += 1
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if "metadata" in code:
                raise OSError("subprocess crashed")
            # dunder 回退
            r.returncode = 0
            r.stdout = "1.2.3"
            return r

        with patch("vibeocr.backend.env_manager.subprocess.run", side_effect=mock_run):
            versions = get_dependency_versions(python_exe)

        # 应至少有一个版本来自 dunder 回退
        assert any(v for v in versions.values())

    def test_dunder_subprocess_exception(self, tmp_path):
        """dunder 回退也异常 → 空串（line 1462-1463）。"""
        from vibeocr.backend.env_manager import get_dependency_versions

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            raise OSError("always crashes")

        with patch("vibeocr.backend.env_manager.subprocess.run", side_effect=mock_run):
            versions = get_dependency_versions(python_exe)

        # 全部异常 → 全空串
        assert all(v == "" for v in versions.values())


# ---------------------------------------------------------------------------
# _install_paddle_stack: cancel mid-loop + GPU retry failure
# ---------------------------------------------------------------------------


class TestInstallPaddleStackCancelAndGpuRetry:
    def test_cancel_between_packages(self, tmp_path):
        """requirements_override 模式下包之间取消（line 1817-1819）。"""
        from vibeocr.backend.env_manager import _install_paddle_stack

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        cancel = threading.Event()
        cancel.set()  # 进入循环前已取消

        with patch("vibeocr.backend.env_manager.subprocess.Popen") as mock_popen:
            ok, msg = _install_paddle_stack(
                python_exe=python_exe,
                specs={"paddleocr": "paddleocr"},
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
                requirements_override=[("PaddleOCR", "paddleocr", "https://x")],
                cancel_event=cancel,
            )
        assert ok is False
        assert "取消" in msg
        # 不应启动任何子进程
        mock_popen.assert_not_called()

    def test_gpu_package_retry_exhausted_records_failure(self, tmp_path):
        """GPU 包重试耗尽 → 记入 failed 继续（line 1877-1927）。"""
        from vibeocr.backend.env_manager import _install_paddle_stack

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            # pip upgrade 成功
            if "upgrade" in " ".join(cmd):
                r.returncode = 0
                r.stderr = ""
                return r
            # GPU 包安装永远失败
            r.returncode = 1
            r.stderr = "Could not find a version"
            r.stdout = ""
            return r

        with patch(
            "vibeocr.backend.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
            ok, msg = _install_paddle_stack(
                python_exe=python_exe,
                specs={"paddlepaddle-gpu": "paddlepaddle-gpu>=3.3.1"},
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=True,
                cuda_version="cu126",
                report_fn=lambda s, m: None,
                success_msg="done",
                # 只装 GPU 包
                requirements_override=[
                    ("PaddlePaddle GPU (cu126)", "paddlepaddle-gpu>=3.3.1", "https://x")
                ],
                skip_pip_upgrade=True,
            )
        assert ok is False
        assert "失败" in msg

    def test_version_not_found_falls_back_to_pypi(self, tmp_path):
        """非 GPU 包 + 版本未找到 → 回退 PyPI（line 1928-1962）。"""
        from vibeocr.backend.env_manager import _install_paddle_stack

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        call = {"n": 0}

        def mock_run(cmd, **kw):
            call["n"] += 1
            r = MagicMock()
            # 第一次（镜像源）失败 - 版本未找到
            if call["n"] == 1 and "-i" in cmd:
                r.returncode = 1
                r.stderr = "Could not find a version"
                r.stdout = ""
                return r
            # 第二次（PyPI 无 -i）成功
            r.returncode = 0
            r.stderr = ""
            return r

        with patch(
            "vibeocr.backend.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
            ok, _msg = _install_paddle_stack(
                python_exe=python_exe,
                specs={"paddleocr": "paddleocr"},
                pip_source="https://mirror/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
                requirements_override=[
                    ("PaddleOCR", "paddleocr", "https://mirror/simple")
                ],
                skip_pip_upgrade=True,
            )
        assert ok is True

    def test_pypi_fallback_also_fails(self, tmp_path):
        """非 GPU 包镜像 + PyPI 都失败 → 记入 failed（line 1951-1962）。"""
        from vibeocr.backend.env_manager import _install_paddle_stack

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1
            r.stderr = "Could not find a version"
            r.stdout = ""
            return r

        with patch(
            "vibeocr.backend.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
            ok, msg = _install_paddle_stack(
                python_exe=python_exe,
                specs={"paddleocr": "paddleocr"},
                pip_source="https://mirror/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
                requirements_override=[
                    ("PaddleOCR", "paddleocr", "https://mirror/simple")
                ],
                skip_pip_upgrade=True,
            )
        assert ok is False
        assert "失败" in msg

    def test_non_gpu_non_version_error_fails_directly(self, tmp_path):
        """非 GPU 且非版本问题（网络中断）→ 直接失败（line 1963-1973）。"""
        from vibeocr.backend.env_manager import _install_paddle_stack

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1
            r.stderr = "Network unreachable"  # 非 "Could not find a version"
            r.stdout = ""
            return r

        with patch(
            "vibeocr.backend.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
            ok, _msg = _install_paddle_stack(
                python_exe=python_exe,
                specs={"paddleocr": "paddleocr"},
                pip_source="https://mirror/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
                requirements_override=[
                    ("PaddleOCR", "paddleocr", "https://mirror/simple")
                ],
                skip_pip_upgrade=True,
            )
        assert ok is False

    def test_install_writes_cache_on_success(self, tmp_path):
        """成功后刷新缓存（line 1992-1999）。"""
        from vibeocr.backend.env_manager import _install_paddle_stack

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.backend.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
            patch("vibeocr.backend.env_manager._quick_verify_deps", return_value={"x": True}),
            patch("vibeocr.backend.env_manager.update_cache_field") as mock_update,
        ):
            ok, _msg = _install_paddle_stack(
                python_exe=python_exe,
                specs={"paddleocr": "paddleocr"},
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
                requirements_override=[
                    ("PaddleOCR", "paddleocr", "https://pypi.org/simple")
                ],
                skip_pip_upgrade=True,
                project_root=tmp_path,
            )
        assert ok is True
        mock_update.assert_called_once_with(tmp_path, "dependencies", {"x": True})

    def test_cache_refresh_failure_warns(self, tmp_path, caplog):
        """成功后刷新缓存失败 → warning（line 1997-1998）。"""
        from vibeocr.backend.env_manager import _install_paddle_stack

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.backend.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
            patch(
                "vibeocr.backend.env_manager._quick_verify_deps",
                side_effect=RuntimeError("cache fail"),
            ),
            caplog.at_level(logging.WARNING, logger="vibeocr.backend.env_manager"),
        ):
            ok, _msg = _install_paddle_stack(
                python_exe=python_exe,
                specs={"paddleocr": "paddleocr"},
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
                requirements_override=[
                    ("PaddleOCR", "paddleocr", "https://pypi.org/simple")
                ],
                skip_pip_upgrade=True,
                project_root=tmp_path,
            )
        assert ok is True
        assert any("刷新依赖缓存失败" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _run_pip: timeout + generic exception + cancel during run
# ---------------------------------------------------------------------------


class TestRunPipBranches:
    def test_cancel_event_pre_set_raises(self):
        """cancel_event 进入前已 set → InstallCancelled（line 1574-1575）。"""
        import threading

        from vibeocr.backend.env_manager import InstallCancelled, _run_pip

        cancel = threading.Event()
        cancel.set()
        with pytest.raises(InstallCancelled, match="已取消命令"):
            _run_pip(["echo", "x"], cancel_event=cancel)

    def test_timeout_raises_timeout_expired(self):
        """超时 → TimeoutExpired（line 1626-1630）。"""
        from unittest.mock import MagicMock, patch

        from vibeocr.backend.env_manager import _run_pip

        proc = MagicMock()
        proc.poll.return_value = None  # 永不退出
        proc.communicate.return_value = ("", "")
        with (
            patch("vibeocr.backend.env_manager.subprocess.Popen", return_value=proc),
            patch("vibeocr.backend.env_manager.subprocess.CREATE_NO_WINDOW", 0),
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                _run_pip(["echo", "x"], timeout=1)

    def test_communicate_exception_kills_proc(self):
        """communicate 抛异常 → 兜底 kill（line 1643-1647）。

        _run_pip 的轮询循环里：poll() 返回非 None（进程已退出）→ join communicate
        线程 → 该线程把 communicate 的异常透传给主线程 → 主线程 raise comm_exc。
        兜底 except 把子进程 kill。
        """
        from unittest.mock import MagicMock, patch

        from vibeocr.backend.env_manager import _run_pip

        proc = MagicMock()
        # poll 第一次返回 None（运行中），第二次返回 0（已退出）→ 跳出循环
        proc.poll.side_effect = [None, 0]
        proc.returncode = 0

        def boom():
            raise RuntimeError("comm fail")

        proc.communicate.side_effect = boom
        with (
            patch("vibeocr.backend.env_manager.subprocess.Popen", return_value=proc),
            patch("vibeocr.backend.env_manager.subprocess.CREATE_NO_WINDOW", 0),
        ):
            with pytest.raises(RuntimeError, match="comm fail"):
                _run_pip(["echo", "x"], timeout=5)
        proc.kill.assert_called()


# ---------------------------------------------------------------------------
# reinstall_embedded_python: progress_callback path
# ---------------------------------------------------------------------------


class TestReinstallEmbeddedPythonProgress:
    def test_progress_callback_invoked(self, tmp_path):
        """progress_callback 在清理阶段被调用（line 865-877）。"""
        from vibeocr.backend.env_manager import reinstall_embedded_python

        messages: list[tuple[str, str]] = []
        with patch(
            "vibeocr.backend.env_manager.install_embedded_python",
            return_value=(True, "ok"),
        ):
            ok, _msg = reinstall_embedded_python(
                tmp_path,
                network_type="domestic",
                progress_callback=lambda s, m: messages.append((s, m)),
            )
        assert ok is True
        assert len(messages) >= 2
        assert any("清理" in m for _s, m in messages)
