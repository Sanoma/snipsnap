"""Build the JSON report and render it for the terminal."""

from pathlib import Path

from .models import (
    Analysis,
    DeadCodeFinding,
    DeadItem,
    DuplicateFinding,
    DuplicateFunction,
    DuplicateGroup,
    FunctionRef,
    OnlyTestedFinding,
    OnlyTestedItem,
    Report,
    Summary,
)

_DEAD_ACTIONS = {
    "import": (
        "remove the unused name from the import "
        "(drop the whole statement if nothing else is imported)"
    ),
    "function": "delete the function if it is truly unused (check for dynamic use first)",
    "method": "delete the method if it is truly unused (check for dynamic use first)",
    "class": "delete the class if it is truly unused (check for dynamic use first)",
}
_TEST_ONLY_ACTION = (
    "no production code uses this; delete it together with the tests that exercise it "
    "(see tested_by), or keep it if it is intentional public API"
)
_DUP_ACTION = "keep one implementation, delete the others, and update their callers to use it"


def _source(root: Path, path: str, line: int, end_line: int) -> str | None:
    try:
        lines = (root / path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    return "\n".join(lines[line - 1 : end_line])


def build_report(
    root: Path,
    n_files: int,
    dead: list[DeadItem],
    dupes: list[DuplicateGroup],
    with_source: bool = False,
    analyses: tuple[Analysis, ...] = ("dead_code", "duplicates", "test_only"),
    only_tested: list[OnlyTestedItem] | None = None,
) -> Report:
    def source(item: DeadItem | FunctionRef) -> str | None:
        return _source(root, item.path, item.line, item.end_line) if with_source else None

    dead_findings = [
        DeadCodeFinding(
            **d.model_dump(),
            id=f"dead-{i}",
            suggested_action=_DEAD_ACTIONS[d.kind],
            source=source(d),
        )
        for i, d in enumerate(dead, 1)
    ]
    dup_findings = [
        DuplicateFinding(
            id=f"dup-{i}",
            kind=g.kind,
            similarity=g.similarity,
            functions=[DuplicateFunction(**f.model_dump(), source=source(f)) for f in g.functions],
            suggested_action=_DUP_ACTION,
        )
        for i, g in enumerate(dupes, 1)
    ]
    test_only_findings = [
        OnlyTestedFinding(
            **t.model_dump(),
            id=f"test-only-{i}",
            suggested_action=_TEST_ONLY_ACTION,
            source=source(t),
        )
        for i, t in enumerate(only_tested or [], 1)
    ]
    return Report(
        root=str(root),
        summary=Summary(
            files_scanned=n_files,
            analyses=list(analyses),
            dead_code=len(dead_findings),
            duplicate_groups=len(dup_findings),
            test_only=len(test_only_findings),
        ),
        dead_code=dead_findings,
        duplicates=dup_findings,
        test_only=test_only_findings,
    )


class _Style:
    def __init__(self, color: bool):
        self.color = color

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def bold(self, t: str) -> str:
        return self._wrap("1", t)

    def dim(self, t: str) -> str:
        return self._wrap("2", t)

    def red(self, t: str) -> str:
        return self._wrap("31", t)

    def yellow(self, t: str) -> str:
        return self._wrap("33", t)

    def cyan(self, t: str) -> str:
        return self._wrap("36", t)


def _span(path: str, line: int, end_line: int) -> str:
    return f"{path}:{line}" if end_line == line else f"{path}:{line}-{end_line}"


def render_text(report: Report, color: bool = False, only: str | None = None) -> str:
    st = _Style(color)
    out: list[str] = []
    s = report.summary
    out.append(st.dim(f"Scanned {s.files_scanned} file(s) under {report.root}"))

    if only in (None, "dead") and "dead_code" in s.analyses:
        out.append("")
        out.append(st.bold(f"Dead code ({len(report.dead_code)})"))
        for d in report.dead_code:
            where = st.cyan(_span(d.path, d.line, d.end_line))
            out.append(f"  {where}  {st.red('unused ' + d.kind)} {d.qualname}  {st.dim(d.id)}")
        if not report.dead_code:
            out.append("  none")

    if only in (None, "test-only") and "test_only" in s.analyses:
        out.append("")
        out.append(st.bold(f"Only used by tests ({len(report.test_only)})"))
        for t in report.test_only:
            where = st.cyan(_span(t.path, t.line, t.end_line))
            out.append(f"  {where}  {st.yellow(t.kind)} {t.qualname}  {st.dim(t.id)}")
            if t.tested_by:
                out.append(st.dim(f"    tested by: {', '.join(t.tested_by)}"))
        if not report.test_only:
            out.append("  none")

    if only in (None, "duplicates") and "duplicates" in s.analyses:
        out.append("")
        out.append(st.bold(f"Duplicate functions ({len(report.duplicates)} group(s))"))
        for g in report.duplicates:
            label = "exact" if g.kind == "exact" else f"similar {g.similarity:.0%}"
            out.append(f"  {st.yellow(label)}  {st.dim(g.id)}")
            for f in g.functions:
                where = st.cyan(_span(f.path, f.line, f.end_line))
                out.append(f"    {where}  {f.qualname}")
        if not report.duplicates:
            out.append("  none")
    return "\n".join(out)
