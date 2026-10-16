"""Definitions in production code that only tests (or other test-only code) use.

A definition is test-only when every reference to its name outside its own body
comes from test modules. References from code that is itself test-only do not
count, so helpers that only a test-only function calls are reported as well.
"""

from collections import Counter

from .deadcode import collect_references, definitions
from .models import DeadItem, OnlyTestedItem
from .project import Module


def find_only_tested(
    modules: list[Module],
    entry_points: set[str],
    dead: list[DeadItem],
    include_overrides: bool = False,
) -> list[OnlyTestedItem]:
    prod = [m for m in modules if not m.is_test]
    tests = [m for m in modules if m.is_test]
    if not prod or not tests:
        return []

    exempt = {"main"} | entry_points
    dead_keys = {(d.path, d.line) for d in dead}
    candidates = [
        (item, node)
        for module in prod
        for item, node in definitions(module, include_overrides)
        if (item.path, item.line) not in dead_keys and item.name not in exempt
    ]
    own = {id(node): collect_references(node) for _, node in candidates}
    everywhere: Counter[str] = Counter()
    for module in modules:
        everywhere.update(collect_references(module.tree))

    removed: set[int] = set()
    flagged: list[DeadItem] = []
    while True:
        live: Counter[str] = Counter()
        for module in prod:
            live.update(collect_references(module.tree, removed))
        found = [
            (item, node)
            for item, node in candidates
            if id(node) not in removed
            and live[item.name] - own[id(node)][item.name] <= 0
            and everywhere[item.name] - own[id(node)][item.name] > 0
        ]
        if not found:
            break
        removed.update(id(node) for _, node in found)
        flagged.extend(item for item, _ in found)

    outermost = [
        item
        for item in flagged
        if not any(
            other is not item
            and other.path == item.path
            and other.line <= item.line
            and item.end_line <= other.end_line
            and (other.line, other.end_line) != (item.line, item.end_line)
            for other in flagged
        )
    ]
    test_names = {m.path.as_posix(): collect_references(m.tree).keys() for m in tests}
    result = [
        OnlyTestedItem(
            **item.model_dump(),
            tested_by=tuple(sorted(p for p, names in test_names.items() if item.name in names)),
        )
        for item in outermost
    ]
    return sorted(result, key=lambda i: (i.path, i.line, i.name))
