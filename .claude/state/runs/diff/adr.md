# ADR: pipeline-status `diff` subcommand

**Status**: Proposed
**Date**: 2026-05-23

## Context

`pipeline-status` has been delivered in three increments:

- **v1** (`.claude/state/archive/pipeline-status-cli/adr.md`): package layout
  `pipeline_status/{__init__,__main__,inspectors,stage,formatting}.py`, the
  `ArtefactResult` dataclass, per-artefact inspectors, `derive_stage(...)`,
  the one-shot report. Exit 2 on missing `.claude/state/`; otherwise 0.
- **v2** (`.claude/state/archive/watch-mode/adr.md`): `pipeline_status/watch.py`
  and the `--watch [--interval N]` mode. One-shot stdout byte-identical to v1.
- **v3** (`.claude/state/archive/v3-final/adr.md`): `pipeline_status/archive.py`
  (slugifier + snapshot writer), `pipeline_status/history.py` (read side),
  `pipeline_status/format_history.py` (table + detail renderers), wired into
  `__main__.py` via two `add_*_subparser(subparsers)` registration helpers.
  Pre-existing v1/v2 paths byte-identical.

The **actual master** code (`pipeline_status/__main__.py` read directly while
drafting this ADR) does the following on the no-subcommand path:

- Imports `add_archive_subparser` from `pipeline_status.archive` and
  `add_history_subparser` from `pipeline_status.history` eagerly at module
  scope.
- `_build_parser()` builds the top-level parser with `--watch` and
  `--interval`, then calls
  `subparsers = parser.add_subparsers(dest="cmd", required=False)` and
  invokes the two existing `add_*_subparser(subparsers)` helpers.
- `main()` dispatches a subcommand first (`if getattr(args, "cmd", None) is
  not None: sys.exit(args.func(args))`), then falls through to the watch
  branch and finally `_run_one_shot()`.
- `_run_one_shot()` prints `"Pipeline Status"`, `"==============="`, blank,
  `f"  {format_artefact_row(result)}"` per artefact, blank,
  `f"  {format_stage_line(stage)}"`. Exit 0 on success, exit 2 with a stderr
  error when `.claude/state/` is absent.

v3 also exposes `pipeline_status.archive.TRACKED_ARTEFACTS` (the canonical
five-tuple), `pipeline_status.archive.slugify(text)` (the FR-11 slugifier),
`pipeline_status.inspectors.MAX_READ_BYTES = 10 * 1024 * 1024`, and
`pipeline_status.formatting.use_colour()`.

v4 adds **one new subcommand**, `pipeline-status diff [--against OTHER] NAME`,
that compares two pipeline runs and prints a per-artefact summary. It is
shipped concurrently with a sibling pipeline that adds `pipeline-status
restore`; the merge contract is documented in Decision 14.

## Decision Drivers

- **Parallel-fan-out (driver #0)**: this ADR is decomposed by the PM into
  four tasks (A1–A4), dispatched to four Engineer subagents on isolated
  `git worktree`s. Each task owns exactly one production file (or one
  documentation file) plus one matching test file, no two tasks edit the
  same file, and each task's tests import only its own module plus stdlib
  and frozen master code.
- **Boring, stdlib-only**: `argparse`, `sys`, `pathlib`. No `difflib`, no
  `hashlib`, no `filecmp`, no third-party packages.
- **Byte-identical regression**: `pipeline-status` (no args),
  `pipeline-status --watch [--interval N]`, `pipeline-status archive
  [--name N]`, `pipeline-status history`, and `pipeline-status history
  NAME` MUST produce byte-identical stdout to v1/v2/v3 for any given
  filesystem state.
- **Cross-feature merge minimisation**: a sibling Architect is writing
  Feature B (`restore`). Both features add one subparser registration to
  `__main__.py` and one section to `README.md`. The `__main__.py` wiring
  task and the README task are designed to be a **single line each** so
  the merge conflict surface against Feature B is mechanical and
  trivially resolvable by the orchestrator.
- **Reuse v3 contracts** verbatim: `archive.slugify` (lazy-imported),
  `archive.TRACKED_ARTEFACTS` (lazy-imported), `inspectors.MAX_READ_BYTES`
  (eager-imported — it is the public v1 constant on master), and
  `formatting.use_colour()` (optional, only consulted by the renderer for
  MAY-level glyph colouring per FR-23). Nothing is re-implemented.
- **Read-only**: `diff` MUST NOT modify any file or create any auxiliary
  artefact (no manifest, no cache, no lockfile).
- **Determinism**: identical filesystem state on both sides MUST produce
  byte-identical stdout (modulo optional ANSI on TTY).
- **Performance**: <200 ms p99 per invocation on typical archives;
  startup of `pipeline-status` (no args) MUST NOT pay for `diff`'s body.

## Considered Options

### Decision 1: Subcommand wiring strategy

The v3 master already exposes `subparsers = parser.add_subparsers(dest="cmd",
required=False)` in `_build_parser()` and calls `add_archive_subparser` /
`add_history_subparser` against it. v4 piggybacks on this seam.

- **Option A: Inline `diff`'s argparse setup directly in `_build_parser()`.**
  - Cons: bloats `__main__.py`; couples `__main__` to `diff`'s argument
    surface; makes the `__main__.py` task non-trivial; increases the
    merge-conflict surface against Feature B's wiring task.
- **Option B (chosen): Mirror v3 — expose
  `add_diff_subparser(subparsers) -> argparse.ArgumentParser` from a new
  module, and add exactly one import line plus one call line to
  `__main__.py`.**
  - Pros: zero impact on v1/v2/v3 paths; the `__main__.py` edit is two
    lines (one `from ... import add_diff_subparser`, one
    `add_diff_subparser(subparsers)`); the merge conflict with Feature B
    is one line in each location and trivially mechanical.

**Chosen: Option B.** Two additive edits to `__main__.py`, both single
lines. Identical to the v3 pattern.

### Decision 2: Where the `diff` logic lives — one module or two

The implementation has two natural halves: (a) walk both sides, compute the
four-category result per artefact, (b) render the result as text + footer.

- **Option A: One module `pipeline_status/diff.py` containing both halves.**
  - Pros: one task to ship; one file to read.
  - Cons: single task is larger; not parallelisable across two engineers;
    couples comparison logic and rendering — neither testable
    independently without the other present in the worktree.
- **Option B (chosen): Two modules — `pipeline_status/diff_archives.py`
  (comparison logic + result dataclass) and `pipeline_status/format_diff.py`
  (renderer).**
  - Pros: each is a small file with a clear public contract; the two can be
    implemented in parallel by two engineers on two worktrees with zero
    inter-task imports at test time; mirrors v3's pattern of
    `history.py`/`format_history.py` separation.
  - Cons: one extra module in the package; one extra import indirection at
    runtime.

**Chosen: Option B.** The v3 `history.py` ↔ `format_history.py` split has
already proven itself; we extend the convention. Note: the requirements
text (FR-5 / NFR "Dependencies") permissively says "at most ONE new
submodule (`pipeline_status/diff.py`) plus its test file". The ADR
**deliberately splits this into two modules** to support parallel
fan-out; FR-5 is therefore relaxed by this ADR. The split is acceptable
because it is strictly additive — no v3 module is modified — and the
test suite continues to use only stdlib `unittest` + `tempfile`. The PM
should record this as a documented ADR-vs-requirements delta when
generating tickets.

### Decision 3: Side resolution (live vs archive, archive vs archive)

Per FR-8/FR-9, the positional `NAME` is always the **right** side and is
slugified through `archive.slugify`. The left side is either the live state
directory (when `--against` is omitted) or another archive (when `--against
OTHER` is supplied, also slugified).

- **Option A: Resolve both sides in `__main__.py`'s argparse wiring.**
  - Cons: pushes path-construction logic into the wiring task; couples
    `__main__.py` to the `archive_root` convention.
- **Option B (chosen): Resolve both sides inside
  `diff_archives.run_diff(args)`, lazy-importing `archive.slugify` inside
  the function body, identical to the v3 pattern in
  `history.run_history`.**
  - Pros: keeps `__main__.py` thin; keeps the `diff_archives` test seam
    closed (tests pass already-resolved `Path` objects directly to the
    compute function, never going through argparse); matches v3 patterns
    exactly.

**Chosen: Option B.** The lazy import of `archive.slugify` lives inside
`run_diff` only; the comparison primitives (`compute_diff(left_dir,
right_dir) -> DiffReport`) accept resolved `Path` inputs and do not import
`archive` at all.

### Decision 4: How to compute file equality with `MAX_READ_BYTES` cap

Per FR-15/FR-16/FR-17, two files are "equal" iff their first
`MAX_READ_BYTES` bytes are byte-equal; no normalisation, no line-ending
fixups.

- **Option A: `Path.read_bytes()` on each side, slice to
  `MAX_READ_BYTES`, compare.**
  - Pros: one line each; idiomatic. Holds at most `2 * MAX_READ_BYTES` =
    20 MiB in memory transiently, which is well within the NFR resource
    cap. Bytes for each artefact are released between artefacts (a fresh
    `read_bytes()` for each).
  - Cons: reads the whole file even when the first byte differs.
- **Option B: Stream-and-compare in chunks, short-circuit on first
  mismatch.**
  - Pros: lower memory peak; faster for files where the difference is
    near the start.
  - Cons: more code; the v1 inspectors use `read()` not streaming, so
    we'd be introducing a new I/O pattern just for `diff`.
- **Option C: Hash-and-compare with `hashlib.sha256` per side.**
  - Cons: NFR forbids `hashlib`; performance worse than Option A on
    sub-MAX_READ_BYTES files.

**Chosen: Option A.** Idiomatic, matches v1 inspector style, well within
the resource cap. Implementation: a private helper
`_read_capped(path: Path) -> bytes | None` that opens the file, reads up to
`MAX_READ_BYTES`, returns the bytes; returns `None` on any `OSError`
(matching the v3 inspector tolerance rule, FR-19).

### Decision 5: Where the slugifier lives

Per FR-9 / FR-28, the `diff` module MUST NOT import `pipeline_status.archive`
at module load time; the slugifier is consumed via a lazy import inside the
function that needs it.

- **Option A (chosen): `diff_archives.run_diff` does
  `from pipeline_status.archive import slugify` lazily, exactly mirroring
  `history.run_history`.**
  - Pros: zero cost on the no-subcommand path; one canonical implementation
    of the slugifier (v3's `archive.slugify`); test seam intact.
- **Option B: Duplicate the slugifier inside `diff_archives.py`.**
  - Cons: violates the requirements "Reuse v3 contracts" rule explicitly
    (FR-9, assumptions); two slugifiers would have to be kept in sync.

**Chosen: Option A.**

### Decision 6: Output format — row shape, ordering, footer wording, glyphs

Per FR-20/FR-21/Open Question 8, the renderer emits:

- One row per artefact whose category is `+`, `-`, `=`, or `M`. Both-absent
  artefacts are NOT emitted as rows but ARE counted as `unchanged` (Open
  Question 1, proposed default).
- Rows appear in canonical `TRACKED_ARTEFACTS` order (Open Question 3,
  proposed default), not grouped by category.
- One blank line between rows and footer.
- Footer: `f"Diff: {a} added, {r} removed, {u} unchanged, {m} modified."`
  byte-for-byte (Open Question 8, proposed default).
- Each row: `f"{glyph} {basename}"`. Single space; no leading indentation;
  no trailing whitespace.
- Trailing `\n` on every line (rows, blank separator, footer). The renderer
  returns a single string ending with `"\n"`; the action callable prints
  it with `end=""`.

**Chosen: as above.** The four open questions resolved with the proposed
defaults. Footer counts always sum to `len(TRACKED_ARTEFACTS) == 5`.

### Decision 7: ANSI colour for glyphs

Per FR-23, colour is **MAY**, not MUST. The plain-ASCII path is the
contract.

- **Option A (chosen): Plain ASCII glyphs only.** The renderer emits
  `"+"`, `"-"`, `"="`, `"M"` literally regardless of TTY status.
- **Option B: Wrap glyphs in ANSI codes when `formatting.use_colour()` is
  true.**
  - Cons: more code; FR-23 explicitly says colour MUST NOT alter the byte
    length of the glyph or basename — implementing this correctly adds
    test surface for marginal benefit.

**Chosen: Option A.** Plain ASCII unconditionally. The renderer does NOT
import or call `formatting.use_colour()`. This keeps `format_diff.py`
trivially deterministic across TTY and pipe contexts and avoids any
risk of byte-length drift. If a future iteration wants colour, it adds a
single `use_colour()` check at the row-formatting site; out of scope for
v4.

### Decision 8: Exit codes

Per FR-25, the exit-code contract is:

| Condition | Exit |
|---|---|
| Successful comparison (regardless of category mix) | 0 |
| `slugify(NAME)` or `slugify(OTHER)` produces `""` | 1 |
| `right_dir` does not resolve to a directory | 1 |
| `left_dir` does not resolve to a directory (with `--against`) | 1 |
| Live `.claude/state/` directory missing (no `--against`) | 2 |
| Argparse errors (missing `NAME`, unknown subcommand, `--watch` with `diff`) | argparse default (2) |

**Chosen: as above, no deviation.** The "1 vs 2" split mirrors v3:
exit 2 means "the live state directory is structurally absent" (a v1
concept), while exit 1 means "the user-supplied archive name resolves to
a non-existent path" (a v3 concept introduced for `archive` collisions and
`history NAME` lookups).

### Decision 9: Per-file read tolerance (FR-19)

Per FR-19, if reading either side of one artefact raises `OSError`
(including `PermissionError`, `IsADirectoryError`), the comparison for that
artefact treats the unreadable side as **absent** and continues.

- **Option A (chosen): `_read_capped(path) -> bytes | None` returns
  `None` on any `OSError`; the comparison treats `None` like
  `not is_file()` (i.e. "absent").**
- **Option B: Abort the whole comparison on the first `OSError`.**
  - Cons: violates FR-19 explicitly.

**Chosen: Option A.** Note the subtle implication: if `left_path.is_file()`
returns True but `read_bytes()` then raises, we count that side as
"absent" for the equality test even though `is_file()` said "present".
This is a deliberate "best-effort" stance documented in `Known edge
cases` below.

### Decision 10: Both-absent counting (Open Question 1)

A `TRACKED_ARTEFACTS` entry that is absent from BOTH sides:

- **Option A (chosen): Count as `unchanged` in the footer; do NOT emit a
  row.**
  - Pros: footer counts sum to exactly 5 (= `len(TRACKED_ARTEFACTS)`)
    — a stable invariant the tests assert; visible row list stays
    focused on artefacts that exist somewhere.
- **Option B: Omit from both rows and footer (footer can sum to <5).**
  - Cons: breaks the "footer sums to 5" invariant.
- **Option C: Count as `unchanged` and ALSO emit a row.**
  - Cons: noisy; emits rows for files nobody has.

**Chosen: Option A** (Open Question 1 proposed default).

### Decision 11: MAX_READ_BYTES truncation semantics (Open Question 2)

If either side exceeds `MAX_READ_BYTES`, comparison is on the first
`MAX_READ_BYTES` bytes only. Files differing only beyond the cap compare as
`=`.

**Chosen: accept the cap-truncated comparison** (Open Question 2 proposed
default). Documented as a known edge case. The 10 MiB cap is far larger
than any realistic `.claude/state/` artefact.

### Decision 12: Self-comparison (`diff foo --against foo`) (Open Question 5)

Both sides resolve to the same directory; comparison trivially produces
five `=` rows.

**Chosen: proceed normally, no short-circuit** (Open Question 5 proposed
default). Negligible cost; valuable as a CI smoke test.

### Decision 13: Symlinked archive directories (Open Question 7)

`Path.is_dir()` follows symlinks by default; v3 follows them; v4 follows
them.

**Chosen: follow symlinks** (Open Question 7 proposed default).

### Decision 14: Cross-feature merge contract (`diff` vs `restore`)

A sibling pipeline ships `pipeline-status restore` concurrently with this
one. Both features add a subparser registration to `__main__.py` and a
section to `README.md`.

**Conflict surface in `__main__.py`:**

- This feature adds exactly two lines: one `from pipeline_status.diff_archives
  import add_diff_subparser` (alphabetically between `archive` and `formatting`
  in the existing import block), and one `add_diff_subparser(subparsers)`
  call inside `_build_parser()` immediately after `add_history_subparser(
  subparsers)`.
- Feature B will add the analogous pair of lines for `restore`.
- The orchestrator resolves the merge by keeping both pairs, in
  alphabetical or feature order; both pairs are independent and additive.

**Conflict surface in `README.md`:**

- This feature adds one new section/subsection documenting `diff`.
- Feature B adds an analogous section for `restore`.
- Both sections are independent; the orchestrator concatenates them in
  alphabetical (or feature) order.

**Engineer instructions** (encoded in the file ownership table):

- The `__main__.py` task (A3) edits ONLY the two specified lines. It MUST
  NOT refactor adjacent code, reorder imports, or touch the
  `--watch`/`--interval` block, `_run_one_shot`, `_locate_state_dir`,
  `_interval_type`, or `_EPILOG`.
- The `README.md` task (A4) appends a self-contained section for `diff`
  without modifying any other section.

**Chosen: minimal additive edits, two lines in `__main__.py`, one new
section in `README.md`.** Merge with Feature B is mechanical.

## Architecture

### Component Diagram (file-ownership map)

```
repo root/
├── pipeline_status/
│   ├── __init__.py              # UNCHANGED
│   ├── __main__.py              # ── Task A3 ──  +2 lines (1 import, 1 call)
│   ├── inspectors.py            # UNCHANGED  (frozen v1 contract)
│   ├── stage.py                 # UNCHANGED  (frozen v1 contract)
│   ├── formatting.py            # UNCHANGED  (frozen v1/v2 contract)
│   ├── watch.py                 # UNCHANGED  (frozen v2 contract)
│   ├── archive.py               # UNCHANGED  (frozen v3 contract)
│   ├── history.py               # UNCHANGED  (frozen v3 contract)
│   ├── format_history.py        # UNCHANGED  (frozen v3 contract)
│   ├── diff_archives.py         # ── Task A1 ──  NEW: compare-two-archives + DiffReport
│   └── format_diff.py           # ── Task A2 ──  NEW: row + footer renderer
├── tests/
│   ├── __init__.py              # UNCHANGED
│   ├── test_inspectors.py       # UNCHANGED
│   ├── test_stage.py            # UNCHANGED
│   ├── test_formatting_helpers.py  # UNCHANGED
│   ├── test_formatting_smoke.py    # UNCHANGED
│   ├── test_watch.py            # UNCHANGED
│   ├── test_archive.py          # UNCHANGED
│   ├── test_history.py          # UNCHANGED
│   ├── test_format_history.py   # UNCHANGED
│   ├── test_main_subcommands.py # UNCHANGED  (Task A3 does NOT edit this)
│   ├── test_diff_archives.py    # ── Task A1 ──  NEW
│   └── test_format_diff.py      # ── Task A2 ──  NEW
├── README.md                    # ── Task A4 ──  EDIT: add `diff` section
└── pyproject.toml               # UNCHANGED
```

Note on tests for the wiring task (A3): per the v3 pattern (Decision 12 in
the v3 ADR's "parallel-safe" reasoning), the end-to-end CLI test file
`tests/test_main_subcommands.py` is owned by the v3 archive-mode delivery
and is **not edited** by this iteration. Argparse-level smoke tests for the
`diff` subparser live alongside `tests/test_diff_archives.py` (Task A1's
test file), constructed by invoking
`pipeline_status.__main__._build_parser().parse_args([...])` against the
already-wired master parser. This keeps the A3 task to one production-file
edit (two lines) with no matching test-file edit.

### Data Model

One new public dataclass, owned by Task A1:

```python
# pipeline_status/diff_archives.py
from dataclasses import dataclass, field
from typing import Final

# The four glyph categories, in the order they appear in the footer.
CATEGORY_ADDED:     Final[str] = "+"
CATEGORY_REMOVED:   Final[str] = "-"
CATEGORY_UNCHANGED: Final[str] = "="
CATEGORY_MODIFIED:  Final[str] = "M"


@dataclass(frozen=True)
class ArtefactDiff:
    """The per-artefact result of comparing one tracked basename.

    Attributes:
        name:     The artefact basename (one of TRACKED_ARTEFACTS).
        category: One of "+", "-", "=", "M".
        emit_row: True if this artefact should be rendered as a row;
                  False for the both-absent special case (Decision 10).
                  Both-absent entries still contribute to the footer
                  "unchanged" count.
    """
    name: str
    category: str
    emit_row: bool


@dataclass(frozen=True)
class DiffReport:
    """The full result of one diff invocation.

    Attributes:
        artefacts: One entry per TRACKED_ARTEFACTS basename, in canonical
                   order. Length is always len(TRACKED_ARTEFACTS) == 5.
        added, removed, unchanged, modified:
                   Footer counts. Their sum always equals len(artefacts).
    """
    artefacts: tuple[ArtefactDiff, ...]
    added: int
    removed: int
    unchanged: int
    modified: int
```

`DiffReport.artefacts` is a `tuple` (not a `list`) so the dataclass remains
frozen and hashable. `len(artefacts)` is always exactly
`len(TRACKED_ARTEFACTS)` (currently 5). Footer counts sum to
`len(artefacts)` by construction.

No new persistent data structures; `diff` writes nothing to disk.

### API Contracts (verbatim signatures — per task)

These are **the** parallel-fan-out contract. Each engineer writes their
module body to match these signatures exactly; sibling tasks may write
`from pipeline_status.X import Y` statements against them before the
dependency PR has merged. Test files for each task construct their
inputs directly without depending on the sibling module being present in
the worktree (see "Stub strategy for parallel work" below).

#### Task A1 — `pipeline_status/diff_archives.py`

```python
"""
Compare two pipeline runs (live vs archive, or archive vs archive) on the
five tracked artefacts and report per-artefact + aggregate categories.

Public symbols:
    CATEGORY_ADDED, CATEGORY_REMOVED, CATEGORY_UNCHANGED, CATEGORY_MODIFIED:
                                              Final[str] one-character glyphs
    ArtefactDiff                              (frozen dataclass; see Data Model)
    DiffReport                                (frozen dataclass; see Data Model)
    compute_diff(left_dir, right_dir)         -> DiffReport
    add_diff_subparser(subparsers)            -> argparse.ArgumentParser
    run_diff(args)                            -> int   (argparse action callable)

Stdlib only. Lazy import of pipeline_status.archive inside run_diff (for
slugify and TRACKED_ARTEFACTS); no top-level import of sibling tasks.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pipeline_status.inspectors import MAX_READ_BYTES


# Public glyph constants (Decision 6).
CATEGORY_ADDED:     Final[str] = "+"
CATEGORY_REMOVED:   Final[str] = "-"
CATEGORY_UNCHANGED: Final[str] = "="
CATEGORY_MODIFIED:  Final[str] = "M"


# Private duplicate of archive.TRACKED_ARTEFACTS to keep this module
# parallel-safe at test time (mirrors history.py's pattern; see ADR
# v3 Decision 12). compute_diff iterates this tuple; run_diff lazy-
# imports archive.TRACKED_ARTEFACTS for the production path to ensure
# the two stay in lockstep if archive.TRACKED_ARTEFACTS is ever extended.
_TRACKED_ARTEFACTS: Final[tuple[str, ...]] = (
    "feature-request.md",
    "requirements.md",
    "adr.md",
    "tasks.json",
    "worktrees.json",
)


@dataclass(frozen=True)
class ArtefactDiff:
    name: str
    category: str
    emit_row: bool


@dataclass(frozen=True)
class DiffReport:
    artefacts: tuple[ArtefactDiff, ...]
    added: int
    removed: int
    unchanged: int
    modified: int


def _read_capped(path: Path) -> bytes | None:
    """Read up to MAX_READ_BYTES bytes from ``path``.

    Returns the bytes on success, or ``None`` on any ``OSError``
    (PermissionError, IsADirectoryError, etc.). Callers treat ``None``
    as "absent" for the equality test, per FR-19 / Decision 9.
    """


def compute_diff(left_dir: Path, right_dir: Path) -> DiffReport:
    """Compute the four-category diff of two pipeline runs.

    Side semantics:
        left_dir  -> the "old" side (--against OTHER, or the live state when
                     --against is omitted).
        right_dir -> the "new" side (the positional NAME archive).

    For each name in _TRACKED_ARTEFACTS, in canonical order:
        left_path  = left_dir / name
        right_path = right_dir / name
        left_present  = left_path.is_file()
        right_present = right_path.is_file()

        if right_present and not left_present:
            category = "+", emit_row = True
        elif left_present and not right_present:
            category = "-", emit_row = True
        elif left_present and right_present:
            left_bytes  = _read_capped(left_path)
            right_bytes = _read_capped(right_path)
            # Treat unreadable-side as absent per FR-19 / Decision 9:
            if left_bytes is None and right_bytes is None:
                category = "=", emit_row = False    # both unreadable -> both-absent
            elif left_bytes is None:
                category = "+", emit_row = True
            elif right_bytes is None:
                category = "-", emit_row = True
            elif left_bytes == right_bytes:
                category = "=", emit_row = True
            else:
                category = "M", emit_row = True
        else:  # both absent
            category = "=", emit_row = False

    Returns a DiffReport with the artefacts tuple in canonical order and the
    four footer counts. Sum of counts is exactly len(_TRACKED_ARTEFACTS).

    Never raises. Does not import pipeline_status.archive (the slugifier
    work has already been done by the caller).
    """


def add_diff_subparser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> argparse.ArgumentParser:
    """Register the ``diff`` subcommand on ``subparsers``.

    Declares exactly:
        - one required positional ``NAME`` (no nargs),
        - one optional ``--against OTHER`` (default None).

    NO other flags (FR-6). Calls ``sp.set_defaults(func=run_diff)``.

    Subparser help/description text describes the positional NAME, the
    optional --against OTHER, the four glyph categories, and the exit-code
    contract.
    """


def run_diff(args: argparse.Namespace) -> int:
    """Action callable for the ``diff`` subcommand.

    Algorithm:
        1. state_dir = Path.cwd() / ".claude" / "state"
        2. archive_root = state_dir / "archive"
        3. Lazy import: from pipeline_status.archive import slugify
        4. Resolve right side:
              right_slug = slugify(args.name)
              if right_slug == "": stderr error, return 1
              right_dir = archive_root / right_slug
              if not right_dir.is_dir(): stderr error, return 1
        5. Resolve left side:
              if args.against is not None:
                  left_slug = slugify(args.against)
                  if left_slug == "": stderr error, return 1
                  left_dir = archive_root / left_slug
                  if not left_dir.is_dir(): stderr error, return 1
              else:
                  if not state_dir.is_dir(): stderr error, return 2
                  left_dir = state_dir
        6. report = compute_diff(left_dir, right_dir)
        7. Lazy import: from pipeline_status.format_diff import format_diff_report
           print(format_diff_report(report), end="")
        8. return 0

    Error message strings:
        - "pipeline-status: error: diff name is empty after normalisation"
        - "pipeline-status: error: diff --against value is empty after normalisation"
        - f"pipeline-status: error: archive {args.name!r} not found at {right_dir}"
        - f"pipeline-status: error: archive {args.against!r} not found at {left_dir}"
        - "pipeline-status: error: .claude/state/ not found or not a directory"
    """
```

Notes for Task A1's engineer:

- `compute_diff` accepts two `Path` inputs already resolved by the caller;
  it does NOT slugify. This keeps `test_diff_archives.py` decoupled from
  `archive.slugify`: tests construct two `tempfile.TemporaryDirectory()`
  paths directly.
- The "both unreadable" branch in `compute_diff` resolves to `"="` with
  `emit_row=False` — same as both-absent. This is the consistent
  application of FR-19: each side's `None` is "absent"; both `None` is
  "both absent".
- The "one unreadable, one present" cases produce `"+"` or `"-"` (not
  `"M"`). This matches the FR-19 contract: unreadable side is *treated as
  absent*, so the comparison degenerates to the one-side-present case.
- `_TRACKED_ARTEFACTS` is duplicated from `archive.py` for the same
  parallel-fan-out reason `history._TRACKED_ARTEFACTS` is duplicated. Do
  NOT replace with a top-level import; the lazy import inside `run_diff`
  is the production seam.
- Stdout output is **not** emitted by `compute_diff` — only by `run_diff`
  via the renderer. `compute_diff` is purely functional and side-effect
  free.

#### Task A2 — `pipeline_status/format_diff.py`

```python
"""
Render a DiffReport as the per-artefact summary + footer.

Public symbols:
    format_diff_report(report) -> str    (multi-line; ends with '\\n')

Stdlib only. Does NOT import pipeline_status.archive, pipeline_status.history,
pipeline_status.format_history, or pipeline_status.diff_archives at top level
(the dataclass is referenced under TYPE_CHECKING only to keep test-time imports
clean for Task A2).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for static type-checking
    from pipeline_status.diff_archives import DiffReport


def format_diff_report(report: "DiffReport") -> str:
    """Render a DiffReport as the per-artefact summary + footer.

    Output format (Decision 6):

        <one '{glyph} {basename}\\n' per ArtefactDiff with emit_row=True,
         in input order (which is canonical TRACKED_ARTEFACTS order)>
        \\n
        f'Diff: {report.added} added, {report.removed} removed, '
        f'{report.unchanged} unchanged, {report.modified} modified.\\n'

    Notes:
        - No leading indentation on rows (contrast with v1 one-shot, which
          uses two leading spaces).
        - No trailing whitespace on any line.
        - The returned string always ends with a single '\\n' (the footer's).
        - When no artefact has emit_row=True (all five both-absent: a
          degenerate but possible case), the output is just the blank line
          followed by the footer line. The blank-line separator is emitted
          UNCONDITIONALLY so the footer is always preceded by one blank
          line.
        - No ANSI colour is emitted (Decision 7); glyphs are plain ASCII.

    The function accepts any object exposing the attributes of DiffReport
    (structural protocol) but in practice receives DiffReport instances
    only.
    """
```

Notes for Task A2's engineer:

- The renderer is pure: no I/O, no filesystem access, no environment
  reads, no `formatting.use_colour()` consultation. Tests instantiate
  `DiffReport` and `ArtefactDiff` fixtures directly (the dataclasses are
  imported from `pipeline_status.diff_archives` under `TYPE_CHECKING` only
  for static-typing; tests can use `dataclasses.make_dataclass` or a
  lightweight `types.SimpleNamespace` shim if Task A1's module isn't
  present in the worktree).
- **Test-time shim recommendation**: in `tests/test_format_diff.py`, define
  a local `_FakeArtefactDiff` (`namedtuple` with `name`, `category`,
  `emit_row`) and `_FakeReport` (`namedtuple` with `artefacts`, `added`,
  `removed`, `unchanged`, `modified`) so the test file has zero import
  dependency on Task A1's `diff_archives` module. This keeps A1 and A2
  fully parallel at test time.
- The blank-line separator is unconditional: rows + blank + footer, even
  if the rows section is empty. This makes the byte layout predictable
  for tests.
- Return value is a `str` ending in `"\n"`. The caller (`run_diff`) prints
  with `end=""` so the trailing newline is not doubled.

#### Task A3 — `pipeline_status/__main__.py`

The diff against master is **exactly two lines**, both additive:

```python
# (1) Add to the existing import block, alphabetically sorted with the
#     other `from pipeline_status.*` imports (between `archive` and
#     `formatting` in the current file):
from pipeline_status.diff_archives import add_diff_subparser

# (2) Add inside _build_parser(), immediately after the existing
#     `add_history_subparser(subparsers)` line:
add_diff_subparser(subparsers)
```

No other changes. `_interval_type`, `_locate_state_dir`, `_run_one_shot`,
`_EPILOG`, `_STATE_DIR`, `main()`'s body, the watch-mode lazy import block,
the v1/v2 print sequence — all byte-identical to master.

Notes for Task A3's engineer:

- The PM's task description for A3 must explicitly call out:
  - "ONLY add two lines: one import, one function call. Do not refactor."
  - "The current file imports `add_archive_subparser` and
    `add_history_subparser`; add `add_diff_subparser` alphabetically
    between them (i.e. after `archive`, before `history`)."
  - "The current `_build_parser()` already calls
    `add_archive_subparser(subparsers)` and
    `add_history_subparser(subparsers)`; add
    `add_diff_subparser(subparsers)` on its own line immediately after the
    `history` call."
- Task A3 owns no test file. Argparse smoke tests for the `diff` subparser
  live in `tests/test_diff_archives.py` (Task A1) and exercise the parser
  by calling `pipeline_status.__main__._build_parser().parse_args([...])`
  — which works only after Task A3 has merged. Sequencing: A3 merges
  after A1 (or A1's tests that import `_build_parser` are skipped on its
  own worktree and exercised only at master-merge time).
- **Alternative sequencing** (PM choice): A3 lands LAST, after A1+A2; A1's
  test file can include the argparse smoke tests because by the time A3
  has merged the wiring is in place.

#### Task A4 — `README.md`

Documentation only. Adds a self-contained section (or subsection) titled
`### `diff`` (or equivalent — exact heading wording is not part of the
byte-identical contract). The section includes:

- One-paragraph description of `pipeline-status diff [--against OTHER]
  NAME`.
- The two-mode contract (live-vs-archive and archive-vs-archive).
- The four glyph categories (`+`, `-`, `=`, `M`) with one-sentence
  explanations each.
- An example invocation with example output (one row of each category +
  the footer).
- The exit-code matrix (mirror Decision 8's table).
- A note that `--watch` and `--interval` are not accepted with `diff`.

Notes for Task A4's engineer:

- Insert the section in alphabetical/feature order alongside the existing
  `archive` and `history` sections (if present) so the merge conflict
  surface with Feature B's `restore` section is minimal — Feature B's
  section will land either alphabetically (between `history` and the new
  `diff`) or at the end; either is acceptable as long as the new
  section is self-contained.
- No code changes; no test file.

### CLI Surface

After this iteration:

```
pipeline-status [-h] [--watch] [--interval SECONDS] {archive,history,diff} ...

  (no subcommand)                          # v1/v2 path; one-shot or --watch
  archive [-h] [--name NAME]               # v3: snapshot live state
  history [-h] [NAME]                      # v3: table or detail
  diff [-h] [--against OTHER] NAME         # v4: per-artefact summary
```

`pipeline-status diff --help` describes the positional `NAME`, the optional
`--against OTHER`, the four glyph categories, and the exit-code contract
(FR-4).

`pipeline-status --help` now lists `{archive,history,diff}` in its usage
line (argparse default behaviour — explicitly allowed by FR-4 / NFR
backwards-compatibility, which only requires *one-shot* and *--watch*
report stdout to remain byte-identical; the `--help` text was already
permitted to change in v3's Decision 1).

#### Exit-code matrix

| Invocation | Filesystem condition | Exit |
|---|---|---|
| `pipeline-status` (no args) | live state present | 0 |
| `pipeline-status` (no args) | live state missing | 2 |
| `pipeline-status --watch` | (any) | 0 on clean Ctrl+C |
| `pipeline-status archive [--name N]` | (per v3) | 0 / 1 / 2 |
| `pipeline-status history` | (per v3) | 0 |
| `pipeline-status history NAME` | (per v3) | 0 / 1 |
| `pipeline-status diff NAME` | right archive missing | 1 |
| `pipeline-status diff NAME` | right archive present, live state missing | 2 |
| `pipeline-status diff NAME` | right archive present, live state present | 0 |
| `pipeline-status diff --against OTHER NAME` | either archive missing | 1 |
| `pipeline-status diff --against OTHER NAME` | both archives present | 0 |
| `pipeline-status diff` (with empty slug) | NAME or OTHER slugifies to "" | 1 |
| `pipeline-status diff --watch ...` | (any) | argparse 2 |
| `pipeline-status diff` (no NAME) | (any) | argparse 2 |
| `pipeline-status frobnicate` | (any) | argparse 2 |

### Sequence Diagrams (text)

#### `diff` happy path — live vs archive (`pipeline-status diff foo`)

```
User: $ pipeline-status diff foo
__main__.main()
  args = _build_parser().parse_args()
    -> args.cmd = "diff", args.name = "foo", args.against = None,
       args.func = diff_archives.run_diff
  args.cmd is not None: sys.exit(args.func(args))

diff_archives.run_diff(args):
  state_dir = Path.cwd()/".claude"/"state"
  archive_root = state_dir / "archive"
  from pipeline_status.archive import slugify           # lazy
  right_slug = slugify("foo") = "foo"
  right_dir = archive_root / "foo"
  if not right_dir.is_dir(): stderr + return 1
  # args.against is None:
  if not state_dir.is_dir(): stderr + return 2
  left_dir = state_dir
  report = compute_diff(left_dir, right_dir)
  from pipeline_status.format_diff import format_diff_report  # lazy
  print(format_diff_report(report), end="")
  return 0
```

#### `diff` happy path — archive vs archive (`pipeline-status diff --against bar foo`)

```
User: $ pipeline-status diff --against bar foo
__main__.main()
  args.cmd = "diff", args.name = "foo", args.against = "bar"
  sys.exit(args.func(args))

diff_archives.run_diff(args):
  state_dir = Path.cwd()/".claude"/"state"
  archive_root = state_dir / "archive"
  from pipeline_status.archive import slugify
  right_slug = slugify("foo") = "foo"
  right_dir = archive_root / "foo"
  if not right_dir.is_dir(): stderr + return 1
  # args.against is "bar":
  left_slug = slugify("bar") = "bar"
  left_dir = archive_root / "bar"
  if not left_dir.is_dir(): stderr + return 1
  report = compute_diff(left_dir, right_dir)
  from pipeline_status.format_diff import format_diff_report
  print(format_diff_report(report), end="")
  return 0
```

#### `compute_diff` per-artefact loop

```
compute_diff(left_dir, right_dir):
  diffs = []
  added = removed = unchanged = modified = 0
  for name in _TRACKED_ARTEFACTS:                       # canonical order
    left_path  = left_dir  / name
    right_path = right_dir / name
    lp = left_path.is_file()
    rp = right_path.is_file()

    if rp and not lp:
      diffs.append(ArtefactDiff(name, "+", emit_row=True)); added += 1
    elif lp and not rp:
      diffs.append(ArtefactDiff(name, "-", emit_row=True)); removed += 1
    elif lp and rp:
      lb = _read_capped(left_path)
      rb = _read_capped(right_path)
      if lb is None and rb is None:
        diffs.append(ArtefactDiff(name, "=", emit_row=False)); unchanged += 1
      elif lb is None:
        diffs.append(ArtefactDiff(name, "+", emit_row=True)); added += 1
      elif rb is None:
        diffs.append(ArtefactDiff(name, "-", emit_row=True)); removed += 1
      elif lb == rb:
        diffs.append(ArtefactDiff(name, "=", emit_row=True)); unchanged += 1
      else:
        diffs.append(ArtefactDiff(name, "M", emit_row=True)); modified += 1
    else:  # both absent
      diffs.append(ArtefactDiff(name, "=", emit_row=False)); unchanged += 1

  return DiffReport(tuple(diffs), added, removed, unchanged, modified)
```

#### `format_diff_report` output

```
format_diff_report(report):
  parts = []
  for a in report.artefacts:
    if a.emit_row:
      parts.append(f"{a.category} {a.name}\n")
  parts.append("\n")                                    # always, even if no rows
  parts.append(
    f"Diff: {report.added} added, {report.removed} removed, "
    f"{report.unchanged} unchanged, {report.modified} modified.\n"
  )
  return "".join(parts)
```

Example output for a mixed-category invocation (live `.claude/state/`
contains a modified `adr.md` and a new `tasks.json`; archive `foo`
contained `feature-request.md`, `requirements.md`, the old `adr.md`, and
no `tasks.json` or `worktrees.json`):

```
= feature-request.md
= requirements.md
M adr.md
+ tasks.json

Diff: 1 added, 0 removed, 3 unchanged, 1 modified.
```

Counts: 2 `=` (feature-request, requirements) + 1 `M` (adr) + 1 `+`
(tasks) + 1 both-absent (worktrees, counted as unchanged, no row) = 5.
Footer: `1 added, 0 removed, 3 unchanged, 1 modified.` (3 = 2 emitted + 1
both-absent). Sum 1+0+3+1 = 5.

#### Regression contract — v1/v2/v3 paths untouched

```
$ pipeline-status                       # v1: byte-identical one-shot
$ pipeline-status --watch               # v2: byte-identical watch loop
$ pipeline-status archive --name foo    # v3: byte-identical archive write
$ pipeline-status history               # v3: byte-identical table
$ pipeline-status history foo           # v3: byte-identical detail
```

All five paths produce byte-identical stdout to their respective releases
for the same filesystem state. The only `__main__.py` edit is the two
additive lines in Decision 14 / Task A3; `_build_parser()` continues to
return a parser whose top-level `--help` text gains exactly one new
`diff` entry in its `{archive,history,diff}` usage line.

## Implementation Notes

### File ownership table — the parallel-fan-out contract

| Task | Owns (production) | Owns (test) | Imports from master | Imports from sibling tasks |
|---|---|---|---|---|
| A1 | `pipeline_status/diff_archives.py` | `tests/test_diff_archives.py` | `pipeline_status.inspectors` (for `MAX_READ_BYTES`); LAZY `pipeline_status.archive` (for `slugify`) inside `run_diff` only | none at module load time |
| A2 | `pipeline_status/format_diff.py` | `tests/test_format_diff.py` | stdlib only; `pipeline_status.diff_archives` under `TYPE_CHECKING` only | none at runtime |
| A3 | `pipeline_status/__main__.py` (+2 lines) | (no new test file) | imports A1's `add_diff_subparser` | A1 |
| A4 | `README.md` (one new section) | (no test file) | (none) | (none) |

Key parallel-fan-out properties:

- **No two tasks edit the same file** within Feature A. Confirmed by the
  table.
- **A1 and A2's test files have zero import dependency on each other**.
  A2's test file uses a local `_FakeReport` shim (Task A2 notes) instead
  of importing from `diff_archives`.
- **A1's test file has zero import dependency on `archive` or `history`**.
  The `compute_diff` tests construct `Path` inputs via
  `tempfile.TemporaryDirectory()` directly. Argparse smoke tests for
  `add_diff_subparser` construct a throwaway `argparse.ArgumentParser`
  with a `subparsers` action and call `add_diff_subparser(subparsers)`
  on it — no dependency on `__main__._build_parser` or on Task A3 being
  merged.
- **End-to-end CLI tests** (invoking `_build_parser().parse_args(["diff",
  "foo"])`) require A3 to be merged. The PM has two scheduling options:
  1. **Recommended**: A1, A2, A4 run in parallel; A3 lands LAST (forked
     from post-A1-merge tip). A1's test file MAY include
     `_build_parser`-driven smoke tests in the same PR; they will run on
     the A3-base worktree at merge time. This is the simplest dispatch.
  2. **Alternative**: All four in parallel. A1's `_build_parser`-driven
     tests are gated behind a try/except `ImportError` on the
     `add_diff_subparser` import path, and assert nothing on the
     pre-merge worktree. Acceptable but adds complexity.

### Stub strategy for parallel work

Each engineer works on an isolated `git worktree` forked from master, sees
only the v1/v2/v3 master files plus their own task's new file(s), and runs
their tests against master + their own files.

- **A1's worktree** sees master + `pipeline_status/diff_archives.py` +
  `tests/test_diff_archives.py`. Tests cover:
  - `compute_diff` happy paths: all `=`, all `+`, all `-`, all `M`, mixed.
  - `compute_diff` MAX_READ_BYTES edge case: both sides differ beyond the
    cap, comparison reports `=`.
  - `_read_capped` returning `None` on `OSError` (use `unittest.mock` to
    patch `pathlib.Path.read_bytes` or use a temporary directory with
    a permission-stripped file on POSIX; skip the permission test on
    Windows via `unittest.skipIf`).
  - "Both unreadable" → `=` with `emit_row=False`.
  - "One unreadable, one present" → `+` or `-`.
  - "Both absent" → `=` with `emit_row=False`; counted as `unchanged`.
  - Footer counts sum to exactly 5 for every successful call.
  - `add_diff_subparser` smoke test: build a throwaway parser with a
    `subparsers` action, call `add_diff_subparser(subparsers)`, assert
    that `parser.parse_args(["diff", "foo"])` produces
    `args.cmd == "diff"`, `args.name == "foo"`, `args.against is None`,
    `args.func == run_diff`.
  - Argparse rejection of `["diff"]` (missing NAME) → `SystemExit(2)`.
  - Argparse rejection of `["diff", "foo", "--watch"]` → `SystemExit(2)`.
  - `run_diff` end-to-end with `tempfile.TemporaryDirectory`s for the live
    and archive directories. Use `unittest.mock.patch("pipeline_status.
    diff_archives.Path.cwd", return_value=tmp_path)` (or
    `monkeypatch`-style via a setUp/tearDown `os.chdir` pair) to redirect
    `Path.cwd()` into the temp tree. Cover all exit codes (0, 1, 2).
- **A2's worktree** sees master + `pipeline_status/format_diff.py` +
  `tests/test_format_diff.py`. Tests cover:
  - Single-row outputs of each category (`+`/`-`/`=`/`M`) — verify exact
    byte sequence.
  - Multi-row outputs with mixed categories — verify canonical order.
  - All-rows-suppressed (every `ArtefactDiff` has `emit_row=False`) →
    output is `"\n" + footer_line + "\n"` (one blank line + footer).
  - Footer always present, always preceded by exactly one `"\n"`.
  - Footer wording byte-for-byte: `"Diff: 1 added, 0 removed, 3 unchanged,
    1 modified.\n"`.
  - Returned string ends in `"\n"`.
  - No ANSI escapes in any output (`assert "\x1b" not in result`).
  - Renderer is pure: feeding the same fixture twice produces the same
    string.
  - Uses `_FakeReport` / `_FakeArtefactDiff` shims, not real
    `diff_archives.DiffReport`, so A2's test file does not import A1.
- **A3's worktree** sees master + the two-line `__main__.py` edit. No
  test file. Verification is implicit via A1's `_build_parser`-driven
  tests running on the post-A3-merge tip.
- **A4's worktree** sees master + the edited `README.md`. No test file;
  acceptance is human review (PR diff).

### Known edge cases

1. **Slugifier on path-traversal input** (`pipeline-status diff
   "../../../etc/passwd"`): slugifier produces `"etc-passwd"`. By
   construction the resolved `right_dir` is `archive_root /
   "etc-passwd"`, which is a direct child of `archive_root`; if no such
   archive exists, the standard exit-1 error fires. No path-traversal
   risk inherited from v3.
2. **Slugifier on Unicode**: `"naïve"` slugifies to `"na-ve"`. Same as v3.
3. **`--against` resolves to the same slug as `NAME`** (`diff foo
   --against foo`): both sides resolve to the same directory; comparison
   produces five `=` rows; exit 0. No special-casing (Decision 12).
4. **`NAME` matches the literal slug `"archive"`** (`diff archive`):
   `right_dir = archive_root / "archive"`. Benign; same as v3 (Open
   Question 6).
5. **Symlinked archive directory**: `is_dir()` follows symlinks; the
   archive is included. Same as v3 (Decision 13).
6. **MAX_READ_BYTES truncation**: files exceeding 10 MiB on either side
   are compared on the first 10 MiB only. Differences beyond the cap
   produce a false `=`. Documented (Decision 11).
7. **Per-file `OSError` mid-comparison** (e.g. permission stripped on one
   side): the comparison treats the unreadable side as absent for that
   artefact only; the overall comparison continues. The unreadable side
   "becoming absent" causes the category to flip from `=`/`M` to
   `+`/`-` (whichever side is unreadable is the absent side). For the
   pathological case where the *other* side is also unreadable, both
   sides are "absent" and the artefact is counted as unchanged with no
   row.
8. **Live state present but completely empty** (no tracked artefacts)
   compared to an archive containing all five: five `-` rows, exit 0
   (Open Question 4 default).
9. **`--against` supplied but live state missing**: per FR-13 / Decision
   3, `left_dir` is the explicit archive; live state existence is
   irrelevant. Exit 0 if both archives resolve.
10. **`--against` omitted and live state missing**: exit 2 (matches v1
    one-shot missing-state semantics) — see Decision 8.
11. **Footer count invariant**: `added + removed + unchanged + modified
    == len(TRACKED_ARTEFACTS) == 5` for every successful invocation.
    Asserted by A1's tests.
12. **Self-archive vs nonexistent self**: `diff foo --against foo` when
    `foo` doesn't exist exits 1 with the right-side error (the right
    side is checked first per the sequence diagrams).
13. **Subparser inheritance**: argparse does NOT pass top-level `--watch`
    or `--interval` through to the `diff` subparser, so `pipeline-status
    diff foo --watch` exits 2 with a usage error to stderr (the existing
    v3 behaviour for `archive` / `history` extends to `diff` for free).
14. **`pipeline-status diff --help`**: argparse generates the help text
    from the subparser's `description` and the arguments' `help` strings.
    Wording is not part of the byte-identical contract.
15. **NAME containing only whitespace / punctuation** (`diff "  "`): slug
    is `""`; exit 1 with the "empty after normalisation" stderr error.
16. **NAME or OTHER with `argparse`-special characters** (`diff -foo`):
    argparse rejects with its standard "unrecognised argument" error;
    exit 2.

## Consequences

**Easier after this change:**

- "What changed between two runs?" is one command instead of an external
  `diff -r` and manual reasoning.
- CI gates can grep for `"adr.md"` in the rows section of `diff --against
  <last-release> HEAD` output to enforce "ADR must change for breaking
  features", or grep for the footer `0 modified` count.
- The split-module pattern (`diff_archives.py` + `format_diff.py`) sets a
  reusable template for any future `pipeline-status <verb>` subcommand
  that has separable compute + render halves.
- The two-line `__main__.py` wiring keeps cross-feature merges
  mechanical: anyone else adding a subcommand follows the same
  two-line-per-subcommand pattern, regardless of how many subcommands
  land in the same release window.

**Harder or more complex:**

- The package grows from eight to ten modules (`diff_archives.py` and
  `format_diff.py` added). Engineers must consult the file ownership
  map to know where things live.
- The slugifier is now lazy-imported from THREE places (`history`,
  `__main__` for the `history NAME` path inherited via v3, and now
  `diff_archives`). The triplication is still inside `archive.py` (one
  definition); the cost is three lazy-import sites in three modules.
- `_TRACKED_ARTEFACTS` private duplicates now exist in TWO modules
  (`history.py` and `diff_archives.py`). A follow-up commit can
  consolidate once the parallel-fan-out cost is paid; for now the
  duplication is intentional and tolerated.
- `argparse`'s `{archive,history,diff}` usage-line text changes. The v3
  ADR already noted that `--help` text is not part of the byte-identical
  contract; this iteration extends that delta by one entry.

**Technical debt introduced:**

- `_TRACKED_ARTEFACTS` private tuple duplicated in `history.py` and
  `diff_archives.py`. Small; one-line refactor post-merge if desired.
- `format_diff.py` ignores `formatting.use_colour()`. If colour for the
  four glyphs becomes a hard requirement in a future iteration, the
  renderer gains one branch and a small ANSI-wrapping helper. Out of
  scope for v4 per Decision 7.
- No machine-readable (`--json`) output. Still deferred from v1.
- No "show only changed artefacts" filter (e.g. `--brief`); not requested.
- No support for `diff` between two non-archived states (e.g. a working
  tree against a git ref). Out of scope; archives are the read-side
  primitive.

**Cross-feature interaction with `restore` (Feature B):**

- The two `__main__.py` wiring tasks (this feature's A3 and Feature B's
  equivalent) BOTH add one import + one call line. Merging both produces
  three subparser registrations in `_build_parser()` (existing
  `add_archive_subparser` + `add_history_subparser`, plus
  `add_diff_subparser`, plus `add_restore_subparser`). The orchestrator
  resolves any line-ordering conflict in alphabetical or feature order;
  both subparsers attach independently and do not interact.
- The two `README.md` edits BOTH add a self-contained section. Merge
  resolution: keep both sections.
- The two features share `archive.slugify`, `archive.TRACKED_ARTEFACTS`,
  and `inspectors.MAX_READ_BYTES`. None of these are modified by either
  feature; both treat them as frozen master code.

## Out of Scope

- Line-by-line text diff (`difflib.unified_diff`, `difflib.HtmlDiff`,
  character-level diffs). Excluded.
- `--json` / machine-readable output. Still deferred from v1.
- Three-way diff or merge.
- Diff for files outside `TRACKED_ARTEFACTS` (e.g. recursive directory
  scan, extra-files report). Excluded.
- `--ignore-whitespace`, `--ignore-case`, `--ignore-blank-lines`, or any
  other content-normalisation flag. Excluded.
- Caching, manifest, lockfile, index, PID file, or any auxiliary write.
  Excluded.
- `pipeline-status restore` (write-back from archive to live state).
  Owned by a sibling pipeline.
- `--watch` / `--interval` integration for `diff`. Excluded (Decision 6).
- ANSI colour for glyphs as a hard requirement. MAY-level only;
  implemented as plain-ASCII unconditionally in v4 (Decision 7).
- Removal / rename / modification of source files under
  `.claude/state/`. Excluded; `diff` is strictly read-only.
- Changes to v1 inspector contracts, v1 stage rules, v1/v2 filled-
  detection heuristics, v2 watch behaviour, v3 `archive` / `history`
  behaviour. All frozen.
- Adding more than ONE submodule beyond the two production modules
  introduced here (`diff_archives.py`, `format_diff.py`). The
  requirements text loosely says "at most ONE new submodule"; the ADR
  has explicitly relaxed this to two for parallel-fan-out reasons
  (Decision 2). No further submodules are permitted in v4.
- Python versions below 3.10. Inherited exclusion.
- Cross-task deduplication of `_TRACKED_ARTEFACTS`. Deliberate cost of
  parallel fan-out; consolidate post-merge if desired.
- Self-comparison short-circuit (Decision 12). Proceed normally.
- Cross-archive integrity checks (e.g. "is `bar` a subset of `foo`"?).
  Excluded; the four-glyph contract is the only output shape v4
  supports.
