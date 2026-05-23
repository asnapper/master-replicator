# Requirements: pipeline-status archive & history subcommands

## Problem Statement
The orchestrator now drives multiple feature deliveries through the same repository, and each completed run leaves a snapshot in `.claude/state/archive/<NAME>/`. Today that directory grows by hand: the orchestrator manually copies `.claude/state/*` into a freshly-named subdirectory between runs, and any developer (or returning Claude Code session) who wants a retrospective view of past pipelines has to open archived files individually. This is error-prone (typos in directory names, accidental overwrites, partial copies) and provides no at-a-glance summary of what has shipped. We need first-class CLI subcommands that (a) snapshot the live state into an archive directory and (b) read historical archives back out in the same per-artefact format the existing inspector already produces.

## Goals
- Add a `pipeline-status archive [--name NAME]` subcommand that copies the five live state artefacts into `.claude/state/archive/<NAME>/` in a single atomic-feeling operation, deriving `<NAME>` from `feature-request.md`'s first heading when `--name` is omitted.
- Add a `pipeline-status history` subcommand that prints a single table summarising every archive directory under `.claude/state/archive/` with name, archived-at timestamp (mtime), total task count, and completed task count.
- Add a `pipeline-status history NAME` form that reproduces the existing one-shot per-artefact report, but reading from `.claude/state/archive/<NAME>/` instead of `.claude/state/`.
- Preserve byte-identical stdout for both existing invocations: `pipeline-status` (no args) and `pipeline-status --watch [--interval N]`. The new subcommands sit alongside them under a single argparse top-level parser.
- Reuse the existing inspector functions and stage-derivation logic against an arbitrary state directory without forking the codebase.
- Ship under the existing stdlib-only contract; no new runtime or test dependencies; tests use stdlib `unittest` and `tempfile.TemporaryDirectory` only.
- Scan up to 100 archive directories for `history` in under 200 ms p99 on a modern laptop.

## Non-Goals
- No `pipeline-status restore` (or any other write-back from an archive into `.claude/state/`).
- No `pipeline-status diff A B`, `--diff`, or any cross-archive comparison.
- No `--json` / machine-readable output mode for any subcommand (still deferred from v1).
- No automatic archiving at end-of-run; the orchestrator continues to drive when `archive` is invoked.
- No compression, encryption, checksum, signing, or external sync of archive directories.
- No search, filter, sort, or paging flags on `history` (e.g. no `--since`, no `--grep`, no `--limit`); defer to shell tools.
- No `--force`, `--overwrite`, or rename flag on `archive`; collisions are hard errors.
- No new files written outside the target archive directory (no PID files, lockfiles, caches, indexes, manifest files).
- No watch-mode equivalent for the new subcommands (e.g. no `pipeline-status history --watch`).
- No removal of source files from `.claude/state/` by the `archive` subcommand; that remains an orchestrator responsibility.
- No changes to the v1 inspector contracts, the v1 stage-derivation rules, the v1/v2 filled-detection heuristics, or the v2 watch-loop behaviour.
- No support for nested archive directories (archives are a flat one-level structure under `.claude/state/archive/`).

## User Stories

> As an orchestrator operator finishing a feature run, I want to invoke `pipeline-status archive` so that the current `.claude/state/*` files are snapshotted into a named archive directory in one command instead of five `cp` calls.

Acceptance criteria:
- [ ] `pipeline-status archive --name foo-bar` copies every existing artefact under `.claude/state/` (of the five tracked names) into `.claude/state/archive/foo-bar/`, preserving file contents byte-for-byte.
- [ ] `pipeline-status archive` (no `--name`) derives the archive name from the first markdown heading in `.claude/state/feature-request.md` by slugifying it, then writes to `.claude/state/archive/<slug>/`.
- [ ] If the derived or supplied name is empty, whitespace-only, or slugifies to an empty string, the command exits non-zero with a clear stderr message and writes nothing.
- [ ] If `.claude/state/archive/<NAME>/` already exists, the command exits non-zero with a clear stderr message and writes nothing (no partial copy, no overwrite).
- [ ] Source files under `.claude/state/` are not modified, renamed, or deleted by the command.
- [ ] Stdout on success contains a single confirmation line including the archive name and the count of files copied.

> As a developer or returning Claude Code session, I want to run `pipeline-status history` so that I can see every past pipeline run in one table without browsing the archive directory by hand.

Acceptance criteria:
- [ ] `pipeline-status history` (no positional arg) prints a header followed by one row per immediate subdirectory of `.claude/state/archive/`.
- [ ] Each row contains: archive name, archived-at ISO-8601 local timestamp (the archive directory's mtime), total task count, completed task count.
- [ ] Rows are sorted by archive name ascending (stable, locale-independent byte order).
- [ ] An archive directory missing `tasks.json` or containing malformed `tasks.json` does not crash the command; the affected row shows blank or zero counts and the loop continues.
- [ ] If `.claude/state/archive/` does not exist or contains no subdirectories, the command prints a single "no archives" message to stdout and exits 0.
- [ ] Exit code is 0 on successful enumeration regardless of whether any archives exist.

> As a developer reviewing a specific past run, I want `pipeline-status history NAME` to give me the same per-artefact report the one-shot inspector produces, but rooted at the named archive directory.

Acceptance criteria:
- [ ] `pipeline-status history foo-bar` reads from `.claude/state/archive/foo-bar/` and prints exactly the same body layout (`Pipeline Status` header, per-artefact lines, task counts, stage line) that one-shot mode prints from `.claude/state/`.
- [ ] If `.claude/state/archive/<NAME>/` does not exist, the command exits non-zero with a stderr message naming the missing path.
- [ ] If the archive directory is partial (some artefacts missing or empty), the report renders the missing/empty status per the v1 filled-detection rules; the command does not crash and exits 0.
- [ ] The stage line is derived from the archived artefacts via the existing `derive_stage(...)` function with no modification.

> As an orchestrator operator who has been using v1 and v2, I want the new subcommands to leave my existing invocations untouched so that I can adopt the feature without retraining muscle memory or breaking scripts.

Acceptance criteria:
- [ ] `pipeline-status` with no arguments produces byte-identical stdout to the v1/v2 release given identical filesystem state.
- [ ] `pipeline-status --watch` and `pipeline-status --watch --interval N` behave byte-identically to the v2 release; the new subcommands are not invoked.
- [ ] `pipeline-status --help` documents the new subcommands and their flags without removing or renaming any existing flag or exit-code line.

> As a maintainer, I want the new subcommands tested under `unittest` with no real filesystem coupling beyond `tempfile`, so that the test suite stays fast and hermetic.

Acceptance criteria:
- [ ] New tests live under `tests/` and use stdlib `unittest` + `tempfile.TemporaryDirectory` only.
- [ ] No test spawns a subprocess, opens a network socket, or touches the real `.claude/state/` directory of the repo.
- [ ] All existing tests under `tests/test_inspectors.py`, `tests/test_stage.py`, `tests/test_formatting.py`, and any v2 watch tests continue to pass unchanged.
- [ ] Full suite (`python -m unittest discover -s tests`) completes in under 5 s wall time on a modern laptop.

## Functional Requirements

### Top-level CLI surface

1. The CLI MUST accept an optional positional subcommand as its first non-flag argument. The recognised subcommands are exactly `archive` and `history`. Any other positional value MUST cause argparse to exit with a non-zero status and print a usage error to stderr.
2. When no subcommand is supplied, the CLI MUST behave exactly as v1/v2: run the one-shot inspector against `.claude/state/`, honouring `--watch` and `--interval` as in v2. Stdout for this code path MUST remain byte-identical to the v2 release.
3. The `--watch` and `--interval` flags MUST be rejected (argparse error to stderr, non-zero exit) when combined with either `archive` or `history`. `--watch`/`--interval` are valid only on the implicit/default invocation.
4. `--help` at the top level MUST list `archive` and `history` as available subcommands, alongside the existing `--watch` and `--interval` documentation. Each subcommand MUST support its own `--help` describing its flags and exit codes.
5. The package layout MUST remain confined to `pipeline_status/{__init__,__main__,inspectors,stage,formatting,watch}.py` plus at most TWO new submodules for this iteration (e.g. `pipeline_status/archive.py` and `pipeline_status/history.py`). No other top-level files are introduced inside the package.

### `archive` subcommand

6. `pipeline-status archive [--name NAME]` MUST copy every file present in `.claude/state/` whose basename is one of the five tracked artefacts (`feature-request.md`, `requirements.md`, `adr.md`, `tasks.json`, `worktrees.json`) into `.claude/state/archive/<NAME>/`. Files among the five that do not exist in `.claude/state/` MUST be skipped silently (no placeholder created in the archive).
7. The destination directory MUST be created with `Path.mkdir(parents=True, exist_ok=False)`. If it already exists, the command MUST write an error to stderr (e.g. `pipeline-status: error: archive '<NAME>' already exists at <path>`) and exit non-zero (SHOULD be exit code 1) WITHOUT creating, modifying, or deleting any file.
8. File copies MUST preserve byte content exactly. Implementations MAY use `shutil.copyfile` or `shutil.copy2`; `copy2` is preferred so the destination mtime tracks the source. The choice MUST be documented in the ADR.
9. When `--name` is omitted, the CLI MUST derive the archive name by:
   - reading `.claude/state/feature-request.md`,
   - locating the first non-blank line that starts with one or more `#` characters followed by whitespace,
   - taking the heading text (everything after the leading `#`s and whitespace, stripped),
   - slugifying it via the rules in FR-11,
   - and using the resulting slug.
10. If the feature-request file is missing, unreadable, contains no heading line, or its heading slugifies to an empty string, the CLI MUST fall back to the current local date in `YYYY-MM-DD` form (e.g. `2026-05-23`). The fallback MUST NOT itself fail; the resulting name is a valid slug by construction.
11. The slugifier MUST be a tiny inline function (no `tomllib`, no third-party slug library, no regex import is required but `re` from stdlib is permitted). It MUST:
    - lowercase the input,
    - replace any run of characters outside `[a-z0-9]` with a single `-`,
    - strip leading and trailing `-`,
    - return an empty string if the result is empty (caller is responsible for the fallback per FR-10),
    - be ASCII-only in output (non-ASCII letters are treated as separators, not transliterated).
12. When `--name` is supplied, the CLI MUST apply the same slugifier to the supplied value. If the slugified value is empty, the command MUST exit non-zero with a clear stderr error and write nothing. The slugifier MUST NOT silently mutate the supplied name into something the user did not request beyond the documented normalisation rules; the `--help` text MUST describe the rules.
13. On success, the CLI MUST write a single line to stdout in the form `Archived <N> file(s) to .claude/state/archive/<NAME>/` and exit 0, where `<N>` is the count of files actually copied (0..5).
14. If `<N>` would be 0 (no source artefacts exist), the CLI MUST still create the empty archive directory, print the same confirmation line with `<N> = 0`, and exit 0. This preserves the "the directory now exists for future history reads" invariant.
15. The `archive` subcommand MUST NOT write or modify any file outside `.claude/state/archive/<NAME>/`. In particular, it MUST NOT touch `.claude/state/*` source files (no rename, no delete, no chmod).
16. The `archive` subcommand MUST refuse to run when `.claude/state/` does not exist or is not a directory: stderr error, non-zero exit (SHOULD be exit code 2 to match v1 missing-state semantics), and no archive directory created.

### `history` subcommand (table form)

17. `pipeline-status history` (no positional arg) MUST enumerate the immediate subdirectories of `.claude/state/archive/` and print a table with one row per subdirectory. Files directly under `.claude/state/archive/` MUST be ignored.
18. The table MUST have columns, in order: `NAME`, `ARCHIVED-AT`, `TASKS`, `DONE`. Column widths MUST be sized to the widest value in each column so the output is stable for fixed input. A single header row MUST precede the data rows. Columns MUST be separated by at least two spaces (no tabs, no Unicode box-drawing).
19. The `ARCHIVED-AT` column MUST contain the archive directory's `Path.stat().st_mtime` rendered as an ISO-8601 local timestamp with offset and second precision, produced by the same mechanism used in v1 for artefact mtime formatting.
20. The `TASKS` column MUST contain the total task count read from `<archive>/tasks.json` using the v1 `inspect_tasks_json` semantics (accept either a top-level array or `{tasks: [...]}` shape; normalise to a list and count).
21. The `DONE` column MUST contain the completed task count using the v1 task-completion detection rules (`status` in {"done","completed"} case-insensitive, OR boolean `completed`/`done` truthy).
22. If `<archive>/tasks.json` is missing, both `TASKS` and `DONE` MUST render as `-` (single ASCII hyphen). If it is present but malformed JSON, both columns MUST also render as `-`. In neither case does the command crash; the loop continues to the next archive.
23. Rows MUST be sorted by `NAME` ascending, using a byte-order comparison (Python's default `sorted(..., key=lambda r: r.name)`), so output is deterministic and locale-independent.
24. If `.claude/state/archive/` does not exist, contains no entries, or contains only files (no subdirectories), the CLI MUST print exactly the line `No archives found.` to stdout and exit 0.
25. The CLI MUST exit 0 on successful enumeration, even when individual archive rows had unreadable `tasks.json` files.

### `history NAME` subcommand (single-archive detail form)

26. `pipeline-status history <NAME>` MUST resolve to the directory `.claude/state/archive/<NAME>/`. The `<NAME>` argument MUST be passed through the same slugifier as `archive --name` before resolution, so that user input matching the on-disk slug is robust to case and separator differences.
27. If the resolved directory does not exist or is not a directory, the CLI MUST write to stderr (e.g. `pipeline-status: error: archive '<NAME>' not found at <path>`) and exit non-zero (SHOULD be exit code 1).
28. If the resolved directory exists, the CLI MUST run the existing inspector + stage-derivation + formatting pipeline against it (treating it as the state directory) and print the resulting body to stdout. The output layout MUST match the one-shot mode exactly, including header, per-artefact lines, task counts, and the trailing `Stage:` line.
29. The single-archive detail form MUST tolerate partial archives: any artefact missing or empty MUST render per the v1 filled-detection rules without crashing the command.
30. The single-archive detail form MUST exit 0 when the archive directory exists, regardless of how partial its contents are.
31. The single-archive detail form MUST NOT modify the archive directory or any of its contents (read-only access only).

### Inspector / stage reuse

32. The existing inspector functions (`inspect_feature_request`, `inspect_requirements`, `inspect_adr`, `inspect_tasks_json`, `inspect_worktrees_json`, `inspect_all`) and the existing `derive_stage` function MUST be reused without modification of their signatures or semantics. If a thin wrapper is needed to parameterise the state directory, it MUST be additive (a new function), not a breaking change to an existing function.
33. The existing `render(results, stage, colour)` helper in `pipeline_status/formatting.py` MUST be reused for the `history NAME` detail output. A signature-level addition (e.g. a new `render_history_table(rows) -> str` helper) is permitted; existing signatures MUST remain backwards-compatible.
34. The v1/v2 contracts around `NO_COLOR`, `sys.stdout.isatty()`, and ANSI emission MUST apply uniformly to the new subcommands: ANSI colour MAY appear only when stdout is a TTY and `NO_COLOR` is unset; clear-screen sequences MUST NOT be emitted by `archive` or `history` under any circumstance (those are watch-mode-only primitives).

### Exit codes

35. The CLI MUST adopt the following exit-code contract for the new subcommands:
    - `archive` success → 0.
    - `archive` invoked with `.claude/state/` missing → 2 (matches v1 missing-state semantics).
    - `archive` invoked with an existing destination, an empty/invalid name, or any other operational failure → 1.
    - `history` (table) success → 0 (including the "no archives" case).
    - `history NAME` success → 0.
    - `history NAME` with missing archive directory → 1.
    - Argparse-level argument errors → argparse's default (typically 2); this is acceptable and need not be remapped.

## Non-Functional Requirements

- **Performance — `history` table**: enumerating up to 100 archive directories and reading each `tasks.json` MUST complete in under 200 ms p99 on a modern laptop with archives totalling ≤ 10 MiB across all `tasks.json` files. Files larger than the existing `MAX_READ_BYTES = 10 MiB` cap from v1 MUST be treated as in v1 (filled but not further parsed for the table).
- **Performance — `archive`**: copying all five artefacts (total ≤ 1 MiB) MUST complete in under 200 ms on a modern laptop. No fsync or explicit flush is required beyond what `shutil.copy2` performs by default.
- **Performance — `history NAME`**: end-to-end runtime MUST remain within the v1 `< 500 ms` budget for a single-archive inspection.
- **Dependencies**: stdlib only. Specifically: `argparse`, `json`, `pathlib`, `datetime`, `shutil`, `re` (optional, for the slugifier), `sys`, `os`. No `tomllib`, no third-party packages, no new packaging dependencies in `pyproject.toml`.
- **Portability**: MUST run on Linux, macOS, and Windows 10+ with Python 3.10+ unchanged. Filesystem operations MUST use `pathlib` and avoid POSIX-only flags.
- **Security**: read-only outside the destination archive directory; no `eval`, no `exec`, no `subprocess`, no network access. The slugifier MUST NOT allow path traversal: any `/`, `\`, or `..` sequence in the input MUST be replaced by `-` before joining with the archive root, and the final path MUST be validated to remain a direct child of `.claude/state/archive/`.
- **Resource limits**: per-file reads MUST respect the v1 `MAX_READ_BYTES = 10 MiB` cap. No file is loaded fully into memory beyond that cap; copies use `shutil.copy2` which streams.
- **Determinism**: given identical filesystem state and a fixed timezone, both `archive` (its stdout confirmation line) and `history` (its table) MUST be byte-identical across runs (modulo ANSI colour on TTY).
- **Backwards compatibility**: every existing test under `tests/` MUST pass unchanged. The v1 stdout for `pipeline-status` with no args, and the v2 stdout for `pipeline-status --watch [--interval N]`, MUST be byte-identical to the prior releases for any given filesystem state.
- **Test execution**: `python -m unittest discover -s tests` MUST complete in under 5 seconds wall time for the full new + existing test suite. No test MUST spawn a subprocess, open a network socket, or call `time.sleep` with a non-zero real-time argument.
- **Test coverage targets**: new tests MUST cover at minimum:
  - slugifier behaviour (empty, whitespace, mixed case, unicode, path-traversal attempts, all-separator input);
  - `archive` happy path (5/5 files copied), partial source (3/5 files copied), missing source dir (exit 2), existing destination (exit 1, no partial write), empty/invalid name (exit 1, no writes), name derivation from `feature-request.md` heading, fallback to date when feature-request is absent or headless;
  - `history` table: empty archive root, missing archive root, multiple archives sorted, archive with missing `tasks.json`, archive with malformed `tasks.json`, archive with mixed completed/incomplete tasks;
  - `history NAME` detail: existing archive, missing archive (exit 1), partial archive renders without crash, name normalisation via slugifier (e.g. `Foo Bar` resolves to `foo-bar`);
  - argparse rejection of `--watch` combined with a subcommand, and of unknown subcommands;
  - byte-identical regression for `pipeline-status` and `pipeline-status --watch` invocations.
- **Compliance / regulatory**: none.

## Open Questions

1. **Archive directory mtime stability under `shutil.copy2`**: `copy2` preserves source file mtimes but the *directory* mtime in `.claude/state/archive/<NAME>/` is set by the OS at directory creation time and may shift if files are subsequently written into it. Is the archive directory's own mtime (as used in the `history` table's `ARCHIVED-AT` column) acceptable as "archived-at", or should we explicitly stamp it (e.g. `os.utime(dest_dir, (now, now))` after the last copy) to capture the moment the snapshot completed? *Proposed default*: explicitly `os.utime(dest_dir, (now, now))` once all copies finish, so `ARCHIVED-AT` always reflects "when `archive` ran", not "when the first file landed". This is a one-line addition and avoids confusing mtime shifts on subsequent reads.

2. **`history` table column for missing `feature-request.md` in an archive**: the table currently exposes `NAME`, `ARCHIVED-AT`, `TASKS`, `DONE` — should it also surface whether the archive's `feature-request.md` was filled (i.e. a fifth column `STAGE` showing the v1-derived stage at archive time)? *Proposed default*: NO — keep the table to four columns for v3. A user wanting the stage runs `history NAME` for the detail view. This keeps the table narrow and the implementation small.

3. **Behaviour when `.claude/state/archive/` exists but contains a symlinked subdirectory**: should `history` follow the symlink and enumerate the target, or skip symlinks? *Proposed default*: follow symlinks (Python's `Path.iterdir()` + `Path.is_dir()` default behaviour), matching v1's "symlinks are acceptable" stance for state files. Documented as a known edge case in the ADR.

4. **Name-conflict UX on `archive`**: should the error message on existing-destination suggest a remediation (e.g. "rerun with `--name <NAME>-2`")? *Proposed default*: NO — print a terse error with the conflicting path; the user picks a new name themselves. Avoids hardcoded retry heuristics.

5. **Concurrent invocations of `archive`**: two simultaneous `pipeline-status archive` calls with the same derived name could race on `mkdir(exist_ok=False)`. Is this an acceptable race (one wins, one fails with the existing-destination error)? *Proposed default*: YES — accept the race; the loser's clear stderr error is sufficient. No lockfile (forbidden by the feature request and v1/v2 NFRs).

6. **Slugifier behaviour for headings containing punctuation that is meaningful in markdown (e.g. backticks, brackets)**: should those be stripped or replaced? *Proposed default*: replace any non-`[a-z0-9]` character with `-` after lowercasing, which strips backticks, brackets, parentheses, colons, etc. uniformly. E.g. `# Add \`pipeline-status archive\`` becomes `add-pipeline-status-archive`. Documented in the ADR.

7. **`history` ordering**: alphabetical by name is proposed; should it instead be reverse-chronological by archived-at to put recent runs first? *Proposed default*: alphabetical ascending (FR-23), because it is deterministic given identical input and matches typical `ls`-style output; users wanting chronological order can pipe through `sort` or wait for a v4 `--sort` flag.

## Assumptions

- The v1 + v2 architecture is in place and stable: package layout `pipeline_status/{__init__,__main__,inspectors,stage,formatting,watch}.py`, `pyproject.toml` declares the `pipeline-status` console script, `tests/` uses stdlib `unittest`, and the existing inspector/stage/formatting contracts are frozen.
- The v2 watch-mode argparse layout uses a single top-level parser with optional flags; adding subcommands is done via `parser.add_subparsers(dest="cmd", required=False)` with `required=False` so the implicit "no subcommand" path continues to land in the existing one-shot/watch dispatch. This is the standard argparse idiom for "optional subcommand with default behaviour".
- The five tracked artefact basenames are exactly: `feature-request.md`, `requirements.md`, `adr.md`, `tasks.json`, `worktrees.json`. No other files in `.claude/state/` are part of the snapshot. (Hidden files, `.gitignore`, or anything else present in the directory MUST be ignored by `archive`.)
- The `.claude/state/archive/` directory may not exist when `archive` runs for the first time; the subcommand MUST create it via `mkdir(parents=True, exist_ok=True)` on the parent before creating the target archive subdirectory.
- `feature-request.md` headings of interest are ATX-style markdown (`# Heading`); Setext-style (`Heading\n=======`) headings are not in scope for the slug derivation. If only Setext headings exist, the slugifier returns empty and the date fallback kicks in (per FR-10).
- All state and archive files use UTF-8 encoding (consistent with v1's assumption).
- The repo root is the current working directory when the command is invoked; no auto-discovery via parent-directory walking is required (consistent with v1's assumption).
- The v1 inspector functions accept an arbitrary `state_dir` path (they take per-file paths derived from a root, per the v1 ADR's `inspect_all(state_dir: Path)` signature); pointing them at an archive directory is a no-op refactor and does not require changes to their bodies.
- Unit tests for the new subcommands use `tempfile.TemporaryDirectory` and `unittest.mock` where helpful (e.g. mocking `datetime.now` for the date-fallback test); no real `.claude/state/` is touched.
- The orchestrator continues to drive *when* `archive` runs (e.g. after Gate 4 closes) and *when* the source `.claude/state/*` files are subsequently cleared for the next run. This feature only provides the primitive; it does not change orchestration policy.
- `--help` rendering is the responsibility of argparse defaults; no custom help formatter is required.
