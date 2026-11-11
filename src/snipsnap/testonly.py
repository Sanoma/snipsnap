"""Definitions in production code that only tests (or other test-only code) use.

A definition is test-only when it is live (not dead) but unreachable from
production code: starting from production code outside any tracked definition,
a definition's body only counts once its own name is live. Helpers that only a
test-only function calls are therefore reported as well, including recursive
functions and groups of functions that only call each other.
"""

from .astutils import collect_references
from .deadcode import definitions, live_names
from .models import DeadItem, OnlyTestedItem
from .project import Module


def find_only_tested(
    modules: list[Module],
    entry_points: set[str],
    dead: list[DeadItem],
    *,
    include_overrides: bool = False,
) -> list[OnlyTestedItem]:
    # ignored modules are never reported on, so their usage counts as production usage
    # even under a tests directory; definitions() already yields nothing for them
    prod = [m for m in modules if not m.is_test or m.ignored]
    tests = [m for m in modules if m.is_test and not m.ignored]
    if not prod or not tests:
        return []

    defs = [
        pair for module in prod for pair in definitions(module, include_overrides=include_overrides)
    ]
    live = live_names(prod, defs, {"main"} | entry_points)
    dead_keys = {(d.path, d.line) for d in dead}
    flagged = [
        item
        for item, _ in defs
        if item.name not in live and (item.path, item.line) not in dead_keys
    ]

    outermost = [
        item
        for item in flagged
        if not any(
            other.path == item.path
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
