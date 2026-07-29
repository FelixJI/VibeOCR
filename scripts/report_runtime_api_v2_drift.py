"""Compare the real FastAPI surface with the historical v2 snapshot."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = (
    ROOT
    / "packages/vibeocr-contracts-py/src/vibeocr/protocol/v2/openapi.snapshot.json"
)

for source in (
    ROOT / "packages/vibeocr-contracts-py/src",
    ROOT / "packages/vibeocr-client-py/src",
    ROOT / "packages/vibeocr-backend/src",
):
    sys.path.insert(0, str(source))

from vibeocr.supervisor.app import create_app  # noqa: E402
from vibeocr.supervisor.module import SupervisorModule, SupervisorOptions  # noqa: E402


def _operations(document: dict) -> dict[tuple[str, str], dict]:
    operations = {}
    for path, path_item in document.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() in {
                "get",
                "put",
                "post",
                "delete",
                "patch",
                "head",
                "options",
                "trace",
            }:
                operations[(method.upper(), path)] = operation
    return operations


def actual_openapi() -> dict:
    with tempfile.TemporaryDirectory(prefix="vibeocr-openapi-drift-") as temp:
        module = SupervisorModule(
            options=SupervisorOptions(instance_id="openapi-drift"),
            stager_root=Path(temp),
            executor=MagicMock(),
        )
        return create_app(module, "0" * 64).openapi()


def build_report() -> str:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    actual = actual_openapi()
    snapshot_ops = _operations(snapshot)
    actual_ops = _operations(actual)
    only_actual = sorted(actual_ops.keys() - snapshot_ops.keys())
    only_snapshot = sorted(snapshot_ops.keys() - actual_ops.keys())
    shared = sorted(actual_ops.keys() & snapshot_ops.keys())
    untyped = [
        key
        for key, operation in sorted(actual_ops.items())
        if not any(
            response.get("content")
            for response in operation.get("responses", {}).values()
            if isinstance(response, dict)
        )
    ]
    generated_ids: dict[str, list[tuple[str, str]]] = {}
    for key, operation in actual_ops.items():
        generated_ids.setdefault(operation.get("operationId", "<missing>"), []).append(key)
    duplicate_ids = {
        name: keys for name, keys in generated_ids.items() if len(keys) > 1
    }

    def rows(items: list[tuple[str, str]]) -> str:
        return "\n".join(f"| `{method}` | `{path}` |" for method, path in items) or "| — | — |"

    return f"""# Runtime API v2 drift report

Generated from commit: `{_git_head()}`

The historical `openapi.snapshot.json` is **non-authoritative**. This report compares its operation surface with `create_app(...).openapi()` from the real Backend without starting OCR providers.

## Summary

| Metric | Count |
|---|---:|
| Historical operations | {len(snapshot_ops)} |
| Actual Backend operations | {len(actual_ops)} |
| Shared operations | {len(shared)} |
| Actual-only operations | {len(only_actual)} |
| Snapshot-only operations | {len(only_snapshot)} |
| Operations without a concrete response content schema | {len(untyped)} |
| Duplicate generated operation IDs | {len(duplicate_ids)} |

## Actual-only operations

| Method | Path |
|---|---|
{rows(only_actual)}

## Snapshot-only operations

| Method | Path |
|---|---|
{rows(only_snapshot)}

## Untyped actual operations

| Method | Path |
|---|---|
{rows(untyped)}

## Consequence

Phase 1 must define stable explicit `operationId`, request/response/error schemas, multipart/binary/NDJSON examples and golden cases for the actual surface. It must not copy the historical snapshot.
"""


def _git_head() -> str:
    import subprocess

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(args.output)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
