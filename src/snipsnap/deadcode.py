"""Name-based dead code detection (unused functions, methods, classes, imports, variables).

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
from collections import defaultdict
from collections.abc import Iterator
from collections.abc import Set as AbstractSet
from pathlib import Path

from .astutils import DefNode, collect_loaded_names, collect_references, end_line
from .models import DeadItem, DeadKind
from .project import Module

_TEST_HOOK_PREFIXES = ("test", "Test", "setup", "teardown", "pytest_")


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


class _Definitions(ast.NodeVisitor):
    """Collect module- and class-level definitions (not nested functions)."""

    def __init__(self, module: Module, include_overrides: bool):
        self.module = module
        self.include_overrides = include_overrides
        self.found: list[tuple[DeadItem, ast.AST]] = []
        self._class_stack: list[ast.ClassDef] = []

    def _record(self, kind: DeadKind, node: DefNode) -> None:
        qualname = ".".join([*(c.name for c in self._class_stack), node.name])
        item = DeadItem(
            kind=kind,
            name=node.name,
            qualname=qualname,
            path=self.module.path.as_posix(),
            line=node.lineno,
            end_line=end_line(node),
        )
        self.found.append((item, node))

    def _skippable(self, node: DefNode) -> bool:
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
    if module.ignored:
        return []  # untracked definitions are live roots, so what they call stays alive
    collector = _Definitions(module, include_overrides)
    collector.visit(module.tree)
    return collector.found


def _unused_imports(module: Module) -> list[DeadItem]:
    if module.path.name == "__init__.py" or module.ignored:
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
    used = collect_loaded_names(module.tree)
    return [
        DeadItem(
            kind="import",
            name=name,
            qualname=name,
            path=module.path.as_posix(),
            line=stmt.lineno,
            end_line=end_line(stmt),
        )
        for name, stmt in bound
        if name not in used
    ]


def _module_level_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Statements that run at import time: nested in if/try/with/for, but not in defs."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for child in ast.iter_child_nodes(stmt):
            if isinstance(child, ast.stmt):
                yield from _module_level_statements([child])
            elif isinstance(child, (ast.ExceptHandler, ast.match_case)):
                yield from _module_level_statements(child.body)


def _dotted_parts(path: Path) -> tuple[str, ...]:
    parts = path.with_suffix("").parts
    return parts[:-1] if parts and parts[-1] == "__init__" else parts


def _imported_candidates(module: Module) -> Iterator[tuple[tuple[str, ...], bool]]:
    """Dotted names ``module`` may import, as (parts, exact); absolute ones match loosely."""
    package = module.path.parent.parts
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield tuple(a.name.split(".")), False
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(package) - (node.level - 1)
                if keep < 0:
                    continue
                base, exact = package[:keep], True
            else:
                base, exact = (), False
            base += tuple(node.module.split(".")) if node.module else ()
            if base:
                yield base, exact
            for a in node.names:
                if a.name != "*":
                    yield (*base, a.name), exact


def _importers(modules: list[Module]) -> dict[Path, list[Module]]:
    """For each module, the modules that import it (or one of its submodule names)."""
    by_last: defaultdict[str, list[Module]] = defaultdict(list)
    for m in modules:
        parts = _dotted_parts(m.path)
        if parts:
            by_last[parts[-1]].append(m)
    result: defaultdict[Path, list[Module]] = defaultdict(list)
    for importer in modules:
        seen: set[Path] = set()
        for candidate, exact in _imported_candidates(importer):
            for target in by_last.get(candidate[-1], ()):
                parts = _dotted_parts(target.path)
                if exact:
                    matches = parts == candidate
                else:  # either may carry extra leading packages (src layout, scan root)
                    matches = (
                        parts[-len(candidate) :] == candidate or candidate[-len(parts) :] == parts
                    )
                if matches and target.path not in seen:
                    seen.add(target.path)
                    result[target.path].append(importer)
    return result


def _unused_variables(
    module: Module,
    live: AbstractSet[str],
    visible: AbstractSet[str],
) -> list[DeadItem]:
    """``visible`` holds the names referenced by ``module`` and by the modules importing it.

    Unlike functions, a variable must also be referenced from a module that can see it,
    so an unrelated variable with the same name elsewhere does not keep it alive.
    """
    if module.path.name == "__init__.py" or module.ignored:
        return []  # variables there are usually the package's public API
    items: list[DeadItem] = []
    for stmt in _module_level_statements(module.tree.body):
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
        else:
            continue  # tuple unpacking, augmented and bare annotated assignments are not tracked
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if name == "_" or _is_dunder(name) or (name in live and name in visible):
                continue
            if module.is_test and (name.startswith(_TEST_HOOK_PREFIXES) or name == "pytestmark"):
                continue
            items.append(
                DeadItem(
                    kind="variable",
                    name=name,
                    qualname=name,
                    path=module.path.as_posix(),
                    line=stmt.lineno,
                    end_line=end_line(stmt),
                )
            )
    return items


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
    refs = {m.path: collect_references(m.tree).keys() for m in modules}
    importers = _importers(modules)
    for module in modules:
        items.extend(_unused_imports(module))
        visible = set(seeds) | refs[module.path]
        for importer in importers.get(module.path, ()):
            visible |= refs[importer.path]
        items.extend(_unused_variables(module, live, visible))
    return sorted(items, key=lambda i: (i.path, i.line, i.name))
