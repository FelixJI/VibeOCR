#!/usr/bin/env python3
"""Validate the WinUI feature-parity matrix.

The matrix lives at ``docs/quality/feature-parity.md`` as a Markdown table.
Each data row must declare: feature, Python source of truth, WinUI status,
automation evidence, and pending-human sign-off columns.

Rules:
- Every row must have a non-empty feature name and source-of-truth.
- ``status`` must be one of PASS, PENDING, BLOCKED.
- With ``--require-pass`` (Phase 5 cutover gate), every row must be PASS.
- A BLOCKED row is allowed during Phase 4 but blocks ``--require-pass``.

Usage:
    python tests/parity/validate_matrix.py docs/quality/feature-parity.md
    python tests/parity/validate_matrix.py docs/quality/feature-parity.md --require-pass
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VALID_STATUSES = {"PASS", "PENDING", "BLOCKED"}


def parse_matrix(text: str) -> list[dict[str, str]]:
    """Parse the Markdown table into a list of row dicts.

    The table is detected by a header row whose cells include the canonical
    column names. Pipes inside cell text are not supported (the matrix does
    not use them).
    """
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if header is None:
            # First |...| row is the header; the next is the separator.
            if cells and "功能" in cells[0]:
                header = cells
            continue
        # Skip separator rows (---).
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        if header is None or len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells, strict=False)))
    return rows


def validate(rows: list[dict[str, str]], *, require_pass: bool) -> list[str]:
    errors: list[str] = []
    if not rows:
        errors.append("matrix has no data rows")
        return errors
    for index, row in enumerate(rows, start=1):
        feature = row.get("功能", "").strip()
        source = row.get("PySide 语义真源", "").strip()
        status = row.get("WinUI 状态", "").strip().upper()
        evidence = row.get("自动化证据", "").strip()
        if not feature:
            errors.append(f"row {index}: missing feature name")
        if not source:
            errors.append(f"row {index} ({feature}): missing source-of-truth")
        if status not in VALID_STATUSES:
            errors.append(
                f"row {index} ({feature}): status '{status}' not in {sorted(VALID_STATUSES)}"
            )
        if status == "PASS" and not evidence:
            errors.append(f"row {index} ({feature}): PASS row missing automation evidence")
        if require_pass and status != "PASS":
            errors.append(
                f"row {index} ({feature}): --require-pass set but status is {status}"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path, help="Path to feature-parity.md")
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Require every row to be PASS (Phase 5 cutover gate).",
    )
    args = parser.parse_args(argv)

    text = args.matrix.read_text(encoding="utf-8")
    rows = parse_matrix(text)
    errors = validate(rows, require_pass=args.require_pass)
    if errors:
        print(f"{args.matrix}: {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"{args.matrix}: {len(rows)} row(s) OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
