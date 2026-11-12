import json
from pathlib import Path

import pytest

from snipsnap.cli import main


def reported_paths(capsys, root: Path, *args: str) -> set[str]:
    assert main(["scan", str(root), "--json", "--only", "dead", *args]) == 0
    return {f["path"] for f in json.loads(capsys.readouterr().out)["dead_code"]}


def config_file(resources: Path, name: str) -> str:
    return str(resources / "config_project" / "configs" / f"{name}.toml")


def test_config_exclude_and_ignore(resources, capsys):
    root = resources / "config_project"
    config = config_file(resources, "exclude_and_ignore")
    assert reported_paths(capsys, root, "--config", config) == {"src/app.py"}


def test_config_include(resources, capsys):
    root = resources / "config_project"
    config = config_file(resources, "include")
    assert reported_paths(capsys, root, "--config", config) == {
        "src/app.py",
        "src/legacy/old.py",
    }


def test_cli_lists_add_to_config(resources, capsys):
    root = resources / "config_project"
    args = ("--config", config_file(resources, "exclude"), "--exclude", "scripts/*")
    assert reported_paths(capsys, root, *args) == {"src/app.py"}


def test_strict_comes_from_config(resources):
    root = resources / "config_project"
    config = config_file(resources, "strict")
    assert main(["scan", str(root), "--only", "dead", "--config", config]) == 1


def test_config_found_from_parent_directory(resources, capsys):
    assert reported_paths(capsys, resources / "config_project" / "src") == {"app.py"}


def test_explicit_config_replaces_discovered_one(resources, capsys):
    root = resources / "config_project"
    args = ("--config", config_file(resources, "exclude_everything"))
    assert reported_paths(capsys, root, *args) == set()


@pytest.mark.parametrize(
    "name",
    ["invalid_unknown_key", "invalid_similarity", "invalid_exclude_type", "invalid_min_lines"],
)
def test_invalid_config_is_reported(resources, capsys, name):
    root = resources / "config_project"
    assert main(["scan", str(root), "--config", config_file(resources, name)]) == 2
    assert "invalid [tool.snipsnap]" in capsys.readouterr().err
