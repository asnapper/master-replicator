# Feature Request

## Feature
Add two new subcommands to the existing `pipeline-status` CLI for **time-travel over past pipeline runs**:

- `pipeline-status archive [--name NAME]` — snapshot the current `.claude/state/*` files into `.claude/state/archive/<NAME>/`. If `--name` is omitted, derive a name from `feature-request.md`'s first heading (slugified), falling back to the current ISO date.
- `pipeline-status history` — list all archived runs as a table (one row per archive) with name, archived-at timestamp (mtime), task count and how many tasks were marked complete in that archive.
- `pipeline-status history NAME` — show the same per-artefact rows that one-shot prints, but reading from `.claude/state/archive/<NAME>/` instead of the live state.

This lets a developer (or returning Claude Code session) see the project's history at a glance instead of digging through `.claude/state/archive/` by hand.

## Context
v1 delivered the one-shot inspector. v2 added `--watch` for live monitoring. Both are concerned only with the **current** pipeline run. As the orchestrator now runs multiple feature deliveries through the same repo, the `.claude/state/archive/` directory is starting to accumulate snapshots (one per past feature: `pipeline-status-cli/`, `watch-mode/`, soon `archive-history/` itself). The orchestrator manually moves state files into `archive/` between runs — this feature replaces that manual step with a proper subcommand AND gives a read-side view of what's in there.

Why this is the right next feature: it sets up the **subcommand infrastructure** that future iterations will reuse (e.g., `--json` output, `lint`, `diff`), turns a manual workflow step into a primitive, and is genuinely useful for retrospectives ("how many features have we shipped through this pipeline so far?").

## Constraints
- **Stdlib only** (per v1/v2 ADRs). `json`, `pathlib`, `datetime`, `argparse`. No `tomllib` use for the slugifier — keep it a tiny inline function.
- **Existing CLI surface MUST NOT regress.** `pipeline-status` (no args) and `pipeline-status --watch` continue to behave byte-identically. The new subcommands sit alongside them. Implementation hint: `argparse` allows mixing a top-level optional flag (`--watch`) with subcommands by making the subcommand argument optional and treating its absence as the "implicit status" command.
- **Archive layout** is a flat directory: `.claude/state/archive/<NAME>/{feature-request.md,requirements.md,adr.md,tasks.json,worktrees.json}`. The `archive` subcommand copies the live files into that directory; it does NOT remove them from `.claude/state/`. (Removal is the orchestrator's job between runs.)
- **Archive identity** is the directory name. Names are slugs (lowercase, dashes, no spaces); the slugifier rejects empty input. If the target directory exists, `archive` fails with a clear stderr error and exit 1 (do not overwrite by default).
- **History reads must tolerate partial archives.** An archive missing `tasks.json` should not crash `history`; it just shows blank counts.
- **Performance**: `history` must scan a directory of up to 100 archives in under 200 ms.
- **No new files written outside the archive directory.** No PID files, lockfiles, or caches.
- **Tests use stdlib `unittest`** + `tempfile.TemporaryDirectory`. No subprocess use. No real network access.

## Out of Scope
- Restoring an archive back into live state (no `pipeline-status restore`). Archives are read-only history.
- Diff between two archives (no `pipeline-status diff A B`).
- JSON / machine-readable output mode (still deferred from v1).
- Automatic archiving at end of run (the orchestrator continues to drive when archiving happens).
- Compression, encryption, or external sync of archives.
- Search / filter flags on `history` (defer to grep / awk for v3).
