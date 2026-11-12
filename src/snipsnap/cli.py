"""Command line interface.

snipsnap scan [PATH ...] [options]   scan and report
snipsnap show REPORT.json            pretty-print a saved JSON report
"""

import argparse
import os
import sys
import time
from pathlib import Path

from .config import ConfigError, load_config
from .deadcode import find_dead_code
from .duplicates import find_duplicates
from .models import Analysis, Report
from .project import common_root, entry_point_names, load_modules
from .report import build_report, render_text
from .testonly import find_only_tested

_ANALYSIS_BY_OPTION: dict[str, Analysis] = {
    "dead": "dead_code",
    "duplicates": "duplicates",
    "test-only": "test_only",
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
    p.add_argument("--only", choices=list(_ANALYSIS_BY_OPTION), help="run just one of the analyses")
    p.add_argument(
        "--config",
        type=_existing_path,
        metavar="FILE",
        help="pyproject.toml whose [tool.snipsnap] table to use "
        "(default: the nearest one at or above the scanned directory)",
    )
    p.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="only scan files matching this glob, added to [tool.snipsnap] include (repeatable)",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="glob of paths to skip, matched against the relative path or file name (repeatable)",
    )
    p.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="GLOB",
        help="glob of paths to analyze but not report findings for (repeatable)",
    )
    p.add_argument(
        "--ignore-finding",
        action="append",
        default=[],
        metavar="HASH",
        help="hash of a single finding to leave out of the report, "
        "added to [tool.snipsnap] ignore-findings (repeatable)",
    )
    p.add_argument(
        "--min-lines",
        type=int,
        help="ignore functions with fewer body lines when looking for duplicates (default: 5)",
    )
    p.add_argument(
        "--similarity",
        type=_similarity,
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
    p.add_argument(
        "--only", choices=list(_ANALYSIS_BY_OPTION), help="show just one of the analyses"
    )
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
    started = time.perf_counter()
    root = common_root(args.paths)
    try:
        config = load_config(root, args.config)
    except ConfigError as exc:
        print(f"snipsnap: {exc}", file=sys.stderr)
        return 2
    # list options add to the configured ones; scalars override them
    min_lines = args.min_lines if args.min_lines is not None else config.min_lines or 5
    similarity = args.similarity if args.similarity is not None else config.similarity or 1.0
    include_overrides = args.include_overrides or config.include_overrides
    strict = args.strict or config.strict

    modules = load_modules(
        args.paths,
        [*config.exclude, *args.exclude],
        root=root,
        ignores=[*config.ignore, *args.ignore],
        includes=[*config.include, *args.include],
    )
    entry_points = set[str]().union(*map(entry_point_names, args.paths))
    analyses = (
        (_ANALYSIS_BY_OPTION[args.only],) if args.only else tuple(_ANALYSIS_BY_OPTION.values())
    )

    # the test-only analysis needs the dead code to tell "unused" from "only tested"
    found_dead = (
        find_dead_code(modules, entry_points, include_overrides=include_overrides)
        if "dead_code" in analyses or "test_only" in analyses
        else []
    )
    dead = found_dead if "dead_code" in analyses else []
    dupes = (
        find_duplicates(modules, min_lines=min_lines, similarity=similarity)
        if "duplicates" in analyses
        else []
    )
    only_tested = (
        find_only_tested(modules, entry_points, found_dead, include_overrides=include_overrides)
        if "test_only" in analyses
        else []
    )

    report = build_report(
        root,
        len(modules),
        dead,
        dupes,
        only_tested=only_tested,
        analyses=analyses,
        with_source=args.with_source,
        duration_seconds=round(time.perf_counter() - started, 3),
        ignored_hashes={*config.ignore_findings, *args.ignore_finding},
    )

    if args.output:
        args.output.write_text(report.to_json() + "\n", encoding="utf-8")
    if args.json:
        print(report.to_json())
    else:
        print(render_text(report, color=_use_color(), only=args.only))
        if args.output:
            print(f"\nJSON report written to {args.output}")

    found = report.dead_code or report.duplicates or report.test_only
    return 1 if strict and found else 0


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

    print(render_text(report, color=_use_color() and not args.no_color, only=args.only))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
