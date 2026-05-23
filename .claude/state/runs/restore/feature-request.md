# Feature Request

## Feature
Add a new subcommand `pipeline-status restore NAME [--force]` that copies an archived run's artefact files back into the live `.claude/state/` directory. This is the read-write counterpart of `pipeline-status archive`.

- `pipeline-status restore foo` — copies each file in `.claude/state/archive/foo/` (limited to the five tracked artefacts) into `.claude/state/`. **Refuses** if any live target file already exists, exits 1 with a stderr message naming the offending file(s).
- `pipeline-status restore foo --force` — same, but overwrites existing live files without prompting.

On success, prints `Restored N file(s) from .claude/state/archive/<slug>/` to stdout and exits 0. `N` is 0..5 depending on which artefacts existed in the archive.

If the archive directory does not exist (after slugifying the supplied name), exit 1 with a stderr error matching the v3 `history NAME` style: `pipeline-status: error: archive '<name>' not found at <path>`.

## Context
v3 added `archive` (snapshot live → archive) and `history` (read past archives). The natural counterpart is `restore` (archive → live). Today the orchestrator (or a developer recovering a corrupted state) must `cp` files by hand from `.claude/state/archive/<NAME>/` back into `.claude/state/`. A first-class `restore` subcommand makes this safe (collision-detection by default) and discoverable (it appears in `--help`).

## Constraints
- **Stdlib only**, consistent with v1–v3. No backup-file rotation, no transactions, no lockfile. The `--force` mode is a simple overwrite.
- **Slugifier reuse**: archive names are slugified using the existing `archive.slugify` (lazy import inside the subcommand action).
- **TRACKED_ARTEFACTS reuse**: the five tracked basenames are imported from `pipeline_status.archive.TRACKED_ARTEFACTS` so the restore set never drifts from the archive set.
- **Collision detection** is per-file: if even ONE live target file exists and `--force` is not set, the subcommand exits 1 with the names of ALL conflicting files listed, and **does NOT copy anything** (no partial restore).
- **`--force` overwrites** any existing live target. It does NOT delete live files that aren't in the archive (e.g. if the live state has `worktrees.json` but the archive doesn't, `--force` leaves `worktrees.json` alone). Restore is purely **additive / overwriting**, never removing.
- **Argparse**: the subparser MUST NOT accept `--watch` or `--interval`. The positional `NAME` is required.
- **Performance**: `<200 ms` for any single archive whose files fit in `MAX_READ_BYTES`.
- **Existing CLI surface MUST NOT regress**: v1/v2/v3 paths byte-identical.
- **Tests** use stdlib `unittest` + `tempfile.TemporaryDirectory`. No subprocess, no network.

## Out of Scope
- Restoring across repos / remote sources.
- Partial restore (only certain files) — restore is all-or-nothing for the five tracked artefacts.
- `--dry-run` mode (defer to v5 if requested).
- Backup-before-overwrite mechanism (use `archive` first if you want a backup).
- Deletion of live files not in the archive (restore is additive).
- Conflict-resolution prompting (it's batch-only — error or `--force`).
- `--json` output.
