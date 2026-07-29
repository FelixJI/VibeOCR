"""Physical workspace package ownership and dependency topology gates."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = {
    "vibeocr-runtime-contracts": ROOT / "packages/vibeocr-contracts-py/src",
    "vibeocr-runtime-client": ROOT / "packages/vibeocr-runtime-client-py/src",
    "vibeocr-backend": ROOT / "packages/vibeocr-backend/src",
    "vibeocr-classic": ROOT / "apps/vibeocr-pyside/src",
}
NAMESPACE_ROOTS = {
    "vibeocr-runtime-contracts": "vibeocr/runtime_contracts",
    "vibeocr-runtime-client": "vibeocr/runtime_client",
    "vibeocr-backend": "vibeocr/backend",
    "vibeocr-classic": "vibeocr/classic",
}


def _project(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))["project"]


def test_root_is_workspace_only_and_not_a_meta_wheel() -> None:
    root = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "project" not in root
    assert "build-system" not in root
    assert root["tool"]["uv"]["package"] is False
    assert not (ROOT / "packages/vibeocr-client-py").exists()


def test_each_wheel_archive_path_has_one_owner() -> None:
    owners: dict[str, str] = {}
    for distribution, source_root in SOURCE_ROOTS.items():
        namespace_root = source_root / NAMESPACE_ROOTS[distribution]
        assert namespace_root.is_dir()
        assert not (source_root / "vibeocr/__init__.py").exists()
        for path in namespace_root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            archive_path = path.relative_to(source_root).as_posix()
            assert archive_path not in owners, (
                f"{archive_path} is owned by both {owners.get(archive_path)} "
                f"and {distribution}"
            )
            owners[archive_path] = distribution


def test_ci_builds_and_smokes_all_four_workspace_wheels() -> None:
    action = (ROOT / ".github/actions/build-workspace-wheels/action.yml").read_text(
        encoding="utf-8"
    )
    for project in (
        "packages/vibeocr-contracts-py",
        "packages/vibeocr-runtime-client-py",
        "packages/vibeocr-backend",
        "apps/vibeocr-pyside",
    ):
        assert f"python -m build --wheel {project}" in action
    assert "packages/vibeocr-client-py" not in action
    assert "python -m build --wheel ." not in action
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Count -ne 4" in ci
    for namespace in NAMESPACE_ROOTS.values():
        assert namespace.replace("/", ".") in ci


def test_dependency_profile_matches_backend_extras() -> None:
    profile_path = (
        SOURCE_ROOTS["vibeocr-backend"]
        / "vibeocr/backend/dependency_profiles.json"
    )
    profiles = json.loads(profile_path.read_text(encoding="utf-8"))
    backend = _project(ROOT / "packages/vibeocr-backend/pyproject.toml")
    optional = backend["optional-dependencies"]
    flattened = "\n".join(optional["cpu"] + optional["gpu-cu126"])
    for name in ("paddleocr", "mineru"):
        assert profiles["dependencies"][name] in flattened
    assert profiles["dependencies"]["paddlepaddle-gpu"] in flattened


def test_internal_distribution_dependencies_are_exact_and_directional() -> None:
    contracts = _project(ROOT / "packages/vibeocr-contracts-py/pyproject.toml")
    runtime = _project(ROOT / "packages/vibeocr-runtime-client-py/pyproject.toml")
    backend = _project(ROOT / "packages/vibeocr-backend/pyproject.toml")
    classic = _project(ROOT / "apps/vibeocr-pyside/pyproject.toml")
    assert contracts["dependencies"] == []
    assert "vibeocr-runtime-contracts==2.0.0" in runtime["dependencies"]
    assert "vibeocr-runtime-contracts==2.0.0" in backend["dependencies"]
    assert "vibeocr-runtime-client==2.0.0" in classic["dependencies"]
    assert "vibeocr-backend==0.7.0" in classic["dependencies"]
