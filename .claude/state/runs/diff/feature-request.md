# Feature Request

## Feature
Add a new subcommand `pipeline-status diff [--against OTHER] NAME` that compares two pipeline runs and prints a per-artefact summary of what changed between them.

- `pipeline-status diff foo` — compares the **live** `.claude/state/` against archive `foo`. Live is the "right side" (the "new" state); archive is the "left side" (the "old" state).
- `pipeline-status diff --against bar foo` — compares archive `foo` (right) against archive `bar` (left).

For each of the five tracked artefacts (`feature-request.md`, `requirements.md`, `adr.md`, `tasks.json`, `worktrees.json`), the output lists exactly one row:
- `+ feature-request.md` — present on the right, absent on the left.
- `- feature-request.md` — absent on the right, present on the left.
- `= feature-request.md` — same content (byte-identical) on both sides.
- `M feature-request.md` — present on both sides but content differs.

A summary footer counts each category: `Diff: 1 added, 0 removed, 3 unchanged, 1 modified.`

If either side is missing entirely (e.g. archive name does not resolve), exit 1 with a clear stderr message. Live state missing on the LIVE side is acceptable — those artefacts simply appear as removed.

## Context
v3 added `pipeline-status archive` (snapshot) and `pipeline-status history` (list / inspect past runs). The natural next question is: **what changed** between two runs? Currently a developer has to `diff -r` two archive directories manually. A first-class `diff` subcommand turns this into one command and sets up the contract that future tooling (e.g. CI gates on "ADR changed since last release") can build on.

## Constraints
- **Stdlib only**, consistent with v1–v3. No `difflib` HTML output, no third-party libraries. Comparison is byte-equality of file contents (read up to `MAX_READ_BYTES` from each side); we are NOT producing a per-line diff in v4.
- **Slugifier reuse**: archive names are slugified using the existing `archive.slugify` (lazy import — same pattern as `history.run_history`).
- **Argparse**: the subparser MUST NOT accept `--watch` or `--interval`. `--against` is an optional flag; the positional `NAME` is required.
- **Read-only**: `diff` MUST NOT modify or write any file. No caching, no manifest, no lockfile.
- **Performance**: `<200 ms` for any two archives where each side fits in `MAX_READ_BYTES`.
- **Deterministic output**: identical inputs produce byte-identical stdout (modulo ANSI when stdout is a TTY).
- **Existing CLI surface MUST NOT regress**: `pipeline-status`, `pipeline-status --watch`, `pipeline-status archive`, `pipeline-status history` all keep their current behaviour byte-identical.
- **Tests** use stdlib `unittest` + `tempfile.TemporaryDirectory`. No subprocesses, no network, no real `.claude/state/` access.

## Out of Scope
- Line-by-line text diff (no `difflib.unified_diff` output in v4).
- Diff for the whole archive tree (only the five tracked artefacts).
- `--json` machine-readable output (still deferred).
- Three-way diff or merge.
- Diff between non-adjacent revisions of the same archive (archives are immutable once written).
- Colour-coded `+`/`-`/`=`/`M` glyphs are MAY (nice-to-have if `formatting.use_colour()` is true), not MUST.
