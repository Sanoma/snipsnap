# snipsnap

Finds dead code and duplicate functions in Python projects. Analysis uses only the standard library (`ast`); the only dependency is `pydantic`, used for the typed report models. It lives in the tool's own environment and adds nothing to the projects you scan.

## Install / run

```sh
# one-off, from any uv project
uvx --from /path/to/snipsnap snipsnap scan

# or install once
uv tool install /path/to/snipsnap
snipsnap scan [PATH ...]
```

## Commands

`snipsnap scan [PATH ...]` analyzes code; `snipsnap show REPORT.json` renders a saved report. Options for `scan`:

| Flag | Meaning |
| --- | --- |
| `--only dead\|duplicates\|test-only` | run one analysis |
| `--exclude GLOB` | skip paths entirely: not analyzed, not reported (repeatable) |
| `--ignore GLOB` | analyze paths but report nothing for them (repeatable) |
| `--min-lines N` | smallest function body considered for duplicates (default 5) |
| `--similarity 0.9` | also report near-duplicates (default `1.0` = exact only) |
| `--include-overrides` | also report unused methods of subclasses |
| `-o, --output FILE` | also write the JSON report to FILE |
| `--json` | print the JSON report to stdout instead of text |
| `--with-source` | embed each finding's source text in the JSON |
| `--strict` | exit 1 if anything is found (for CI) |

View a saved report in the terminal (colored when stdout is a TTY; `-` reads stdin):

```sh
snipsnap scan -o report.json --with-source
snipsnap show report.json [--only dead|duplicates|test-only] [--no-color]
```

`.venv`, `build`, `dist`, caches, etc. are skipped automatically. Inside a git repository, files ignored by `.gitignore` are skipped too.

### Excluding vs. ignoring

- `--exclude GLOB` drops matching files from the scan entirely. Nothing in them is analyzed, so anything they call can look unused.
- `--ignore GLOB` still analyzes matching files but never reports findings for them (dead code, duplicates, test-only). Their code counts as live usage. Use it for code that is only invoked externally but calls into your code, such as Alembic migrations:

```sh
snipsnap scan . --ignore 'alembic/versions/*'
```

Globs are matched against the path relative to the scan root or the file name, and both flags can be repeated.

## JSON report

```jsonc
{
  "version": 1,
  "root": "/abs/path",              // all "path" values are relative to this
  "summary": {"files_scanned": 12, "analyses": ["dead_code", "duplicates", "test_only"],
              "dead_code": 3, "duplicate_groups": 1, "test_only": 1,
              "duration_seconds": 0.042},   // wall-clock scan time; not shown in the text output
  "dead_code": [{
    "id": "dead-1", "kind": "function",   // function | method | class | import
    "name": "helper", "qualname": "Cls.helper",
    "path": "src/m.py", "line": 3, "end_line": 7,   // 1-based, inclusive
    "suggested_action": "...", "source": "..."      // source only with --with-source
  }],
  "test_only": [{                         // production code only tests use
    "id": "test-only-1", "kind": "function", "name": "run", "qualname": "run",
    "path": "src/m.py", "line": 10, "end_line": 20,
    "tested_by": ["tests/test_m.py"],     // empty if only reached via other test-only code
    "suggested_action": "...", "source": "..."
  }],
  "duplicates": [{
    "id": "dup-1", "kind": "exact",       // exact | similar
    "similarity": 1.0,
    "functions": [{"qualname": "a", "path": "src/m.py", "line": 3, "end_line": 7}],
    "suggested_action": "..."
  }]
}
```

The schema is defined by the pydantic models in `src/snipsnap/models.py` (`Report.model_json_schema()` exports a JSON Schema). `show` validates reports against it and rejects unknown fields or versions.

Line ranges cover the whole definition (including decorators for duplicates). For an `import`, the range is the whole import statement, but only the named unused name should be removed. Line numbers shift as code is edited, so re-run after each batch of changes or apply edits bottom-up.

## How it works

**Dead code** is name-based: a function, method, class or import is reported when its name cannot be reached from live code. Code outside any tracked definition (module level, decorated functions, dunder methods, tests) is live, and a function's body only counts once the function itself is live, so unused recursive functions, groups of functions that only call each other, and helpers used only by dead code are all reported. Treated as used: dunder names, decorated definitions, `[project.scripts]` entry points, `main`, test functions, and any short string constant matching the name (`__all__`, `getattr(x, "name")`, forward-reference annotations). References from tests count as usage. Dynamic or framework-driven use can still produce false positives, so review before deleting.

**Only used by tests** is a separate category for functions, methods and classes in non-test code whose name is referenced only from test files (files named `test_*.py`/`*_test.py`/`conftest.py` or under a `tests` directory). Code reachable only from other test-only code is included, so helpers of a test-only function (and mutually recursive groups) are reported too; `tested_by` lists the test files that reference each item. Nothing is deleted for you: the suggested action is to remove the code together with its tests, unless it is intentional public API. This needs both production and test files in the scan, so pass every Python directory.

**Duplicates** compare normalized ASTs: function names, docstrings, type annotations, decorators and the names of parameters/locals are ignored. With `--similarity`, functions whose structure is similar enough are grouped too.

## Agent skill

`skills/fix-snipsnap-report/SKILL.md` is a Claude Code skill that applies a report to the codebase (verifies each finding, deletes or merges code, re-scans). It lives here so it changes together with the report format. Link it into your user skills to use it everywhere:

```sh
ln -s "$PWD/skills/fix-snipsnap-report" ~/.claude/skills/fix-snipsnap-report
```

A test checks the skill against the report schema and CLI flags, so it fails when they drift apart.

## Develop

```sh
uv run pytest
uv run ruff format .
uv run ruff check .
uv run ty check
```
