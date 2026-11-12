import shutil
import subprocess
from collections.abc import Sequence

from snipsnap.deadcode import find_dead_code
from snipsnap.project import load_modules


def test_exclude_matches_reported_path_across_multiple_scan_dirs(resources):
    root = resources / "multiple_scan_dirs"
    paths = [root / "src", root / "alembic"]

    def loaded(excludes: list[str]) -> set[str]:
        return {m.path.as_posix() for m in load_modules(paths, excludes)}

    assert loaded([]) == {"src/a.py", "alembic/versions/m1.py", "alembic/env.py"}
    assert loaded(["alembic/versions/*"]) == {"src/a.py", "alembic/env.py"}
    assert loaded(["env.py"]) == {"src/a.py", "alembic/versions/m1.py"}


def test_ignore_keeps_code_as_live_usage_but_unreported(resources):
    root = resources / "ignored_migrations"
    paths = [root / "src", root / "alembic"]

    def dead(excludes: Sequence[str] = (), ignores: Sequence[str] = ()) -> set[tuple[str, str]]:
        modules = load_modules(paths, excludes, ignores=ignores)
        return {(i.path, i.name) for i in find_dead_code(modules, set())}

    assert dead() == {
        ("src/app.py", "helper"),
        ("src/app.py", "orphan"),
        ("alembic/versions/m1.py", "upgrade"),
    }
    # excluded: migrations are not analysed at all, so helper looks dead
    assert ("src/app.py", "helper") in dead(excludes=["alembic/*"])
    # ignored: helper stays alive, nothing is reported for the migration
    assert dead(ignores=["alembic/versions/*"]) == {("src/app.py", "orphan")}


def test_gitignored_files_are_not_scanned(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("ignored/\n")
    (tmp_path / "kept.py").write_text("def a(): pass\n")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "skipped.py").write_text("def b(): pass\n")

    modules = load_modules([tmp_path], [])

    assert [m.path.as_posix() for m in modules] == ["kept.py"]


def test_files_are_scanned_outside_a_git_repository(resources, tmp_path):
    root = shutil.copytree(resources / "non_git_project", tmp_path / "project")

    modules = load_modules([root], [])

    assert [m.path.as_posix() for m in modules] == ["ignored/found.py"]


def test_unparseable_deep_nesting_is_skipped(resources, tmp_path, capsys):
    root = tmp_path / "project"
    root.mkdir()
    shutil.copy(resources / "no_tests.py", root / "lib.py")
    (root / "deep.py").write_text("x = " + "[" * 100000 + "]" * 100000 + "\n")

    modules = load_modules([root], [])

    assert [m.path.name for m in modules] == ["lib.py"]
    assert "skipping" in capsys.readouterr().err
