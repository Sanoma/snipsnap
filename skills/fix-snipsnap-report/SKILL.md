---
name: fix-snipsnap-report
description: "Apply the findings of a snipsnap JSON report (dead code, test-only code, duplicate functions) to a Python codebase: verify each finding, delete or merge the code, clean up imports, and confirm with tests, linters and a re-scan. Use when the user provides a snipsnap report.json, says 'fix the snipsnap report', 'remove the dead code snipsnap found', 'clean up snipsnap findings', or asks to run snipsnap and fix what it finds."
---

# Fixing a snipsnap report

snipsnap finds dead code, code only used by tests, and duplicate functions. This skill turns its `report.json` into code changes. The report is **name-based and static**, so every finding is a *candidate*: verify before you delete.

## 1. Get and check the report

- If the user gave a report path, use it. Otherwise produce one (`--with-source` makes stale-report checks easy):
  - `snipsnap scan --with-source -o report.json <dirs>`
  - If `snipsnap` is not on PATH, this file lives in the snipsnap checkout: resolve its real path (it is usually symlinked into `~/.claude/skills`), and run `uv run --project <checkout> snipsnap scan ...` or install it with `uv tool install --editable <checkout>`.
  - Pass **every** Python directory including tests, or the test-only analysis is empty.
  - snipsnap reads an optional `[tool.snipsnap]` table in the nearest `pyproject.toml` (`include`, `exclude`, `ignore`, ...). **Don't assume it exists.** Check for it first (`grep -n 'tool.snipsnap' pyproject.toml`, also in parent directories of the scan root). If it is there, a plain `scan` honours it, so don't pass flags that undo it. If it is missing, snipsnap runs with built-in defaults that know nothing about this project's migrations, generated or vendored code, or entry points: tell the user, and ask which paths to `--ignore` (analyzed, not reported) or `--exclude` (not analyzed) before scanning. Pass those as command line flags; don't create the config table unless asked.
  - A report the user hands you doesn't record which config produced it. Ask how it was generated, or re-scan with the setup above.
  - Exit code 2 means a config or argument error: fix that, don't ignore it.
  - Files in `.gitignore` are skipped automatically.
- Require `version == 1`; stop and tell the user otherwise.
- All finding `path`s are relative to the report's `root`, not the cwd. Resolve them against `root`, and refuse to touch files outside it.
- If the report is older than the files it points at, or a finding's `source` no longer matches the file at `path:line-end_line`, the line numbers are stale. Re-scan instead of guessing.

List findings per file, **highest line first** (deleting bottom-up keeps earlier line numbers valid):

```sh
jq -r '[.dead_code[]|{id,kind,path,line,end_line,qualname}] | group_by(.path)[]
       | sort_by(-.line)[] | "\(.path):\(.line)-\(.end_line) \(.kind) \(.qualname) \(.id)"' report.json
```

## 2. Prepare

- Run `git status` and note the current branch. If the tree has unrelated uncommitted changes, tell the user and work only on the files in the report; never stage those files later.
- Find the project's check commands: CI config (`.github/workflows`), `pyproject.toml`, README, Makefile/justfile. You need four: **formatter**, **linter**, **type checker** (if the project has one) and **tests** (e.g. `ruff format`, `ruff check`, `ty check`/`mypy`, `pytest`; prefix with `uv run` in uv projects). Prefer exactly what CI runs.
- Establish a baseline by running them all before editing. Record what already fails so you don't blame yourself for it.

## 3. Dead code (`dead_code`)

Apply automatically once each finding passes verification. For every finding:

1. **Verify it is really unused.** Grep the whole repo (not just `.py`) for the bare `name`: configs, YAML/TOML, templates, docs, CLI entry points, `getattr`/string lookups, plugin registries, serialization (pydantic/ORM field and hook names), framework conventions (Django, pytest fixtures, Click commands, Lambda handlers, `__all__`). Same-name definitions elsewhere hide each other in snipsnap, so a hit on an unrelated same-named thing does not make it used, but a dynamic reference does.
2. **Library or public API?** If the project is a published package and the item is exported (in `__init__.py`, `__all__`, or documented), skip it and report it instead.
3. **Delete it** with Edit, not sed:
   - function/method/class: remove lines `line`..`end_line`, plus a directly preceding comment that belongs to it, and collapse the extra blank lines so spacing stays PEP 8.
   - if removing leaves an empty body (a class with only that method, an `if`/`else` branch), put `pass` or `...` there rather than leaving invalid code.
   - `import`: remove only the unused *name*. For `import a, b` or `from x import a, b` keep the others; drop the whole statement only if nothing remains. Skip imports with `# noqa`, imports inside `try/except ImportError`, and imports that exist for side effects.
   - `variable`: remove the assignment statement (`line`..`end_line`). For chained assignments (`a = b = 1`) remove only the reported name. If the right-hand side has side effects (a call that registers, writes or connects), keep it as a bare expression statement instead. Skip variables that settings loaders, templates or plugins read by name (e.g. Django `settings.py`).
   
4. **Clean up the fallout.** Deleting code can orphan imports. Run the linter's unused-import fix (`ruff check --select F401 --fix <files>`) or remove them by hand.

If a finding is a false positive because the code is invoked from outside the scanned code (migrations, framework entry points, scripts), do not delete it and do not edit `[tool.snipsnap]` yourself (it may not exist). List it and suggest the user either add an `ignore` glob (analyzed, not reported; its calls still count as usage) or, for code that should not be analyzed at all, `exclude`, or, for a single finding, put its `hash` in `ignore-findings`. Each is available in `[tool.snipsnap]` or as an `--ignore`/`--exclude`/`--ignore-finding` flag.

Deleting code can make *more* code dead (callees of the removed code). That is expected: after finishing the pass, re-scan and repeat until no new dead-code findings appear (cap at 5 rounds, and say so if you hit it).

## 4. Test-only code (`test_only`)

These have no production callers, only tests. Removing them means **deleting tests too**, so do not do this silently.

- Show the user the list (`qualname`, `tested_by`) and ask which to remove, unless they already said to remove all.
- For each approved item: delete the definition, then delete only the test functions/fixtures/imports that exist to exercise it. Remove a whole test file only if nothing else is left in it. A test that also covers other code stays; just drop the part that used the removed item.
- Skip anything that looks like intentional public API and list it for the user.

## 5. Duplicates (`duplicates`)

Merge only after showing the user the plan, unless they said to merge everything.

- **`similar` groups are not equivalent.** Never auto-merge them. Show the differences and let the user decide.
- **`exact` groups** are equal after ignoring function names, docstrings, annotations, decorators and parameter/local names. Before merging check what that normalization hides:
  - callers passing **keyword arguments** that use a different parameter name;
  - **decorators** that differ (`@staticmethod`, `@lru_cache`, `@property`, framework decorators);
  - methods in different classes: they depend on `self`/`cls` state, so do not hoist them blindly. Report these for manual refactoring unless the merge is trivial.
- Pick one canonical copy: prefer the one with the best name, docstring and annotations, living in the module the others can import without creating an import cycle. Delete the others, update their callers and imports to use the canonical one, and keep the canonical copy's docstring and types.
- If the callers are in many files, do it one duplicate at a time and run the tests between merges.

## 6. Verify

1. Run the formatter (apply it, only to the files you changed), then the linter, type checker and tests, the same ones as the baseline. Any new failure is yours: fix it or revert that edit. Do not proceed to step 7 while anything is newly red.
2. Re-run snipsnap with the same arguments. Findings you fixed must be gone, and nothing should be newly reported except legitimate cascades.
3. `git diff --stat` to confirm only intended files changed.

## 7. Report back, then offer to ship

Short summary: counts removed per category, anything **skipped with the reason** (public API, dynamic use found, stale line numbers, `similar` duplicates, needs user decision), and baseline vs. final status of format, lint, types and tests.

Then, only if everything in step 6 passed, ask the user whether to commit and push / open a PR. Don't commit, push or open a PR before they agree, and a yes covers only what they agreed to.

- **Commit:** stage the changed files by name (never `git add -A`, never unrelated pre-existing changes), and commit with a message summarizing what was removed or merged, following the repo's message style. If the current branch is the default branch (`main`/`master`), create a branch first (e.g. `snipsnap/cleanup-<date>`) unless the user says otherwise. Never skip hooks; if a hook fails, fix the cause and make a new commit.
- **Push / PR:** push the branch with `-u`, never force-push. Open the PR with `gh pr create` if asked: the body lists counts per category, what was skipped and why, and the check results. Return the PR URL.
- If checks fail and you can't fix them, don't offer to ship; report the failures instead.

## Rules of thumb

- When unsure whether something is used, leave it and list it. A false deletion costs more than a missed one.
- Edit files with the Edit tool, bottom-up per file; never regenerate whole files.
- Never commit, push or open a PR without the user's agreement (step 7).
- Don't "improve" surrounding code. Only change what the report calls for plus the direct cleanup it causes.
