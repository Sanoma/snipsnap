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
