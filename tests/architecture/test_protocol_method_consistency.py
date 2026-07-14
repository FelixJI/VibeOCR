"""Protocol method-table consistency across three sources of truth.

Per ADR §自动化架构守卫第 6 条 and DUAL_UI_IMPLEMENTATION_PLAN.md §10:
adding a protocol method must update all three surfaces or CI fails:

1. ``contracts/v1/methods.schema.json`` — the JSON-Schema allow-list of method
   names + their request/response payload shapes.
2. ``src/dotnet/VibeOCR.Contracts/RpcMethods.cs`` — C# ``RpcMethods.All``.
3. ``src/vibeocr/worker_host/method_validation.py`` — Python ``PUBLIC_METHODS``.

This test parses each source and asserts the method-name sets are identical.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _schema_methods() -> set[str]:
    schema_path = _REPO_ROOT / "contracts" / "v1" / "methods.schema.json"
    doc = json.loads(schema_path.read_text(encoding="utf-8"))
    props = doc.get("properties", {})
    # Every top-level property is a method name.
    return set(props)


def _python_methods() -> set[str]:
    from vibeocr.worker_host.method_validation import PUBLIC_METHODS

    return set(PUBLIC_METHODS)


def _csharp_methods() -> set[str]:
    """Parse ``RpcMethods.All`` string literals from the C# source.

    We avoid building .NET tooling here: the ``All`` property is an array of
    string constants assigned earlier in the same class, so we collect all
    ``public const string X = "domain.action";`` literals.
    """
    cs_path = _REPO_ROOT / "src" / "dotnet" / "VibeOCR.Contracts" / "RpcMethods.cs"
    text = cs_path.read_text(encoding="utf-8")
    # Match: public const string <Name> = "<method>";
    return set(re.findall(r'=\s*"([a-z]+\.[a-z_]+)"\s*;', text))


def test_schema_python_csharp_method_sets_agree() -> None:
    schema = _schema_methods()
    python = _python_methods()
    csharp = _csharp_methods()
    sources = {
        "contracts/v1/methods.schema.json": schema,
        "Python PUBLIC_METHODS": python,
        "C# RpcMethods": csharp,
    }
    union = schema | python | csharp
    problems: list[str] = []
    for method in sorted(union):
        present_in = {name for name, s in sources.items() if method in s}
        if len(present_in) != len(sources):
            missing = set(sources) - present_in
            problems.append(f"  {method!r} missing from: {sorted(missing)}")
    assert not problems, (
        "协议方法名在三方 source-of-truth 中不一致。\n"
        "新增协议方法必须同时更新 contracts/v1/methods.schema.json、\n"
        "src/dotnet/VibeOCR.Contracts/RpcMethods.cs 和\n"
        "src/vibeocr/worker_host/method_validation.py：\n"
        + "\n".join(problems)
    )


def test_protocol_version_is_one() -> None:
    """The handshake protocol major version is frozen at 1 for the v1 contract."""
    from vibeocr.worker_host.contracts import PROTOCOL_VERSION

    assert PROTOCOL_VERSION == 1, f"protocol version drift: expected 1, got {PROTOCOL_VERSION}"
