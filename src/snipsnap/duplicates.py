"""Duplicate function detection on normalized ASTs.

Functions are normalized so that renamed parameters/locals, docstrings, type
annotations and the function name itself do not matter. Functions with an
identical normalized AST are exact duplicates; with ``similarity < 1`` we also
group functions whose node-type sequences are similar.
"""

import ast
import copy
from collections import defaultdict
from collections.abc import Iterator
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .astutils import FuncNode, docstring_node, end_line, start_line
from .models import DuplicateGroup, FunctionRef
from .project import Module


class _Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: FunctionRef
    key: str
    shape: list[str]


def _iter_functions(tree: ast.AST, prefix: str = "") -> Iterator[tuple[str, FuncNode]]:
    """Yield (qualname, node) for module-level functions and methods (any class depth)."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, FuncNode):
            yield f"{prefix}{node.name}", node
        elif isinstance(node, ast.ClassDef):
            yield from _iter_functions(node, f"{prefix}{node.name}.")
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            yield from _iter_functions(node, prefix)


def _strip_docstring(node: FuncNode) -> None:
    if docstring_node(node) is not None:
        node.body = node.body[1:] or [ast.Pass()]


def _normalize(func: FuncNode) -> FuncNode:
    node = copy.deepcopy(func)
    _strip_docstring(node)
    node.name = "_"
    node.decorator_list = []
    node.returns = None
    node.type_comment = None
    args = node.args
    all_args = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    all_args += [a for a in (args.vararg, args.kwarg) if a]
    for arg in all_args:
        arg.annotation = None

    local_names = {a.arg for a in all_args}
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            local_names.add(n.id)

    mapping: dict[str, str] = {}

    def rename(name: str) -> str:
        if name not in local_names:
            return name
        return mapping.setdefault(name, f"v{len(mapping)}")

    # ast.walk is breadth-first and deterministic, so structurally identical
    # functions get identical numbering regardless of the original names.
    for n in ast.walk(node):
        if isinstance(n, ast.arg):
            n.arg = rename(n.arg)
        elif isinstance(n, ast.Name):
            n.id = rename(n.id)
        elif isinstance(n, ast.AnnAssign):
            n.annotation = ast.Constant(value=None)
    return node


def _body_lines(node: FuncNode) -> int:
    body = node.body[1:] if docstring_node(node) is not None else node.body
    if not body:
        return 0
    return (end_line(node) - body[0].lineno) + 1


def _candidates(modules: list[Module], min_lines: int) -> list[_Candidate]:
    result: list[_Candidate] = []
    for module in modules:
        for qualname, func in _iter_functions(module.tree):
            if _body_lines(func) < min_lines:
                continue
            normalized = _normalize(func)
            result.append(
                _Candidate(
                    ref=FunctionRef(
                        qualname=qualname,
                        path=module.path.as_posix(),
                        line=start_line(func),
                        end_line=end_line(func),
                    ),
                    key=ast.dump(normalized, annotate_fields=False, include_attributes=False),
                    shape=[type(n).__name__ for n in ast.walk(normalized)],
                )
            )
    return result


def _find_similar(candidates: list[_Candidate], threshold: float) -> list[tuple[list[int], float]]:
    """Cluster distinct-shape candidates via union-find on pairwise similarity."""
    n = len(candidates)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    lowest: dict[int, float] = {}
    order = sorted(range(n), key=lambda i: len(candidates[i].shape))
    for pos, i in enumerate(order):
        a = candidates[i].shape
        for j in order[pos + 1 :]:
            b = candidates[j].shape
            if len(a) / len(b) < threshold:
                break  # sorted by length: later ones are only longer
            matcher = SequenceMatcher(None, a, b, autojunk=False)
            if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                continue
            ratio = matcher.ratio()
            if ratio >= threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
                lowest[i] = min(lowest.get(i, 1.0), ratio)
                lowest[j] = min(lowest.get(j, 1.0), ratio)

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)
    return [
        (members, min(lowest[m] for m in members))
        for members in clusters.values()
        if len(members) > 1
    ]


def find_duplicates(
    modules: list[Module], min_lines: int = 5, similarity: float = 1.0
) -> list[DuplicateGroup]:
    candidates = _candidates(modules, min_lines)

    by_key: dict[str, list[_Candidate]] = defaultdict(list)
    for c in candidates:
        by_key[c.key].append(c)

    groups = [
        _group("exact", 1.0, [c.ref for c in members])
        for members in by_key.values()
        if len(members) > 1
    ]

    if similarity < 1.0:
        # one representative per exact-duplicate set; members are merged back below
        reps = [members[0] for members in by_key.values()]
        for indexes, ratio in _find_similar(reps, similarity):
            refs = [c.ref for i in indexes for c in by_key[reps[i].key]]
            groups.append(_group("similar", round(ratio, 3), refs))

    groups.sort(key=lambda g: (g.kind != "exact", -len(g.functions), g.functions[0].path))
    return groups


def _group(
    kind: Literal["exact", "similar"], similarity: float, refs: list[FunctionRef]
) -> DuplicateGroup:
    ordered = sorted(refs, key=lambda f: (f.path, f.line))
    return DuplicateGroup(kind=kind, similarity=similarity, functions=tuple(ordered))
