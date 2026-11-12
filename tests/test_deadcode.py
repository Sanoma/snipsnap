from pathlib import Path

from snipsnap.deadcode import find_dead_code
from snipsnap.project import entry_point_names, load_modules


def dead_items(project: Path, **kw) -> set[tuple[str, str]]:
    modules = load_modules([project], [])
    items = find_dead_code(modules, entry_point_names(project), **kw)
    return {(i.kind, i.name) for i in items}


def test_dead_code(resources):
    assert dead_items(resources / "dead_code_project") == {
        ("function", "dead_func"),
        ("class", "DeadClass"),
        ("class", "Base"),  # only referenced by the dead Child
        ("class", "Child"),
        ("method", "method"),
        ("method", "hook"),
        ("import", "os"),
    }


def test_overrides_are_only_reported_on_request(resources):
    project = resources / "dead_code_project"
    assert ("method", "other_hook") in dead_items(project, include_overrides=True)
    assert ("method", "other_hook") not in dead_items(project)
    assert ("method", "hook") in dead_items(project)


def test_pytest_hooks_are_not_dead(resources):
    modules = load_modules([resources / "pytest_hooks"], [])
    assert {i.name for i in find_dead_code(modules, set())} == {"helper"}


def test_recursive_dead_code(resources):
    modules = load_modules([resources / "recursive_dead_code.py"], [])
    dead = {i.name for i in find_dead_code(modules, set())}
    assert dead == {"dead_self", "ping", "pong", "dead_caller", "helper"}


def test_unused_variables(resources):
    unused = {name for kind, name in dead_items(resources / "variables") if kind == "variable"}
    assert unused == {
        "UNUSED",
        "ANNOTATED",
        "CHAINED_A",
        "CHAINED_B",
        "ONLY_FOR_DEAD",
        "_private_unused",
        "counter",
        "IN_BRANCH",
        "FALLBACK",
        "helper_value",
    }


def test_variable_used_from_another_module_or_tests_is_live(resources):
    assert dead_items(resources / "cross_module_variables") == set()


def test_same_named_variable_in_unrelated_module_does_not_keep_it_alive(resources):
    modules = load_modules([resources / "same_named_variables" / "src"], [])
    variables = {(i.path, i.name) for i in find_dead_code(modules, set()) if i.kind == "variable"}
    assert variables == {("a/config.py", "PROMPTS")}
