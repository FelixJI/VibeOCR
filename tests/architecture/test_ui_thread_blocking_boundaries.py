"""Architecture guard against reintroducing known GUI-thread blocking calls.

This deliberately checks UI entry functions rather than banning synchronous
clients globally: the same clients remain valid inside QThread, QRunnable and
``asyncio.to_thread`` worker bodies.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class _Rule:
    relative_path: str
    function: str
    forbidden_calls: frozenset[str]


_RULES = (
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/pyside/pdf_session_manager.py",
        "start_ocr",
        frozenset({"_ensure_mineru_models_blocking", "reset_cancel"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/pyside/pdf_session_manager.py",
        "_start_mutate",
        frozenset({"reset_cancel"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/pyside/pdf_session_manager.py",
        "_on_deskew_all_done",
        frozenset({"get_model"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/tabs/pdf_tab.py",
        "_render_preview_page",
        frozenset({"render_preview", "detect_text_layers"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/tabs/pdf_tab.py",
        "_on_block_text_edited",
        frozenset({"update_page_block_text", "_render_preview_page"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/tabs/pdf_tab.py",
        "_on_remove_file",
        frozenset({"close_session"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/main_window.py",
        "_try_load_cache",
        frozenset({"is_cache_valid"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/main_window.py",
        "_maybe_prompt_dependency_updates",
        frozenset({"detect_dependency_updates"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/main_window.py",
        "_start_supervisor",
        frozenset({"resolve_use_gpu"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/main_window.py",
        "_on_open_image",
        frozenset({"QPixmap"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/main_window.py",
        "_on_open_file_from_preview",
        frozenset({"QPixmap"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py",
        "_on_update_deps",
        frozenset({"detect_dependency_updates"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py",
        "_on_create_desktop_shortcut",
        frozenset({"_create_windows_shortcut"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py",
        "_on_create_start_menu_shortcut",
        frozenset({"_create_windows_shortcut"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py",
        "_on_refresh_cache_clicked",
        frozenset({"refresh_cache"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py",
        "_on_install_missing",
        frozenset({"resolve_use_gpu"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/batch_recognition_tab.py",
        "_on_export_current",
        frozenset({"export_result"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/batch_recognition_tab.py",
        "_on_export_all",
        frozenset({"export_result"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/widgets/result_view_widget.py",
        "_on_export_file",
        frozenset({"export_result"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/widgets/result_view_widget.py",
        "display_result",
        frozenset({"_render_block", "_build_full_html"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/widgets/preview_widget.py",
        "_load_image_file",
        frozenset({"QPixmap"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/tabs/qrcode_tab.py",
        "_on_save",
        frozenset({"_call_backend_generate_svg", "write_text", "save"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/tabs/qrcode_tab.py",
        "_on_select_image",
        frozenset({"QPixmap"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/tabs/single_recognition_tab.py",
        "_on_file_btn_clicked",
        frozenset({"QPixmap"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/tabs/single_recognition_tab.py",
        "process_file",
        frozenset({"get_runtime_gpu_capability"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/views/tabs/single_recognition_tab.py",
        "_on_ocr_finished",
        frozenset({"loadFromData"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/widgets/screen_capture_overlay.py",
        "_on_copy",
        frozenset({"_pixmap_to_png", "_write_temp_clip_file"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/widgets/screen_capture_overlay.py",
        "_on_save",
        frozenset({"save"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/widgets/batch_file_list_widget.py",
        "add_files",
        frozenset({"any", "insertRow"}),
    ),
    _Rule(
        "apps/vibeocr-pyside/src/vibeocr/utils/single_instance.py",
        "_on_new_connection",
        frozenset({"waitForReadyRead", "waitForBytesWritten"}),
    ),
)


_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class _Function:
    node: _FunctionNode
    qualname: str
    owner_class: str | None
    is_nested: bool


@dataclass(frozen=True)
class _Reference:
    """A callable reference resolved far enough for this finite call graph."""

    leaf: str
    kind: str = "bare"
    class_name: str | None = None


class _DefinitionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.functions: list[_Function] = []
        self._classes: list[str] = []
        self._functions: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._classes.append(node.name)
        self.generic_visit(node)
        self._classes.pop()

    def _visit_function(self, node: _FunctionNode) -> None:
        parts = [*self._classes, *self._functions, node.name]
        self.functions.append(
            _Function(
                node=node,
                qualname=".".join(parts),
                owner_class=".".join(self._classes) or None,
                is_nested=bool(self._functions),
            )
        )
        self._functions.append(node.name)
        self.generic_visit(node)
        self._functions.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


class _ExecutableNodeCollector(ast.NodeVisitor):
    """Collect one eager scope, excluding deferred nested callables."""

    def __init__(self) -> None:
        self.nodes: list[ast.AST] = []

    def generic_visit(self, node: ast.AST) -> None:
        self.nodes.append(node)
        super().generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _scope_nodes(statements: list[ast.stmt]) -> list[ast.AST]:
    collector = _ExecutableNodeCollector()
    for statement in statements:
        collector.visit(statement)
    return collector.nodes


def _import_aliases(nodes: list[ast.AST]) -> dict[str, _Reference]:
    aliases: dict[str, _Reference] = {}
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                local_name = imported.asname or imported.name
                aliases[local_name] = _Reference(
                    imported.name.rsplit(".", 1)[-1], "external"
                )
        elif isinstance(node, ast.Import):
            for imported in node.names:
                local_name = imported.asname or imported.name.split(".", 1)[0]
                aliases[local_name] = _Reference(imported.name, "module")
    return aliases


def _literal_getattr_reference(
    node: ast.AST,
    aliases: dict[str, _Reference],
) -> _Reference | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return None
    receiver = node.args[0]
    leaf = node.args[1].value
    if isinstance(receiver, ast.Name) and receiver.id in {"self", "cls"}:
        return _Reference(leaf, "method")
    if isinstance(receiver, ast.Name):
        base = aliases.get(receiver.id)
        if base is not None and base.kind == "module":
            return _Reference(leaf, "external")
    return _Reference(leaf, "external")


def _reference(
    node: ast.AST,
    aliases: dict[str, _Reference],
) -> _Reference | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, _Reference(node.id))
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name):
            receiver = node.value.id
            if receiver in {"self", "cls"}:
                return _Reference(node.attr, "method")
            base = aliases.get(receiver)
            if base is not None and base.kind in {"external", "module"}:
                return _Reference(node.attr, "external")
            if receiver[:1].isupper():
                return _Reference(node.attr, "class", receiver)
        return _Reference(node.attr, "external")
    return _literal_getattr_reference(node, aliases)


def _assignment_aliases(
    nodes: list[ast.AST],
    seed: dict[str, _Reference],
) -> dict[str, _Reference]:
    aliases = dict(seed)
    pending: list[tuple[str, ast.AST]] = []
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if value is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    pending.append((target.id, value))

    # Resolve chains such as ``a = b; b = blocking`` with a finite pass count.
    for _ in range(len(pending) + 1):
        changed = False
        for name, value in pending:
            resolved = _reference(value, aliases)
            if resolved is not None and aliases.get(name) != resolved:
                aliases[name] = resolved
                changed = True
        if not changed:
            break
    return aliases


def _module_index(tree: ast.Module) -> tuple[list[_Function], dict[str, _Reference]]:
    definitions = _DefinitionCollector()
    definitions.visit(tree)
    module_nodes = _scope_nodes(tree.body)
    module_aliases = _assignment_aliases(module_nodes, _import_aliases(module_nodes))
    return definitions.functions, module_aliases


def _resolve_helper(
    reference: _Reference,
    caller: _Function,
    functions: list[_Function],
) -> _Function | None:
    if reference.kind == "bare":
        candidates = [
            function
            for function in functions
            if function.node.name == reference.leaf
            and (
                (function.owner_class is None and not function.is_nested)
                or (
                    function.is_nested
                    and function.qualname.rsplit(".", 1)[0] == caller.qualname
                )
            )
        ]
    elif reference.kind == "method":
        candidates = [
            function
            for function in functions
            if function.node.name == reference.leaf
            and function.owner_class == caller.owner_class
            and not function.is_nested
        ]
    elif reference.kind == "class":
        candidates = [
            function
            for function in functions
            if function.node.name == reference.leaf
            and function.owner_class is not None
            and function.owner_class.rsplit(".", 1)[-1] == reference.class_name
            and not function.is_nested
        ]
    else:
        return None
    return candidates[0] if len(candidates) == 1 else None


def _inspect_rule(
    tree: ast.Module,
    rule: _Rule,
) -> tuple[list[str], list[str]]:
    functions, module_aliases = _module_index(tree)
    targets = [function for function in functions if function.node.name == rule.function]
    if len(targets) != 1:
        return (
            [
                f"{rule.relative_path}: configured target {rule.function}() "
                f"resolved {len(targets)} times (expected exactly once)"
            ],
            [],
        )

    violations: list[str] = []
    visited: set[str] = set()

    def inspect(function: _Function, chain: tuple[str, ...]) -> None:
        if function.qualname in visited:
            return
        visited.add(function.qualname)
        nodes = _scope_nodes(function.node.body)
        local_imports = _import_aliases(nodes)
        aliases = _assignment_aliases(
            nodes,
            {**module_aliases, **local_imports},
        )
        for node in nodes:
            if not isinstance(node, ast.Call):
                continue
            called = _reference(node.func, aliases)
            if called is None:
                continue
            if called.leaf in rule.forbidden_calls:
                call_chain = "() -> ".join((*chain, called.leaf)) + "()"
                violations.append(
                    f"{rule.relative_path}:{node.lineno} {call_chain}"
                )
                continue
            helper = _resolve_helper(called, function, functions)
            if helper is not None:
                inspect(helper, (*chain, helper.node.name))

    inspect(targets[0], (rule.function,))
    return [], violations


def _inspect_source(source: str, rule: _Rule) -> tuple[list[str], list[str]]:
    tree = ast.parse(source, filename=rule.relative_path)
    return _inspect_rule(tree, rule)


@pytest.fixture(
    params=(
        pytest.param(
            ("""
class Window:
    def renamed_entry(self):
        reset_cancel()
""", "missing"),
            id="renamed-target",
        ),
        pytest.param(
            ("""
class First:
    def entry(self):
        pass

class Second:
    def entry(self):
        pass
""", "ambiguous"),
            id="duplicate-target",
        ),
        pytest.param(
            ("""
from backend import reset_cancel as reset

class Window:
    def entry(self):
        reset()
""", "violation"),
            id="import-alias",
        ),
        pytest.param(
            ("""
def blocking_helper():
    operation = reset_cancel
    operation()

class Window:
    def entry(self):
        blocking_helper()
""", "violation"),
            id="helper-and-assignment-alias",
        ),
        pytest.param(
            ("""
class Window:
    def entry(self):
        def local_helper():
            reset_cancel()
        local_helper()
""", "violation"),
            id="called-local-helper",
        ),
        pytest.param(
            ("""
class Window:
    def entry(self):
        getattr(self.backend, "reset_cancel")()
""", "violation"),
            id="literal-getattr",
        ),
    ),
)
def _guard_mutation(request: pytest.FixtureRequest) -> tuple[str, str]:
    return request.param


def test_guard_rejects_mutations(_guard_mutation: tuple[str, str]) -> None:
    source, expected = _guard_mutation
    rule = _Rule("mutation.py", "entry", frozenset({"reset_cancel"}))
    errors, violations = _inspect_source(source, rule)

    if expected in {"missing", "ambiguous"}:
        assert errors
        assert not violations
    else:
        assert not errors
        assert violations


def test_guard_does_not_follow_helpers_submitted_to_workers() -> None:
    source = """
def background_helper():
    reset_cancel()

class Window:
    def entry(self):
        self.jobs.start(background_helper)
"""
    rule = _Rule("background.py", "entry", frozenset({"reset_cancel"}))

    errors, violations = _inspect_source(source, rule)

    assert not errors
    assert not violations


def test_known_gui_entrypoints_do_not_call_blocking_primitives() -> None:
    configuration_errors: list[str] = []
    violations: list[str] = []
    parsed: dict[str, ast.Module] = {}

    for rule in _RULES:
        tree = parsed.get(rule.relative_path)
        if tree is None:
            path = _ROOT / rule.relative_path
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            parsed[rule.relative_path] = tree
        errors, found = _inspect_rule(tree, rule)
        configuration_errors.extend(errors)
        violations.extend(found)

    assert not configuration_errors, (
        "Gate 0 规则配置失效；目标函数必须存在且只能唯一解析：\n"
        + "\n".join(f"  {item}" for item in configuration_errors)
    )
    assert not violations, (
        "以下 GUI 入口仍直接调用已知阻塞原语；请把调用移入明确 worker 边界：\n"
        + "\n".join(f"  {item}" for item in violations)
    )
