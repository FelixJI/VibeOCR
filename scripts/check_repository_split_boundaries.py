"""Architecture gates for the four-repository split transition."""

from __future__ import annotations

import argparse
import ast
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
OWNERSHIP_DIR = ROOT / "config/repository-split/ownership"
MODULE_MAP = ROOT / "config/repository-split/module-map.json"
LEGACY_DEBT = ROOT / "config/repository-split/legacy-import-debt.json"

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

PYTHON_SOURCE_ROOTS = (
    ROOT / "packages/vibeocr-contracts-py/src",
    ROOT / "packages/vibeocr-client-py/src",
    ROOT / "packages/vibeocr-backend/src",
    ROOT / "apps/vibeocr-pyside/src",
)

WHEEL_SOURCE_ROOTS = {
    "vibeocr-contracts-py": ROOT / "packages/vibeocr-contracts-py/src",
    "vibeocr-client-py": ROOT / "packages/vibeocr-client-py/src",
    "vibeocr-backend": ROOT / "packages/vibeocr-backend/src",
    "vibeocr-pyside": ROOT / "apps/vibeocr-pyside/src",
}


@dataclass(frozen=True)
class Ownership:
    repository: str
    patterns: tuple[str, ...]
    forbidden_dependencies: tuple[str, ...]
    allowed_repository_dependencies: tuple[str, ...]
    allowed_current_import_prefixes: tuple[str, ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_ownership() -> tuple[Ownership, ...]:
    manifests = []
    for path in sorted(OWNERSHIP_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        _require(data["schema_version"] == 1, f"unsupported manifest schema: {path}")
        manifests.append(
            Ownership(
                repository=data["repository"],
                patterns=tuple(data["current_source_patterns"]),
                forbidden_dependencies=tuple(data.get("forbidden_dependencies", ())),
                allowed_repository_dependencies=tuple(
                    data.get("allowed_repository_dependencies", ())
                ),
                allowed_current_import_prefixes=tuple(
                    data.get("allowed_current_import_prefixes", ())
                ),
            )
        )
    _require(len(manifests) == 4, "exactly four ownership manifests are required")
    _require(
        len({item.repository for item in manifests}) == 4,
        "ownership repository names must be unique",
    )
    return tuple(manifests)


def _expand_braces(pattern: str) -> tuple[str, ...]:
    match = re.search(r"\{([^{}]+)\}", pattern)
    if match is None:
        return (pattern,)
    prefix = pattern[: match.start()]
    suffix = pattern[match.end() :]
    return tuple(
        expanded
        for choice in match.group(1).split(",")
        for expanded in _expand_braces(f"{prefix}{choice}{suffix}")
    )


def _matches(path: str, pattern: str) -> bool:
    for expanded in _expand_braces(pattern.replace("\\", "/")):
        if expanded.endswith("/**"):
            prefix = expanded[:-3]
            if path == prefix or path.startswith(f"{prefix}/"):
                return True
        elif fnmatchcase(path, expanded):
            return True
    return False


def owners_for(path: Path, manifests: tuple[Ownership, ...]) -> tuple[str, ...]:
    relative = path.relative_to(ROOT).as_posix()
    return tuple(
        manifest.repository
        for manifest in manifests
        if any(_matches(relative, pattern) for pattern in manifest.patterns)
    )


def source_files() -> tuple[Path, ...]:
    allowed_suffixes = {".py", ".json", ".cs", ".csproj", ".xaml"}
    ignored_parts = {
        ".git",
        ".venv",
        "__pycache__",
        "bin",
        "obj",
        "site-packages",
    }
    return tuple(
        path
        for tree in SOURCE_TREES
        if tree.exists()
        for path in tree.rglob("*")
        if path.is_file()
        and path.suffix.lower() in allowed_suffixes
        and not ignored_parts.intersection(path.parts)
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
    _require(
        not violations,
        "source ownership must be total and unique:\n" + "\n".join(violations),
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


def _python_module(path: Path) -> str | None:
    for source_root in PYTHON_SOURCE_ROOTS:
        try:
            relative = path.relative_to(source_root)
        except ValueError:
            continue
        if relative.suffix != ".py":
            return None
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)
    return None


def _module_owners(
    manifests: tuple[Ownership, ...],
) -> dict[str, frozenset[str]]:
    result: dict[str, set[str]] = {}
    for path in source_files():
        module = _python_module(path)
        owners = owners_for(path, manifests)
        if module and len(owners) == 1:
            result.setdefault(module, set()).add(owners[0])
    return {module: frozenset(owners) for module, owners in result.items()}


def _resolve_import_owners(
    module: str, module_owners: dict[str, frozenset[str]]
) -> frozenset[str]:
    candidate = module
    while candidate:
        if candidate in module_owners:
            return module_owners[candidate]
        candidate = candidate.rpartition(".")[0]
    return frozenset()


def _manifest_by_repo(
    manifests: tuple[Ownership, ...],
) -> dict[str, Ownership]:
    return {manifest.repository: manifest for manifest in manifests}


def _legacy_import_violations() -> tuple[str, ...]:
    manifests = load_ownership()
    by_repo = _manifest_by_repo(manifests)
    module_owners = _module_owners(manifests)
    violations: list[str] = []
    for path in source_files():
        if path.suffix != ".py":
            continue
        owners = owners_for(path, manifests)
        if len(owners) != 1:
            continue
        owner = owners[0]
        for module in _imported_modules(path):
            manifest = by_repo[owner]
            forbidden_target = any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in manifest.forbidden_dependencies
            )
            if forbidden_target:
                violations.append(
                    "|".join(
                        (
                            path.relative_to(ROOT).as_posix(),
                            module,
                            "forbidden-target-namespace",
                        )
                    )
                )
                continue
            imported_owners = _resolve_import_owners(module, module_owners)
            external = imported_owners - {owner}
            if not external:
                continue
            for imported_owner in sorted(external):
                allowed_repo = (
                    imported_owner in manifest.allowed_repository_dependencies
                )
                allowed_prefix = (
                    not manifest.allowed_current_import_prefixes
                    or any(
                        module == prefix or module.startswith(f"{prefix}.")
                        for prefix in manifest.allowed_current_import_prefixes
                    )
                )
                if not (allowed_repo and allowed_prefix):
                    violations.append(
                        "|".join(
                            (
                                path.relative_to(ROOT).as_posix(),
                                module,
                                imported_owner,
                            )
                        )
                    )
    return tuple(sorted(set(violations)))


def check_target_import_boundaries() -> None:
    current = set(_legacy_import_violations())
    data = json.loads(LEGACY_DEBT.read_text(encoding="utf-8"))
    _require(data["schema_version"] == 1, "unsupported legacy debt schema")
    baseline = set(data["violations"])
    new = sorted(current - baseline)
    stale = sorted(baseline - current)
    _require(
        not new,
        "new forbidden target dependencies:\n" + "\n".join(new),
    )
    _require(
        not stale,
        "stale legacy import debt must be removed:\n" + "\n".join(stale),
    )


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
    _require(
        not violations,
        "mutable/local release dependency:\n" + "\n".join(violations),
    )


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
    _require(declared == expected, f"unexpected namespace declarations: {declared}")
    _require(
        all(
            PurePosixPath(name.replace(".", "/")).parts[0] == "vibeocr"
            for name in declared
        ),
        "all Python namespace roots must be under vibeocr",
    )


def check_module_map() -> None:
    manifests = load_ownership()
    repositories = {manifest.repository for manifest in manifests}
    data = json.loads(MODULE_MAP.read_text(encoding="utf-8"))
    _require(data["schema_version"] == 1, "unsupported module-map schema")
    _require(
        re.fullmatch(r"[0-9a-f]{40}", data["source_commit"]) is not None,
        "module-map source_commit must be a full Git SHA",
    )
    entries = data["mapping"]
    _require(entries, "module-map cannot be empty")
    for entry in entries:
        _require(entry["owner"] in repositories, f"unknown map owner: {entry}")
        _require(bool(_expand_braces(entry["current"])), f"invalid map glob: {entry}")

    violations = []
    for path in source_files():
        relative = path.relative_to(ROOT).as_posix()
        matches = [entry for entry in entries if _matches(relative, entry["current"])]
        owners = owners_for(path, manifests)
        if len(matches) != 1 or len(owners) != 1 or matches[0]["owner"] != owners[0]:
            violations.append(
                f"{relative}: map={matches!r}, ownership={list(owners)!r}"
            )
    _require(
        not violations,
        "module-map must cover every source exactly once and agree with ownership:\n"
        + "\n".join(violations),
    )


def check_wheel_archive_path_ownership() -> None:
    owners: dict[str, str] = {}
    collisions = []
    for distribution, source_root in WHEEL_SOURCE_ROOTS.items():
        for path in source_root.rglob("*"):
            if (
                not path.is_file()
                or "__pycache__" in path.parts
                or ".venv" in path.parts
            ):
                continue
            archive_path = path.relative_to(source_root).as_posix()
            previous = owners.setdefault(archive_path, distribution)
            if previous != distribution:
                collisions.append(f"{archive_path}: {previous}, {distribution}")
    _require(
        not collisions,
        "wheel archive paths must have one owner:\n" + "\n".join(collisions),
    )


def check_dotnet_project_boundaries() -> None:
    manifests = load_ownership()
    by_repo = _manifest_by_repo(manifests)
    violations = []
    for project in ROOT.joinpath("src/dotnet").glob("*/*.csproj"):
        project_owners = owners_for(project, manifests)
        if len(project_owners) != 1:
            continue
        owner = project_owners[0]
        tree = ET.parse(project)
        for reference in tree.findall(".//ProjectReference"):
            include = reference.attrib.get("Include")
            if not include:
                continue
            target = (project.parent / include).resolve()
            target_owners = owners_for(target, manifests)
            for target_owner in set(target_owners) - {owner}:
                if target_owner not in by_repo[owner].allowed_repository_dependencies:
                    violations.append(
                        f"{project.relative_to(ROOT)} -> {target.relative_to(ROOT)}"
                    )
    _require(
        not violations,
        "forbidden .NET project dependency:\n" + "\n".join(violations),
    )


def run_all() -> None:
    check_unique_source_ownership()
    check_module_map()
    check_target_import_boundaries()
    check_dotnet_project_boundaries()
    check_wheel_archive_path_ownership()
    check_release_dependencies_are_immutable()
    check_namespace_roots_are_declared()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-legacy-baseline", action="store_true")
    args = parser.parse_args()
    if args.write_legacy_baseline:
        LEGACY_DEBT.parent.mkdir(parents=True, exist_ok=True)
        LEGACY_DEBT.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "violations": list(_legacy_import_violations()),
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(LEGACY_DEBT.relative_to(ROOT))
        return 0
    run_all()
    print("repository split architecture gates: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
