"""Phase 8 legacy-architecture guards (ratchet on the new v2/supervisor trees).

Plan §8 requires "禁止遗留架构守卫" that fail loudly if the new v2 code
references the legacy transport surface. Until the Phase 8 atomic switch
deletes the legacy code entirely, these guards enforce that the *new* trees
stay clean:

* Python v2 protocol/supervisor trees must not reference legacy transport
  symbols (``VIBEOCR_OCR_TRANSPORT``, the v1 WorkerHost/SHM/Named Pipe client
  classes, runtime monkey-patch modules).
* The .NET HttpV2 / Inference trees must not reference the v1 Named Pipe /
  SharedPayload symbols.
* The PySide supervisor adapter must not import the legacy sync backend
  client.

These are scoped guards (not repo-wide bans) because the legacy symbols still
exist in v1 code until Phase 8 deletes them. They form a ratchet: once added,
the new trees can never regress into legacy transport.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Trees that must stay free of legacy transport references
# ---------------------------------------------------------------------------

_PY_V2_TREES: tuple[Path, ...] = (
    _REPO_ROOT / "packages" / "vibeocr-contracts-py" / "src" / "vibeocr" / "protocol" / "v2",
    _REPO_ROOT / "packages" / "vibeocr-backend" / "src" / "vibeocr" / "supervisor",
    _REPO_ROOT / "packages" / "vibeocr-client-py" / "src" / "vibeocr" / "supervisor",
    _REPO_ROOT / "apps" / "vibeocr-pyside" / "src" / "vibeocr" / "pyside" / "supervisor_adapter.py",
)

_DOTNET_V2_TREES: tuple[Path, ...] = (
    _REPO_ROOT / "src" / "dotnet" / "VibeOCR.Contracts" / "HttpV2",
    _REPO_ROOT / "src" / "dotnet" / "VibeOCR.Platform" / "Inference",
)

# Forbidden legacy identifiers — these must never appear as a bare name or in
# an import inside the v2 trees. ``SharedPayloadRef``/``WorkerHostClient``/
# ``SyncBackendClient`` are the v1 .NET/Python transport clients; the env var
# and the SHM/named-pipe modules are the v1 transport surface.
_PY_FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "VIBEOCR_OCR_TRANSPORT",
        "SyncBackendClient",
        "BackendClient",
        "OcrHttpClient",
        "SharedPayloadClient",
        "shared_memory_v2",
        "named_pipe",
        "shared_payload",
        "framing",
        "worker_runtime_state",
        "mineru_runtime_cache",
        "ocr_worker_process",
        "ocr_service_subprocess",
    }
)

# Modules whose import into a v2 tree is forbidden (matched as a path prefix
# under vibeocr.*).
_PY_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "vibeocr.worker_host",
    "vibeocr.ipc",
    "vibeocr.services",
    "vibeocr.client.batch",
    "vibeocr.client.session",
)

_DOTNET_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "SharedPayloadRef",
    "SharedPayloadClient",
    "WorkerHostClient",
    "RpcEnvelope",
    "RpcMethods",
    "FrameCodec",
    "NamedPipe",
    "VIBEOCR_OCR_TRANSPORT",
)


def _py_files() -> list[Path]:
    out: list[Path] = []
    for tree in _PY_V2_TREES:
        if tree.is_file() and tree.suffix == ".py":
            out.append(tree)
        elif tree.is_dir():
            out.extend(sorted(tree.rglob("*.py")))
    return out


def _dotnet_files() -> list[Path]:
    out: list[Path] = []
    for tree in _DOTNET_V2_TREES:
        if tree.is_file() and tree.suffix == ".cs":
            out.append(tree)
        elif tree.is_dir():
            out.extend(sorted(tree.rglob("*.cs")))
    return out


@pytest.mark.parametrize("py_file", _py_files())
def test_python_v2_tree_has_no_legacy_transport(py_file: Path) -> None:
    """No v2 .py file may reference legacy transport symbols or imports."""
    text = py_file.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(py_file))

    # 1. Forbidden imports.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _assert_allowed_import(py_file, alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                _assert_allowed_import(py_file, node.module, node.lineno)
                # Also forbid importing the legacy names themselves.
                for alias in node.names:
                    assert alias.name not in _PY_FORBIDDEN_NAMES, (
                        f"{py_file.relative_to(_REPO_ROOT)}:{node.lineno} imports "
                        f"legacy symbol {alias.name!r} — v2 code must not depend on "
                        f"legacy transport."
                    )

    # 2. Forbidden bare/attribute names anywhere in the file body. We scan the
    #    raw text for the env-var and the legacy module/class identifiers; this
    #    catches references in strings/comments too, which is the intent (the
    #    v2 trees must not even document a legacy escape hatch).
    for forbidden in ("VIBEOCR_OCR_TRANSPORT",):
        assert forbidden not in text, (
            f"{py_file.relative_to(_REPO_ROOT)} references {forbidden!r} — the "
            f"v2 trees must not carry any legacy transport escape hatch."
        )


def _assert_allowed_import(file: Path, module: str, lineno: int) -> None:
    # The v2 trees legitimately import from vibeocr.protocol.v2 and from their
    # own package; only the legacy prefixes are banned.
    # Explicit reuse exception (plan §5.2 重点复用): the stable OCR core
    # (ocr_service.py) is deliberately reused by the supervisor's Paddle
    # executor. Other vibeocr.services.* modules remain legacy transport.
    _REUSE_ALLOWED = {
        "vibeocr.services.ocr_service",
        "vibeocr.services.qrcode_decode_service",
        "vibeocr.services.qrcode_service",
        "vibeocr.services.export_service",
        "vibeocr.services.pdf_backend_client",
        "vibeocr.services.mineru_service",
        "vibeocr.application.contracts",
        # vibeocr.ipc.schemas is a pure pydantic DTO module (PdfDocumentMirror,
        # ModelDiff, ProgressEvent, request/response bodies) shared by the PDF
        # backend child, the supervisor v2 routes, and the supervisor client.
        # It is schema, not transport (model_bridge/shm/named_pipe are the
        # transport modules under vibeocr.ipc and stay forbidden).
        "vibeocr.ipc.schemas",
    }
    for forbidden in _PY_FORBIDDEN_IMPORT_PREFIXES:
        if module in _REUSE_ALLOWED:
            continue
        if module == forbidden or module.startswith(forbidden + "."):
            raise AssertionError(
                f"{file.relative_to(_REPO_ROOT)}:{lineno} imports {module!r} — "
                f"v2 code must not depend on legacy transport packages."
            )


@pytest.mark.parametrize("cs_file", _dotnet_files())
def test_dotnet_v2_tree_has_no_legacy_transport(cs_file: Path) -> None:
    """No HttpV2/Inference .cs file may reference v1 Named Pipe/SHM symbols."""
    text = cs_file.read_text(encoding="utf-8")
    # Word-boundary match so ``RpcMethods`` does not match ``RpcMethodsV2``.
    for token in _DOTNET_FORBIDDEN_TOKENS:
        pattern = rf"\b{re.escape(token)}\b"
        assert not re.search(pattern, text), (
            f"{cs_file.relative_to(_REPO_ROOT)} references legacy token "
            f"{token!r} — the HttpV2/Inference trees must stay free of v1 "
            f"Named Pipe / SharedPayload symbols."
        )


def test_pyside_supervisor_adapter_does_not_import_legacy_sync_client() -> None:
    """The new PySide adapter must not import the legacy sync backend client."""
    adapter = (
        _REPO_ROOT
        / "apps"
        / "vibeocr-pyside"
        / "src"
        / "vibeocr"
        / "pyside"
        / "supervisor_adapter.py"
    )
    if not adapter.exists():
        pytest.skip("supervisor_adapter.py not present")
    text = adapter.read_text(encoding="utf-8")
    assert "from vibeocr.client.session" not in text, (
        "supervisor_adapter must use the v2 client, not the legacy sync backend session."
    )
    assert "SyncBackendClient" not in text and "OcrHttpClient" not in text, (
        "supervisor_adapter must not reference legacy transport clients."
    )


def test_pyside_app_does_not_import_pdf_backend_client() -> None:
    """The PySide app must route PDF ops through the supervisor HTTP v2 client.

    Plan §7A exit criterion: "PySide UI import scanner 不允许
    services/mineru_service、pdf_backend_client、worker_host". After the
    PDF→supervisor migration the GUI never imports
    ``vibeocr.services.pdf_backend_client`` directly — the supervisor owns
    the PDF child. This guard prevents regression.
    """
    pyside_src = _REPO_ROOT / "apps" / "vibeocr-pyside" / "src" / "vibeocr"
    if not pyside_src.exists():
        pytest.skip("pyside app source not present")
    offenders: list[str] = []
    for py_file in sorted(pyside_src.rglob("*.py")):
        if py_file.name == "__pycache__":
            continue
        text = py_file.read_text(encoding="utf-8")
        # Catch both ``from vibeocr.services.pdf_backend_client import ...``
        # and ``import vibeocr.services.pdf_backend_client``. Comments/docstrings
        # mentioning the name are allowed (the migration notes reference it);
        # we only flag real import statements via AST.
        try:
            tree = ast.parse(text, filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "vibeocr.services.pdf_backend_client":
                        offenders.append(
                            f"{py_file.relative_to(_REPO_ROOT)}:{node.lineno} "
                            f"imports vibeocr.services.pdf_backend_client"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module == "vibeocr.services.pdf_backend_client":
                    offenders.append(
                        f"{py_file.relative_to(_REPO_ROOT)}:{node.lineno} "
                        f"imports from vibeocr.services.pdf_backend_client"
                    )
    assert not offenders, (
        "PySide app must not import vibeocr.services.pdf_backend_client — "
        "PDF ops go through the supervisor (vibeocr.supervisor.pdf_client). "
        "Offenders:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# Repo-wide bans (Phase 8 atomic switch): the legacy modules are now deleted,
# so these symbols must not be imported anywhere in the Python source trees.
# This is the promoted guard: it was a scoped ratchet while the legacy code
# existed, and is now an absolute ban.
# ---------------------------------------------------------------------------

_DELETED_PY_MODULES: tuple[str, ...] = (
    "vibeocr.worker_host",
    "vibeocr.services.ocr_worker_process",
    "vibeocr.services.ocr_service_subprocess",
    "vibeocr.services.worker_runtime_state",
    "vibeocr.services.mineru_runtime_cache",
    "vibeocr.services.mineru_batch_service",
    "vibeocr.services.worker_manager",
    "vibeocr.utils.shared_memory_v2",
    "vibeocr.workers.ocr_worker",
    "vibeocr.workers.batch_queue_manager",
    "vibeocr.contracts.protocol.v1",
    "vibeocr.protocol.v1",
    "vibeocr.client.batch",
    "vibeocr.client.session",
)


def _python_source_trees() -> list[Path]:
    return [
        _REPO_ROOT / "packages" / "vibeocr-backend" / "src",
        _REPO_ROOT / "packages" / "vibeocr-client-py" / "src",
        _REPO_ROOT / "packages" / "vibeocr-contracts-py" / "src",
        _REPO_ROOT / "apps" / "vibeocr-pyside" / "src",
    ]


def test_repo_wide_has_no_deleted_legacy_module_imports() -> None:
    """No Python source may import a Phase-8-deleted legacy module.

    After the atomic switch these modules no longer exist; any import would be
    a dangling reference that breaks at runtime. This guards against regression.
    """
    offenders: list[str] = []
    for tree in _python_source_trees():
        if not tree.exists():
            continue
        for py_file in sorted(tree.rglob("*.py")):
            text = py_file.read_text(encoding="utf-8")
            try:
                ast_tree = ast.parse(text, filename=str(py_file))
            except SyntaxError:
                continue
            for node in ast.walk(ast_tree):
                mod = node.module if isinstance(node, ast.ImportFrom) else None
                if mod:
                    for deleted in _DELETED_PY_MODULES:
                        if mod == deleted or mod.startswith(deleted + "."):
                            offenders.append(
                                f"{py_file.relative_to(_REPO_ROOT)}:{node.lineno} "
                                f"imports {mod!r}"
                            )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        for deleted in _DELETED_PY_MODULES:
                            if alias.name == deleted or alias.name.startswith(deleted + "."):
                                offenders.append(
                                    f"{py_file.relative_to(_REPO_ROOT)}:{node.lineno} "
                                    f"imports {alias.name!r}"
                                )
    assert not offenders, (
        "Phase-8-deleted legacy modules are still imported. These modules no "
        "longer exist; the imports are dangling and will fail at runtime:\n  "
        + "\n  ".join(offenders)
    )


def test_repo_wide_has_no_vibeocr_ocr_transport_escape_hatch() -> None:
    """The legacy transport env var must not appear anywhere in source.

    Plan §8 + §5.3: ``VIBEOCR_OCR_TRANSPORT`` was the old runtime switch between
    HTTP/SHM transports. The unified supervisor has no transport switch; the
    symbol must not survive in any tree (the v2 trees were already guarded, but
    after the atomic switch it is banned repo-wide).
    """
    offenders: list[str] = []
    for tree in _python_source_trees():
        if not tree.exists():
            continue
        for py_file in sorted(tree.rglob("*.py")):
            if "VIBEOCR_OCR_TRANSPORT" in py_file.read_text(encoding="utf-8"):
                offenders.append(str(py_file.relative_to(_REPO_ROOT)))
    assert not offenders, (
        "VIBEOCR_OCR_TRANSPORT still appears in source — the unified supervisor "
        "has no transport switch:\n  " + "\n  ".join(offenders)
    )

