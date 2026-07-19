"""Online backend profile installation regression tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from vibeocr import dependency_bootstrap, env_manager


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
