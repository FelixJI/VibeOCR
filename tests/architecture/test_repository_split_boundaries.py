"""Phase 0 gates for the four-repository cutover."""

import pytest

from scripts.check_repository_split_boundaries import (
    _require,
    check_dotnet_project_boundaries,
    check_module_map,
    check_namespace_roots_are_declared,
    check_release_dependencies_are_immutable,
    check_target_import_boundaries,
    check_unique_source_ownership,
    check_wheel_archive_path_ownership,
)


def test_gate_failures_are_not_removed_by_python_optimization() -> None:
    with pytest.raises(RuntimeError, match="sentinel"):
        _require(False, "sentinel")


def test_every_source_module_has_one_target_repository() -> None:
    check_unique_source_ownership()


def test_target_dependency_direction_has_no_new_reverse_edges() -> None:
    check_target_import_boundaries()


def test_dotnet_project_references_follow_target_dependency_direction() -> None:
    check_dotnet_project_boundaries()


def test_module_map_is_total_unique_and_matches_ownership() -> None:
    check_module_map()


def test_wheel_archive_paths_have_one_physical_owner() -> None:
    check_wheel_archive_path_ownership()


def test_release_dependencies_are_immutable() -> None:
    check_release_dependencies_are_immutable()


def test_target_namespace_roots_are_explicit() -> None:
    check_namespace_roots_are_declared()
