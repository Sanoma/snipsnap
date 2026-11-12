import json

import pytest

from snipsnap.cli import main


def test_scan_json_and_strict(resources, capsys):
    project = resources / "dead_code_project"
    code = main(["scan", str(project), "--json", "--strict"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert out["version"] == 1
    assert out["summary"]["dead_code"] == len(out["dead_code"])
    assert {d["name"] for d in out["dead_code"]} >= {"dead_func", "DeadClass"}
    assert main(["scan", str(project / "pkg" / "b.py"), "--only", "dead"]) == 0


def test_report_locations_and_source(resources, tmp_path):
    project = resources / "dead_code_project"
    out_file = tmp_path / "report.json"
    main(["scan", str(project), "-o", str(out_file), "--with-source"])
    report = json.loads(out_file.read_text())
    assert report["root"] == str(project.resolve())

    by_name = {d["qualname"]: d for d in report["dead_code"]}
    dead = by_name["dead_func"]
    assert (dead["path"], dead["line"], dead["end_line"]) == ("pkg/a.py", 13, 14)
    assert dead["source"] == "def dead_func():\n    return 1"
    assert by_name["DeadClass.method"]["kind"] == "method"
    assert by_name["os"]["kind"] == "import"


def test_scan_reports_test_only_section(resources, capsys):
    project = resources / "tested_project"
    assert main(["scan", str(project), "--json", "--only", "test-only", "--with-source"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["analyses"] == ["test_only"]
    assert out["summary"]["test_only"] == len(out["test_only"]) == 4
    assert out["dead_code"] == [] and out["duplicates"] == []
    first = next(f for f in out["test_only"] if f["qualname"] == "only_tests")
    assert first["id"].startswith("test-only-") and first["tested_by"] == ["tests/test_lib.py"]
    assert first["source"].startswith("def only_tests")

    assert main(["scan", str(project), "--strict", "--only", "test-only"]) == 1
    text = capsys.readouterr().out
    assert "Only used by tests (4)" in text and "tested by: tests/test_lib.py" in text
    assert "Dead code" not in text


def test_show_roundtrip(resources, tmp_path, capsys):
    out_file = tmp_path / "r.json"
    main(["scan", str(resources / "dead_code_project"), "-o", str(out_file)])
    capsys.readouterr()
    assert main(["show", str(out_file), "--no-color"]) == 0
    text = capsys.readouterr().out
    assert "pkg/a.py:13-14" in text and "unused function dead_func" in text


def test_show_missing_report(tmp_path):
    assert main(["show", str(tmp_path / "missing.json")]) == 2


def test_show_rejects_invalid_report(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 2}')
    assert main(["show", str(bad)]) == 2
    assert "cannot read report" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [[], ["scan", "{tmp}/nope"], ["scan", "{tmp}", "--similarity", "1.5"], ["show"]],
    ids=["no-command", "missing-path", "similarity-out-of-range", "show-without-report"],
)
def test_argument_errors_are_handled_by_argparse(tmp_path, argv):
    with pytest.raises(SystemExit) as exc:
        main([arg.format(tmp=tmp_path) for arg in argv])
    assert exc.value.code == 2


def test_missing_path_is_explained(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["scan", str(tmp_path / "nope")])
    assert "no such path" in capsys.readouterr().err
