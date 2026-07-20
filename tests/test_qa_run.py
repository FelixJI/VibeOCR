from __future__ import annotations

from argparse import Namespace

from qa import run


def _args(**overrides) -> Namespace:
    values = {
        "checks": [],
        "all": False,
        "no_interactive": False,
        "ci": False,
        "quick": False,
    }
    values.update(overrides)
    return Namespace(**values)


def test_ci_default_runs_only_non_mutating_checks() -> None:
    selected = run._select_checks(_args(ci=True))

    assert selected == list(run.NON_MUTATING_CHECKS)
    assert "coverage" in selected
    assert "upgrade_deps" not in selected


def test_all_flag_matches_documented_non_mutating_suite() -> None:
    assert run._select_checks(_args(all=True)) == list(run.NON_MUTATING_CHECKS)
    assert run._select_checks(_args(checks=["all"])) == list(
        run.NON_MUTATING_CHECKS
    )


def test_quick_mode_skips_coverage_and_dependency_upgrade() -> None:
    selected = run._select_checks(_args(no_interactive=True, quick=True))

    assert "coverage" not in selected
    assert "upgrade_deps" not in selected


def test_dependency_upgrade_requires_explicit_selection() -> None:
    selected = run._select_checks(_args(checks=["upgrade_deps"], ci=True))

    assert selected == ["upgrade_deps"]


def test_main_accepts_documented_all_flag(monkeypatch) -> None:
    selected: list[str] = []

    def fake_run(checks, **_kwargs):
        selected.extend(checks)
        return {}

    monkeypatch.setattr(run.sys, "argv", ["qa/run.py", "--all", "--no-report"])
    monkeypatch.setattr(run, "run_selected_checks", fake_run)
    monkeypatch.setattr(run, "print_summary", lambda _results: 0)

    assert run.main() == 0
    assert selected == list(run.NON_MUTATING_CHECKS)
