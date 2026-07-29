"""Compare the real FastAPI surface with the historical v2 snapshot."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = (
    ROOT
    / "packages/vibeocr-contracts-py/src/vibeocr/protocol/v2/openapi.snapshot.json"
)
PYTHON_CLIENT_FILES = (
    ROOT / "packages/vibeocr-client-py/src/vibeocr/supervisor/client.py",
    ROOT / "packages/vibeocr-client-py/src/vibeocr/supervisor/pdf_client.py",
)
CSHARP_CLIENT_FILES = tuple(
    ROOT.joinpath("src/dotnet/VibeOCR.Platform/Inference").glob("*HttpClient.cs")
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


def _placeholder(expression: ast.AST) -> str:
    text = ast.unparse(expression)
    lowered = text.lower()
    if "job" in lowered:
        return "{job_id}"
    if "session" in lowered or text == "sid":
        return "{session_id}"
    return "{" + text + "}"


def _python_url(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value
    elif isinstance(node, ast.JoinedStr):
        value = "".join(
            part.value
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
            else _placeholder(part.value)
            if isinstance(part, ast.FormattedValue)
            else ""
            for part in node.values
        )
    else:
        return None
    return value.split("?", 1)[0] if value.startswith("/v2/") else None


def python_client_operations() -> set[tuple[str, str]]:
    operations: set[tuple[str, str]] = set()
    for path in PYTHON_CLIENT_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            call_name = node.func.attr.lower()
            method: str | None = None
            url_node: ast.AST | None = None
            if call_name in {"get", "post", "put", "delete"} and node.args:
                method = call_name.upper()
                url_node = node.args[0]
            elif call_name == "request" and len(node.args) >= 2:
                if isinstance(node.args[0], ast.Constant):
                    method = str(node.args[0].value).upper()
                    url_node = node.args[1]
            elif call_name == "_mutation" and node.args:
                if isinstance(node.args[0], ast.Constant):
                    method = "POST"
                    operations.add(
                        (
                            method,
                            f"/v2/pdf/sessions/{{session_id}}/{node.args[0].value}",
                        )
                    )
            if method and url_node:
                url = _python_url(url_node)
                if url and "{op}" not in url:
                    operations.add((method, url))
    return operations


_CSHARP_CALL = re.compile(
    r"\b(?P<method>Get|Post|Put|Delete)Async\s*\(\s*"
    r'\$?"(?P<url>/v2/[^"]+)"',
    re.DOTALL,
)


def _normalize_csharp_url(url: str) -> str:
    url = re.sub(
        r"\{Uri\.EscapeDataString\((?P<name>[^)]+)\)\}",
        lambda match: _csharp_placeholder(match.group("name")),
        url,
    )
    url = re.sub(
        r"\{(?P<name>[^{}]+)\}",
        lambda match: _csharp_placeholder(match.group("name")),
        url,
    )
    return url.split("?", 1)[0]


def _csharp_placeholder(name: str) -> str:
    lowered = name.lower()
    if "job" in lowered:
        return "{job_id}"
    if "session" in lowered:
        return "{session_id}"
    return "{" + name + "}"


def csharp_client_operations() -> set[tuple[str, str]]:
    operations: set[tuple[str, str]] = set()
    for path in CSHARP_CLIENT_FILES:
        text = path.read_text(encoding="utf-8")
        for match in _CSHARP_CALL.finditer(text):
            operations.add(
                (
                    match.group("method").upper(),
                    _normalize_csharp_url(match.group("url")),
                )
            )
    return operations


def build_report() -> str:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    actual = actual_openapi()
    snapshot_ops = _operations(snapshot)
    actual_ops = _operations(actual)
    python_ops = python_client_operations()
    csharp_ops = csharp_client_operations()
    client_ops = python_ops | csharp_ops
    only_actual = sorted(actual_ops.keys() - snapshot_ops.keys())
    only_snapshot = sorted(snapshot_ops.keys() - actual_ops.keys())
    client_only = sorted(client_ops - actual_ops.keys())
    backend_without_client = sorted(actual_ops.keys() - client_ops)
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
| Python Runtime Client operations | {len(python_ops)} |
| C# Next Runtime Client operations | {len(csharp_ops)} |
| Shared operations | {len(shared)} |
| Actual-only operations | {len(only_actual)} |
| Snapshot-only operations | {len(only_snapshot)} |
| Client-only operations | {len(client_only)} |
| Backend operations not observed in either client | {len(backend_without_client)} |
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

## Client-only operations

| Method | Path |
|---|---|
{rows(client_only)}

## Backend operations not observed in either client

| Method | Path |
|---|---|
{rows(backend_without_client)}

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
