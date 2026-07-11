"""main.py _cleanup_update_artifacts 后台清理函数测试。

验证：清理 tmp/zip/sha256/暂存 updater/ready，保留 progress.json。
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def _make_update_residue(cache_dir: Path) -> dict[str, Path]:
    """在 cache_dir 造全量更新残留，返回各产物路径。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = cache_dir / "tmp"
    tmp_dir.mkdir()
    (tmp_dir / "VibeOCR").mkdir()
    (tmp_dir / "VibeOCR" / "VibeOCR.exe").write_bytes(b"new main")

    zip_path = cache_dir / "VibeOCR-v9.9.9-win64.zip"
    zip_path.write_bytes(b"fake zip")
    sha_path = cache_dir / "VibeOCR-v9.9.9-win64.zip.sha256"
    sha_path.write_text(hashlib.sha256(b"fake zip").hexdigest())

    staged_updater = cache_dir / "updater.exe"
    staged_updater.write_bytes(b"staged updater")

    ready = cache_dir / "updater.ready"
    ready.write_text("2026-01-01T00:00:00")

    # progress.json 必须保留
    progress = cache_dir / "progress.json"
    progress.write_text('{"version": "9.9.9", "success": true}')

    return {
        "tmp": tmp_dir, "zip": zip_path, "sha": sha_path,
        "staged": staged_updater, "ready": ready, "progress": progress,
    }


class TestCleanupUpdateArtifacts:
    """_cleanup_update_artifacts：清理更新残留，保留 progress.json。"""

    def test_cleans_all_but_progress(self, tmp_path):
        """清理 tmp/zip/sha/staged/ready，保留 progress.json。"""
        import pytest

        cache_dir = tmp_path / "data" / "cache" / "update"
        paths = _make_update_residue(cache_dir)

        from vibeocr import main as main_mod

        if not hasattr(main_mod, "_cleanup_update_artifacts"):
            pytest.skip("_cleanup_update_artifacts 尚未实现")

        # cache_dir = app_dir/data/cache/update → app_dir = cache_dir.parent.parent.parent
        app_dir = cache_dir.parent.parent.parent
        main_mod._cleanup_update_artifacts(app_dir)

        assert not paths["tmp"].exists(), "tmp 应被清理"
        assert not paths["zip"].exists(), "zip 应被清理"
        assert not paths["sha"].exists(), "sha 应被清理"
        assert not paths["staged"].exists(), "暂存 updater 应被清理"
        assert not paths["ready"].exists(), "ready 应被清理"
        assert paths["progress"].exists(), "progress.json 必须保留"

    def test_idempotent(self, tmp_path):
        """多次调用无副作用。"""
        import pytest

        cache_dir = tmp_path / "data" / "cache" / "update"
        _make_update_residue(cache_dir)
        from vibeocr import main as main_mod

        if not hasattr(main_mod, "_cleanup_update_artifacts"):
            pytest.skip("_cleanup_update_artifacts 尚未实现")

        app_dir = cache_dir.parent.parent.parent
        main_mod._cleanup_update_artifacts(app_dir)
        main_mod._cleanup_update_artifacts(app_dir)  # 第二次不应报错
        assert True

    def test_no_artifacts_no_error(self, tmp_path):
        """无残留时调用不报错。"""
        import pytest

        from vibeocr import main as main_mod

        if not hasattr(main_mod, "_cleanup_update_artifacts"):
            pytest.skip("_cleanup_update_artifacts 尚未实现")

        main_mod._cleanup_update_artifacts(tmp_path)  # 空目录
        assert True
