"""Tests for the Pyright workspace namespace staging seam."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from scripts.stage_pyright_namespace import stage_namespace

if TYPE_CHECKING:
    from pathlib import Path


def test_stage_namespace_merges_fragments_without_cache_files(
    tmp_path: Path,
) -> None:
    contracts = tmp_path / "contracts" / "vibeocr"
    backend = tmp_path / "backend" / "vibeocr"
    (contracts / "protocol" / "v2").mkdir(parents=True)
    (backend / "supervisor" / "__pycache__").mkdir(parents=True)
    (contracts / "protocol" / "v2" / "dtos.py").write_text(
        "PROTOCOL = 2\n",
        encoding="utf-8",
    )
    (backend / "supervisor" / "main.py").write_text(
        "ENTRYPOINT = True\n",
        encoding="utf-8",
    )
    (backend / "supervisor" / "__pycache__" / "main.pyc").write_bytes(b"cache")

    package = stage_namespace(tmp_path / "stage", (contracts, backend))

    assert (package / "protocol" / "v2" / "dtos.py").is_file()
    assert (package / "supervisor" / "main.py").is_file()
    assert not list(package.rglob("*.pyc"))


def test_stage_namespace_rejects_path_collisions(tmp_path: Path) -> None:
    first = tmp_path / "first" / "vibeocr"
    second = tmp_path / "second" / "vibeocr"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "shared.py").write_text("OWNER = 1\n", encoding="utf-8")
    (second / "shared.py").write_text("OWNER = 2\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="namespace path collision"):
        stage_namespace(tmp_path / "stage", (first, second))


def test_stage_namespace_requires_empty_output(tmp_path: Path) -> None:
    fragment = tmp_path / "fragment" / "vibeocr"
    fragment.mkdir(parents=True)
    (fragment / "main.py").write_text("pass\n", encoding="utf-8")
    output = tmp_path / "stage"
    output.mkdir()
    (output / "unexpected.txt").write_text("stale", encoding="utf-8")

    with pytest.raises(RuntimeError, match="is not empty"):
        stage_namespace(output, (fragment,))
