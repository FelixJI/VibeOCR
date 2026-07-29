"""Architecture gates for the four-repository split transition."""

from __future__ import annotations

import argparse
import ast
import json
import re
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
OWNERSHIP_DIR = ROOT / "config/repository-split/ownership"

SOURCE_TREES = (
    ROOT / "packages/vibeocr-contracts-py",
    ROOT / "packages/vibeocr-client-py",
    ROOT / "packages/vibeocr-backend",
    ROOT / "apps/vibeocr-pyside",
    ROOT / "src/dotnet/VibeOCR.Contracts",
    ROOT / "src/dotnet/VibeOCR.App",
    ROOT / "src/dotnet/VibeOCR.Platform",
    ROOT / "src/dotnet/VibeOCR.Bootstrapper",
)

PYTHON_OWNER_ROOTS = {
    "FelixJI/vibeocr-protocol": (
        ROOT / "packages/vibeocr-contracts-py/src",
        ROOT / "packages/vibeocr-client-py/src",
    ),
    "FelixJI/vibeocr-backend": (
        ROOT / "packages/vibeocr-backend/src",
        ROOT / "packages/vibeocr-client-py/src",
    ),
    "FelixJI/vibeocr-classic": (
        ROOT / "apps/vibeocr-pyside/src",
        ROOT / "packages/vibeocr-client-py/src",
    ),
}

FORBIDDEN_IMPORTS = {
    "FelixJI/vibeocr-protocol": (
        "vibeocr.backend",
        "vibeocr.classic",
    ),
    "FelixJI/vibeocr-backend": (
        "vibeocr.runtime_client",
        "vibeocr.classic",
    ),
    "FelixJI/vibeocr-classic": ("vibeocr.backend",),
}


@dataclass(frozen=True)
class Ownership:
    repository: str
    patterns: tuple[str, ...]


def load_ownership() -> tuple[Ownership, ...]:
    manifests = []
    for path in sorted(OWNERSHIP_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1, path
        manifests.append(
            Ownership(data["repository"], tuple(data["current_source_patterns"]))
        )
    assert len(manifests) == 4, "exactly four ownership manifests are required"
    assert len({item.repository for item in manifests}) == 4
    return tuple(manifests)


def _matches(path: str, pattern: str) -> bool:
    normalized = pattern.replace("\\", "/")
    if normalized.endswith("/**"):
        prefix = normalized[:-3]
        return path == prefix or path.startswith(f"{prefix}/")
    return fnmatchcase(path, normalized)


def owners_for(path: Path, manifests: tuple[Ownership, ...]) -> tuple[str, ...]:
    relative = path.relative_to(ROOT).as_posix()
    return tuple(
        manifest.repository
        for manifest in manifests
        if any(_matches(relative, pattern) for pattern in manifest.patterns)
    )


def source_files() -> tuple[Path, ...]:
    allowed_suffixes = {".py", ".json", ".cs", ".csproj", ".xaml"}
    return tuple(
        path
        for tree in SOURCE_TREES
        if tree.exists()
        for path in tree.rglob("*")
        if path.is_file()
        and path.suffix.lower() in allowed_suffixes
        and "__pycache__" not in path.parts
    )


def check_unique_source_ownership() -> None:
    manifests = load_ownership()
    violations = []
    for path in source_files():
        owners = owners_for(path, manifests)
        if len(owners) != 1:
            violations.append(
                f"{path.relative_to(ROOT).as_posix()}: owners={list(owners)}"
            )
    assert not violations, "source ownership must be total and unique:\n" + "\n".join(
        violations
    )


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return tuple(modules)


def check_target_import_boundaries() -> None:
    manifests = load_ownership()
    violations = []
    for path in source_files():
        if path.suffix != ".py":
            continue
        owners = owners_for(path, manifests)
        if len(owners) != 1:
            continue
        owner = owners[0]
        for module in _imported_modules(path):
            for forbidden in FORBIDDEN_IMPORTS.get(owner, ()):
                if module == forbidden or module.startswith(f"{forbidden}."):
                    violations.append(
                        f"{path.relative_to(ROOT).as_posix()}: {owner} imports {module}"
                    )
    assert not violations, "forbidden target dependency:\n" + "\n".join(violations)


_DIRECT_REFERENCE = re.compile(
    r"(^|[\s\"'])((git\+|file:|https?://).+|\.{0,2}/[^\"'\s]+)", re.IGNORECASE
)


def check_release_dependencies_are_immutable() -> None:
    violations = []
    pyprojects = (
        ROOT / "pyproject.toml",
        ROOT / "packages/vibeocr-contracts-py/pyproject.toml",
        ROOT / "packages/vibeocr-client-py/pyproject.toml",
        ROOT / "packages/vibeocr-backend/pyproject.toml",
        ROOT / "apps/vibeocr-pyside/pyproject.toml",
    )
    for path in pyprojects:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        project = data.get("project", {})
        dependencies = list(project.get("dependencies", ()))
        for group in project.get("optional-dependencies", {}).values():
            dependencies.extend(group)
        for dependency in dependencies:
            if " @ " in dependency or dependency.startswith(
                ("git+", "file:", "-e ", "../", "./")
            ):
                violations.append(f"{path.relative_to(ROOT)}: {dependency}")

    for path in ROOT.joinpath(".github/workflows").glob("*.y*ml"):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            lowered = line.lower()
            if (
                ("pip install" in lowered or "uv pip install" in lowered)
                and ("git+" in lowered or " -e " in lowered or "../" in lowered)
            ):
                violations.append(
                    f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}"
                )
    assert not violations, "mutable/local release dependency:\n" + "\n".join(violations)


def check_namespace_roots_are_declared() -> None:
    load_ownership()
    expected = {
        "vibeocr.runtime_contracts",
        "vibeocr.runtime_client",
        "vibeocr.backend",
        "vibeocr.classic",
    }
    declared: set[str] = set()
    for path in OWNERSHIP_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        declared.update(data.get("python_namespaces", ()))
    assert declared == expected
    assert all(PurePosixPath(name.replace(".", "/")).parts[0] == "vibeocr" for name in declared)


def run_all() -> None:
    check_unique_source_ownership()
    check_target_import_boundaries()
    check_release_dependencies_are_immutable()
    check_namespace_roots_are_declared()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    run_all()
    print("repository split architecture gates: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
