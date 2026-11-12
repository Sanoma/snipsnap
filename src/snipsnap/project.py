"""Discover and parse the Python files of a project."""

import ast
import fnmatch
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {
    ".git",
    ".hg",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    ".tox",
    ".nox",
    ".github",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "site-packages",
}

_TEST_WORDS = {"test", "tests"}


def _is_test_dir(name: str) -> bool:
    # word-based so "tests-integration" and "__tests__" match but "latest" does not
    return not _TEST_WORDS.isdisjoint(re.split(r"[^a-z0-9]+", name.lower()))


@dataclass(frozen=True, slots=True)
class Module:
    path: Path  # path relative to the scan root, used for display
    tree: ast.Module
    # analysed (its code counts as live usage) but never reported on
    ignored: bool = False

    @property
    def is_test(self) -> bool:
        name = self.path.name
        return (
            name == "conftest.py"
            or name.startswith("test_")
            or name.endswith("_test.py")
            or any(_is_test_dir(part) for part in self.path.parts[:-1])
        )


def _is_excluded(rel: Path, patterns: Sequence[str]) -> bool:
    posix = rel.as_posix()
    return any(fnmatch.fnmatch(posix, p) or fnmatch.fnmatch(rel.name, p) for p in patterns)


def _is_skipped_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.endswith(".egg-info")


def _git_python_files(root: Path) -> list[Path] | None:
    """Tracked and untracked-but-not-ignored ``.py`` files, or None outside a git repository."""
    try:
        result = subprocess.run(
            [
                *("git", "-C", str(root), "ls-files", "-z"),
                *("--cached", "--others", "--exclude-standard", "--", "*.py"),
            ],
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
    except OSError, subprocess.CalledProcessError:
        return None
    rels = (Path(name) for name in result.stdout.split("\0") if name)
    return [
        root / rel
        for rel in rels
        if not any(_is_skipped_dir(part) for part in rel.parts[:-1]) and (root / rel).is_file()
    ]


def _walked_python_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # prune in place so os.walk never descends into node_modules, .venv, ...
        dirnames[:] = [d for d in dirnames if not _is_skipped_dir(d)]
        found.extend(Path(dirpath, f) for f in filenames if f.endswith(".py"))
    return found


def _is_selected(rel: Path, includes: Sequence[str], excludes: Sequence[str]) -> bool:
    return (not includes or _is_excluded(rel, includes)) and not _is_excluded(rel, excludes)


def _iter_files(
    root: Path, includes: Sequence[str], excludes: Sequence[str], base: Path
) -> Iterator[Path]:
    if root.is_file():
        if _is_selected(root.resolve().relative_to(base), includes, excludes):
            yield root
        return
    if (found := _git_python_files(root)) is None:
        found = _walked_python_files(root)
    for path in sorted(found):
        if _is_selected(path.resolve().relative_to(base), includes, excludes):
            yield path


def common_root(paths: list[Path]) -> Path:
    """Directory that all reported paths are relative to."""
    bases = [(p if p.is_dir() else p.parent).resolve() for p in paths]
    return Path(os.path.commonpath(bases))


def load_modules(
    paths: list[Path],
    excludes: Sequence[str],
    *,
    root: Path | None = None,
    ignores: Sequence[str] = (),
    includes: Sequence[str] = (),
) -> list[Module]:
    base = root or common_root(paths)
    modules: list[Module] = []
    for scan in paths:
        for file in _iter_files(scan, includes, excludes, base):
            try:
                tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            except (SyntaxError, UnicodeDecodeError, ValueError, RecursionError) as exc:
                print(f"snipsnap: skipping {file}: {exc}", file=sys.stderr)
                continue
            rel = file.resolve().relative_to(base)
            modules.append(
                Module(
                    path=rel,
                    tree=tree,
                    ignored=_is_excluded(rel, ignores),
                )
            )
    return modules


def entry_point_names(root: Path) -> set[str]:
    """Function names referenced by [project.scripts]/[project.gui-scripts]."""
    pyproject = (root if root.is_dir() else root.parent) / "pyproject.toml"
    if not pyproject.is_file():
        return set()
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return set()
    project = data.get("project", {})
    targets = [
        *project.get("scripts", {}).values(),
        *project.get("gui-scripts", {}).values(),
    ]
    # "pkg.module:object.attr [extra]" -> "object"
    return {
        obj.split("[")[0].strip().split(".")[0]
        for _, sep, obj in (target.partition(":") for target in targets)
        if sep
    }
