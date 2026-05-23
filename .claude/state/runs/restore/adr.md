# ADR: pipeline-status `restore` subcommand

**Status**: Proposed
**Date**: 2026-05-23

## Context

`pipeline-status` v3 added two complementary subcommands:

- **`archive`** (write side, owned by `pipeline_status/archive.py`) — snapshots the five canonical artefact files from `.claude/state/` into `.claude/state/archive/<slug>/`. Exposes the public symbols `TRACKED_ARTEFACTS`, `slugify`, `derive_default_name`, `run_archive`, `add_archive_subparser`.
- **`history`** (read side, owned by `pipeline_status/history.py` + `pipeline_status/format_history.py`) — lists past archives or renders one in detail.

The natural counterpart is **`restore`** (archive → live): copy the five tracked artefacts from `.claude/state/archive/<slug>/` back into `.claude/state/`. v3 deliberately left this out (Out of Scope section). v4 adds it as a single new subcommand with safe-by-default per-file collision detection and an opt-in `--force` overwrite.

This ADR is sized to be decomposed by the PM into **three small tasks** dispatched to three parallel Engineer subagents on isolated `git worktree`s. A sibling Architect is concurrently writing the v4 `diff` ADR (Feature A); both features add a subcommand to `__main__.py` and a subsection to `README.md`. The shape of this ADR explicitly minimises the merge-conflict surface against Feature A's wiring task (one-line conflict each).

### Master code that this ADR builds on

The actual master code (read directly before drafting this ADR) shows:

- `pipeline_status/__main__.py::_build_parser()` already creates `subparsers = parser.add_subparsers(dest="cmd", required=False)` and registers `archive` and `history` via `add_archive_subparser(subparsers)` and `add_history_subparser(subparsers)`. Registering `restore` is one additional call on the same `subparsers` action.
- `pipeline_status/__main__.py::main()` already dispatches via `if getattr(args, "cmd", None) is not None: sys.exit(args.func(args))`. The dispatch line does **not** change.
- `pipeline_status/archive.py` exposes `TRACKED_ARTEFACTS: Final[tuple[str, ...]]` and `slugify(text: str) -> str` as public symbols.  `restore` lazy-imports both inside `run_restore` (not at module top level), preserving the v3 import-cost invariant on every non-`restore` path.

## Decision Drivers

- **Parallel-fan-out within Feature B (driver #0)**: this ADR decomposes into exactly **three** tasks (B1/B2/B3), each owning one production file plus one test file. No two Feature-B tasks edit the same file. The contract for the only cross-task linkage (B1's `add_restore_subparser` consumed by B2's wiring) is pinned by exact signature in the API Contracts section, so B2's worktree can `import` against the contract before B1's PR has merged.
- **Cross-feature merge-conflict minimisation**: B2 (the `__main__.py` wiring task) adds **exactly two lines** — one import line and one `add_restore_subparser(subparsers)` call line. Feature A's wiring task does the symmetric edit. The resulting merge conflict is mechanical and three-way-resolvable in seconds. B3 (README) is similarly scoped to add one self-contained subsection so it does not collide with Feature A's README delta.
- **Strong v3-contract reuse**: `archive.TRACKED_ARTEFACTS` is the canonical list of five basenames; `archive.slugify` is the canonical name → slug rule. `restore` consumes both **lazy-imported inside `run_restore`**, never at module top level. No duplication, no monkey-patching.
- **Boring, stdlib-only**: `argparse`, `pathlib`, `shutil`, `sys`. No `os.path`, no `os.replace`, no third-party packages, no new packaging.
- **Byte-identical regression**: all v1, v2, v3 stdout paths (`pipeline-status` no-args, `--watch`, `archive`, `history`, `history NAME`) MUST be byte-identical after v4. The only `--help` delta is `restore` appearing alongside `archive` and `history` in the `{archive,history,restore}` subcommand usage line.
- **All-or-nothing collision detection**: enumerate ALL conflicts first; then either fail with the full list (and copy nothing) or proceed (when `--force`). No partial restore. No silent overwrite without `--force`.
- **Error-message style consistency with v3**: the "archive not found" wording mirrors v3's `history NAME` style verbatim: `pipeline-status: error: archive '<name>' not found at <path>`.
- **Determinism**: same archive + same live state + same args ⇒ same exit code, same stdout bytes, same stderr bytes. No ANSI colour (`restore` is operational output, not a status report).
- **Performance**: end-to-end <200 ms p99 for any archive whose files fit within `MAX_READ_BYTES` (10 MiB cap inherited from v1 inspectors).

## Considered Options

### Decision 1: File-copy primitive (`shutil.copy2` vs `shutil.copyfile`)

`restore` copies `archive_dir / b` → `state_dir / b` for each tracked basename `b` present in the archive.

- **Option A: `shutil.copyfile(src, dst)`** — copies content only; destination mtime is "now".
- **Option B: `shutil.copy2(src, dst)`** — copies content + mtime + permissions. Source mtime carries to the destination.
- **Chosen: Option B (`shutil.copy2`)**.

Rationale: this matches v3 `archive.run_archive`'s primitive (which also uses `shutil.copy2`). The symmetry means `archive` followed by `restore` round-trips with identical mtimes, which is the principle of least surprise and helps any future per-file diff logic. The `--force` overwrite semantic falls out for free: `shutil.copy2` replaces the destination's content + mtime + permissions atomically (modulo a small window during the write, which is acceptable for the trusted-local-state threat model).

### Decision 2: `--force` semantics

Three plausible semantics for `--force`:

- **Option A: `--force` overwrites only the conflicting files**; non-conflicting present files are always copied (which is the default behaviour anyway).
- **Option B: `--force` is required only when there is at least one conflict**; without conflicts, the flag is a no-op.
- **Option C: `--force` is a permission grant**; with the flag, all `present` files in the archive overwrite their live counterparts (if any) and create the rest. Without the flag, the all-or-nothing collision check applies.
- **Chosen: Option C**.

Rationale: A and B are equivalent in behaviour but A's wording implies the flag is per-file. C makes the flag a single coarse permission grant ("yes, I accept overwrites") which is simpler to reason about, simpler to document, and aligns with the user story for the orchestrator that deliberately wants to roll the live state back to a snapshot. The success line is the same in all three options — `Restored N file(s) from .claude/state/archive/<slug>/` — so this decision is purely about the mental model exposed in `--help` and the README.

### Decision 3: Collision-detection pre-flight algorithm (all-or-nothing)

The collision check is **all-or-nothing**: if any single file would overwrite an existing live file and `--force` is not set, the entire operation aborts with the full conflict list and **no file is copied**.

The algorithm is a two-phase scan:

1. **Phase 1 — enumerate**: iterate `TRACKED_ARTEFACTS` in declaration order. For each basename `b`:
   - If `(archive_dir / b).is_file()` is `True`, append `b` to `present`.
   - If `b in present` and `(state_dir / b).exists()` is `True`, append `b` to `conflicts`.
2. **Phase 2 — gate**:
   - If `not args.force` and `conflicts` is non-empty: print the conflict error (FR-11 format) to stderr, return exit code 1. **Do not copy anything**, including the non-conflicting files in `present`.
   - If `args.force` is set: additionally walk `present` and check that no `(state_dir / b)` is a directory (see Decision 5 below). If any is, print the directory-conflict error and return 1 before any copy.
   - Otherwise: proceed to the copy loop.
3. **Phase 3 — copy**: iterate `present` in `TRACKED_ARTEFACTS` declaration order. `shutil.copy2(archive_dir / b, state_dir / b)`. Increment `n` each iteration. After the loop, print the success line and return 0.

Phases 1 and 2 perform no writes. Phase 3 is the only write phase. The contract "no partial restore" is satisfied by construction: any error path returns before Phase 3 starts.

### Decision 4: Exit-code matrix

| Invocation | Filesystem condition | Exit code | Output channel |
|---|---|---|---|
| `pipeline-status restore NAME [--force]` | `.claude/state/` missing or not a directory | **2** | stderr |
| `pipeline-status restore NAME [--force]` | `slugify(NAME) == ""` | **1** | stderr |
| `pipeline-status restore NAME [--force]` | `archive_dir` missing / not a directory | **1** | stderr |
| `pipeline-status restore NAME` | any conflict, no `--force` | **1** | stderr |
| `pipeline-status restore NAME --force` | any live target in `present` is a directory | **1** | stderr |
| `pipeline-status restore NAME [--force]` | success (`N` ≥ 0) | **0** | stdout |
| `pipeline-status restore --watch ...` | (any) | argparse **2** | stderr |
| `pipeline-status restore --interval ...` | (any) | argparse **2** | stderr |
| `pipeline-status restore` (missing positional) | (any) | argparse **2** | stderr |

Exit-code rationale: `2` is reserved for "environment-not-set-up" errors (matches v1's missing-state-dir convention and argparse's own usage-error convention). `1` is the operational-error code (missing archive, conflict, empty slug, directory-where-file-expected). `0` is success, including the `N = 0` case.

### Decision 5: Live-target-is-a-directory guard (Open Question 5 from requirements)

The requirements note that with `--force`, if a live target file is actually a directory (e.g. a user manually `mkdir`'d `.claude/state/tasks.json/`), `shutil.copy2` would raise. To keep the all-or-nothing contract, we adopt the requirements' Proposed Default:

- After the conflict-check passes (i.e. we are about to enter Phase 3), perform one more pre-flight scan: for every `b` in `present`, if `(state_dir / b).is_dir() and not (state_dir / b).is_symlink()`, print `pipeline-status: error: cannot overwrite directory: <state_dir>/<basename>` to stderr and return exit code 1. **No copy occurs**.
- Without `--force`: the existence check at Phase 1 already caught this case (`.exists()` returns `True` for directories), so the user got the standard collision error and the additional guard is not exercised. We list the directory in the conflict message just like any other conflict.

This guard is small (5 lines) and lives only in `restore.py`. Test case (i) in FR-21 covers it.

### Decision 6: Slug-based path resolution + `archive_dir` definition

`archive_dir` is computed exactly as `state_dir / "archive" / slugify(args.name)`. The slugifier's output is `[a-z0-9-]` only, so `/`, `\`, and `..` cannot survive — no path-traversal write or read is possible by construction. No additional `Path.resolve()` or parentage assertion is needed; the constructive proof is the slugifier's type.

We adopt a single `archive_dir.is_dir()` gate per FR-9. This single call handles:
- `state_dir / "archive"` itself missing (parent doesn't exist → child doesn't exist either, `is_dir()` returns `False`).
- `archive_dir` is a regular file (`is_dir()` returns `False`).
- `archive_dir` is a broken symlink (`is_dir()` follows symlinks; broken target ⇒ `False`).
- `archive_dir` is a valid symlinked directory (`is_dir()` follows symlinks; resolved target is a directory ⇒ `True` — handled as a normal happy path, matching v3 `history`'s Decision 7 stance).

### Decision 7: Error-message wording (verbatim)

To preserve consistency with v3, the error messages adopt the v3 style verbatim. **No string differs from what is listed below.** Tests in FR-21 assert these exact strings.

| Condition | Stderr message (one line, no trailing whitespace, no ANSI) |
|---|---|
| `state_dir` missing | `pipeline-status: error: .claude/state/ not found or not a directory` |
| Empty slug | `pipeline-status: error: archive name is empty after normalisation` |
| Missing archive dir | `pipeline-status: error: archive '<NAME>' not found at <archive_dir>` |
| Collision (no `--force`) | `pipeline-status: error: refusing to overwrite existing file(s): <basename1>, <basename2>, ...` |
| Live target is a directory (`--force`) | `pipeline-status: error: cannot overwrite directory: <state_dir>/<basename>` |

Notes:
- `<NAME>` is the **raw user-supplied** `args.name`, single-quoted (via `repr` semantics: `f"{args.name!r}"`). Python's `!r` on a string emits single quotes when the string contains no single-quote characters; this matches the v3 `history NAME not found` wording.
- `<archive_dir>` is the resolved-path **string** (no quoting), produced by `str(archive_dir)`. This shows the slug-normalised location, which is what the user needs to fix a typo.
- The collision basename list is **comma-space (`", "`) separated** in `TRACKED_ARTEFACTS` declaration order, **not** in `state_dir.iterdir()` order. This is deterministic and matches FR-11.

### Decision 8: Subparser registration site

The `restore` subparser is registered **after** `archive` and `history` in `_build_parser()`. The argparse subparser order only affects the rendering of the `{archive,history,restore}` usage line; behaviour is identical. Listing `restore` last matches the chronological feature addition (v3 added `archive` and `history`; v4 adds `restore`) and matches the requirements' Open Question 6 Proposed Default.

### Decision 9: Symlink semantics

`restore` follows symlinks at both ends:

- **Reading the source**: `(archive_dir / b).is_file()` follows symlinks; `shutil.copy2(src, dst)` defaults to `follow_symlinks=True`. If the archive contains a symlink that points to a regular file, we write the target's bytes into the destination as a regular file. The symlink itself is not preserved. (Matches v3 `archive` write-side stance and Open Question 10.)
- **Writing the destination**: `shutil.copy2` writes through any existing symlink at the destination. This means if `.claude/state/requirements.md` is a symlink to `/etc/passwd`, `restore --force` will redirect the write. This is consistent with v3's stance: `.claude/state/` is trusted local state; users who do not trust their checkout MUST NOT run `restore --force` from it. Documented in the README delta (Task B3).

### Decision 10: Lazy-import discipline

Inside `run_restore`, the imports of `TRACKED_ARTEFACTS` and `slugify` from `pipeline_status.archive` MUST happen lazily — i.e. as `from pipeline_status.archive import TRACKED_ARTEFACTS, slugify` placed **inside the `run_restore` function body**, not at module top level. This:

- Preserves the v3 invariant that `pipeline_status archive` and `pipeline_status history` (let alone the no-args one-shot and `--watch`) pay no import cost for the `restore` module's transitive dependencies. The registration helper `add_restore_subparser` itself only needs `argparse` (already imported by `__main__.py`).
- Lets the test file `tests/test_restore.py` import `pipeline_status.restore` first and observe the production lazy-import behaviour (i.e. without importing `archive` first).
- Lets `tests/test_restore.py` also import `pipeline_status.archive` eagerly to construct fixtures (build an archive on disk with `shutil.copy2`) without any conflict — production code remains lazy.

### Decision 11: Open Questions adopted as Proposed Defaults

Requirements list 10 Open Questions. **All 10 Proposed Defaults are adopted.** Summary:

| Q# | Topic | Adopted decision |
|---|---|---|
| 1 | Distinguish overwrite vs. create in success line | **No** — single `Restored N file(s) ...` format |
| 2 | Warn on `N = 0` (empty archive) | **No** — silent success |
| 3 | `-f` short alias for `--force` | **No** — long form only |
| 4 | Archive contains a tracked basename that is a directory | **Skip silently** — `.is_file()` filter excludes |
| 5 | Live target is a directory | **Treat as conflict + special `--force` guard** (Decision 5) |
| 6 | Subparser registration position | **After `history`** (Decision 8) |
| 7 | Test imports of `archive` | **Direct imports allowed** in tests; production stays lazy |
| 8 | README update | **Yes** in this iteration (Task B3) |
| 9 | Success-line note when `--force` had nothing to overwrite | **No** — unified message |
| 10 | Symlinked archive file | **Follow symlinks** — `shutil.copy2` default (Decision 9) |

## Architecture

### Component Diagram (text/ASCII — file-ownership map)

```
repo root/
├── pipeline_status/
│   ├── __init__.py              # UNCHANGED
│   ├── __main__.py              # ── Task B2 ── (+2 lines: 1 import, 1 register call)
│   ├── inspectors.py            # UNCHANGED (frozen v1 contract)
│   ├── stage.py                 # UNCHANGED (frozen v1 contract)
│   ├── formatting.py            # UNCHANGED (frozen v1+v2 contract)
│   ├── watch.py                 # UNCHANGED (frozen v2 contract)
│   ├── archive.py               # UNCHANGED (frozen v3 contract — read-only consumer)
│   ├── history.py               # UNCHANGED (frozen v3 contract)
│   ├── format_history.py        # UNCHANGED (frozen v3 contract)
│   └── restore.py               # ── Task B1 ── NEW: collision detect + copy + --force
├── tests/
│   ├── __init__.py              # UNCHANGED
│   ├── test_archive.py          # UNCHANGED
│   ├── test_history.py          # UNCHANGED
│   ├── test_format_history.py   # UNCHANGED
│   ├── test_main_subcommands.py # UNCHANGED (Feature A may touch this; we do not)
│   └── test_restore.py          # ── Task B1 ── NEW
└── README.md                    # ── Task B3 ── EDIT: add one `### restore` subsection

.claude/state/                   # WRITE target for `restore`; READ for `archive`/`history`
├── feature-request.md           # restore writes if archive contains it
├── requirements.md              # ditto
├── adr.md                       # ditto
├── tasks.json                   # ditto
├── worktrees.json               # ditto
└── archive/                     # READ-ONLY for `restore`
    └── <slug>/                  # the source directory
        ├── feature-request.md   # 0..1 of each
        ├── requirements.md
        ├── adr.md
        ├── tasks.json
        └── worktrees.json
```

### File-ownership table (the parallel-fan-out contract)

| Task | Owns (production) | Owns (test) | Imports from master | Imports from sibling Feature-B tasks |
|---|---|---|---|---|
| **B1** | `pipeline_status/restore.py` | `tests/test_restore.py` | stdlib only (production); `pipeline_status.archive` (lazy in production, eager in tests for fixtures) | none |
| **B2** | `pipeline_status/__main__.py` (+2 lines) | none (the master `tests/test_main_subcommands.py` already covers subparser wiring; no Feature-B-specific test is required for B2 — Task B1's tests cover the action callable) | the existing `__main__.py` imports | imports `add_restore_subparser` from `pipeline_status.restore` (B1) |
| **B3** | `README.md` (+1 subsection) | none | none | none |

**Cross-feature merge surface**: B2 conflicts with Feature A's symmetric `__main__.py` edit (one new import line + one new `add_X_subparser(subparsers)` call line). B3 conflicts with Feature A's README delta (each adds a self-contained subsection). All conflicts are mechanical to resolve.

### Data Model

**No new dataclasses, no new persistent files, no new state.**

`restore`'s entire state is two local lists inside `run_restore`:

```python
present: list[str]    # basenames in TRACKED_ARTEFACTS order that exist in archive_dir as files
conflicts: list[str]  # basenames in present whose state_dir counterpart already exists
```

Both are populated by Phase 1 of the algorithm (Decision 3) and discarded when `run_restore` returns.

### API Contracts (per task — verbatim signatures)

These signatures are **the** interface contract for the parallel Engineer subagents. B2's worktree can write `from pipeline_status.restore import add_restore_subparser` against this contract before B1's PR has merged.

#### Task B1 — `pipeline_status/restore.py`

```python
"""Restore archived artefacts from .claude/state/archive/<NAME>/ into .claude/state/.

Public symbols:
    run_restore(args)             -> int    (argparse action callable; returns exit code)
    add_restore_subparser(subparsers) -> argparse.ArgumentParser (registers `restore`)

stdlib only at module scope.  TRACKED_ARTEFACTS and slugify are lazy-imported
from pipeline_status.archive INSIDE run_restore (not at top level), to keep the
non-restore CLI paths free of restore's transitive import cost.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def run_restore(args: argparse.Namespace) -> int:
    """Action callable for the ``restore`` subcommand.

    Algorithm (Decisions 3 + 5):
      1. state_dir = Path.cwd()/".claude"/"state". If not a directory, stderr
         error + return 2 (matches v1/v3 missing-state exit code).
      2. Lazy-import TRACKED_ARTEFACTS and slugify from pipeline_status.archive.
      3. slug = slugify(args.name). If "" → stderr error + return 1.
      4. archive_dir = state_dir/"archive"/slug. If not archive_dir.is_dir():
         stderr error `pipeline-status: error: archive {args.name!r} not found
         at {archive_dir}` + return 1.
      5. Phase 1: enumerate ``present`` (basenames in TRACKED_ARTEFACTS order
         whose file exists in archive_dir) and ``conflicts`` (subset of
         present whose state_dir counterpart .exists()).
      6. Phase 2 gate:
            * If conflicts and not args.force: print the conflict error to
              stderr (basenames comma-separated in TRACKED_ARTEFACTS order)
              + return 1. NO FILE IS COPIED.
            * If args.force: scan present for any state_dir/b that is a
              directory and not a symlink. If found, print the
              "cannot overwrite directory" error + return 1. NO FILE IS COPIED.
      7. Phase 3 (copy): for each b in present (in TRACKED_ARTEFACTS order),
         shutil.copy2(archive_dir/b, state_dir/b). n += 1 per copy.
      8. print(f"Restored {n} file(s) from .claude/state/archive/{slug}/")
      9. return 0.

    Never writes outside state_dir/<basename>.  Never reads outside
    state_dir (for the existence check) and archive_dir (for the copy
    source).  No lockfile, no PID file, no manifest, no backup file.
    """


def add_restore_subparser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> argparse.ArgumentParser:
    """Register the ``restore`` subcommand on ``subparsers``.

    Declares one required positional ``NAME`` (string) and one optional
    boolean flag ``--force`` (action="store_true", default False).  Does NOT
    declare ``--watch``, ``--interval``, ``--name``, ``--json``, ``--dry-run``,
    or any short-form alias.  Calls ``sp.set_defaults(func=run_restore)``.
    Returns the created subparser.
    """
```

**Test contract for `tests/test_restore.py`** (also owned by Task B1):

The test file MUST use `unittest` + `tempfile.TemporaryDirectory` + `os.chdir`/`addCleanup` patterns. No subprocess, no network. Test cases mirror FR-21:

| # | Scenario | Asserts |
|---|---|---|
| a | All 5 artefacts in archive, no live state files | exit 0, `Restored 5 file(s) ...` on stdout, every live file is byte-identical to the archive copy |
| b | Partial archive (e.g. 2 of 5 artefacts) | exit 0, `Restored 2 file(s) ...`, only those 2 live files exist |
| c | Empty archive directory (`N = 0` happy path) | exit 0, `Restored 0 file(s) ...`, no files created |
| d | One conflict, no `--force` | exit 1, stderr lists the one conflict, live file unchanged byte-for-byte |
| e | Multiple conflicts, no `--force` | exit 1, stderr lists all conflicts comma-separated **in `TRACKED_ARTEFACTS` order** |
| f | Conflicts present + `--force` set | exit 0, every conflicting live file overwritten with archive content |
| g | `--force` does NOT delete live files absent from archive | live `worktrees.json` (not in archive) remains untouched and byte-identical |
| h | Archive directory missing | exit 1, stderr matches `pipeline-status: error: archive '<NAME>' not found at <archive_dir>` |
| i | State directory missing | exit 2, stderr matches v1 missing-state message |
| j | Empty-slug input (e.g. `"!!!"`) | exit 1, stderr matches empty-slug message |
| k | Subparser rejects `--watch` and `--interval` | argparse `SystemExit` with code 2 (uses `_build_parser` from `__main__` OR builds a local parser and registers via `add_restore_subparser`) |
| l | (Decision 5 guard) live target is a directory + `--force` | exit 1, stderr matches directory-overwrite message, no live file modified |
| m | Lazy-import discipline | importing `pipeline_status.restore` does NOT import `pipeline_status.archive` (assert `"pipeline_status.archive" not in sys.modules` immediately after `import pipeline_status.restore`; pop it from `sys.modules` first if necessary) |

#### Task B2 — `pipeline_status/__main__.py` (exactly two added lines)

The diff against master is **exactly** two new lines plus a one-line conflict surface against Feature A. No other change is permitted in B2.

Master (current relevant lines, 22-28 of `__main__.py`):

```python
from pipeline_status.archive import add_archive_subparser
from pipeline_status.formatting import (
    format_artefact_row,
    format_stage_line,
    use_colour,
)
from pipeline_status.history import add_history_subparser
```

Master `_build_parser` (lines 102-105):

```python
    subparsers = parser.add_subparsers(dest="cmd", required=False)
    add_archive_subparser(subparsers)
    add_history_subparser(subparsers)
    return parser
```

**The B2 patch**:

1. Add one import line alongside the existing `add_*_subparser` imports, alphabetically sorted (so it lands between `history` and `inspectors`):

   ```python
   from pipeline_status.restore import add_restore_subparser
   ```

2. Add one call line inside `_build_parser`, immediately after `add_history_subparser(subparsers)` (Decision 8):

   ```python
       add_restore_subparser(subparsers)
   ```

**Nothing else changes in `__main__.py`**. The dispatch in `main()` already handles arbitrary subcommands via `args.func(args)`. The argparse parser shape (`subparsers = parser.add_subparsers(dest="cmd", required=False)`) is already in place.

**Merge-conflict-surface contract**: Feature A will make the symmetric edit (one import line + one call line). The git three-way merge will see Feature A and Feature B touching adjacent or identical lines; the resolution is "keep both". No semantic conflict.

#### Task B3 — `README.md` (one self-contained subsection)

Add a `### restore` subsection under the existing `## CLI` (or equivalent) heading. The subsection MUST be self-contained — no edits to other parts of the README — so that Feature A's symmetric `### diff` subsection can land independently without textual conflict.

Subsection content (one paragraph + two examples + exit-code matrix):

```markdown
### restore

`pipeline-status restore NAME [--force]` copies the five canonical artefact
files from `.claude/state/archive/<slug>/` back into `.claude/state/`, where
`<slug>` is `slugify(NAME)`. By default, `restore` refuses to overwrite any
existing live artefact and exits 1 listing every conflicting basename. Pass
`--force` to overwrite. Files absent from the archive are never created,
modified, or deleted by `restore`.

Examples:

    # Safe restore — fails if any live target already exists:
    pipeline-status restore pipeline-status-cli

    # Roll the live state back to a snapshot, overwriting:
    pipeline-status restore watch-mode --force

Exit codes:

    0  restore succeeded (N may be 0..5; printed as
       "Restored N file(s) from .claude/state/archive/<slug>/")
    1  archive not found, empty-slug NAME, conflict without --force, or
       a live target is a directory (with --force)
    2  .claude/state/ is missing or is not a directory

`restore` is the read-write counterpart of `archive`; see also `history`
for read-only inspection of past archives.
```

The subsection MUST be placed alphabetically after `archive` and `history` in the CLI section (i.e. last among the three v3+v4 subcommands), matching Decision 8.

### CLI Surface

Top-level usage tree after Feature B lands (Feature A may add another `{...}` entry):

```
pipeline-status [-h] [--watch] [--interval SECONDS] {archive,history,restore} ...

  (no subcommand)                # v1/v2 path; one-shot or --watch
  archive [-h] [--name NAME]     # v3: snapshot live state
  history [-h] [NAME]            # v3: list / detail
  restore NAME [--force]         # v4 Feature B: copy archive → live
```

`pipeline-status restore --help` shows:

- the positional `NAME` argument with help text like `Archive name (slugified) to restore from .claude/state/archive/<slug>/.`,
- the `--force` flag with help text like `Overwrite existing live files (all-or-nothing collision check otherwise).`,
- a description paragraph summarising the all-or-nothing collision semantics.

The argparse defaults satisfy FR-3 automatically: combining `restore` with `--watch` or `--interval` is rejected by argparse with exit 2 because the `restore` subparser does not inherit the parent's optionals at parse-consumption time.

### Sequence Diagrams

#### Happy path: full restore, no live files (`pipeline-status restore foo-bar`)

```
User: $ pipeline-status restore foo-bar
__main__.main():
  args = _build_parser().parse_args()      # args.cmd="restore", args.name="foo-bar",
                                           # args.force=False, args.func=restore.run_restore
  sys.exit(args.func(args))                # restore.run_restore(args)

restore.run_restore(args):
  state_dir = Path.cwd()/".claude"/"state"
  state_dir.is_dir() → True
  from pipeline_status.archive import TRACKED_ARTEFACTS, slugify   # lazy
  slug = slugify("foo-bar") = "foo-bar"
  archive_dir = state_dir/"archive"/"foo-bar"
  archive_dir.is_dir() → True
  # Phase 1
  for b in TRACKED_ARTEFACTS:
    if (archive_dir/b).is_file(): present.append(b)
    # state_dir/b does not exist → no conflicts
  # Phase 2 — conflicts is empty, args.force=False, but no conflicts means proceed
  # Phase 3
  for b in present:
    shutil.copy2(archive_dir/b, state_dir/b); n += 1
  print("Restored 5 file(s) from .claude/state/archive/foo-bar/")
  return 0
```

#### Collision path, no `--force` (`pipeline-status restore foo-bar`)

```
restore.run_restore(args):
  ...
  # Phase 1: e.g. present = ["feature-request.md", "requirements.md", "adr.md"]
  # conflicts = ["requirements.md", "adr.md"]  (in TRACKED_ARTEFACTS order)
  # Phase 2: conflicts non-empty, not args.force
  print("pipeline-status: error: refusing to overwrite existing file(s): "
        "requirements.md, adr.md", file=sys.stderr)
  return 1
  # No file is copied.  feature-request.md is NOT created either,
  # even though it has no conflict.
```

#### `--force` happy path (`pipeline-status restore foo-bar --force`)

```
restore.run_restore(args):
  ...
  # Phase 1: same as collision case
  # Phase 2: args.force=True → skip the conflict gate.
  #         Pre-flight directory check: for b in present, assert
  #         not (state_dir/b).is_dir() or (state_dir/b).is_symlink()
  #         All checks pass.
  # Phase 3:
  for b in ["feature-request.md", "requirements.md", "adr.md"]:
    shutil.copy2(archive_dir/b, state_dir/b)   # overwrites the live file
    n += 1
  print("Restored 3 file(s) from .claude/state/archive/foo-bar/")
  return 0
  # state_dir/tasks.json and state_dir/worktrees.json are UNTOUCHED
  # (they were not in present because the archive lacked them).
```

#### Missing-archive path (`pipeline-status restore typo`)

```
restore.run_restore(args):
  state_dir = Path.cwd()/".claude"/"state"
  state_dir.is_dir() → True
  slug = slugify("typo") = "typo"
  archive_dir = state_dir/"archive"/"typo"
  archive_dir.is_dir() → False
  print("pipeline-status: error: archive 'typo' not found at "
        f"{archive_dir}", file=sys.stderr)
  return 1
```

#### Empty-slug path (`pipeline-status restore "!!!"`)

```
restore.run_restore(args):
  state_dir = Path.cwd()/".claude"/"state"
  state_dir.is_dir() → True
  slug = slugify("!!!") = ""
  print("pipeline-status: error: archive name is empty after normalisation",
        file=sys.stderr)
  return 1
```

#### State-dir missing path (`pipeline-status restore foo`)

```
restore.run_restore(args):
  state_dir = Path.cwd()/".claude"/"state"
  state_dir.is_dir() → False
  print("pipeline-status: error: .claude/state/ not found or not a directory",
        file=sys.stderr)
  return 2
```

## Implementation Notes

### Module-by-module guidance

**`pipeline_status/restore.py` (Task B1)**

- Lazy-import `TRACKED_ARTEFACTS` and `slugify` from `pipeline_status.archive` **inside `run_restore`**, immediately after the `state_dir` check (i.e. only when we know we have real work to do). This matches the v3 `__main__.py::main()` lazy-import pattern for `watch` and reinforces NFR-P2.
- The conflict message uses the `TRACKED_ARTEFACTS` order, not the disk order. Concretely: build `conflicts` by iterating `present` (which was built by iterating `TRACKED_ARTEFACTS` and filtering). Then `", ".join(conflicts)` is already in the right order.
- For the directory-conflict guard (Decision 5), the symlink exception (`not (state_dir / b).is_symlink()`) is essential: a symlink to a directory satisfies `.is_dir()` but `shutil.copy2` will replace the symlink with a regular file (not write into the directory). Symlinks are therefore safe to overwrite under `--force`; only genuine directories trigger the guard.
- Use `shutil.copy2` (Decision 1). Do not use `shutil.copyfile`, `os.replace`, or `pathlib.Path.write_bytes`.
- Do NOT call `Path.resolve()` on `archive_dir` or `state_dir`. The constructive proof from `slugify` (Decision 6) guarantees no traversal, and `resolve()` would change behaviour for symlinks (Decision 9) and add filesystem cost.
- Error emission helper: B1 may copy the `_emit_error` helper pattern from `archive.py` (single-stderr-write wrapper) for testability, or inline `print(..., file=sys.stderr)` calls. Either is fine; the existing v3 codebase uses both styles.
- No ANSI colour. No use of `pipeline_status.formatting` helpers (FR-25). The output is two possible stdout lines (`Restored N file(s) ...`) and five possible stderr lines (Decision 7). No table formatting, no row formatting.

**`pipeline_status/__main__.py` (Task B2)**

- Add the import alphabetically: between `pipeline_status.history` and `pipeline_status.inspectors` in the existing import block. This is a one-line addition; the alphabetisation makes the cross-feature merge with Feature A trivial (Feature A's import is `pipeline_status.diff`, which lands earlier in alphabetical order and thus on a different physical line).
- Add the registration call as a new line directly after `add_history_subparser(subparsers)` and before `return parser`. Three other lines exist in that block; the new line is the fourth.
- DO NOT modify `_interval_type`, `_locate_state_dir`, `_run_one_shot`, `_EPILOG`, `_STATE_DIR`, the docstring, or any other function.
- DO NOT add any new tests in B2. The master `tests/test_main_subcommands.py` already exercises the subparser-dispatch wiring for arbitrary subcommands via `args.func(args)`. Task B1's `tests/test_restore.py` covers the action callable end-to-end (test case `k` exercises the subparser registration directly).

**`README.md` (Task B3)**

- Insert the `### restore` subsection at the position described above. Do not edit any other paragraph.
- Use the exact content sketched in the Task B3 contract above. The example invocations, exit codes, and behaviour wording must match the production code's actual strings (so the docs are truth, not aspiration).
- One brief mention may be added at the top of the CLI section listing `restore` alongside `archive` and `history` IF such a list already exists; otherwise no top-level edits. Engineer judgement, but keep it minimal.

### Known edge cases

1. **Slug-empty input** (`restore "!!!"` or `restore ""` or `restore "   "`): caught at the post-slugify check; exit 1 with the empty-slug message. Tested in (j).
2. **Archive directory exists but is a regular file**: `is_dir()` returns `False`; "not found" error is reported. Same path as a typo.
3. **Archive directory is a broken symlink**: `is_dir()` follows symlinks; broken target ⇒ `False`. Same as missing.
4. **Archive directory is a symlinked-good directory**: happy path, treated normally (Decision 9).
5. **Archive contains a tracked basename as a sub-directory** (e.g. someone `mkdir`'d `.claude/state/archive/foo/tasks.json/`): `.is_file()` returns `False`; the basename is not in `present`; no copy, no conflict for that basename. (Tested implicitly in (b); explicit test optional.)
6. **Live target is a sub-directory** (e.g. `.claude/state/tasks.json/` is a directory): without `--force`, `.exists()` returns `True` ⇒ conflict ⇒ exit 1 with conflict message. With `--force`, the Decision 5 guard triggers ⇒ exit 1 with directory-overwrite message. Tested in (l).
7. **Live target is a symlink to a regular file**: `--force` overwrites the symlink with a regular file (per `shutil.copy2` default). Documented; no test required.
8. **`N = 0` (archive exists but empty)**: happy path, exit 0, prints `Restored 0 file(s) ...`. Tested in (c).
9. **Concurrent `restore` calls**: no lockfile. Last writer wins per file. Accepted under the trusted-local-state threat model.
10. **`restore` immediately after `archive` round-trip**: byte-identical content; `shutil.copy2` carries mtime, so `archive` then `restore --force` then `archive` again would produce an archive identical to the original (modulo any other CLI activity). No test required.
11. **`restore` with `--force` against a fully clean live state**: no overwrites happen, but `--force` is a permission grant, not a status (Decision 11 Q9). Success line is the same as without `--force`. Tested implicitly.
12. **Argparse rejects `pipeline-status restore` (missing positional)**: exit 2, usage error. Standard argparse; not specially tested.
13. **`pipeline-status restore foo --watch`**: rejected by argparse; not specially tested (test (k) covers the same code path).
14. **State directory exists but `state_dir / "archive"` does not**: `archive_dir.is_dir()` returns `False` (parent missing ⇒ child missing); "not found" error. Same path as typo. No special handling.

## Consequences

**Easier after this change:**

- The orchestrator's manual `cp .claude/state/archive/<name>/* .claude/state/` recovery step is replaced by `pipeline-status restore <name> [--force]`.
- A developer recovering a corrupted local state has a discoverable, safe-by-default tool. `--help` lists it; default behaviour refuses to clobber uncommitted work.
- The trio (`archive`, `history`, `restore`) is symmetric: write-side, list/inspect-side, read-back-side. The mental model is complete.
- The subparser layout is now exercised by three subcommands, validating the v3 infrastructure investment.

**Harder or more complex:**

- The package grows from eight modules to nine (one new file). Engineers learn one more entry on the file-ownership map.
- The CLI surface grows by one subcommand and one new flag (`--force`). `--help` output gains one subcommand entry.
- The error-message taxonomy grows by two strings (conflict + directory-conflict).

**Technical debt introduced:**

- None new. The pre-existing `_TRACKED_ARTEFACTS` duplication between `archive.py` and `history.py` is unchanged (v3 noted it as accepted debt). `restore.py` does **not** add a third copy; it lazy-imports `archive.TRACKED_ARTEFACTS`.
- No `--dry-run`, no `--json`, no `-f` short alias, no partial-restore selector. All explicitly deferred.

**Compatibility guarantees:**

- v1/v2/v3 stdout paths (no-args, `--watch`, `archive`, `history`, `history NAME`) are byte-identical post-change.
- `--help` gains exactly one delta: `restore` appears in the `{archive,history,restore}` subcommand usage line.
- All v3 module contracts (`archive.py`, `history.py`, `format_history.py`, `inspectors.py`, `stage.py`, `formatting.py`, `watch.py`) are frozen.

## Out of Scope

- Restore across repositories or from remote sources. Source is always `.claude/state/archive/<slug>/` under CWD.
- Partial / selective restore (e.g. `--only tasks.json`). Restore considers exactly the five tracked basenames; whichever subset is in the archive is what gets copied.
- `--dry-run` mode. Defer to v5 if requested.
- Backup-before-overwrite. Users who want a safety net run `pipeline-status archive` first.
- Deletion of live files absent from the archive. Restore is additive/overwriting, never removing.
- Interactive conflict-resolution prompts. Behaviour is batch only: succeed, or hard-error with the full conflict list.
- `--json` machine-readable output.
- `--watch` / `--interval` on the `restore` subparser. Argparse rejects naturally.
- A short `-f` alias for `--force`. Long form only (Open Question 3).
- Modifying `archive.py`, `history.py`, `format_history.py`, `inspectors.py`, `stage.py`, `formatting.py`, or `watch.py` (other than `archive.py` being a read-only consumed dependency via lazy import). Frozen.
- Modifying the v1 one-shot stdout path or the v2 watch path. Byte-identical regression mandatory.
- Compression, encryption, checksum, or signing of restored files. Pure `shutil.copy2`.
- Cross-task deduplication of `TRACKED_ARTEFACTS`. Inherited from v3; deferred.
- Coordinated landing with Feature A (`diff`). Either feature may land first; the merge conflict is mechanical and resolvable in seconds.
