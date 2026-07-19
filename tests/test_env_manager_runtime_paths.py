"""Runtime-root behavior for source, frozen and normal wheel installations."""

from __future__ import annotations

import sys

from vibeocr import env_manager


def test_wheel_inside_repo_uses_its_active_environment(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "packages/vibeocr-client-py").mkdir(parents=True)
    (repo / "apps/vibeocr-pyside").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    prefix = repo / ".review-venv"
    executable = prefix / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    installed_module = prefix / "Lib/site-packages/vibeocr/env_manager.py"
    monkeypatch.setattr(env_manager, "__file__", str(installed_module))
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert env_manager.get_project_root() == prefix.resolve()
    assert env_manager.get_environment_mode(prefix) == "venv"
    assert env_manager.get_embedded_python_executable(prefix) == executable.resolve()
    assert env_manager.get_embedded_python_path(prefix) == executable.resolve().parent
    assert env_manager.get_embedded_venv_python(prefix) == executable.resolve()


def test_source_checkout_still_resolves_workspace_root() -> None:
    root = env_manager.get_project_root()
    assert (root / "packages/vibeocr-client-py/src/vibeocr").is_dir()
    assert (root / "apps/vibeocr-pyside/src/vibeocr").is_dir()
