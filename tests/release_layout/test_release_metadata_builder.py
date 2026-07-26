from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_builder():
    root = Path(__file__).parents[2]
    path = root / "scripts" / "build_release_metadata.py"
    spec = importlib.util.spec_from_file_location("build_release_metadata", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_generates_version_and_non_empty_updater(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    builder = _load_builder()
    original_dist_base = builder.bump_version.DIST_BASE_DIR

    def fake_version_json(version: str, output: Path) -> None:
        (output / "version.json").write_text(version, encoding="utf-8")

    def fake_version_file(version: str, output: Path, *, target: str) -> Path:
        assert target == "updater"
        path = output / f"{version}.version"
        path.write_text("metadata", encoding="utf-8")
        return path

    def fake_build(output: Path, *, version_file: Path) -> bool:
        assert version_file.is_file()
        (output / "updater.exe").write_bytes(b"updater")
        return True

    monkeypatch.setattr(builder.bump_version, "_generate_version_json", fake_version_json)
    monkeypatch.setattr(builder.bump_version, "_generate_version_file", fake_version_file)
    monkeypatch.setattr(builder.bump_version, "_build_updater", fake_build)

    output = tmp_path / "release"
    builder.build_release_metadata(version="1.2.3", output=output)

    assert (output / "version.json").read_text(encoding="utf-8") == "1.2.3"
    assert (output / "updater.exe").read_bytes() == b"updater"
    assert not (output / ".updater-build").exists()
    assert original_dist_base == builder.bump_version.DIST_BASE_DIR
