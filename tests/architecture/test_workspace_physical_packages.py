"""Physical workspace package ownership and dependency topology gates."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = {
    "vibeocr-contracts-py": ROOT / "packages/vibeocr-contracts-py/src",
    "vibeocr-client-py": ROOT / "packages/vibeocr-client-py/src",
    "vibeocr-backend": ROOT / "packages/vibeocr-backend/src",
    "vibeocr-pyside": ROOT / "apps/vibeocr-pyside/src",
}


def _project(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))["project"]


def test_root_is_code_free_compatibility_meta_package() -> None:
    product_files = list((ROOT / "src/vibeocr").glob("*.py"))
    assert not product_files
    root = _project(ROOT / "pyproject.toml")
    version = root["version"]
    assert root["dependencies"] == [
        f"vibeocr-contracts-py=={version}",
        f"vibeocr-client-py=={version}",
        f"vibeocr-backend=={version}",
        f"vibeocr-pyside=={version}",
    ]


def test_each_wheel_archive_path_has_one_owner() -> None:
    owners: dict[str, str] = {}
    for distribution, source_root in SOURCE_ROOTS.items():
        assert (source_root / "vibeocr").is_dir()
        for path in source_root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            archive_path = path.relative_to(source_root).as_posix()
            assert archive_path not in owners, (
                f"{archive_path} is owned by both {owners.get(archive_path)} "
                f"and {distribution}"
            )
            owners[archive_path] = distribution


def test_dependency_profile_matches_backend_extras() -> None:
    profile_path = (
        SOURCE_ROOTS["vibeocr-client-py"] / "vibeocr/dependency_profiles.json"
    )
    profiles = json.loads(profile_path.read_text(encoding="utf-8"))
    backend = _project(ROOT / "packages/vibeocr-backend/pyproject.toml")
    optional = backend["optional-dependencies"]
    flattened = "\n".join(optional["cpu"] + optional["gpu-cu126"])
    for name in ("paddleocr", "mineru"):
        assert profiles["dependencies"][name] in flattened
    assert profiles["dependencies"]["paddlepaddle-gpu"] in flattened


def test_pyside_pdf_module_does_not_require_backend_wheel() -> None:
    """Frontend modules may load with contracts/client/pyside installed alone."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        str(SOURCE_ROOTS[name])
        for name in (
            "vibeocr-contracts-py",
            "vibeocr-client-py",
            "vibeocr-pyside",
        )
    )
    # ``-S`` prevents editable-install .pth files from silently adding every
    # workspace source root. Add site-packages back as a plain directory so
    # third-party dependencies remain importable without processing those
    # editable .pth files.
    site_packages = sysconfig.get_path("purelib")
    probe = (
        f"import sys; sys.path.append({site_packages!r}); "
        "import importlib.util; "
        "import vibeocr.views.tabs.pdf_tab; "
        "assert importlib.util.find_spec('vibeocr.utils.shared_memory_v2') is None"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", probe],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
