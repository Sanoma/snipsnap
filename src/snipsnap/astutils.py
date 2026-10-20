"""Small AST helpers shared by the analysers."""

import ast
import re
from collections import Counter
from collections.abc import Iterator
from collections.abc import Set as AbstractSet

FuncNode = ast.FunctionDef | ast.AsyncFunctionDef
DefNode = FuncNode | ast.ClassDef

_IDENT = re.compile(r"[A-Za-z_]\w*")
_MAX_STRING_REF_LEN = 100


def docstring_node(node: ast.AST) -> ast.Constant | None:
    body = getattr(node, "body", None)
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value
    return None


def start_line(node: DefNode) -> int:
    """First line including decorators, so a deletion range is complete."""
    return min([node.lineno, *(d.lineno for d in node.decorator_list)])


def end_line(node: ast.stmt) -> int:
    return node.end_lineno or node.lineno


def string_idents(node: ast.AST) -> list[str]:
    """Identifiers inside a short string constant (forward refs, ``__all__``, ``getattr``)."""
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and len(node.value) <= _MAX_STRING_REF_LEN
    ):
        return _IDENT.findall(node.value)
    return []


def walk_pruned(root: ast.AST, skip: AbstractSet[int]) -> Iterator[ast.AST]:
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
        and (d := docstring_node(n)) is not None
    }
    for node in walk_pruned(root, skip):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Store):
            refs[node.id] += 1
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            refs[node.attr] += 1
        elif isinstance(node, ast.ImportFrom):
            refs.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            refs.update(a.name.split(".")[0] for a in node.names)
        elif id(node) not in docstrings:
            refs.update(string_idents(node))
    return refs


def collect_loaded_names(root: ast.AST) -> set[str]:
    """Names read anywhere under ``root`` (including in short strings and docstrings).

    Unlike ``collect_references`` this ignores attributes and import statements, so an
    imported name only counts as used when the code actually reads it.
    """
    used: set[str] = set()
    for node in ast.walk(root):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Store):
            used.add(node.id)
        else:
            used.update(string_idents(node))
    return used
