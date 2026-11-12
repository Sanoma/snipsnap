import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from snipsnap.cli import main
from snipsnap.deadcode import find_dead_code
from snipsnap.duplicates import find_duplicates
from snipsnap.project import entry_point_names, load_modules


def write(root: Path, rel: str, code: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(code))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    write(tmp_path, "pkg/__init__.py", "from .a import used_func\n")
    write(
        tmp_path,
        "pkg/a.py",
        """
        import os
        import sys
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            from pkg.b import Fwd

        def used_func(x: "Fwd"):
            return sys.argv

        def dead_func():
            return 1

        class DeadClass:
            def method(self):
                pass

        class Base:
            def hook(self):
                pass

        class Child(Base):
            def other_hook(self):
                pass

        def cli():
            pass

        @some_decorator
        def decorated():
            pass

        def __getattr__(name):
            pass
    """,
    )
    write(tmp_path, "pkg/b.py", "class Fwd: ...\n")
    write(tmp_path, "tests/test_a.py", "def test_x():\n    pass\n")
    write(tmp_path, ".venv/lib/x.py", "def ignored_dead(): pass\n")
    write(tmp_path, "pyproject.toml", '[project]\nname="p"\n[project.scripts]\np = "pkg.a:cli"\n')
    return tmp_path


def dead_names(project: Path, **kw) -> set[tuple[str, str]]:
    modules = load_modules([project], [])
    items = find_dead_code(modules, entry_point_names(project), **kw)
    return {(i.kind, i.name) for i in items}


def test_dead_code(project):
    assert dead_names(project) == {
        ("function", "dead_func"),
        ("class", "DeadClass"),
        ("class", "Base"),  # only referenced by the dead Child
        ("class", "Child"),
        ("method", "method"),
        ("method", "hook"),
        ("import", "os"),
    }


def test_unused_variables(tmp_path):
    write(
        tmp_path,
        "pkg/__init__.py",
        "PUBLIC_API_CONSTANT = 1\n",
    )
    write(
        tmp_path,
        "pkg/m.py",
        """
        import sys

        __version__ = "1"
        __all__ = ["EXPORTED"]
        USED = 1
        EXPORTED = 2
        UNUSED = 3
        ANNOTATED: int = 4
        BARE_ANNOTATION: int
        CHAINED_A = CHAINED_B = 5
        a, b = 1, 2
        _ = sys.argv
        ONLY_FOR_DEAD = 6
        _private_unused = 7
        counter = 0
        counter += 1
        MUTATED = []
        MUTATED.append(USED)

        if sys.platform == "win32":
            IN_BRANCH = 8
        else:
            IN_BRANCH = 9

        try:
            import json
        except ImportError:
            json = None
            FALLBACK = 10

        def dead_func():
            LOCAL = 1
            return ONLY_FOR_DEAD

        def main():
            return json
        """,
    )
    write(
        tmp_path,
        "tests/test_m.py",
        "pytestmark = []\nhelper_value = 1\n\ndef test_x():\n    pass\n",
    )
    unused = {name for kind, name in dead_names(tmp_path) if kind == "variable"}
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


def test_variable_used_from_another_module_or_tests_is_live(tmp_path):
    write(tmp_path, "lib.py", "LIMIT = 10\nOTHER = 1\nTEST_ONLY = 2\n\ndef main():\n    pass\n")
    write(tmp_path, "app.py", "import lib\n\nprint(lib.LIMIT)\nprint(lib.OTHER)\n")
    write(
        tmp_path, "tests/test_lib.py", "from lib import TEST_ONLY\n\ndef test_x():\n    TEST_ONLY\n"
    )
    assert dead_names(tmp_path) == set()


def test_same_named_variable_in_unrelated_module_does_not_keep_it_alive(tmp_path):
    write(tmp_path, "src/a/config.py", "PROMPTS = {'x': 1}\nSHARED = 1\nVIA_ATTR = 1\nSTAR = 1\n")
    write(tmp_path, "src/b/config.py", "PROMPTS = {'y': 2}\n")
    write(tmp_path, "src/b/use.py", "from .config import PROMPTS\n\nprint(PROMPTS)\n")
    write(
        tmp_path,
        "src/a/use.py",
        """
        from a import config
        from a.config import SHARED
        from a.config import *

        print(config.VIA_ATTR, SHARED, STAR)
        """,
    )
    write(tmp_path, "src/c/config.py", "PREFIXED = 1\n")
    write(tmp_path, "src/c/use.py", "from src.c.config import PREFIXED\n\nprint(PREFIXED)\n")
    variables = {
        (i.path, i.name)
        for i in find_dead_code(load_modules([tmp_path / "src"], []), set())
        if i.kind == "variable"
    }
    assert variables == {("a/config.py", "PROMPTS")}


def test_include_overrides(project):
    assert ("method", "other_hook") in dead_names(project, include_overrides=True)
    assert ("method", "hook") in dead_names(project)


def test_duplicates_ignore_names_docstrings_and_annotations(tmp_path):
    write(
        tmp_path,
        "m.py",
        '''
        def total(items: list[int]) -> int:
            """Sum things."""
            acc = 0
            for item in items:
                acc += item
            return acc

        class K:
            @staticmethod
            def add_all(values):
                result = 0
                for v in values:
                    result += v
                return result

        def different(items):
            acc = 1
            for item in items:
                acc *= item
            return acc
    ''',
    )
    groups = find_duplicates(load_modules([tmp_path], []), min_lines=3)
    assert len(groups) == 1
    assert {f.qualname for f in groups[0].functions} == {"total"}.union({"K.add_all"})


def test_near_duplicates(tmp_path):
    write(
        tmp_path,
        "m.py",
        """
        def a(xs):
            out = []
            for x in xs:
                if x > 0:
                    out.append(x)
            return out

        def b(xs):
            out = []
            for x in xs:
                if x > 0:
                    out.append(x)
            print(out)
            return out
    """,
    )
    modules = load_modules([tmp_path], [])
    assert find_duplicates(modules, min_lines=3) == []
    groups = find_duplicates(modules, min_lines=3, similarity=0.8)
    assert [g.kind for g in groups] == ["similar"]


def test_cli_json_and_strict(project, capsys):
    code = main(["scan", str(project), "--json", "--strict"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert out["version"] == 1
    assert out["summary"]["dead_code"] == len(out["dead_code"])
    assert {d["name"] for d in out["dead_code"]} >= {"dead_func", "DeadClass"}
    assert main(["scan", str(project / "pkg" / "b.py"), "--only", "dead"]) == 0


def test_report_locations_and_source(project, tmp_path_factory, capsys):
    out_file = tmp_path_factory.mktemp("out") / "report.json"
    main(["scan", str(project), "-o", str(out_file), "--with-source"])
    report = json.loads(out_file.read_text())
    assert report["root"] == str(project.resolve())

    by_name = {d["qualname"]: d for d in report["dead_code"]}
    dead = by_name["dead_func"]
    assert (dead["path"], dead["line"], dead["end_line"]) == ("pkg/a.py", 12, 13)
    assert dead["source"] == "def dead_func():\n    return 1"
    assert by_name["DeadClass.method"]["kind"] == "method"
    assert by_name["os"]["kind"] == "import"


def test_duplicate_range_includes_decorators(tmp_path):
    write(
        tmp_path,
        "m.py",
        """
        @cache
        def a(xs):
            out = []
            for x in xs:
                out.append(x * 2)
            return out

        def b(xs):
            out = []
            for x in xs:
                out.append(x * 2)
            return out
    """,
    )
    (group,) = find_duplicates(load_modules([tmp_path], []), min_lines=3)
    assert (group.functions[0].line, group.functions[0].end_line) == (2, 7)


def test_show_roundtrip(project, tmp_path, capsys):
    out_file = tmp_path / "r.json"
    main(["scan", str(project), "-o", str(out_file)])
    capsys.readouterr()
    assert main(["show", str(out_file), "--no-color"]) == 0
    text = capsys.readouterr().out
    assert "pkg/a.py:12-13" in text and "unused function dead_func" in text
    assert main(["show", str(tmp_path / "missing.json")]) == 2


def test_argument_errors_are_handled_by_argparse(tmp_path, capsys):
    for argv in (
        [],
        ["scan", str(tmp_path / "nope")],
        ["scan", str(tmp_path), "--similarity", "1.5"],
        ["show"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2
    assert "no such path" in capsys.readouterr().err


def test_show_rejects_invalid_report(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 2}')
    assert main(["show", str(bad)]) == 2
    assert "cannot read report" in capsys.readouterr().err


def test_exclude_matches_reported_path_across_multiple_scan_dirs(tmp_path):
    write(tmp_path, "src/a.py", "def a(): pass\n")
    write(tmp_path, "alembic/versions/m1.py", "def upgrade(): pass\n")
    write(tmp_path, "alembic/env.py", "def run(): pass\n")
    paths = [tmp_path / "src", tmp_path / "alembic"]
    names = lambda ex: {m.path.as_posix() for m in load_modules(paths, ex)}  # noqa: E731
    assert names([]) == {"src/a.py", "alembic/versions/m1.py", "alembic/env.py"}
    assert names(["alembic/versions/*"]) == {"src/a.py", "alembic/env.py"}
    assert names(["env.py"]) == {"src/a.py", "alembic/versions/m1.py"}


def test_pytest_hooks_are_not_dead(tmp_path):
    write(
        tmp_path,
        "tests/test_x.py",
        "def teardown_function(): pass\ndef setup_module(): pass\ndef helper(): pass\n",
    )
    modules = load_modules([tmp_path], [])
    assert {i.name for i in find_dead_code(modules, set())} == {"helper"}


@pytest.fixture
def tested_project(tmp_path: Path) -> Path:
    write(
        tmp_path,
        "lib.py",
        """
        def shared():
            return 1

        def only_tests():
            return _helper() + shared()

        def _helper():
            return 2

        def recursive(n):
            return recursive(n - 1) if n else 0

        def really_dead():
            return 3

        class Tested:
            def run(self):
                return 4

        def entry():
            return shared()
    """,
    )
    write(
        tmp_path,
        "tests/test_lib.py",
        """
        from lib import only_tests, recursive, Tested

        def test_all():
            assert only_tests() and recursive(2) is not None and Tested().run()
    """,
    )
    write(tmp_path, "pyproject.toml", '[project]\nname="p"\n[project.scripts]\np = "lib:entry"\n')
    return tmp_path


def test_find_only_tested(tested_project):
    from snipsnap.testonly import find_only_tested

    modules = load_modules([tested_project], [])
    dead = find_dead_code(modules, entry_point_names(tested_project))
    assert {i.name for i in dead if i.kind != "import"} == {"really_dead"}

    items = find_only_tested(modules, entry_point_names(tested_project), dead)
    by_name = {i.qualname: i for i in items}
    assert set(by_name) == {"only_tests", "_helper", "recursive", "Tested"}
    assert by_name["only_tests"].tested_by == ("tests/test_lib.py",)
    assert by_name["_helper"].tested_by == ()  # reached only through only_tests


def test_only_tested_needs_tests(tmp_path):
    from snipsnap.testonly import find_only_tested

    write(tmp_path, "lib.py", "def a():\n    return b()\n\ndef b():\n    return 1\n")
    modules = load_modules([tmp_path], [])
    assert find_only_tested(modules, set(), find_dead_code(modules, set())) == []


@pytest.mark.parametrize(
    "test_dir",
    ["tests", "test", "__tests__", "tests-integration", "integration_tests", "src/pkg/tests"],
)
def test_helpers_in_test_directories_are_not_test_only(tmp_path, test_dir):
    from snipsnap.testonly import find_only_tested

    write(tmp_path, "lib.py", "def used():\n    return 1\n")
    write(tmp_path, f"{test_dir}/helpers.py", "def helper():\n    return 1\n")
    write(
        tmp_path,
        f"{test_dir}/test_it.py",
        "from helpers import helper\nfrom lib import used\n\n"
        "def test_it():\n    helper()\n    used()\n",
    )
    modules = load_modules([tmp_path], [])
    names = {i.name for i in find_only_tested(modules, set(), find_dead_code(modules, set()))}
    assert names == {"used"}


def test_directories_merely_containing_test_are_production(tmp_path):
    from snipsnap.testonly import find_only_tested

    write(tmp_path, "latest/helpers.py", "def helper():\n    return 1\n")
    write(
        tmp_path, "tests/test_it.py", "from helpers import helper\n\ndef test_it():\n    helper()\n"
    )
    modules = load_modules([tmp_path], [])
    names = {i.name for i in find_only_tested(modules, set(), find_dead_code(modules, set()))}
    assert names == {"helper"}


def test_cli_reports_test_only_section(tested_project, capsys):
    assert (
        main(["scan", str(tested_project), "--json", "--only", "test-only", "--with-source"]) == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["analyses"] == ["test_only"]
    assert out["summary"]["test_only"] == len(out["test_only"]) == 4
    assert out["dead_code"] == [] and out["duplicates"] == []
    first = next(f for f in out["test_only"] if f["qualname"] == "only_tests")
    assert first["id"].startswith("test-only-") and first["tested_by"] == ["tests/test_lib.py"]
    assert first["source"].startswith("def only_tests")

    assert main(["scan", str(tested_project), "--strict", "--only", "test-only"]) == 1
    text = capsys.readouterr().out
    assert "Only used by tests (4)" in text and "tested by: tests/test_lib.py" in text
    assert "Dead code" not in text


def test_recursive_dead_code(tmp_path):
    write(
        tmp_path,
        "lib.py",
        """
        def used(n):
            return 1 if n == 0 else n * used(n - 1)

        def dead_self(n):
            return 0 if n == 0 else dead_self(n - 1)

        def ping(n):
            return pong(n - 1) if n else 0

        def pong(n):
            return ping(n - 1) if n else 0

        def dead_caller():
            return helper()

        def helper():
            return 1

        def main():
            return used(3)
    """,
    )
    modules = load_modules([tmp_path], [])
    dead = {i.name for i in find_dead_code(modules, set())}
    assert dead == {"dead_self", "ping", "pong", "dead_caller", "helper"}


def test_mutually_recursive_test_only(tmp_path):
    from snipsnap.testonly import find_only_tested

    write(
        tmp_path,
        "lib.py",
        """
        def ping(n):
            return pong(n - 1) if n else 0

        def pong(n):
            return ping(n - 1) if n else 0

        def main():
            return 1
    """,
    )
    write(tmp_path, "tests/test_lib.py", "from lib import ping\n\ndef test_x():\n    ping(2)\n")
    modules = load_modules([tmp_path], [])
    dead = find_dead_code(modules, set())
    assert [i.name for i in dead if i.kind != "import"] == []
    items = find_only_tested(modules, set(), dead)
    assert {i.name for i in items} == {"ping", "pong"}


def test_unparseable_deep_nesting_is_skipped(tmp_path, capsys):
    write(tmp_path, "ok.py", "def f():\n    return 1\n")
    (tmp_path / "deep.py").write_text("x = " + "[" * 100000 + "]" * 100000 + "\n")
    modules = load_modules([tmp_path], [])
    assert [m.path.name for m in modules] == ["ok.py"]
    assert "skipping" in capsys.readouterr().err


def test_gitignored_files_are_not_scanned(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    write(tmp_path, ".gitignore", "ignored/\n")
    write(tmp_path, "kept.py", "def a(): pass\n")
    write(tmp_path, "ignored/skipped.py", "def b(): pass\n")

    modules = load_modules([tmp_path], [])

    assert [m.path.as_posix() for m in modules] == ["kept.py"]


def test_files_are_scanned_outside_a_git_repository(tmp_path):
    write(tmp_path, "ignored/found.py", "def b(): pass\n")
    write(tmp_path, "node_modules/skipped.py", "def c(): pass\n")

    modules = load_modules([tmp_path], [])

    assert [m.path.as_posix() for m in modules] == ["ignored/found.py"]


def test_ignore_keeps_code_as_live_usage_but_unreported(tmp_path):
    write(tmp_path, "src/app.py", "def helper(): pass\ndef orphan(): pass\n")
    write(
        tmp_path,
        "alembic/versions/m1.py",
        "import src.app\n\ndef upgrade():\n    src.app.helper()\n",
    )
    paths = [tmp_path / "src", tmp_path / "alembic"]

    def dead(**kw):
        modules = load_modules(paths, [], **kw)
        return {(i.path, i.name) for i in find_dead_code(modules, set())}

    assert dead() == {
        ("src/app.py", "helper"),
        ("src/app.py", "orphan"),
        ("alembic/versions/m1.py", "upgrade"),
    }
    # fully excluded: migrations are not analysed, helper looks dead
    assert ("src/app.py", "helper") in {
        (i.path, i.name) for i in find_dead_code(load_modules(paths, ["alembic/*"]), set())
    }
    # ignored: helper stays alive, nothing is reported for the migration
    assert dead(ignores=["alembic/versions/*"]) == {("src/app.py", "orphan")}


def test_ignored_module_under_tests_dir_counts_as_production_usage(tmp_path):
    from snipsnap.testonly import find_only_tested

    write(tmp_path, "lib.py", "def via_ignored():\n    pass\n\ndef via_tests():\n    pass\n")
    write(tmp_path, "tests/e2e/runner.py", "from lib import via_ignored\n\nvia_ignored()\n")
    write(
        tmp_path,
        "tests/test_lib.py",
        "from lib import via_tests\n\ndef test_x():\n    via_tests()\n",
    )

    def flagged(ignores: list[str]) -> list[str]:
        modules = load_modules([tmp_path], [], ignores=ignores)
        dead = find_dead_code(modules, set())
        return [i.name for i in find_only_tested(modules, set(), dead)]

    assert flagged([]) == ["via_ignored", "via_tests"]
    assert flagged(["tests/e2e/*"]) == ["via_tests"]


def config_project(tmp_path: Path, table: str) -> Path:
    write(tmp_path, "pyproject.toml", f'[project]\nname = "p"\n\n[tool.snipsnap]\n{table}\n')
    write(tmp_path, "src/app.py", "def orphan(): pass\n")
    write(tmp_path, "src/legacy/old.py", "def old_orphan(): pass\n")
    write(tmp_path, "scripts/tool.py", "def script_orphan(): pass\n")
    return tmp_path


def reported_paths(capsys, root: Path, *args: str) -> set[str]:
    assert main(["scan", str(root), "--json", "--only", "dead", *args]) == 0
    return {f["path"] for f in json.loads(capsys.readouterr().out)["dead_code"]}


def test_config_exclude_ignore_and_include(tmp_path, capsys):
    root = config_project(tmp_path, 'exclude = ["src/legacy/*"]\nignore = ["scripts/*"]')
    assert reported_paths(capsys, root) == {"src/app.py"}

    root = config_project(tmp_path, 'include = ["src/*"]')
    assert reported_paths(capsys, root) == {"src/app.py", "src/legacy/old.py"}


def test_cli_lists_add_to_config(tmp_path, capsys):
    root = config_project(tmp_path, 'exclude = ["src/legacy/*"]')
    assert reported_paths(capsys, root, "--exclude", "scripts/*") == {"src/app.py"}


def test_strict_and_scalars_come_from_config(tmp_path):
    root = config_project(tmp_path, "strict = true")
    assert main(["scan", str(root), "--only", "dead"]) == 1


def test_config_found_from_parent_directory(tmp_path, capsys):
    root = config_project(tmp_path, 'exclude = ["legacy/*"]')
    assert reported_paths(capsys, root / "src") == {"app.py"}


def test_explicit_config_file(tmp_path, capsys):
    root = config_project(tmp_path, "")
    other = tmp_path / "other.toml"
    other.write_text('[tool.snipsnap]\nexclude = ["scripts/*", "src/*"]\n')
    assert reported_paths(capsys, root, "--config", str(other)) == set()


@pytest.mark.parametrize(
    "table", ["unknown = 1", "similarity = 2", 'exclude = "src/*"', "min-lines = 0"]
)
def test_invalid_config_is_reported(tmp_path, capsys, table):
    root = config_project(tmp_path, table)
    assert main(["scan", str(root)]) == 2
    assert "invalid [tool.snipsnap]" in capsys.readouterr().err
