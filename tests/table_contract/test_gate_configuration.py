"""表格语义门禁配置的静态回归测试。"""

from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]


def test_main_table_regression_covers_cross_layer_surfaces_and_has_ratchet():
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    for path in (
        "tests/services/test_export_service_extra.py",
        "tests/supervisor/test_export_route_tables.py",
        "tests/utils/test_table_model_reducer.py",
        "tests/views/tabs/test_base_tab.py",
        "tests/widgets/test_result_view_widget.py",
    ):
        assert path in workflow
    assert "--cov=vibeocr.tables" in workflow
    assert "--cov-branch" in workflow
    assert "--cov-fail-under=85" in workflow


def test_release_runs_provider_gate_and_exact_artifact_gate():
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    provider_gate = workflow.index("Run offline table provider contract gate")
    wheel_build = workflow.index("Build physical Python workspace wheels")
    artifact_gate = workflow.index("Verify table semantics in release artifacts")
    assert provider_gate < wheel_build < artifact_gate
    assert "scripts/run_table_contract_gate.ps1" in workflow
    assert "scripts/verify_table_artifact.py" in workflow
    assert "reports/table-release-contract" in workflow


def test_release_requires_full_parity_only_when_winui_is_selected():
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert 'steps.variants.outputs.build_winui }}" == "true"' in workflow
    assert "feature-parity.md --require-pass" in workflow
    unconditional_validation = (
        "python tests/parity/validate_matrix.py "
        "docs/quality/feature-parity.md\n"
    )
    assert unconditional_validation in workflow
