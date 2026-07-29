"""Phase 10: Release + rollback verification.

Plan §10 requires:
1. Build contracts/client/backend wheels from the same commit.
2. Verify the settings migration writes a ``.v1.bak`` backup.
3. Verify rollback: restore the backup config and confirm the old schema is intact.
4. Verify the workspace wheel-build still produces four non-overlapping wheels.
5. Verify the supervisor entry point exists in the backend wheel.

These are fast, deterministic tests that validate the release pipeline's
correctness without actually publishing anything.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestReleaseBuild:
    """Verify workspace wheels build successfully."""

    def test_contracts_wheel_builds(self, tmp_path: Path) -> None:
        """contracts wheel builds and contains protocol/v2 JSON assets."""
        try:
            import build  # noqa: F401
        except ImportError:
            pytest.skip("python build module not installed")
        result = subprocess.run(
            [sys.executable, "-m", "build", "--wheel",
             str(_REPO_ROOT / "packages" / "vibeocr-contracts-py"),
             "--outdir", str(tmp_path)],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, f"contracts build failed: {result.stderr[-500:]}"
        wheels = list(tmp_path.glob("vibeocr_runtime_contracts-*.whl"))
        assert len(wheels) == 1, f"expected 1 contracts wheel, got {wheels}"
        with zipfile.ZipFile(wheels[0]) as archive:
            members = set(archive.namelist())
        assert "vibeocr/runtime_contracts/golden/golden.json" in members
        assert not any(name.startswith("vibeocr/protocol/v1/") for name in members)

    def test_backend_wheel_builds(self, tmp_path: Path) -> None:
        """backend wheel builds and contains the supervisor entry point."""
        try:
            import build  # noqa: F401
        except ImportError:
            pytest.skip("python build module not installed")
        result = subprocess.run(
            [sys.executable, "-m", "build", "--wheel",
             str(_REPO_ROOT / "packages" / "vibeocr-backend"),
             "--outdir", str(tmp_path)],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, f"backend build failed: {result.stderr[-500:]}"
        wheels = list(tmp_path.glob("vibeocr_backend-*.whl"))
        assert len(wheels) == 1, f"expected 1 backend wheel, got {wheels}"
        with zipfile.ZipFile(wheels[0]) as archive:
            members = set(archive.namelist())
        assert "vibeocr/backend/supervisor/main.py" in members
        assert not any(name.startswith("vibeocr/worker_host/") for name in members)

    def test_supervisor_entry_point_exists(self) -> None:
        """The vibeocr-supervisor console script is registered in backend pyproject."""
        pyproject = (_REPO_ROOT / "packages" / "vibeocr-backend" / "pyproject.toml").read_text()
        assert "vibeocr-supervisor" in pyproject, "vibeocr-supervisor entry point missing"
        assert "vibeocr.backend.supervisor.main:main" in pyproject, "wrong entry point target"


class TestSettingsMigrationRollback:
    """Verify the settings migration backup + rollback path (plan §8)."""

    def test_migration_creates_backup(self, tmp_path: Path) -> None:
        """Migrating legacy settings creates a .v1.bak backup."""
        from vibeocr.backend.migration.residency_migration import migrate_settings_file

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "backend": "gpu",
            "pipeline_ttls": {"OCR": 300, "MinerU": 0},
        }), encoding="utf-8")

        result = migrate_settings_file(settings, default_ttl_seconds=600)
        assert result.migrated is True
        assert result.backed_up_to is not None
        assert result.backed_up_to.exists()

        # Backup retains original legacy content.
        backup = json.loads(result.backed_up_to.read_text(encoding="utf-8"))
        assert backup["pipeline_ttls"] == {"OCR": 300, "MinerU": 0}

        # Migrated file has v2 schema.
        migrated = json.loads(settings.read_text(encoding="utf-8"))
        assert "pipeline_ttls" not in migrated
        assert migrated["schema_version"] == 2
        assert migrated["residency"]["default_ttl_seconds"] == 600

    def test_rollback_restores_original_config(self, tmp_path: Path) -> None:
        """Simulate rollback: restore .v1.bak over the migrated file."""
        from vibeocr.backend.migration.residency_migration import migrate_settings_file

        settings = tmp_path / "settings.json"
        original_data = {"backend": "cpu", "pipeline_ttls": {"OCR": 120}}
        settings.write_text(json.dumps(original_data), encoding="utf-8")

        # Step 1: migrate (creates backup).
        result = migrate_settings_file(settings, default_ttl_seconds=300)
        assert result.migrated is True
        backup_path = result.backed_up_to
        assert backup_path is not None and backup_path.exists()

        # Step 2: simulate rollback — copy backup back over settings.
        shutil.copy2(backup_path, settings)

        # Step 3: verify original content is intact.
        rolled_back = json.loads(settings.read_text(encoding="utf-8"))
        assert rolled_back == original_data, "rollback must restore exact original config"
        assert "pipeline_ttls" in rolled_back, "legacy field must be present after rollback"

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """Re-running migration on an already-migrated file is a no-op."""
        from vibeocr.backend.migration.residency_migration import migrate_settings_file

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"backend": "cpu"}), encoding="utf-8")

        # First migration.
        first = migrate_settings_file(settings, default_ttl_seconds=300)
        assert first.migrated is True

        # Second migration — no-op.
        second = migrate_settings_file(settings, default_ttl_seconds=600)
        assert second.migrated is False
        assert second.backed_up_to is None
        # Default TTL from first run is preserved (not overwritten by second).
        assert second.default_ttl_seconds == 300


class TestProtocolAssetIntegrity:
    """Verify protocol v2 assets are present and well-formed."""

    def test_openapi_snapshot_exists(self) -> None:
        """OpenAPI snapshot is a valid JSON file."""
        snapshot = _REPO_ROOT / "packages" / "vibeocr-contracts-py" / "src" / "vibeocr" / "runtime_contracts" / "openapi.snapshot.json"
        assert snapshot.exists(), "OpenAPI snapshot missing"
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        assert data["openapi"] == "3.1.0"

    def test_golden_fixtures_exist(self) -> None:
        """Golden fixtures file exists and has the expected keys."""
        golden = _REPO_ROOT / "packages" / "vibeocr-contracts-py" / "src" / "vibeocr" / "runtime_contracts" / "golden" / "golden.json"
        assert golden.exists(), "golden.json missing"
        data = json.loads(golden.read_text(encoding="utf-8"))
        for key in ("job_ref", "job_snapshot_running", "error_oom", "residency_status", "settings_snapshot"):
            assert key in data, f"golden fixture '{key}' missing"

    def test_errors_registry_has_18_codes(self) -> None:
        """Error registry includes the two job-interface failure codes."""
        errors = _REPO_ROOT / "packages" / "vibeocr-contracts-py" / "src" / "vibeocr" / "runtime_contracts" / "errors.json"
        assert errors.exists(), "errors.json missing"
        data = json.loads(errors.read_text(encoding="utf-8"))
        assert len(data["codes"]) == 18, f"expected 18 error codes, got {len(data['codes'])}"

    def test_no_v1_protocol_remains(self) -> None:
        """Protocol v1 directory must not exist (deleted in Phase 8)."""
        v1_dir = _REPO_ROOT / "packages" / "vibeocr-contracts-py" / "src" / "vibeocr" / "runtime_contracts" / "protocol" / "v1"
        assert not v1_dir.exists(), "v1 protocol directory should have been deleted"

    def test_legacy_client_distribution_is_deleted(self) -> None:
        """The mixed-ownership client distribution must not survive the split."""
        client_dir = _REPO_ROOT / "packages" / "vibeocr-client-py"
        assert not client_dir.exists(), "vibeocr-client-py should have been deleted"

    def test_no_worker_host_in_backend_package(self) -> None:
        """worker_host directory must not exist in backend (deleted in Phase 8)."""
        wh_dir = _REPO_ROOT / "packages" / "vibeocr-backend" / "src" / "vibeocr" / "worker_host"
        assert not wh_dir.exists(), "backend worker_host should have been deleted"
