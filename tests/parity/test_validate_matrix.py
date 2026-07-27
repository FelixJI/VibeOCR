"""Tests for the feature-parity matrix validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.parity.validate_matrix import main, parse_matrix, validate

VALID_TABLE = """\
# 矩阵

| 功能 | PySide 语义真源 | WinUI 状态 | 自动化证据 | 待人工签核 |
|---|---|---|---|---|
| 单图输入 | SingleRecognitionTab | PASS | RecognitionViewModelTests | 多显示器 |
| 批量识别 | BatchRecognitionTab | PASS | BatchViewModelTests | — |
"""

MISSING_EVIDENCE = """\
| 功能 | PySide 语义真源 | WinUI 状态 | 自动化证据 | 待人工签核 |
|---|---|---|---|---|
| 单图输入 | SingleRecognitionTab | PASS |  | 多显示器 |
"""

BAD_STATUS = """\
| 功能 | PySide 语义真源 | WinUI 状态 | 自动化证据 | 待人工签核 |
|---|---|---|---|---|
| 单图输入 | SingleRecognitionTab | DONE | x | — |
"""


def test_parse_matrix_returns_rows() -> None:
    rows = parse_matrix(VALID_TABLE)
    assert len(rows) == 2
    assert rows[0]["功能"] == "单图输入"
    assert rows[0]["WinUI 状态"] == "PASS"


def test_validate_accepts_valid_rows() -> None:
    rows = parse_matrix(VALID_TABLE)
    assert validate(rows, require_pass=False) == []
    assert validate(rows, require_pass=True) == []


def test_validate_rejects_pass_without_evidence() -> None:
    rows = parse_matrix(MISSING_EVIDENCE)
    errors = validate(rows, require_pass=False)
    assert any("missing automation evidence" in e for e in errors)


def test_validate_rejects_unknown_status() -> None:
    rows = parse_matrix(BAD_STATUS)
    errors = validate(rows, require_pass=False)
    assert any("not in" in e for e in errors)


def test_require_pass_rejects_pending(tmp_path: Path) -> None:
    table = VALID_TABLE.replace("PASS | BatchViewModelTests", "PENDING | Phase 4.x")
    path = tmp_path / "matrix.md"
    path.write_text(table, encoding="utf-8")
    assert main([str(path), "--require-pass"]) == 1


def test_main_exits_zero_on_valid_matrix(tmp_path: Path) -> None:
    path = tmp_path / "matrix.md"
    path.write_text(VALID_TABLE, encoding="utf-8")
    assert main([str(path)]) == 0


def test_main_exits_nonzero_on_empty_matrix(tmp_path: Path) -> None:
    path = tmp_path / "matrix.md"
    path.write_text("# no table here\n", encoding="utf-8")
    assert main([str(path)]) == 1


def test_real_matrix_parses() -> None:
    """The checked-in docs/quality/feature-parity.md must parse and validate."""
    repo_root = Path(__file__).parents[2]
    matrix = repo_root / "docs" / "quality" / "feature-parity.md"
    if not matrix.exists():
        pytest.skip("feature-parity.md not found")
    rows = parse_matrix(matrix.read_text(encoding="utf-8"))
    assert len(rows) >= 4
    errors = validate(rows, require_pass=False)
    assert errors == []


def test_real_matrix_does_not_claim_winui_table_edit_from_pyside_only_evidence(
) -> None:
    repo_root = Path(__file__).parents[2]
    matrix = repo_root / "docs" / "quality" / "feature-parity.md"
    rows = parse_matrix(matrix.read_text(encoding="utf-8"))
    table_edit = next(row for row in rows if row["功能"].startswith("表格编辑"))

    assert table_edit["WinUI 状态"] == "PENDING"
    assert "PySide" in table_edit["自动化证据"]
    assert "WinUI 尚无" in table_edit["自动化证据"]
