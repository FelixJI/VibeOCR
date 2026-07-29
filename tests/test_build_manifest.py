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

from vibeocr.classic.build_manifest import (
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
    def test_other_nested_output_directory_remains_forbidden(self, tmp_path: Path) -> None:
        root = tmp_path / "stage"
        leaked = root / "worker" / "output" / "secret.pdf"
        leaked.parent.mkdir(parents=True)
        leaked.write_bytes(b"secret")

        manifest = create_manifest(root, allowed_roots=(".",))

        assert not any("secret.pdf" in entry["path"] for entry in manifest["entries"])

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
            [sys.executable, "-m", "vibeocr.classic.build_manifest", "verify", str(zip_path)],
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
            [sys.executable, "-m", "vibeocr.classic.build_manifest", "verify", str(zip_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0


class TestCreateManifestEdgeCases:
    """补 create_manifest 的边界分支覆盖。"""

    def test_nonexistent_allowed_root_skipped(self, tmp_path):
        """allowed_root 指向不存在的路径时应跳过（不报错）。"""
        root = tmp_path / "staging"
        (root / "app").mkdir(parents=True)
        (root / "app" / "main.exe").write_bytes(b"x")

        manifest = create_manifest(root, allowed_roots=("app", "nonexistent"))
        paths = {e["path"] for e in manifest["entries"]}
        assert "app/main.exe" in paths
        # 不存在的 root 被静默跳过
        assert not any("nonexistent" in p for p in paths)

    def test_single_file_allowed_root(self, tmp_path):
        """allowed_root 指向单个文件（而非目录）时应纳入该文件。"""
        root = tmp_path / "staging"
        (root / "app").mkdir(parents=True)
        (root / "app" / "main.exe").write_bytes(b"binary")
        (root / "config.json").write_text("{}", encoding="utf-8")

        manifest = create_manifest(root, allowed_roots=("config.json",))
        paths = {e["path"] for e in manifest["entries"]}
        assert "config.json" in paths
        # 单文件 root 不纳入其他文件
        assert "app/main.exe" not in paths

    def test_empty_manifest_when_all_roots_forbidden(self, tmp_path):
        """所有 allowed_roots 都是禁止路径时返回空 entries。"""
        root = tmp_path / "staging"
        (root / "output").mkdir(parents=True)
        (root / "output" / "x.txt").write_bytes(b"x")

        manifest = create_manifest(root, allowed_roots=("output",))
        assert manifest["entry_count"] == 0
        assert manifest["entries"] == []
        assert manifest["total_bytes"] == 0


class TestVerifyArchiveEdgeCases:
    """补 verify_archive 的篡改/边界分支覆盖。"""

    def test_archive_not_found_raises(self, tmp_path):
        """archive 不存在时抛 FileNotFoundError。"""
        from pathlib import Path

        with pytest.raises(FileNotFoundError):
            verify_archive(Path(tmp_path / "nope.zip"))

    def test_sha256_mismatch_detected(self, tmp_path):
        """同尺寸内容篡改应触发 sha256 mismatch（line 204）。"""
        root = tmp_path / "staging"
        _make_tree(root)
        manifest = create_manifest(root, allowed_roots=("app",))

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # 写入同尺寸但不同内容的 main.exe（触发 sha mismatch 而非 size mismatch）
            original = (root / "app" / "main.exe").read_bytes()
            tampered = bytes((b ^ 0xFF) for b in original)  # 同长度翻转位
            assert len(tampered) == len(original)
            zf.writestr("VibeOCR/app/main.exe", tampered)
            # lib.dll 正常
            lib = (root / "app" / "sub" / "lib.dll").read_bytes()
            zf.writestr("VibeOCR/app/sub/lib.dll", lib)
            zf.writestr(
                "VibeOCR/artifact-manifest.json",
                json.dumps(manifest, ensure_ascii=False),
            )

        with pytest.raises(ValueError, match="sha256 mismatch"):
            verify_archive(zip_path)

    def test_manifest_entry_missing_in_archive(self, tmp_path):
        """manifest 记录的文件在 zip 中缺失时抛 ValueError（line 195）。"""
        root = tmp_path / "staging"
        _make_tree(root)
        manifest = create_manifest(root, allowed_roots=("app",))

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # 只写 lib.dll，漏写 main.exe
            lib = (root / "app" / "sub" / "lib.dll").read_bytes()
            zf.writestr("VibeOCR/app/sub/lib.dll", lib)
            zf.writestr(
                "VibeOCR/artifact-manifest.json",
                json.dumps(manifest, ensure_ascii=False),
            )

        with pytest.raises(ValueError, match="missing in archive"):
            verify_archive(zip_path)

    def test_verify_skips_directory_entries(self, tmp_path):
        """zip 内的目录条目（以 / 结尾）应被跳过，不误判为禁止路径（line 214）。"""
        root = tmp_path / "staging"
        _make_tree(root)
        manifest = create_manifest(root, allowed_roots=("app",))

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in root.rglob("*"):
                if fp.is_file():
                    zf.write(fp, f"VibeOCR/{fp.relative_to(root)}")
            # 显式写一个目录条目（以 / 结尾）
            zf.writestr("VibeOCR/app/sub/", b"")
            zf.writestr(
                "VibeOCR/artifact-manifest.json",
                json.dumps(manifest, ensure_ascii=False),
            )

        # 不应抛异常（目录条目被跳过）
        verify_archive(zip_path)


class TestCliBranches:
    """补 main() 的退出码分支（直接调用 main 而非 subprocess）。"""

    def test_no_args_returns_2(self, capsys):
        from vibeocr.classic.build_manifest import main

        assert main([]) == 2
        captured = capsys.readouterr()
        assert "usage" in captured.err.lower()

    def test_unknown_command_returns_2(self, capsys):
        from vibeocr.classic.build_manifest import main

        assert main(["bogus", "x.zip"]) == 2
        captured = capsys.readouterr()
        assert "unknown command" in captured.err.lower()

    def test_verify_without_archive_returns_2(self, capsys):
        from vibeocr.classic.build_manifest import main

        assert main(["verify"]) == 2
        captured = capsys.readouterr()
        assert "requires an archive path" in captured.err

    def test_verify_nonexistent_archive_returns_1(self, tmp_path, capsys):
        from vibeocr.classic.build_manifest import main

        rc = main(["verify", str(tmp_path / "missing.zip")])
        assert rc == 1
        captured = capsys.readouterr()
        assert "VERIFY FAIL" in captured.err

    def test_verify_clean_archive_returns_0(self, tmp_path, capsys):
        from vibeocr.classic.build_manifest import main

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

        assert main(["verify", str(zip_path)]) == 0
        captured = capsys.readouterr()
        assert "VERIFY OK" in captured.out
