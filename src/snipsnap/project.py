"""Discover and parse the Python files of a project."""

import ast
import fnmatch
import os
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict

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
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "site-packages",
}


class Module(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    path: Path  # path relative to the scan root, used for display
    tree: ast.Module

    @property
    def is_test(self) -> bool:
        name = self.path.name
        return (
            name == "conftest.py"
            or name.startswith("test_")
            or name.endswith("_test.py")
            or "tests" in self.path.parts
        )


def _is_excluded(rel: Path, patterns: list[str]) -> bool:
    posix = rel.as_posix()
    return any(fnmatch.fnmatch(posix, p) or fnmatch.fnmatch(rel.name, p) for p in patterns)


def _iter_files(root: Path, excludes: list[str], base: Path) -> Iterator[Path]:
    if root.is_file():
        if not _is_excluded(root.resolve().relative_to(base), excludes):
            yield root
        return
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        parts = rel.parts[:-1]
        if any(p in SKIP_DIRS or p.endswith(".egg-info") for p in parts):
            continue
        if _is_excluded(path.resolve().relative_to(base), excludes):
            continue
        yield path


def common_root(paths: list[Path]) -> Path:
    """Directory that all reported paths are relative to."""
    bases = [(p if p.is_dir() else p.parent).resolve() for p in paths]
    return Path(os.path.commonpath(bases))


def load_modules(paths: list[Path], excludes: list[str], root: Path | None = None) -> list[Module]:
    base = root or common_root(paths)
    modules: list[Module] = []
    for scan in paths:
        for file in _iter_files(scan, excludes, base):
            try:
                tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
                print(f"snipsnap: skipping {file}: {exc}", file=sys.stderr)
                continue
            modules.append(Module(path=file.resolve().relative_to(base), tree=tree))
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
    names: set[str] = set()
    for target in targets:
        if ":" in target:
            names.add(target.split(":", 1)[1].split("[")[0].strip().split(".")[0])
    return names
