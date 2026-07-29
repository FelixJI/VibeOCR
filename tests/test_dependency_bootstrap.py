"""Online backend profile installation regression tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from vibeocr.backend import dependency_bootstrap, env_manager


def test_packaged_profile_is_available_without_repository(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(env_manager, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(env_manager, "_dep_specs_cache", None)
    specs = env_manager._load_dep_specs()
    assert specs["paddlepaddle-gpu"] == "paddlepaddle-gpu>=3.3.1"
    assert specs["paddleocr"] == "paddleocr[doc-parser]>=3.7.0"
    assert specs["mineru"] == "mineru[core]>=3.4.3"


def test_meta_version_json_keeps_packaged_heavy_specs(monkeypatch, tmp_path) -> None:
    (tmp_path / "version.json").write_text(
        json.dumps({"dep_versions": {"vibeocr-backend": "==0.4.37"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(env_manager, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(env_manager, "_dep_specs_cache", None)
    specs = env_manager._load_dep_specs()
    assert specs["vibeocr-backend"] == "vibeocr-backend==0.4.37"
    assert specs["paddleocr"] == "paddleocr[doc-parser]>=3.7.0"
    assert specs["paddlepaddle-gpu"] == "paddlepaddle-gpu>=3.3.1"


def test_gpu_profile_reuses_installer_with_explicit_cu126(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        env_manager,
        "_load_dep_specs",
        lambda: {"paddlepaddle-gpu": "paddlepaddle-gpu>=3.3.1"},
    )
    monkeypatch.setattr(env_manager, "get_pip_source", lambda _network: "pypi")

    def fake_install(**kwargs):
        captured.update(kwargs)
        return True, "ok"

    monkeypatch.setattr(env_manager, "_install_paddle_stack", fake_install)
    success, _message = env_manager.install_backend_dependencies(
        Path(sys.executable),
        profile="gpu-cu126",
        network_type="international",
    )
    assert success
    assert captured["python_exe"] == Path(sys.executable).resolve()
    assert captured["use_gpu"] is True
    assert captured["cuda_version"] == "cu126"
    assert captured["network_type"] == "international"


def test_cli_auto_selects_gpu_profile(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(dependency_bootstrap, "detect_gpu", lambda: (True, "cu126"))

    def fake_install(python_exe, **kwargs):
        captured["python_exe"] = python_exe
        captured.update(kwargs)
        return True, "ok"

    monkeypatch.setattr(
        dependency_bootstrap,
        "install_backend_dependencies",
        fake_install,
    )
    assert dependency_bootstrap.main(["--profile", "auto"]) == 0
    assert captured["profile"] == "gpu-cu126"


def test_resolve_profile_explicit_cpu(monkeypatch) -> None:
    """_resolve_profile 对非 auto 请求直接返回（line 14）。"""
    # detect_gpu 不应被调用
    def _fail_detect():
        raise AssertionError("detect_gpu should not be called for explicit profile")

    monkeypatch.setattr(dependency_bootstrap, "detect_gpu", _fail_detect)
    assert dependency_bootstrap._resolve_profile("cpu") == "cpu"
    assert dependency_bootstrap._resolve_profile("gpu-cu126") == "gpu-cu126"


def test_resolve_profile_auto_uses_detect_gpu(monkeypatch) -> None:
    monkeypatch.setattr(dependency_bootstrap, "detect_gpu", lambda: (False, None))
    assert dependency_bootstrap._resolve_profile("auto") == "cpu"

    monkeypatch.setattr(dependency_bootstrap, "detect_gpu", lambda: (True, "cu126"))
    assert dependency_bootstrap._resolve_profile("auto") == "gpu-cu126"


def test_cli_invokes_progress_callback(monkeypatch, capsys) -> None:
    """main 的 report callback 被触发并打印 stage 消息（line 42-43）。"""
    monkeypatch.setattr(dependency_bootstrap, "detect_gpu", lambda: (False, None))

    def fake_install(python_exe, profile, network_type, progress_callback):
        # 模拟安装器调用 progress callback
        progress_callback("download", "fetching paddle")
        progress_callback("install", "installing")
        return True, "done"

    monkeypatch.setattr(
        dependency_bootstrap, "install_backend_dependencies", fake_install
    )
    rc = dependency_bootstrap.main(["--profile", "cpu"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "[download]" in captured.out
    assert "[install]" in captured.out
    assert "done" in captured.out


def test_cli_failure_returns_nonzero(monkeypatch) -> None:
    """安装失败时返回 1（line 51-52）。"""
    monkeypatch.setattr(dependency_bootstrap, "detect_gpu", lambda: (False, None))

    def fake_install(python_exe, profile, network_type, progress_callback):
        return False, "install failed"

    monkeypatch.setattr(
        dependency_bootstrap, "install_backend_dependencies", fake_install
    )
    rc = dependency_bootstrap.main(["--profile", "cpu"])
    assert rc == 1
