from pathlib import Path

import pytest

from snipsnap.deadcode import find_dead_code
from snipsnap.project import entry_point_names, load_modules
from snipsnap.testonly import find_only_tested


def only_tested_names(project: Path, ignores: list[str] | None = None) -> set[str]:
    modules = load_modules([project], [], ignores=ignores or [])
    dead = find_dead_code(modules, set())
    return {i.name for i in find_only_tested(modules, set(), dead)}


def test_find_only_tested(resources):
    project = resources / "tested_project"
    modules = load_modules([project], [])
    dead = find_dead_code(modules, entry_point_names(project))
    assert {i.name for i in dead if i.kind != "import"} == {"really_dead"}

    items = find_only_tested(modules, entry_point_names(project), dead)
    by_name = {i.qualname: i for i in items}
    assert set(by_name) == {"only_tests", "_helper", "recursive", "Tested"}
    assert by_name["only_tests"].tested_by == ("tests/test_lib.py",)
    assert by_name["_helper"].tested_by == ()  # reached only through only_tests


def test_only_tested_needs_tests(resources):
    modules = load_modules([resources / "no_tests.py"], [])
    assert find_only_tested(modules, set(), find_dead_code(modules, set())) == []


@pytest.mark.parametrize(
    "case",
    ["tests", "test", "__tests__", "tests-integration", "integration_tests", "src_pkg_tests"],
)
def test_helpers_in_test_directories_are_not_test_only(resources, case):
    assert only_tested_names(resources / "test_dirs" / case) == {"used"}


def test_directories_merely_containing_test_are_production(resources):
    assert only_tested_names(resources / "latest_dir") == {"helper"}


def test_mutually_recursive_test_only(resources):
    modules = load_modules([resources / "mutually_recursive_test_only"], [])
    dead = find_dead_code(modules, set())
    assert [i.name for i in dead if i.kind != "import"] == []
    items = find_only_tested(modules, set(), dead)
    assert {i.name for i in items} == {"ping", "pong"}


def test_ignored_module_under_tests_dir_counts_as_production_usage(resources):
    project = resources / "ignored_tests_module"
    assert only_tested_names(project) == {"via_ignored", "via_tests"}
    assert only_tested_names(project, ["tests/e2e/*"]) == {"via_tests"}
