"""Command line interface.

snipsnap scan [PATH ...] [options]   scan and report
snipsnap show REPORT.json            pretty-print a saved JSON report
"""

import argparse
import os
import sys
from pathlib import Path

from .deadcode import find_dead_code
from .duplicates import find_duplicates
from .models import Analysis, Report
from .project import common_root, entry_point_names, load_modules
from .report import build_report, render_text
from .testonly import find_only_tested

_ANALYSES = ["dead", "duplicates", "test-only"]
_ANALYSES_BY_ONLY: dict[str | None, tuple[Analysis, ...]] = {
    None: ("dead_code", "duplicates", "test_only"),
    "dead": ("dead_code",),
    "duplicates": ("duplicates",),
    "test-only": ("test_only",),
}


def _use_color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _existing_path(value: str) -> Path:
    path = Path(value)
    if not path.exists():
        raise argparse.ArgumentTypeError(f"no such path: {value}")
    return path


def _similarity(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value}") from None
    if not 0 < number <= 1:
        raise argparse.ArgumentTypeError("must be in (0, 1]")
    return number


def _add_scan_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "paths",
        nargs="*",
        type=_existing_path,
        default=[Path(".")],
        help="files or directories to scan (default: current directory)",
    )
    p.add_argument("--only", choices=_ANALYSES, help="run just one of the analyses")
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="glob of paths to skip, matched against the relative path or file name (repeatable)",
    )
    p.add_argument(
        "--min-lines",
        type=int,
        default=5,
        help="ignore functions with fewer body lines when looking for duplicates (default: 5)",
    )
    p.add_argument(
        "--similarity",
        type=_similarity,
        default=1.0,
        metavar="0-1",
        help="also report near-duplicates at or above this similarity; "
        "1.0 means exact (normalized) matches only (default: 1.0)",
    )
    p.add_argument(
        "--include-overrides",
        action="store_true",
        help="also report unused methods of classes that have base classes "
        "(skipped by default as they may override framework hooks)",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="FILE",
        help="write the JSON report to FILE (text summary still printed)",
    )
    p.add_argument(
        "--json", action="store_true", help="print the JSON report to stdout instead of text"
    )
    p.add_argument(
        "--with-source",
        action="store_true",
        help="embed the source text of each finding in the JSON report",
    )
    p.add_argument("--strict", action="store_true", help="exit with status 1 if anything is found")


def _add_show_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("report", help="report file, or - for stdin")
    p.add_argument("--only", choices=_ANALYSES, help="show just one of the analyses")
    p.add_argument("--no-color", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snipsnap",
        description="Find dead code and duplicate functions in a Python project.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    scan = commands.add_parser(
        "scan",
        help="scan code and report findings",
        description="Scan code for dead code and duplicate functions.",
    )
    _add_scan_arguments(scan)
    scan.set_defaults(handler=_scan)

    show = commands.add_parser(
        "show",
        help="pretty-print a saved JSON report",
        description="Pretty-print a snipsnap JSON report.",
    )
    _add_show_arguments(show)
    show.set_defaults(handler=_show)
    return parser


def _scan(args: argparse.Namespace) -> int:
    root = common_root(args.paths)
    modules = load_modules(args.paths, args.exclude, root)
    entry_points: set[str] = set()
    for path in args.paths:
        entry_points |= entry_point_names(path)

    found_dead = (
        []
        if args.only == "duplicates"
        else find_dead_code(modules, entry_points, args.include_overrides)
    )
    dead = found_dead if args.only in (None, "dead") else []
    dupes = (
        find_duplicates(modules, args.min_lines, args.similarity)
        if args.only in (None, "duplicates")
        else []
    )
    only_tested = (
        find_only_tested(modules, entry_points, found_dead, args.include_overrides)
        if args.only in (None, "test-only")
        else []
    )
    analyses = _ANALYSES_BY_ONLY[args.only]
    report = build_report(root, len(modules), dead, dupes, args.with_source, analyses, only_tested)

    if args.output:
        args.output.write_text(report.to_json() + "\n", encoding="utf-8")
    if args.json:
        print(report.to_json())
    else:
        print(render_text(report, _use_color(), args.only))
        if args.output:
            print(f"\nJSON report written to {args.output}")

    return 1 if args.strict and (dead or dupes or only_tested) else 0


def _show(args: argparse.Namespace) -> int:
    try:
        text = (
            sys.stdin.read()
            if args.report == "-"
            else Path(args.report).read_text(encoding="utf-8")
        )
        report = Report.model_validate_json(text)
    except (OSError, ValueError) as exc:  # pydantic.ValidationError is a ValueError
        print(f"snipsnap: cannot read report: {exc}", file=sys.stderr)
        return 2

    print(render_text(report, _use_color() and not args.no_color, args.only))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
