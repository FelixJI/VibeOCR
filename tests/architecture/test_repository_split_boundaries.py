"""Phase 0 gates for the four-repository cutover."""

from scripts.check_repository_split_boundaries import (
    check_namespace_roots_are_declared,
    check_release_dependencies_are_immutable,
    check_target_import_boundaries,
    check_unique_source_ownership,
)


def test_every_source_module_has_one_target_repository() -> None:
    check_unique_source_ownership()


def test_target_dependency_direction_has_no_new_reverse_edges() -> None:
    check_target_import_boundaries()


def test_release_dependencies_are_immutable() -> None:
    check_release_dependencies_are_immutable()


def test_target_namespace_roots_are_explicit() -> None:
    check_namespace_roots_are_declared()
