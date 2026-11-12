from snipsnap.duplicates import find_duplicates
from snipsnap.project import load_modules


def test_duplicates_ignore_names_docstrings_and_annotations(resources):
    modules = load_modules([resources / "duplicates_ignoring_names.py"], [])
    groups = find_duplicates(modules, min_lines=3)
    assert len(groups) == 1
    assert {f.qualname for f in groups[0].functions} == {"total", "K.add_all"}


def test_near_duplicates_need_a_similarity_threshold(resources):
    modules = load_modules([resources / "near_duplicates.py"], [])
    assert find_duplicates(modules, min_lines=3) == []
    groups = find_duplicates(modules, min_lines=3, similarity=0.8)
    assert [g.kind for g in groups] == ["similar"]


def test_duplicate_range_includes_decorators(resources):
    modules = load_modules([resources / "decorated_duplicates.py"], [])
    (group,) = find_duplicates(modules, min_lines=3)
    assert (group.functions[0].line, group.functions[0].end_line) == (1, 6)


def test_protocol_stubs_are_not_duplicates(resources):
    modules = load_modules([resources / "protocol_stubs.py"], [])
    (group,) = find_duplicates(modules, min_lines=1)
    assert [f.qualname for f in group.functions] == ["C.f", "C.g"]
