import json
import shutil

from snipsnap.cli import main

DUPLICATES = """\
def total(items):
    acc = 0
    for item in items:
        acc += item
    return acc


def add_all(values):
    result = 0
    for v in values:
        result += v
    return result
"""


def scan(capsys, *args: str) -> dict:
    main(["scan", *args, "--json"])
    return json.loads(capsys.readouterr().out)


def hashes(report: dict) -> dict[str, str]:
    return {
        **{f["qualname"]: f["hash"] for f in report["dead_code"] + report["test_only"]},
        **{g["id"]: g["hash"] for g in report["duplicates"]},
    }


def test_hashes_ignore_line_numbers(resources, tmp_path, capsys):
    project = tmp_path / "project"
    shutil.copytree(resources / "dead_code_project", project)
    (project / "dups.py").write_text(DUPLICATES)
    before = scan(capsys, str(project), "--min-lines", "4")

    for file in ("pkg/a.py", "dups.py"):
        path = project / file
        path.write_text("# a new comment\n\n\n" + path.read_text())
    after = scan(capsys, str(project), "--min-lines", "4")

    assert before["duplicates"] and before["dead_code"]
    assert hashes(before) == hashes(after)
    moved = {f["qualname"]: f["line"] for f in after["dead_code"]}
    assert moved["dead_func"] == 16


def test_duplicate_hash_survives_edits_that_keep_the_duplication(tmp_path, capsys):
    (tmp_path / "dups.py").write_text(DUPLICATES)
    before = scan(capsys, str(tmp_path), "--min-lines", "4")["duplicates"][0]
    (tmp_path / "dups.py").write_text(
        DUPLICATES.replace("acc += item", "acc += item * 2").replace(
            "result += v", "result += v * 2"
        )
    )
    after = scan(capsys, str(tmp_path), "--min-lines", "4")["duplicates"][0]
    assert before["hash"] == after["hash"]


def test_hashes_differ_between_findings(resources, capsys):
    report = scan(capsys, str(resources / "dead_code_project"))
    found = [f["hash"] for f in report["dead_code"]]
    assert len(found) == len(set(found)) > 1


def test_ignore_finding_flag_drops_only_that_finding(resources, capsys):
    project = str(resources / "dead_code_project")
    full = scan(capsys, project)
    target = next(f for f in full["dead_code"] if f["qualname"] == "dead_func")

    report = scan(capsys, project, "--ignore-finding", target["hash"])
    assert {f["qualname"] for f in full["dead_code"]} - {
        f["qualname"] for f in report["dead_code"]
    } == {"dead_func"}
    assert report["summary"]["dead_code"] == len(report["dead_code"]) == len(full["dead_code"]) - 1
    assert [f["id"] for f in report["dead_code"]] == [
        f"dead-{i}" for i in range(1, len(report["dead_code"]) + 1)
    ]


def test_ignore_findings_from_config_and_duplicates(tmp_path, capsys):
    (tmp_path / "dups.py").write_text(DUPLICATES)
    group = scan(capsys, str(tmp_path), "--min-lines", "4")["duplicates"][0]
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.snipsnap]\nmin-lines = 4\nstrict = true\nignore-findings = ["{group["hash"]}"]\n'
    )
    assert main(["scan", str(tmp_path), "--only", "duplicates"]) == 0
    capsys.readouterr()
    assert scan(capsys, str(tmp_path), "--only", "duplicates")["duplicates"] == []


def test_ignored_dead_code_still_counts_for_test_only_analysis(resources, capsys):
    project = str(resources / "tested_project")
    full = scan(capsys, project, "--only", "test-only")
    item = full["test_only"][0]
    report = scan(capsys, project, "--only", "test-only", "--ignore-finding", item["hash"])
    assert [f["qualname"] for f in report["test_only"]] == [
        f["qualname"] for f in full["test_only"][1:]
    ]


def test_text_output_shows_hash(resources, capsys):
    project = resources / "dead_code_project"
    report = scan(capsys, str(project))
    main(["scan", str(project), "--only", "dead"])
    text = capsys.readouterr().out
    assert all(f["hash"] in text for f in report["dead_code"])


def test_hash_does_not_depend_on_scan_root(resources, tmp_path, capsys):
    project = tmp_path / "elsewhere" / "dead_code_project"
    shutil.copytree(resources / "dead_code_project", project)
    original = hashes(scan(capsys, str(resources / "dead_code_project")))
    assert hashes(scan(capsys, str(project))) == original
