# ADR: pipeline-status `archive` & `history` subcommands

**Status**: Proposed
**Date**: 2026-05-23

## Context

The `pipeline-status` CLI has been delivered in two prior increments:

- **v1** (see `.claude/state/archive/pipeline-status-cli/adr.md`) introduced the package layout `pipeline_status/{__init__,__main__,inspectors,stage,formatting}.py`, an `ArtefactResult` dataclass, per-artefact inspectors, `derive_stage(...)`, and the human-readable one-shot report. Exit code 2 on missing `.claude/state/`; otherwise 0.
- **v2** (see `.claude/state/archive/watch-mode/adr.md`) added `pipeline_status/watch.py` and a `--watch [--interval N]` mode driven by a `WatchConfig` dataclass with injected `sleep_fn`/`clock_fn`/`max_iterations` for testability. The one-shot stdout is byte-identical to v1.

The **actual** master code in `pipeline_status/__main__.py` (read directly while drafting this ADR; the v2 ADR's reference to `formatting.render(...)` is a misnomer) uses:

- `_build_parser()` returning an `argparse.ArgumentParser` with `--watch` and `--interval` as flat optionals,
- `_interval_type(raw: str) -> int` as the argparse converter for `--interval`,
- `_run_one_shot() -> int` which inspects, derives stage, and prints the report **inline** (no `format_report` call): header `"Pipeline Status"` + `"==============="` + blank line + `f"  {format_artefact_row(result)}"` per artefact + blank line + `f"  {format_stage_line(stage)}"`,
- `_locate_state_dir()` returning `Path.cwd()/.claude/state` if it `is_dir()` else `None`,
- a **lazy import** of `WatchConfig`/`run_watch` from `pipeline_status.watch` inside `main()` only when `--watch` is set.

The available inspector module-level names are `inspect_feature_request`, `inspect_requirements`, `inspect_adr`, `inspect_tasks`, `inspect_worktrees` (note: `inspect_tasks` and `inspect_worktrees`, **not** `inspect_tasks_json` / `inspect_worktrees_json` as the requirements text loosely refers to them — engineers must use the master names). `derive_stage` consumes a `Mapping[str, ArtefactResult]`, not a list. `formatting.format_report(...)` exists but is **not** used by `_run_one_shot()`; the one-shot output is assembled by the inline `print()` calls listed above. v3 must preserve this exact byte-for-byte sequence for the no-subcommand path.

v3 adds two new subcommands — `archive` (write side: snapshot live state) and `history` / `history NAME` (read side: list past archives, or render one). Implementation must remain stdlib-only, must not regress the v1 one-shot path or the v2 watch path, and must be testable under `unittest` + `tempfile` only.

## Decision Drivers

- **Parallel-fan-out constraint (driver #0)**: the PM agent will decompose this ADR into 5 tasks (A–E) and dispatch them to 5 Engineer subagents running concurrently on isolated `git worktree`s. The ADR is therefore organised so that **each task owns exactly one production file plus one test file**, no two tasks edit the same file, and each task's tests import only its own module plus stdlib and frozen master code (`inspectors`, `stage`, `formatting`). Inter-task contracts are documented as exact public function signatures so engineers can write `import` statements against the contract without waiting for a dependency's PR to merge.
- **Boring, stdlib-only**: `argparse`, `json`, `pathlib`, `datetime`, `shutil`, `re`, `sys`, `os`. No `tomllib`, no third-party slug library, no new packaging.
- **Byte-identical regression**: `pipeline-status` (no args) and `pipeline-status --watch [--interval N]` must produce **byte-identical stdout** to v1/v2 for the same filesystem state.
- **Read-only outside the destination archive directory**: no PID files, lockfiles, caches, indexes, or manifests anywhere.
- **Determinism**: `archive`'s stdout confirmation line and `history`'s table must be byte-identical across runs given identical filesystem state and timezone (modulo ANSI on TTY).
- **Performance**: `history` over ≤100 archives in <200 ms p99; `archive` of all 5 artefacts in <200 ms; `history NAME` within v1's <500 ms budget.

## Considered Options

### Decision 1: Subcommand wiring strategy in `__main__.py`

The existing parser is a flat `ArgumentParser` with `--watch` / `--interval` optionals. We need `archive` and `history` as subcommands **without** regressing `pipeline-status` (no args) or `pipeline-status --watch`.

- **Option A: Convert to mandatory subparsers with a default "status" subcommand**
  - Pros: cleanest argparse idiom.
  - Cons: changes `--help` layout (a `status` row appears); breaks the byte-identical contract on `--help`; users must learn `pipeline-status status` for backwards compatibility.
- **Option B: Add `parser.add_subparsers(dest="cmd", required=False)` with two subparsers (`archive`, `history`); dispatch on `args.cmd`. When `args.cmd is None`, fall through to the existing `--watch`/one-shot dispatch unchanged.**
  - Pros: zero impact on the no-args and `--watch` paths; the `--watch` and `--interval` flags stay on the top-level parser; argparse naturally rejects unknown subcommands with exit 2 and a usage error to stderr; argparse naturally rejects `--watch` combined with a subcommand because neither subparser declares `--watch`.
  - Cons: top-level `--help` now mentions `{archive,history}` in the usage line — this is the only intended `--help` delta and is explicitly required by FR-4 ("`--help` MUST list `archive` and `history`").
- **Chosen**: **Option B**. This is the standard "optional subcommand with default behaviour" idiom and satisfies the byte-identical-stdout constraint (`--help` *output* is not part of that contract; only one-shot and `--watch` *report* stdout is).

Wiring detail (lives entirely in Task D, `pipeline_status/__main__.py`):

```python
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipeline-status", ...)  # unchanged top-level help text
    parser.add_argument("--watch", action="store_true", ...)        # unchanged
    parser.add_argument("--interval", type=_interval_type, ...)     # unchanged
    subparsers = parser.add_subparsers(dest="cmd", required=False)

    # Task A registers itself on this parser:
    from pipeline_status.archive import add_archive_subparser
    add_archive_subparser(subparsers)

    # Task B+C wiring (history table + detail share one subparser):
    from pipeline_status.history import add_history_subparser
    add_history_subparser(subparsers)

    return parser
```

The two `add_*_subparser` functions are public registration helpers exposed by the owning modules; this is the **single** central wiring point and it lives only in Task D. Tasks A/B don't import each other; they only export their registration function plus their action callable.

### Decision 2: Slugifier ownership (one module owns it; others import)

FR-11 mandates a tiny inline slugifier. Both `archive` (write side) and `history NAME` (read side) need it for path resolution (FR-26).

- **Option A: Duplicate the slugifier in both modules**
  - Cons: violates DRY; two tasks must agree on identical rules; risk of skew.
- **Option B: Slugifier lives in `archive.py`, `history.py` imports it**
  - Cons: forces `history.py` (Task B) to take a runtime dependency on `archive.py` (Task A). Tests for Task B then transitively load Task A's module body, which still runs on master because `archive.py` is a new file owned by Task A on the same series of merges. **For parallel fan-out we must avoid this**: Task B's worktree won't have Task A's file until both PRs land.
- **Option C: Slugifier lives in `history.py`, `archive.py` imports it**
  - Same problem in the other direction.
- **Option D: Slugifier lives in `pipeline_status/archive.py` (Task A); engineers writing Task B reimplement the slugifier inside `history.py` (Task C does NOT need it because the detail-renderer takes a resolved `Path`).**
  - Wait: FR-26 says `history NAME` must slugify the user-supplied `NAME` before path resolution. That slugification happens in `__main__.py`'s argparse-wired action OR in `history.py`. To keep Task D minimal (no logic, just wiring + lazy imports) and to give engineers a clean parallel path, **we adopt this rule**:
    - **Task A** (`pipeline_status/archive.py`) is the canonical home for the slugifier. It exposes `slugify(text: str) -> str` as a public function.
    - **Task B** (`pipeline_status/history.py`) **does NOT import from `archive.py`**. Where `history.py` needs slug behaviour (only at one site, see below), it imports `slugify` lazily from inside the function that needs it. In the test file for Task B, the slugifier is monkey-patched if the import path is unavailable, **OR** — preferred — the test passes pre-slugified inputs directly to the read-side functions (which take `Path` arguments, not raw names). This way **Task B's tests have zero import dependency on Task A**.
    - **Task D** (`__main__.py`) re-imports `slugify` lazily inside the `history`-subcommand action so that the no-subcommand and `--watch` paths still cost nothing.
- **Chosen**: **Option D** — slugifier owned by `archive.py`, with the cross-task usage pattern described above. **Crucially, the read-side primitives in `history.py` accept resolved `Path` objects, not raw names**, so unit tests for Task B and Task C can be written entirely against `Path` inputs without needing the slugifier at all. Only `__main__.py` (Task D) wires the slugifier into the `history NAME` argparse action; Task D will have access to Task A's `slugify` because Task D is the wiring task that lands after the others (and is allowed to import them).

### Decision 3: Archive overwrite policy

FR-7 mandates `mkdir(parents=True, exist_ok=False)`; on collision, stderr error + exit 1, no partial write.

- **Option A: `mkdir(parents=True, exist_ok=False)` and let `FileExistsError` bubble**
  - Pros: atomic check-and-create; no TOCTOU race; FR-7 reads as exactly this.
- **Option B: `if dest.exists(): error; else: mkdir + copy`**
  - Cons: TOCTOU race between `exists()` and `mkdir`; two concurrent runs could both pass the check.
- **Chosen**: **Option A**. We catch `FileExistsError` in `archive.run_archive(...)`, print the FR-7 stderr message, and return exit 1. Concurrent races (Open Question 5) are accepted: the loser sees the same clear error as a deliberate collision.

### Decision 4: File-copy primitive

FR-8 permits `shutil.copyfile` or `shutil.copy2`; the ADR must pick one.

- **Option A: `shutil.copyfile(src, dst)`** — copies content only; destination mtime is "now".
- **Option B: `shutil.copy2(src, dst)`** — copies content + mtime + permissions + (where supported) flags. Source mtime carries to the destination.
- **Chosen**: **Option B (`shutil.copy2`)**. Per Open Question 1 (proposed default), we then run `os.utime(dest_dir, (now, now))` on the **archive directory itself** after the last file copy. Per-file mtimes track their source files (for any future per-file diff), while the **directory** mtime (used by `history`'s `ARCHIVED-AT` column) records when `archive` ran. Single call site (Task A).

### Decision 5: Archive directory mtime stamping (Open Question 1)

Adopt the proposed default: **after all file copies complete, call `os.utime(dest_dir, (now, now))`** where `now = time.time()`. This stamps `ARCHIVED-AT` as "when archive completed", independent of which file was copied last. One line in `archive.run_archive(...)`.

### Decision 6: `history` table column set (Open Question 2)

Adopt the proposed default: **keep the table to four columns** — `NAME`, `ARCHIVED-AT`, `TASKS`, `DONE`. A fifth `STAGE` column is rejected for v3 to keep the table narrow and the implementation small. Users wanting stage information run `history NAME` for the detail view.

### Decision 7: `history` and symlinked archive directories (Open Question 3)

Adopt the proposed default: **follow symlinks** via `Path.iterdir()` + `Path.is_dir()` (which follow symlinks by default). This matches v1's stance ("symlinks are acceptable for state files"). Documented as a known edge case; no special handling in code.

### Decision 8: Archive collision UX (Open Question 4)

Adopt the proposed default: **terse error, no remediation hint**. Message format:

```
pipeline-status: error: archive 'foo-bar' already exists at .claude/state/archive/foo-bar
```

### Decision 9: Concurrent `archive` calls (Open Question 5)

Adopt the proposed default: **accept the race**. `mkdir(exist_ok=False)` is the atomic gate; the loser sees the standard collision error. No lockfile.

### Decision 10: Slugifier rules for markdown punctuation (Open Question 6)

Adopt the proposed default. The slugifier:

1. Lowercases the input.
2. Replaces any run of characters outside `[a-z0-9]` (after lowercasing) with a single `-`. Implementation: `re.sub(r"[^a-z0-9]+", "-", text.lower())`.
3. Strips leading and trailing `-` (`text.strip("-")`).
4. Returns the empty string if the result is empty (caller handles the fallback).

This treats backticks, brackets, parentheses, colons, etc. uniformly. Example: `# Add \`pipeline-status archive\`` → `add-pipeline-status-archive`.

Path-traversal safety (NFR security clause): because the slugifier's output character set is `[a-z0-9-]` only, `/`, `\`, and `..` cannot appear in slug output by construction. Task D additionally asserts that the resolved archive path is a direct child of `.claude/state/archive/` (one defensive check; documented below).

### Decision 11: `history` sort order (Open Question 7)

Adopt the proposed default: **alphabetical by name ascending**, using Python's `sorted(...)` with default byte-order key. Deterministic; locale-independent; matches typical `ls`-style output.

### Decision 12: How to keep tests parallel-safe (the seam)

The five engineers must run on five isolated worktrees, each with **only their own task's files** plus master. Tests on each worktree therefore see:

- master modules: `inspectors.py`, `stage.py`, `formatting.py`, `__main__.py` (unchanged at worktree fork time), `watch.py`,
- their own task's new file(s).

For tests to be parallel-safe:

- **Task A's tests** import only `pipeline_status.archive` (its own new module) + stdlib + `pipeline_status.inspectors` (for `MAX_READ_BYTES` if needed; otherwise not). It does NOT import `pipeline_status.history`.
- **Task B's tests** import only `pipeline_status.history` (its own new module) + stdlib + master `inspectors`/`stage`. It does NOT import `pipeline_status.archive`. The read-side primitives in `history.py` accept `Path` inputs (already-resolved archive directories), so no slugifier is needed at test time.
- **Task C's tests** import only `pipeline_status.format_history` + stdlib + master `formatting` (if reusing helpers) + `inspectors.ArtefactResult` (for constructing fixtures). It does NOT import `archive` or `history`.
- **Task D's tests** are the only place where end-to-end CLI tests live (it owns `__main__.py` and the wiring). At the time Task D's tests run, ALL the other modules must exist; therefore **Task D is sequenced LAST** in the PM's dispatch graph. The other four can run truly in parallel; Task D merges after them. This is a one-edge dependency in an otherwise fully-parallel DAG.
  - Alternative: Task D can be parallel with the others by mocking the imports of `archive` / `history` / `format_history` inside its tests. We **don't** mandate this — the PM may choose either. The ADR specifies enough contract that either approach works.
- **Task E (README)** has no test dependency at all and can run in full parallel with A/B/C/D.

### Decision 13: How `__main__.py`'s lazy imports work

The lazy-import pattern from v2 (inside `main()`, only when `--watch` is set, do `from pipeline_status.watch import ...`) is **preserved and extended**. Specifically:

- `_build_parser()` performs `add_subparsers` and then calls **two registration helpers** (`add_archive_subparser` and `add_history_subparser`) that live in `archive.py` and `history.py` respectively. These helpers register the subparsers' arguments and their `set_defaults(func=...)` action callbacks. **The action callbacks are themselves imported lazily** — the registration helpers attach a small dispatcher function that, when called, imports the heavy work from its own module. This means a user typing `pipeline-status` (no args) never triggers the import of `archive.py` or `history.py` beyond the registration call itself (which is cheap: it just creates an `argparse` subparser object).
- The `_run_one_shot()` function is **unchanged byte-for-byte**.
- The `--watch` path is **unchanged byte-for-byte**.
- A new `_run_archive(args) -> int` and `_run_history(args) -> int` dispatcher live in `__main__.py` and are called via `args.func` when a subcommand is supplied.

Pseudocode for the dispatch in `main()`:

```python
def main() -> None:
    args = _build_parser().parse_args()
    if getattr(args, "cmd", None) is None:
        # v1/v2 path: unchanged
        if args.watch:
            from pipeline_status.watch import WatchConfig, run_watch
            ...
            sys.exit(run_watch(config))
        sys.exit(_run_one_shot())
    # v3 subcommand path
    sys.exit(args.func(args))  # set by add_archive_subparser / add_history_subparser
```

## Architecture

### Component Diagram (text/ASCII — one box per task)

```
repo root/
├── pipeline_status/
│   ├── __init__.py              # UNCHANGED
│   ├── __main__.py              # ── Task D ──  (subparser wiring + dispatch glue)
│   │                            #   adds subparsers; lazy-imports the action
│   │                            #   callables from archive/history/format_history
│   ├── inspectors.py            # UNCHANGED  (frozen v1 contract)
│   ├── stage.py                 # UNCHANGED  (frozen v1 contract)
│   ├── formatting.py            # UNCHANGED  (frozen v1+v2 contract)
│   ├── watch.py                 # UNCHANGED  (frozen v2 contract)
│   ├── archive.py               # ── Task A ──  NEW: slugifier + run_archive
│   ├── history.py               # ── Task B ──  NEW: discovery + parsing
│   └── format_history.py        # ── Task C ──  NEW: table + detail renderers
├── tests/
│   ├── __init__.py              # UNCHANGED
│   ├── test_inspectors.py       # UNCHANGED
│   ├── test_stage.py            # UNCHANGED
│   ├── test_formatting_helpers.py  # UNCHANGED
│   ├── test_formatting_smoke.py    # UNCHANGED
│   ├── test_watch.py            # UNCHANGED
│   ├── test_archive.py          # ── Task A ──  NEW
│   ├── test_history.py          # ── Task B ──  NEW
│   ├── test_format_history.py   # ── Task C ──  NEW
│   └── test_main_subcommands.py # ── Task D ──  NEW  (end-to-end CLI tests)
├── README.md                    # ── Task E ──  EDIT: document new subcommands
└── pyproject.toml               # UNCHANGED

.claude/state/                   # READ-ONLY at runtime for `history`
├── feature-request.md           # read by archive; copied into <archive>/
├── requirements.md              # ditto
├── adr.md                       # ditto
├── tasks.json                   # ditto
├── worktrees.json               # ditto
└── archive/                     # WRITE-ONLY target for `archive`; READ for `history`
    └── <slug>/
        ├── feature-request.md   # 0..1 of each, depending on what was present
        ├── requirements.md
        ├── adr.md
        ├── tasks.json
        └── worktrees.json
```

**File ownership table** (the parallel-fan-out contract):

| Task | Owns (production) | Owns (test) | Imports from master | Imports from sibling tasks |
|---|---|---|---|---|
| A | `pipeline_status/archive.py` | `tests/test_archive.py` | stdlib only | none |
| B | `pipeline_status/history.py` | `tests/test_history.py` | `pipeline_status.inspectors` (for `inspect_tasks` and `MAX_READ_BYTES`) | none |
| C | `pipeline_status/format_history.py` | `tests/test_format_history.py` | `pipeline_status.inspectors.ArtefactResult`, `pipeline_status.formatting` (for `format_artefact_row`, `format_stage_line`) | none |
| D | `pipeline_status/__main__.py`, `tests/test_main_subcommands.py` | (same file) | all of master | imports A's `add_archive_subparser`, B's `add_history_subparser`; lazily imports C via B's wiring |
| E | `README.md` | (none) | (none) | (none) |

### Data Model

One new dataclass, owned by Task B:

```python
# pipeline_status/history.py
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ArchiveEntry:
    """One row in the history table.

    Attributes:
        name:           The archive directory name (already a slug on disk).
        path:           Absolute Path to the archive directory.
        mtime:          float; the directory's st_mtime at scan time.
        total_tasks:    Total tasks count from <archive>/tasks.json, or None
                        if tasks.json is missing or malformed.
        completed_tasks: Completed-tasks count, or None under the same conditions.
    """
    name: str
    path: Path
    mtime: float
    total_tasks: int | None
    completed_tasks: int | None
```

`None` for either count is rendered as `"-"` by Task C; `int` values are rendered as decimal strings.

No new persistent data structures (archives are existing-format state files copied into subdirectories; no manifest, no index, no metadata file).

### API Contracts

These signatures are **the** interface contract for the parallel engineers. Each engineer writes their module's body to match these signatures exactly; sibling tasks can write `from pipeline_status.X import Y` against them before the dependency PR has merged.

#### Task A — `pipeline_status/archive.py`

```python
"""
Snapshot the live .claude/state/ artefacts into .claude/state/archive/<NAME>/.

Public symbols:
    TRACKED_ARTEFACTS:        Final[tuple[str, ...]] of the five basenames.
    slugify(text)             -> str    (FR-11 rules; "" if empty after normalisation)
    derive_default_name(...)  -> str    (FR-9/FR-10 heading-or-date fallback)
    run_archive(args)         -> int    (the argparse action callable; returns exit code)
    add_archive_subparser(subparsers) -> None  (registers the `archive` subcommand)
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Final

# Public constant: the exact five tracked basenames, in copy order.
TRACKED_ARTEFACTS: Final[tuple[str, ...]] = (
    "feature-request.md",
    "requirements.md",
    "adr.md",
    "tasks.json",
    "worktrees.json",
)


def slugify(text: str) -> str:
    """Slugify per FR-11.

    Lowercase, replace every run of non-[a-z0-9] with a single '-', strip
    leading/trailing '-'. Returns '' if the result is empty. ASCII-only output
    by construction (non-ASCII letters are treated as separators, not
    transliterated). Path-traversal safe: '/', '\\', and '..' cannot survive.
    """


def derive_default_name(
    feature_request_path: Path,
    *,
    today: datetime | None = None,
) -> str:
    """Return the default archive name per FR-9/FR-10.

    Reads the first ATX-style markdown heading from ``feature_request_path``
    (a non-blank line starting with one or more '#' followed by whitespace).
    Slugifies the heading text. If the slug is non-empty, returns it.
    Otherwise — including when the file is missing, unreadable, contains no
    heading, or its heading slugifies to empty — returns ``today``'s local
    date in ``YYYY-MM-DD`` form. ``today`` defaults to
    ``datetime.now().astimezone()``; tests pass a fixed value.

    This function never raises; the date fallback is the catch-all.
    """


def run_archive(args: argparse.Namespace) -> int:
    """Action callable for the `archive` subcommand.

    Consumed by ``__main__.main()`` via ``args.func``. Performs:
      1. Locate state_dir = Path.cwd()/".claude"/"state". If not a directory,
         stderr error and return 2 (matches v1 missing-state exit code).
      2. Resolve the archive name:
            - If args.name is provided: ``slug = slugify(args.name)``. If
              ``slug == ""``, stderr error and return 1.
            - Else: ``slug = derive_default_name(state_dir/"feature-request.md")``.
              (derive_default_name guarantees a non-empty slug.)
      3. dest_root = state_dir/"archive"; ensure ``dest_root.mkdir(parents=True,
         exist_ok=True)``.
      4. dest = dest_root/slug; call ``dest.mkdir(parents=True, exist_ok=False)``.
         On ``FileExistsError``: stderr error per Decision 8 and return 1.
         No partial cleanup needed because nothing was written yet.
      5. For each name in TRACKED_ARTEFACTS:
            if (state_dir/name).is_file(): shutil.copy2(state_dir/name, dest/name); n += 1
      6. os.utime(dest, (now, now)) with now = time.time().  (Decision 5)
      7. print(f"Archived {n} file(s) to .claude/state/archive/{slug}/")
      8. return 0.

    Does NOT remove or modify any source file under .claude/state/.
    """


def add_archive_subparser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    """Register the `archive` subcommand on the given subparsers action.

    Adds one optional ``--name NAME`` flag with help describing the slugifier
    rules (FR-12 mandate). Calls ``sp.set_defaults(func=run_archive)``.
    Returns the created subparser for any caller-side inspection.
    """
```

Notes for Task A's engineer:

- `TRACKED_ARTEFACTS` is exported as a module-level constant so Task B can `from pipeline_status.archive import TRACKED_ARTEFACTS` if it wants to reuse the same canonical list — **but it MUST NOT**, because parallel-safe testing requires Task B to be independent of Task A. Instead, Task B redefines its own private `_TRACKED_ARTEFACTS` tuple at module scope. The duplication is intentional and accepted; both tuples must list the exact same five names in the exact same order. If they ever drift, the byte-identical regression tests will catch the drift on the next master build.
- The `archive` subparser must NOT accept `--watch` or `--interval`; argparse's default behaviour (subparsers don't inherit parent optionals at parse time when consuming a subcommand) takes care of this naturally. No extra code needed.
- Stdout success line is **exactly** `f"Archived {n} file(s) to .claude/state/archive/{slug}/"` followed by `print()`'s automatic newline.

#### Task B — `pipeline_status/history.py`

```python
"""
Discover and parse archived pipeline runs under .claude/state/archive/.

Public symbols:
    ArchiveEntry                            (dataclass; see Data Model)
    list_archives(archive_root)             -> list[ArchiveEntry]
    inspect_archive(archive_dir)            -> dict[str, ArtefactResult]
    add_history_subparser(subparsers)       -> None  (registers `history`)
    run_history(args)                       -> int   (argparse action callable)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from pipeline_status.inspectors import (
    ArtefactResult,
    MAX_READ_BYTES,
    inspect_feature_request,
    inspect_requirements,
    inspect_adr,
    inspect_tasks,
    inspect_worktrees,
)

# Private duplicate of archive.TRACKED_ARTEFACTS to keep Task B parallel-safe.
_TRACKED_ARTEFACTS: Final[tuple[str, ...]] = (
    "feature-request.md",
    "requirements.md",
    "adr.md",
    "tasks.json",
    "worktrees.json",
)


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    path: Path
    mtime: float
    total_tasks: int | None
    completed_tasks: int | None


def list_archives(archive_root: Path) -> list[ArchiveEntry]:
    """Enumerate immediate subdirectories of ``archive_root`` and parse each.

    Behaviour:
      - If ``archive_root`` does not exist or is not a directory, return [].
      - Iterate ``archive_root.iterdir()``; for each child that ``is_dir()``
        (symlinks to directories included, per Decision 7), build an
        ArchiveEntry. Files directly under archive_root are ignored.
      - Sort the result by ``entry.name`` ascending (Python default
        sorted(...) byte-order, locale-independent — FR-23).
      - For each archive's tasks.json:
            * Missing file        -> total_tasks=None, completed_tasks=None.
            * Read error / decode error / json.JSONDecodeError -> same.
            * Top-level {} or []  -> total_tasks=0, completed_tasks=0.
              (Empty JSON parses but is not malformed; FR-20 says count = 0.)
            * Valid list, or dict with "tasks" key whose value is a list:
                  total = len(tasks_list), completed = (same rules as v1
                  inspect_tasks).
            * Any other shape -> total_tasks=None, completed_tasks=None.
        Reads are capped at MAX_READ_BYTES bytes (NFR resource limit).

    Never raises; partial failures show up as None counts on the affected row.
    """


def inspect_archive(archive_dir: Path) -> dict[str, ArtefactResult]:
    """Run the v1 inspectors against ``archive_dir`` instead of .claude/state/.

    Returns a dict suitable for passing to ``stage.derive_stage(...)``. Keys
    are the five canonical names; each value is the ArtefactResult produced
    by the corresponding v1 inspector. Missing files render per v1 rules
    (exists=False, filled=False); the function does not raise on partial
    archives.

    This is the single point where the v1 inspector contract is reused for the
    `history NAME` detail form. The inspectors themselves are NOT modified
    (FR-32).
    """


def add_history_subparser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    """Register the `history` subcommand on the given subparsers action.

    Adds one optional positional ``name`` argument (nargs='?'). Calls
    ``sp.set_defaults(func=run_history)``. Returns the subparser.
    """


def run_history(args: argparse.Namespace) -> int:
    """Action callable for the `history` subcommand.

    Dispatches on whether ``args.name`` is set:
      - Unset (table form):
            archive_root = Path.cwd()/".claude"/"state"/"archive"
            entries = list_archives(archive_root)
            if not entries:
                print("No archives found.")
                return 0
            from pipeline_status.format_history import format_history_table
            print(format_history_table(entries), end="")  # function emits trailing newline
            return 0
      - Set (detail form):
            from pipeline_status.archive import slugify   # lazy import; see Decision 2
            slug = slugify(args.name)
            archive_root = Path.cwd()/".claude"/"state"/"archive"
            archive_dir = archive_root / slug
            if not archive_dir.is_dir():
                print(f"pipeline-status: error: archive {args.name!r} not found at {archive_dir}",
                      file=sys.stderr)
                return 1
            from pipeline_status.format_history import format_archive_detail
            from pipeline_status.stage import derive_stage
            results = inspect_archive(archive_dir)
            stage = derive_stage(results)
            print(format_archive_detail(list(results.values()), stage), end="")
            return 0
    """
```

Notes for Task B's engineer:

- The lazy imports of `archive.slugify` and `format_history.format_*` inside `run_history` are deliberate: they let Task B's **test file** import `history.py` without pulling Task A or Task C, by **mocking those import sites** (`unittest.mock.patch` of `pipeline_status.archive.slugify` and similar) **or, preferred, by testing `list_archives` and `inspect_archive` directly with `Path` inputs** and leaving `run_history`'s full CLI dispatch to Task D's end-to-end tests.
- `inspect_archive` is the new thin wrapper FR-32 explicitly anticipates. It does NOT modify any existing inspector signature.
- The `history` subparser must NOT accept `--watch` or `--interval`; same argparse-default reasoning as `archive`.

#### Task C — `pipeline_status/format_history.py`

```python
"""
Renderers for the history table and the per-archive detail view.

Public symbols:
    format_history_table(entries)            -> str   (multi-line; ends with '\n')
    format_archive_detail(results, stage)    -> str   (multi-line; ends with '\n')
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline_status.history import ArchiveEntry  # forward ref only for typing
from pipeline_status.inspectors import ArtefactResult
from pipeline_status.formatting import format_artefact_row, format_stage_line


def format_history_table(entries: Sequence["ArchiveEntry"]) -> str:
    """Render the `history` table.

    Columns, in order: NAME, ARCHIVED-AT, TASKS, DONE.
    Separator: at least two spaces between columns (no tabs, no Unicode
    box-drawing). One header row precedes the data rows. Column widths size
    to the widest value in each column (header included). Trailing newline.

    Cell formatting:
        NAME         -> entry.name verbatim.
        ARCHIVED-AT  -> datetime.fromtimestamp(entry.mtime,
                            tz=datetime.now().astimezone().tzinfo)
                        .isoformat(timespec="seconds")
                        (matches v1's mtime formatting exactly — same one-liner
                         as inspectors._mtime_iso. We re-implement inline rather
                         than importing _mtime_iso because that is a private
                         helper.)
        TASKS        -> str(entry.total_tasks) if entry.total_tasks is not None
                        else "-"
        DONE         -> str(entry.completed_tasks) if entry.completed_tasks is
                        not None else "-"

    Rows are emitted in input order (caller — list_archives — has already
    sorted them by name ascending per FR-23).

    Example output (2 archives, 2-space gutters, widths sized to data):

        NAME              ARCHIVED-AT                TASKS  DONE
        pipeline-status   2026-05-20T14:32:01+02:00  3      3
        watch-mode        2026-05-22T09:15:00+02:00  4      2
    """


def format_archive_detail(
    results: Sequence[ArtefactResult],
    stage: str,
) -> str:
    """Render the per-archive detail view for `history NAME`.

    Output must match the v1 one-shot body byte-for-byte, treating the archive
    as the state directory. Specifically (FR-28), emit::

        Pipeline Status\n
        ===============\n
        \n
        <one '  ' + format_artefact_row(r) + '\\n' per r in results, in input order>
        \n
        '  ' + format_stage_line(stage) + '\\n'

    Note: this mirrors the inline print() sequence in
    ``pipeline_status.__main__._run_one_shot()`` exactly. Returned as a single
    string (caller passes ``end=""`` to print()). Colour is governed by the
    same ``formatting.use_colour()`` helper the rest of the package uses; the
    caller does NOT pass a separate colour flag (FR-34).
    """
```

Notes for Task C's engineer:

- Both functions return strings ending in a newline. The action callables in Task B use `print(..., end="")` so we don't double up the newline.
- The detail renderer **must** produce byte-identical output to the v1 one-shot body for identical artefact state. The simplest implementation is to mirror `_run_one_shot()`'s sequence using `format_artefact_row` and `format_stage_line` from `pipeline_status.formatting`. There is no need to call `format_report` (which uses a different header — "Pipeline Artefact Status" + 60 dashes — and would diverge from the v1 inspector's "Pipeline Status" + 15 `=` header). **Do not use `format_report`.**
- `ArchiveEntry` is imported under `TYPE_CHECKING` to avoid a runtime circular dependency. The function accepts any object with `.name`, `.mtime`, `.total_tasks`, `.completed_tasks` attributes — a structural protocol. At runtime, Task B's `ArchiveEntry` instances are what's passed.

#### Task D — `pipeline_status/__main__.py`

The diff against the current master file is small and entirely additive:

```python
# At top, add imports for the registration helpers (lazy is fine; eager is also fine
# since the import cost is sub-millisecond):
from pipeline_status.archive import add_archive_subparser
from pipeline_status.history import add_history_subparser


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser. Extracted for unit-testability."""
    parser = argparse.ArgumentParser(...)  # unchanged top-level args
    parser.add_argument("--watch", ...)    # unchanged
    parser.add_argument("--interval", ...) # unchanged

    # NEW in v3:
    subparsers = parser.add_subparsers(dest="cmd", required=False)
    add_archive_subparser(subparsers)
    add_history_subparser(subparsers)
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    # NEW in v3 — subcommand dispatch lands FIRST so that --watch and --interval
    # are silently ignored if (somehow) combined with a subcommand. But argparse
    # forbids that combination already because neither subparser declares them,
    # so this branch handles only the well-formed cases.
    if getattr(args, "cmd", None) is not None:
        sys.exit(args.func(args))

    # v1/v2 paths below are byte-identical to master:
    if args.watch:
        from pipeline_status.watch import WatchConfig, run_watch
        ...  # unchanged
        sys.exit(run_watch(config))

    sys.exit(_run_one_shot())
```

`_interval_type`, `_locate_state_dir`, `_run_one_shot`, the `_EPILOG` string, and the module docstring are **unchanged**.

#### Task E — `README.md`

Docs only. Adds a section documenting the two new subcommands, their flags, exit codes, and one example each. Does not modify any code.

### CLI Surface

Top-level usage tree:

```
pipeline-status [-h] [--watch] [--interval SECONDS] {archive,history} ...

  (no subcommand)                # v1/v2 path; one-shot or --watch
  archive [-h] [--name NAME]     # snapshot live state
  history [-h] [NAME]            # NAME omitted: table; NAME given: detail
```

`pipeline-status archive --help` shows the slugifier normalisation rules in the `--name` help text (FR-12 mandate).

`pipeline-status history --help` shows the positional `NAME` (optional) and a one-line description of both forms.

`pipeline-status --help` shows the existing v2 help text plus a new `{archive,history}` line in the usage line (argparse default behaviour).

#### Exit-code matrix (FR-35)

| Invocation | Stdin condition | Exit code |
|---|---|---|
| `pipeline-status` (no args) | `.claude/state/` present | 0 |
| `pipeline-status` (no args) | `.claude/state/` missing | 2 |
| `pipeline-status --watch` | (any) | 0 on clean Ctrl+C |
| `pipeline-status archive [--name N]` | `.claude/state/` present, dest clear | 0 |
| `pipeline-status archive [--name N]` | `.claude/state/` missing | 2 |
| `pipeline-status archive [--name N]` | dest exists / invalid name | 1 |
| `pipeline-status history` | (any) | 0 |
| `pipeline-status history NAME` | archive dir exists | 0 |
| `pipeline-status history NAME` | archive dir missing | 1 |
| `pipeline-status archive --watch ...` | (any) | argparse exit 2 to stderr |
| `pipeline-status frobnicate` | (any) | argparse exit 2 to stderr |

### Sequence Diagrams (text)

#### `archive` happy path (`pipeline-status archive --name foo-bar`)

```
User: $ pipeline-status archive --name foo-bar
__main__.main()
  args = _build_parser().parse_args()   # args.cmd = "archive", args.func = archive.run_archive
  sys.exit(args.func(args))             # i.e. archive.run_archive(args)

archive.run_archive(args):
  state_dir = Path.cwd()/".claude"/"state"
  if not state_dir.is_dir(): stderr + return 2
  slug = slugify("foo-bar") = "foo-bar"
  dest_root = state_dir/"archive"; dest_root.mkdir(parents=True, exist_ok=True)
  dest = dest_root/"foo-bar"; dest.mkdir(parents=True, exist_ok=False)
  for name in TRACKED_ARTEFACTS:
    if (state_dir/name).is_file():
      shutil.copy2(state_dir/name, dest/name); n += 1
  os.utime(dest, (now, now))
  print(f"Archived {n} file(s) to .claude/state/archive/foo-bar/")
  return 0
```

#### `history` table happy path (`pipeline-status history`)

```
User: $ pipeline-status history
__main__.main()
  args.cmd = "history", args.name = None, args.func = history.run_history
  sys.exit(history.run_history(args))

history.run_history(args):
  archive_root = Path.cwd()/".claude"/"state"/"archive"
  entries = list_archives(archive_root)
  if not entries:
    print("No archives found."); return 0
  body = format_history.format_history_table(entries)
  print(body, end="")
  return 0

list_archives(archive_root):
  if not archive_root.is_dir(): return []
  out = []
  for child in archive_root.iterdir():
    if not child.is_dir(): continue
    mtime = child.stat().st_mtime
    tj = child / "tasks.json"
    total, completed = (None, None)
    if tj.is_file():
      try:
        raw = tj.read_bytes()[:MAX_READ_BYTES]
        parsed = json.loads(raw.decode("utf-8"))
        if parsed == [] or parsed == {}:
          total, completed = 0, 0
        else:
          if isinstance(parsed, list): items = parsed
          elif isinstance(parsed, dict) and isinstance(parsed.get("tasks"), list):
            items = parsed["tasks"]
          else: items = None
          if items is not None:
            total = len(items)
            completed = sum(
              1 for it in items if isinstance(it, dict) and (
                str(it.get("status","")).lower() in {"done","completed"}
                or it.get("completed") is True or it.get("done") is True
              )
            )
      except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass  # leave (None, None)
    out.append(ArchiveEntry(name=child.name, path=child, mtime=mtime,
                            total_tasks=total, completed_tasks=completed))
  out.sort(key=lambda e: e.name)
  return out
```

#### `history NAME` happy path (`pipeline-status history watch-mode`)

```
User: $ pipeline-status history watch-mode
__main__.main()
  args.cmd = "history", args.name = "watch-mode"
  sys.exit(history.run_history(args))

history.run_history(args):
  from pipeline_status.archive import slugify
  slug = slugify("watch-mode") = "watch-mode"
  archive_dir = Path.cwd()/".claude"/"state"/"archive"/"watch-mode"
  if not archive_dir.is_dir():
    stderr error; return 1
  from pipeline_status.format_history import format_archive_detail
  from pipeline_status.stage import derive_stage
  results = inspect_archive(archive_dir)   # dict[name, ArtefactResult]
  stage = derive_stage(results)
  body = format_archive_detail(list(results.values()), stage)
  print(body, end="")
  return 0

inspect_archive(archive_dir):
  return {
    "feature-request.md": inspect_feature_request(archive_dir/"feature-request.md"),
    "requirements.md":    inspect_requirements(archive_dir/"requirements.md"),
    "adr.md":             inspect_adr(archive_dir/"adr.md"),
    "tasks.json":         inspect_tasks(archive_dir/"tasks.json"),
    "worktrees.json":     inspect_worktrees(archive_dir/"worktrees.json"),
  }
```

#### `--watch` continues to work (regression contract)

```
User: $ pipeline-status --watch --interval 3
__main__.main()
  args.cmd = None
  args.watch = True
  -> from pipeline_status.watch import WatchConfig, run_watch  (lazy)
  -> run_watch(config)   # unchanged from v2
```

#### One-shot continues byte-identically

```
User: $ pipeline-status
__main__.main()
  args.cmd = None
  args.watch = False
  -> _run_one_shot()  # unchanged from master; the in-place prints produce
                      # the same bytes as v1/v2
```

## Implementation Notes

### Module-by-module guidance

**`pipeline_status/archive.py` (Task A)**

- `slugify`: use `re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")`. One line of logic. Test with: empty string, whitespace only, mixed case (`"Foo Bar"` → `"foo-bar"`), unicode (`"naïve"` → `"na-ve"`), path-traversal attempts (`"../../etc"` → `"etc"`; the leading runs of `.` and `/` collapse to a single `-` which is then stripped), all-separator input (`"!!!"` → `""`), backticks (`"\`pipeline\`"` → `"pipeline"`).
- `derive_default_name`: open the file, read up to `MAX_READ_BYTES` (re-import from `pipeline_status.inspectors` if you want the constant; otherwise hardcode `10 * 1024 * 1024`). Iterate lines. The first line that, after `.lstrip()`, starts with one or more `#` followed by whitespace, has its heading text extracted (`stripped.lstrip("#").strip()`) and slugified. If slug is non-empty, return it. Otherwise return `today.strftime("%Y-%m-%d")`. The `today` parameter defaults to `datetime.now().astimezone()`; tests pass a fixed datetime for the fallback assertion.
- `run_archive`: implementation as described in the contract. Error message formats:
  - Missing state dir: `pipeline-status: error: .claude/state/ not found or not a directory` to stderr, return 2 (matches v1).
  - Empty name: `pipeline-status: error: archive name is empty after normalisation`, return 1.
  - Existing dest: `pipeline-status: error: archive {slug!r} already exists at {dest}`, return 1. (`!r` quoting matches the `history NAME not found` error for consistency.)
- `add_archive_subparser`: standard pattern. Single `--name` flag, no positional args.

**`pipeline_status/history.py` (Task B)**

- The `_TRACKED_ARTEFACTS` private tuple is intentional duplication for parallel-fan-out safety; do not refactor it to import from `archive.py`. If a future task wants to deduplicate, it can do so post-merge.
- `list_archives`: keep the body small; pull the tasks.json parsing into a private `_parse_task_counts(path)` helper if you prefer; either way the function never raises.
- `inspect_archive`: trivial dispatch to the five v1 inspectors with paths rooted at the archive dir.
- `run_history`: lazy-imports `slugify` from `archive` and `format_*` from `format_history` so that tests for `list_archives` / `inspect_archive` don't pull those modules.
- The `history` subparser declares one positional `name` (`nargs="?"`, default `None`) and no other args. Do not declare `--watch` or `--interval`.

**`pipeline_status/format_history.py` (Task C)**

- `format_history_table`: compute the width of each column over (header + data values), assemble each row by joining columns with `"  "` (two spaces). Emit one `"\n"` after each row. Final string ends with `"\n"`. No ANSI colour (the table is metadata, not a status report).
- `format_archive_detail`: literally mirror the byte sequence emitted by `_run_one_shot()` for the report body. Use a list of strings and `"".join(...)`. Each `print()` call in `_run_one_shot()` emits `"<text>\n"`; the renderer emits the same bytes.

**`pipeline_status/__main__.py` (Task D)**

- Two new imports near the top: `from pipeline_status.archive import add_archive_subparser` and `from pipeline_status.history import add_history_subparser`. Eager imports are fine here — these modules' import cost is dominated by stdlib imports they each do, which `__main__.py` already triggers via the other v1/v2 imports.
- `_build_parser` gains `subparsers = parser.add_subparsers(dest="cmd", required=False)` + two calls. Three lines added.
- `main()` gains the subcommand-dispatch branch at the top: four lines.
- `_interval_type`, `_locate_state_dir`, `_run_one_shot`, `_EPILOG` are untouched.
- `tests/test_main_subcommands.py` does end-to-end argparse smoke tests by invoking `__main__._build_parser().parse_args([...])` and then `args.func(args)` with a `tempfile.TemporaryDirectory()` set up as the cwd via `os.chdir` (saved + restored in `setUp`/`tearDown` or via `unittest.TestCase`'s `addCleanup`). It also asserts that combining `--watch` with `archive` produces an argparse `SystemExit` with code 2.

**`README.md` (Task E)**

- Add a `## CLI` section (or extend the existing one) with three subsections: one-shot (existing), watch mode (existing), and v3 subcommands (new). For each new subcommand: one-paragraph description, one example invocation, and one line per exit code.

### Stub strategy for parallel work

Each engineer implements their own module against the contracts above. The five worktrees develop independently:

- Task A's worktree contains `pipeline_status/archive.py` + `tests/test_archive.py`. Tests run on master + these two files.
- Task B's worktree contains `pipeline_status/history.py` + `tests/test_history.py`. Tests run on master + these two files. **Critically**, Task B's tests do NOT need `pipeline_status.archive` to be present in the worktree, because `history.py`'s only reference to `archive.slugify` is lazy (inside `run_history`), and Task B's tests cover `list_archives` and `inspect_archive` directly — not `run_history`.
- Task C's worktree contains `pipeline_status/format_history.py` + `tests/test_format_history.py`. Tests run on master + these two files. The renderers take their inputs as plain data (sequence of objects with `.name`/`.mtime`/etc., or list of `ArtefactResult`); Task C's tests construct those inputs directly without importing `history.py`.
- Task D's worktree contains the edited `pipeline_status/__main__.py` + `tests/test_main_subcommands.py`. **Task D depends on A, B, C** to import their registration functions and action callables. Two PM strategies are acceptable:
  1. **Sequential merge with stub-friendly contracts** (recommended): A/B/C/E run in parallel on worktrees forked from master; D runs on a worktree forked from the post-A/B/C-merge tip. This is the simplest dispatch.
  2. **Fully parallel with mocks**: D's tests mock `pipeline_status.archive.add_archive_subparser` and `pipeline_status.history.add_history_subparser` (e.g. by inserting stub modules into `sys.modules` in `setUp`). D's production code still imports the real symbols, so D's PR must be rebased on top of A/B/C before merging. This is acceptable but adds complexity; recommended only if the PM needs maximum throughput.
- Task E (README) has no code dependency; it runs in parallel with everything.

### Edge cases to handle

1. **Slugifier on path-traversal input**: `slugify("../../../etc/passwd")` → `"etc-passwd"`. By construction, no `/`, `\\`, or `..` can appear in slug output. Task D's defensive assertion (`assert (archive_root / slug).parent == archive_root`) provides belt-and-braces, but is optional.
2. **Slugifier on unicode**: non-ASCII letters (`"naïve"`) become separators, not transliterated. Slug is `"na-ve"`. This is intentional per FR-11 ("ASCII-only in output (non-ASCII letters are treated as separators, not transliterated)").
3. **`archive` with no source files**: `state_dir` exists but contains none of the five tracked names. `archive` creates the dest dir, prints `Archived 0 file(s) to .claude/state/archive/<slug>/`, exits 0. FR-14.
4. **`archive` with partial source** (e.g. only `feature-request.md` and `requirements.md` exist): the loop copies what exists, skips silently otherwise. N reflects the actual count.
5. **`archive` source is a symlink**: `shutil.copy2` follows symlinks by default and copies the target content. Acceptable.
6. **`history` with empty archive root**: `list_archives` returns `[]`; `run_history` prints `No archives found.` and exits 0.
7. **`history` with missing archive root**: `list_archives` returns `[]`; same path as empty root.
8. **`history` with a regular file directly under `archive/`** (e.g. someone left a `.DS_Store`): `list_archives` skips non-directories. Files under the archive root are ignored.
9. **`history` with a symlinked archive subdirectory**: `is_dir()` follows symlinks; the archive is included.
10. **`history` with malformed `tasks.json`**: `list_archives` catches `JSONDecodeError`, leaves counts as `None`, renders `-`/`-`. The loop continues to the next archive.
11. **`history` with `tasks.json` shape that is neither list nor `{"tasks": [...]}`**: counts are `None`; the row renders `-`/`-`.
12. **`history NAME` with mixed-case input** (`pipeline-status history Foo-Bar`): slugifier lowercases to `foo-bar`; path resolution targets `.claude/state/archive/foo-bar/`. FR-26.
13. **`history NAME` with missing archive**: stderr error, exit 1.
14. **`history NAME` with partial archive**: each missing artefact renders per v1 filled-detection rules; the report still emits header + 5 rows (some marked MISSING) + stage line. Exit 0.
15. **`history` table with a single archive**: column widths size to header + that one row. Trivial happy path.
16. **Concurrent `pipeline-status archive` runs targeting the same slug**: one wins, one fails with the standard collision error. Accepted per Open Question 5.
17. **`--watch` combined with `archive` or `history`**: argparse rejects (subparsers don't accept the parent's `--watch`/`--interval`), exit 2 with usage error to stderr. FR-3.
18. **Unknown subcommand**: argparse rejects, exit 2 with usage error to stderr. FR-1.

## Consequences

**Easier after this change:**

- The orchestrator's manual `cp .claude/state/*.{md,json} .claude/state/archive/<name>/` step is replaced by `pipeline-status archive`.
- A returning Claude Code session can run `pipeline-status history` to see every past feature run at a glance.
- Investigating an archived run is `pipeline-status history NAME` — one command, identical layout to the live inspector.
- The subparser-aware argparse layout in `__main__.py` is the v3 foundation for future subcommands (`--json`, `lint`, `diff`, `restore`).
- Five small, file-scoped tasks make this iteration trivially parallelisable; engineers can ship in five concurrent worktrees with one merge stage at the end.

**Harder or more complex:**

- The package grows from six to eight modules. Engineers must learn the file ownership map (above) to know where things live.
- The slugifier is duplicated by intent in two modules (the `_TRACKED_ARTEFACTS` private tuple as well). This is a price paid for parallel-fan-out independence; a follow-up refactor can consolidate post-merge.
- `__main__.py` now has a three-way dispatch (subcommand vs `--watch` vs one-shot) instead of v2's two-way. Slightly larger surface for argparse tests to cover.

**Technical debt introduced:**

- `_TRACKED_ARTEFACTS` duplication between `archive.py` and `history.py` is technical debt — small (one tuple), but worth a single-line follow-up.
- No `--json` mode anywhere yet. Still deferred from v1.
- No `restore`, no `diff`, no `--force`, no `--sort` on `history`. All deferred to v4 if/when actually requested.
- The `format_history.format_archive_detail` renderer hard-codes the v1 one-shot output structure (header + rows + stage line). If v1's structure ever changes, both `_run_one_shot()` and `format_archive_detail` must be updated in lockstep; a future v4 refactor could merge them into a single helper that both call.

## Out of Scope

- `pipeline-status restore` (or any write-back from archive to live state). Explicitly excluded.
- `pipeline-status diff A B` or any cross-archive comparison. Excluded.
- `--json` / machine-readable output mode for any subcommand. Still deferred.
- Automatic archiving at end-of-run. The orchestrator continues to drive when `archive` runs.
- Compression, encryption, checksum, signing, or external sync of archive directories. Excluded.
- Search / filter / sort / paging / `--since` / `--grep` / `--limit` flags on `history`. Excluded; defer to shell tools.
- `--force` / `--overwrite` / rename flag on `archive`. Excluded; collisions are hard errors.
- Manifest files, index files, lockfiles, PID files, or any other auxiliary write outside the archive directory. Excluded by NFR.
- Watch-mode equivalent for the new subcommands (e.g. `pipeline-status history --watch`). Excluded.
- Removal of source files from `.claude/state/` by `archive`. Excluded; remains an orchestrator responsibility.
- Changes to v1 inspector contracts, v1 stage rules, v1/v2 filled-detection heuristics, or v2 watch behaviour. Frozen.
- Nested archive directories. Archives are flat under `.claude/state/archive/`.
- Python versions below 3.10. Excluded (inherited).
- Cross-task deduplication of `slugify` / `TRACKED_ARTEFACTS` / `_TRACKED_ARTEFACTS`. Deliberate cost of parallel fan-out; a single follow-up commit can consolidate post-merge if desired.
