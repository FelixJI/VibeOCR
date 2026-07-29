from __future__ import annotations

import hashlib
import zipfile
from typing import TYPE_CHECKING

import pytest

from scripts.build_protocol_release_assets import build_protocol_release_assets

if TYPE_CHECKING:
    from pathlib import Path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_protocol_release_archives_are_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "contracts"
    (source / "schemas").mkdir(parents=True)
    (source / "golden").mkdir()
    (source / "openapi.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")
    (source / "schemas" / "z.json").write_text("{}\n", encoding="utf-8")
    (source / "schemas" / "a.json").write_text("[]\n", encoding="utf-8")
    (source / "golden" / "sample.json").write_text("{}\n", encoding="utf-8")

    first = build_protocol_release_assets(
        contracts_root=source,
        version="2.0.0",
        output_dir=tmp_path / "first",
    )
    second = build_protocol_release_assets(
        contracts_root=source,
        version="2.0.0",
        output_dir=tmp_path / "second",
    )

    assert [_digest(path) for path in first] == [_digest(path) for path in second]
    with zipfile.ZipFile(first[1]) as archive:
        assert archive.namelist() == ["schemas/a.json", "schemas/z.json"]
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())


def test_protocol_release_requires_nonempty_schema_directory(tmp_path: Path) -> None:
    source = tmp_path / "contracts"
    (source / "schemas").mkdir(parents=True)
    (source / "golden").mkdir()
    (source / "openapi.yaml").write_text("openapi: 3.1.0\n", encoding="utf-8")
    (source / "golden" / "sample.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="archive source is empty"):
        build_protocol_release_assets(
            contracts_root=source,
            version="2.0.0",
            output_dir=tmp_path / "dist",
        )
