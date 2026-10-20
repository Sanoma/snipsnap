"""Name-based dead code detection (unused functions, methods, classes, imports).

A definition is reported when its name is not reachable from live code: code
outside any tracked definition (module level, decorated functions, dunder
methods, ...) is live, and a definition's body only counts once its own name is
live. So recursive functions, and groups of functions that only call each other,
are reported when nothing else uses them. This is deliberately conservative: two
unrelated things sharing a name hide each other, but there are few false alarms.
Dynamic usage (getattr, plugins, framework hooks) cannot be seen; decorated
definitions, dunder methods, entry points and short string constants are treated
as used.
"""

import ast
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from collections.abc import Set as AbstractSet

from .models import DeadItem, DeadKind
from .project import Module

_IDENT = re.compile(r"[A-Za-z_]\w*")
_MAX_STRING_REF_LEN = 100
_TEST_HOOK_PREFIXES = ("test", "Test", "setup", "teardown", "pytest_")


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _docstring_node(node: ast.AST) -> ast.Constant | None:
    body = getattr(node, "body", None)
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value
    return None


def _walk_pruned(root: ast.AST, skip: AbstractSet[int]) -> Iterator[ast.AST]:
    """Walk over the abstract syntax tree; skips over nodes of which IDs
    are part of the skip set."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node is not root and id(node) in skip:
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def collect_references(root: ast.AST, skip: AbstractSet[int] = frozenset()) -> Counter[str]:
    """Count name references under ``root``, ignoring the subtrees whose node id is in ``skip``.

    ``root`` itself is never skipped, so a definition's own references can be counted
    while the definitions nested inside it are left out.
    """
    refs: Counter[str] = Counter()
    docstrings = {
        id(d)
        for n in ast.walk(root)
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and (d := _docstring_node(n)) is not None
    }
    for node in _walk_pruned(root, skip):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Store):
            refs[node.id] += 1
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            refs[node.attr] += 1
        elif isinstance(node, ast.ImportFrom):
            refs.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            refs.update(a.name.split(".")[0] for a in node.names)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and len(node.value) <= _MAX_STRING_REF_LEN
        ):
            # forward-reference annotations, __all__, getattr(obj, "name")
            refs.update(_IDENT.findall(node.value))
    return refs


class _Definitions(ast.NodeVisitor):
    """Collect module- and class-level definitions (not nested functions)."""

    def __init__(self, module: Module, include_overrides: bool):
        self.module = module
        self.include_overrides = include_overrides
        self.found: list[tuple[DeadItem, ast.AST]] = []
        self._class_stack: list[ast.ClassDef] = []

    def _record(
        self, kind: DeadKind, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> None:
        qualname = ".".join([*(c.name for c in self._class_stack), node.name])
        item = DeadItem(
            kind=kind,
            name=node.name,
            qualname=qualname,
            path=self.module.path.as_posix(),
            line=node.lineno,
            end_line=node.end_lineno or node.lineno,
        )
        self.found.append((item, node))

    def _skippable(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
        name = node.name
        if _is_dunder(name) or node.decorator_list:
            return True
        return self.module.is_test and name.startswith(_TEST_HOOK_PREFIXES)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if not self._skippable(node):
            self._record("class", node)
        self._class_stack.append(node)
        for child in node.body:
            self.visit(child)
        self._class_stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # we never descend into function bodies, so a non-empty stack means "method"
        if not self._skippable(node):
            if not self._class_stack:
                self._record("function", node)
            elif self.include_overrides or not self._class_stack[-1].bases:
                self._record("method", node)
        # nested functions are not tracked

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


def definitions(module: Module, include_overrides: bool = False) -> list[tuple[DeadItem, ast.AST]]:
    collector = _Definitions(module, include_overrides)
    collector.visit(module.tree)
    return collector.found


def _unused_imports(module: Module) -> list[DeadItem]:
    if module.path.name == "__init__.py":
        return []  # imports there are usually re-exports
    bound: list[tuple[str, ast.stmt]] = []
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname and a.asname == a.name:
                    continue  # explicit re-export idiom
                bound.append(((a.asname or a.name.split(".")[0]), node))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for a in node.names:
                if a.name == "*" or (a.asname and a.asname == a.name):
                    continue
                bound.append(((a.asname or a.name), node))
    if not bound:
        return []
    used = _collect_names_used_in_file(module)
    return [
        DeadItem(
            kind="import",
            name=name,
            qualname=name,
            path=module.path.as_posix(),
            line=stmt.lineno,
            end_line=stmt.end_lineno or stmt.lineno,
        )
        for name, stmt in bound
        if name not in used
    ]


def _collect_names_used_in_file(module: Module) -> set[str]:
    used: set[str] = set()
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Store):
            used.add(node.id)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and len(node.value) <= _MAX_STRING_REF_LEN
        ):
            used.update(_IDENT.findall(node.value))
    return used


def live_names(
    modules: list[Module],
    defs: list[tuple[DeadItem, ast.AST]],
    seeds: AbstractSet[str],
) -> set[str]:
    """Names reachable from the code of ``modules`` that lies outside ``defs``.

    A definition contributes the names it references only once its own name is live.
    """
    skip = {id(node) for _, node in defs}
    live: set[str] = set(seeds)
    for module in modules:
        live |= collect_references(module.tree, skip).keys()

    by_name: defaultdict[str, list[ast.AST]] = defaultdict(list)
    for item, node in defs:
        by_name[item.name].append(node)

    expanded: set[int] = set()
    pending = list(live)
    while pending:
        for node in by_name.get(pending.pop(), ()):
            if id(node) in expanded:
                continue
            expanded.add(id(node))
            for name in collect_references(node, skip):
                if name not in live:
                    live.add(name)
                    pending.append(name)
    return live


def find_dead_code(
    modules: list[Module],
    entry_points: set[str],
    include_overrides: bool = False,
) -> list[DeadItem]:
    defs = [pair for module in modules for pair in definitions(module, include_overrides)]
    seeds = entry_points | {"main"}
    live = live_names(modules=modules, defs=defs, seeds=seeds)

    items = [item for item, _ in defs if item.name not in live]
    for module in modules:
        items.extend(_unused_imports(module))
    return sorted(items, key=lambda i: (i.path, i.line, i.name))
