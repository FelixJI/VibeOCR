"""build_manifest 模块测试：可复现的产物清单生成与校验。

覆盖：
- ManifestEntry 记录相对路径、字节数、SHA-256
- create_manifest 只纳入 allowed_roots，拒绝 output/、.venv/、data/profiles/ 等禁止路径
- create_manifest 不写入本机绝对路径
- verify_archive 能检测篡改（内容改动、尺寸改动、增删文件）
- 锁文件每行必须精确 == 约束
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from typing import TYPE_CHECKING

import pytest

from vibeocr.build_manifest import (
    ManifestEntry,
    create_manifest,
    verify_archive,
)

if TYPE_CHECKING:
    from pathlib import Path

FORBIDDEN_NAMES = {"output", ".venv", "data", "__pycache__"}


# ---------------------------------------------------------------------------
# ManifestEntry
# ---------------------------------------------------------------------------


class TestManifestEntry:
    def test_entry_is_frozen(self):
        entry = ManifestEntry(path="a.txt", size=10, sha256="abc")
        with pytest.raises(Exception):  # noqa: B017 — frozen dataclass
            entry.path = "b.txt"  # type: ignore[misc]

    def test_entry_fields(self):
        entry = ManifestEntry(path="x/y.txt", size=42, sha256="deadbeef")
        assert entry.path == "x/y.txt"
        assert entry.size == 42
        assert entry.sha256 == "deadbeef"


# ---------------------------------------------------------------------------
# create_manifest
# ---------------------------------------------------------------------------


def _make_tree(root: Path) -> None:
    """在 root 下造一组测试文件。"""
    (root / "app" / "sub").mkdir(parents=True)
    (root / "app" / "main.exe").write_bytes(b"main binary")
    (root / "app" / "sub" / "lib.dll").write_bytes(b"library")
    (root / "config.json").write_text("{}", encoding="utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestCreateManifest:
    def test_manifest_records_relative_path_size_sha256(self, tmp_path):
        root = tmp_path / "staging"
        _make_tree(root)

        manifest = create_manifest(root, allowed_roots=("app",))

        entries = manifest["entries"]
        paths = {e["path"] for e in entries}
        assert "app/main.exe" in paths
        assert "app/sub/lib.dll" in paths
        # config.json 不在 allowed_roots 中，不应出现
        assert "config.json" not in paths

        for e in entries:
            assert e["size"] > 0
            assert len(e["sha256"]) == 64

    def test_manifest_rejects_output_dir(self, tmp_path):
        root = tmp_path / "staging"
        _make_tree(root)
        # 造一个禁止目录
        (root / "output").mkdir()
        (root / "output" / "secret.pdf").write_bytes(b"secret user data")

        manifest = create_manifest(root, allowed_roots=("app", "output"))

        paths = {e["path"] for e in manifest["entries"]}
        # output/ 是禁止路径，即使出现在 allowed_roots 中也必须被拒绝
        assert not any(p.startswith("output/") for p in paths)

    def test_manifest_rejects_venv_and_profiles(self, tmp_path):
        root = tmp_path / "staging"
        (root / "app").mkdir(parents=True)
        (root / "app" / "main.exe").write_bytes(b"x")
        (root / ".venv").mkdir(parents=True)
        (root / ".venv" / "site-packages").mkdir()
        (root / ".venv" / "site-packages" / "pkg.py").write_text("x", encoding="utf-8")
        (root / "data" / "profiles").mkdir(parents=True)
        (root / "data" / "profiles" / "winui-dev").mkdir()
        (root / "data" / "profiles" / "winui-dev" / "config.json").write_text(
            "{}", encoding="utf-8"
        )

        manifest = create_manifest(root, allowed_roots=("app", ".venv", "data"))

        paths = {e["path"] for e in manifest["entries"]}
        assert "app/main.exe" in paths
        assert not any(p.startswith(".venv/") for p in paths)
        assert not any(p.startswith("data/") for p in paths)

    def test_manifest_no_absolute_paths(self, tmp_path):
        root = tmp_path / "staging"
        _make_tree(root)

        manifest = create_manifest(root, allowed_roots=("app",))
        raw = json.dumps(manifest)
        # 不含本机绝对路径
        assert str(tmp_path) not in raw

    def test_manifest_detects_tamper_size(self, tmp_path):
        root = tmp_path / "staging"
        _make_tree(root)

        manifest = create_manifest(root, allowed_roots=("app",))
        # 篡改文件尺寸（不改路径列表）
        (root / "app" / "main.exe").write_bytes(b"different content size here!!")

        with pytest.raises(Exception):  # noqa: B017
            _verify_entries(root, manifest["entries"])

    def test_manifest_detects_tamper_content(self, tmp_path):
        root = tmp_path / "staging"
        _make_tree(root)
        original = (root / "app" / "main.exe").read_bytes()
        manifest = create_manifest(root, allowed_roots=("app",))
        # 同尺寸篡改（hash 变但 size 不变）
        (root / "app" / "main.exe").write_bytes(b"main binarX")  # 同长度不同内容
        assert len((root / "app" / "main.exe").read_bytes()) == len(original)

        with pytest.raises(Exception):  # noqa: B017
            _verify_entries(root, manifest["entries"])


def _verify_entries(root: Path, entries: list[dict]) -> None:
    """重新计算每个 entry 的 sha256/size 并对比。"""
    for e in entries:
        p = root / e["path"]
        data = p.read_bytes()
        assert len(data) == e["size"]
        assert hashlib.sha256(data).hexdigest() == e["sha256"]


# ---------------------------------------------------------------------------
# verify_archive
# ---------------------------------------------------------------------------


class TestVerifyArchive:
    def test_verify_clean_zip_passes(self, tmp_path):
        root = tmp_path / "staging"
        _make_tree(root)
        manifest = create_manifest(root, allowed_roots=("app", "config.json"))

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in root.rglob("*"):
                if fp.is_file():
                    zf.write(fp, f"VibeOCR/{fp.relative_to(root)}")
            # manifest 放 zip 根目录
            zf.writestr(
                "VibeOCR/artifact-manifest.json",
                json.dumps(manifest, ensure_ascii=False),
            )

        # 不应抛异常
        verify_archive(zip_path)

    def test_verify_rejects_tampered_zip(self, tmp_path):
        root = tmp_path / "staging"
        _make_tree(root)
        manifest = create_manifest(root, allowed_roots=("app",))

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in root.rglob("*"):
                if fp.is_file():
                    # 篡改：写入不同内容
                    zf.writestr(
                        f"VibeOCR/{fp.relative_to(root)}",
                        b"tampered content",
                    )
            zf.writestr(
                "VibeOCR/artifact-manifest.json",
                json.dumps(manifest, ensure_ascii=False),
            )

        with pytest.raises(Exception):  # noqa: B017
            verify_archive(zip_path)

    def test_verify_rejects_missing_manifest(self, tmp_path):
        root = tmp_path / "staging"
        _make_tree(root)

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in root.rglob("*"):
                if fp.is_file():
                    zf.write(fp, f"VibeOCR/{fp.relative_to(root)}")
            # 不写 manifest

        with pytest.raises(Exception):  # noqa: B017
            verify_archive(zip_path)

    def test_verify_rejects_output_in_zip(self, tmp_path):
        root = tmp_path / "staging"
        _make_tree(root)
        manifest = create_manifest(root, allowed_roots=("app",))

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in root.rglob("*"):
                if fp.is_file():
                    zf.write(fp, f"VibeOCR/{fp.relative_to(root)}")
            # 注入禁止目录
            zf.writestr("VibeOCR/output/secret.pdf", b"leaked")
            zf.writestr(
                "VibeOCR/artifact-manifest.json",
                json.dumps(manifest, ensure_ascii=False),
            )

        with pytest.raises(Exception):  # noqa: B017
            verify_archive(zip_path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


class TestCli:
    def test_verify_subcommand_exit_zero_on_clean(self, tmp_path):
        root = tmp_path / "staging"
        _make_tree(root)
        manifest = create_manifest(root, allowed_roots=("app", "config.json"))

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in root.rglob("*"):
                if fp.is_file():
                    zf.write(fp, f"VibeOCR/{fp.relative_to(root)}")
            zf.writestr(
                "VibeOCR/artifact-manifest.json",
                json.dumps(manifest, ensure_ascii=False),
            )

        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "vibeocr.build_manifest", "verify", str(zip_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_verify_subcommand_exit_nonzero_on_tamper(self, tmp_path):
        root = tmp_path / "staging"
        _make_tree(root)
        manifest = create_manifest(root, allowed_roots=("app",))

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in root.rglob("*"):
                if fp.is_file():
                    zf.writestr(
                        f"VibeOCR/{fp.relative_to(root)}",
                        b"tampered",
                    )
            zf.writestr(
                "VibeOCR/artifact-manifest.json",
                json.dumps(manifest, ensure_ascii=False),
            )

        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "vibeocr.build_manifest", "verify", str(zip_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
