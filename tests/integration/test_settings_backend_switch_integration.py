"""Real GPU backend-switch integration test.

Drives the production ``JsonSettingsAdapter.switch_backend`` (the boundary
behind the ``settings.switch_backend`` RPC) against a real profile config
on a machine with a CUDA GPU, verifying the cpu↔gpu switch persists and
the snapshot reads it back. This exercises the full protocol path the
WinUI settings tab uses (no protected hook).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from vibeocr.app_paths import resolve_app_paths
from vibeocr.worker_host.composition import JsonSettingsAdapter


def _has_cuda() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available()) and torch.cuda.device_count() > 0
    except Exception:
        return False


@pytest.mark.skipif(not _has_cuda(), reason="no CUDA GPU on this machine")
def test_switch_backend_persists_cpu_gpu_roundtrip(tmp_path: Path) -> None:
    """cpu→gpu→cpu via the adapter; each switch must persist + round-trip."""
    paths = resolve_app_paths(tmp_path, profile="winui-dev")
    adapter = JsonSettingsAdapter(paths, backend_resolver=lambda: "cpu")

    # Seed an initial config with cpu.
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(json.dumps({"backend": "cpu", "preload_pipelines": []}), encoding="utf-8")
    assert adapter.get_snapshot().backend == "cpu"

    # Switch to gpu (this machine has a CUDA device).
    new_backend = adapter.switch_backend("gpu")
    assert new_backend == "gpu"
    # Persisted to disk.
    on_disk = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert on_disk["backend"] == "gpu"
    # Snapshot reads it back.
    assert adapter.get_snapshot().backend == "gpu"

    # Switch back to cpu.
    assert adapter.switch_backend("cpu") == "cpu"
    assert json.loads(paths.config_file.read_text(encoding="utf-8"))["backend"] == "cpu"
    assert adapter.get_snapshot().backend == "cpu"


def test_switch_backend_invalid_target_raises(tmp_path: Path) -> None:
    """The adapter must reject unsupported backends (the handler maps this to INVALID_REQUEST)."""
    paths = resolve_app_paths(tmp_path, profile="winui-dev")
    adapter = JsonSettingsAdapter(paths, backend_resolver=lambda: "cpu")
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unsupported backend"):
        adapter.switch_backend("tpu")


def test_switch_backend_preserves_other_fields(tmp_path: Path) -> None:
    """A switch must not clobber unrelated config fields."""
    paths = resolve_app_paths(tmp_path, profile="winui-dev")
    adapter = JsonSettingsAdapter(paths, backend_resolver=lambda: "cpu")
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    original = {"backend": "cpu", "preload_pipelines": ["OCR"], "pipeline_ttl_seconds": 600}
    paths.config_file.write_text(json.dumps(original), encoding="utf-8")

    adapter.switch_backend("gpu")

    on_disk = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert on_disk["backend"] == "gpu"
    assert on_disk["preload_pipelines"] == ["OCR"]
    assert on_disk["pipeline_ttl_seconds"] == 600
