# Multi-Agent Pipeline Setup

## What this is
A Claude Code orchestrator that runs PO → Architect → PM → Engineer agents in sequence on a software feature, with human approval gates between each phase.

## Prerequisites
- Claude Code installed (`npm install -g @anthropic-ai/claude-code`)
- Git repo initialised
- (Optional) Jira MCP configured for automatic ticket creation

## Setup Steps

### 1. Copy scaffold into your repo
```bash
cp -r project-scaffold/.claude /your/repo/.claude
cp project-scaffold/CLAUDE.md /your/repo/CLAUDE.md
```

### 2. Write your feature request
Edit `.claude/state/feature-request.md` with the feature you want built.

### 3. Start the orchestrator
```bash
cd /your/repo
claude
```
Claude Code will read `CLAUDE.md` and begin the pipeline automatically.

### 4. Follow the gates
The orchestrator will pause at each gate and ask for your approval:
- **Gate 1** — Review requirements in `.claude/state/requirements.md`
- **Gate 2** — Review ADR in `.claude/state/adr.md`
- **Gate 3** — Review task list in `.claude/state/tasks.json`
- **Gate 4** — Review the opened PRs on GitHub

At each gate, reply `APPROVE` to continue or provide feedback to iterate.

### 5. Engineer agents run in parallel
After Gate 3, the orchestrator creates git worktrees automatically:
```
your-repo/           ← main session (orchestrator)
../your-repo-task-001/  ← Engineer agent 1
../your-repo-task-002/  ← Engineer agent 2
...
```
Each Engineer agent opens a PR when done. You review and merge normally.

### 6. Cleanup
After all PRs are merged:
```bash
git worktree prune
```

---

## Resuming After a Restart
State is persisted in `.claude/state/`. If your Claude Code session dies mid-pipeline:
1. Restart Claude Code in the repo root
2. Tell it: *"Resume the pipeline. Requirements/ADR/tasks are already approved, continue from [step]."*
3. It will read the state files and pick up where it left off.

## Optional: Jira / Confluence Integration
If you have Atlassian MCP configured, the PM Agent will create Jira tickets automatically and any agent can read/write Confluence pages.

Use Atlassian's official remote MCP server (Cloud-hosted, OAuth, covers both Jira and Confluence):
```bash
claude mcp add --scope user --transport http atlassian https://mcp.atlassian.com/v1/mcp/authv2
```
Restart Claude Code afterwards. On the first tool call your browser will open for OAuth against your Atlassian Cloud workspace.

> The `https://mcp.atlassian.com/v1/sse` endpoint is being deprecated on 30 June 2026 — use the `/v1/mcp/authv2` HTTP endpoint above.

For Atlassian Server / Data Center (or if you prefer a self-hosted setup), see the community `mcp-atlassian` server, which uses an API token instead of OAuth.


---

## pipeline-status CLI

The `pipeline-status` package provides a CLI command to inspect
the current state of the multi-agent pipeline.

### Installation

```bash
pip install -e .
```

### Usage

Two equivalent invocation forms are supported:

```bash
# Form 1 -- module invocation
python -m pipeline_status

# Form 2 -- direct entry point (after pip install)
pipeline-status
```

Both forms accept an optional `--state-dir` argument:

```bash
pipeline-status --state-dir /path/to/.claude/state
```

### Sample Output

```
Pipeline Status  --  stage: requirements
------------------------------------------------------------
feature-request.md       EXISTS   FILLED  2026-05-23T09:12:00
requirements.md          EXISTS   FILLED  2026-05-23T09:45:31
adr.md                   MISSING  EMPTY   —
tasks.json               MISSING  EMPTY   —
------------------------------------------------------------
[Requirements     -- PO agent output]
```

### Exit Codes

| Code | Meaning |
|------|---------|
| `0`  | One-shot: successful inspection. Watch mode: clean exit after Ctrl+C |
| `2`  | One-shot: `.claude/state/` is missing or not a directory. Watch mode tolerates a missing state directory and continues looping |

### Watch mode

`--watch` turns `pipeline-status` into a live dashboard: the same report is re-rendered every `--interval` seconds until you press Ctrl+C. Useful while the orchestrator pipeline is actively running and you want to see gates advance without re-invoking the CLI.

```bash
# Default 2 s refresh
pipeline-status --watch

# Slower refresh (5 s)
pipeline-status --watch --interval 5
```

The watch loop appends a footer line with the last-refresh timestamp:

```
Pipeline Status
===============

  feature-request.md   EXISTS  FILLED  2026-05-23T06:17:09+02:00
  requirements.md      EXISTS  FILLED  2026-05-23T06:17:09+02:00
  adr.md               EXISTS  FILLED  2026-05-23T06:17:09+02:00
  tasks.json           EXISTS  FILLED  2026-05-23T06:17:09+02:00  (3/5 tasks done)
  worktrees.json       EXISTS  FILLED  2026-05-23T06:31:28+02:00

  Stage: Engineering in progress

Last refresh: 2026-05-23T05:54:36+02:00  (interval: 2s, press Ctrl+C to stop)
```

#### `--interval SECONDS`

Refresh cadence. Default `2`. Must be an integer in `[1, 3600]`. Floats (`0.5`), non-numeric strings (`abc`), and out-of-range values are rejected by argparse with exit code 2 *before* any inspector runs. Passing `--interval` *without* `--watch` is accepted and silently ignored — the one-shot stdout remains byte-identical to `pipeline-status` with no flags.

#### Ctrl+C

A single Ctrl+C exits cleanly with code 0. A trailing newline is emitted so the shell prompt lands on its own line.

#### TTY vs. non-TTY behaviour

| Stream | Inter-render behaviour |
|---|---|
| TTY (interactive terminal) | ANSI escape `\x1b[H\x1b[2J` clears the screen between renders |
| Non-TTY (pipe, redirect, `tee`) | No clear-screen escape; consecutive renders are separated by exactly one blank line — clean for `grep`, `tee`, log files |

#### Missing `.claude/state/` in watch mode

Unlike the one-shot path (exit 2), watch mode renders a placeholder body and continues polling:

```
Pipeline Status
===============

  .claude/state/: MISSING

Last refresh: 2026-05-23T05:54:36+02:00  (interval: 2s, press Ctrl+C to stop)
```

This lets you start `pipeline-status --watch` *before* the orchestrator initialises state files; the placeholder switches to the real report on the next poll once the directory appears.

#### Cross-platform notes

- Linux, macOS, and Windows 10+ with Virtual Terminal Processing (on by default in PowerShell and Windows Terminal) all render the clear-screen escape correctly.
- No `colorama` or other third-party Windows-ANSI shim is bundled or required.
- On terminals shorter than the report, the top of the report will scroll off — accepted limitation for v2; see `--help` epilog.

### NO_COLOR Environment Variable

| Variable | Effect |
|----------|---------|
| `NO_COLOR` | Set to any value (including empty string) to disable ANSI colour output |

By default, colour is emitted only when stdout is an interactive TTY.
Setting `NO_COLOR` (per the no-color.org convention) disables it unconditionally.

```bash
NO_COLOR=1 pipeline-status
```

### Subcommands (v3)

In addition to the v1 one-shot inspection and the v2 `--watch` mode (both
documented above and unchanged), `pipeline-status` now exposes two
subcommands for managing snapshots of past pipeline runs:

```
pipeline-status [--watch] [--interval SECONDS] {archive,history} ...
```

`pipeline-status --help` now lists `{archive,history}` in the usage line.
Neither new subcommand accepts `--watch` or `--interval` — those flags live
on the top-level parser and apply only to the no-subcommand path. Combining
either flag with a subcommand is rejected by argparse with exit code 2 and a
usage error to stderr.

#### `archive` — snapshot the live state directory

Copies the current `.claude/state/` artefacts (`feature-request.md`,
`requirements.md`, `adr.md`, `tasks.json`, `worktrees.json`) into a fresh
subdirectory under `.claude/state/archive/<NAME>/`. Whatever subset of those
files happens to exist is copied; nothing is removed from the source state
directory. On completion `archive` prints a confirmation line of the form
`Archived 5 file(s) to .claude/state/archive/<NAME>/` and exits 0.

```bash
# Default name: derived from the first markdown heading of feature-request.md,
# slugified; falls back to today's local date (YYYY-MM-DD) if no heading.
pipeline-status archive

# Explicit name; the value is passed through the slugifier described below.
pipeline-status archive --name "My Feature!"
# -> writes .claude/state/archive/my-feature/
```

**`--name NAME`** — optional. The supplied value is normalised by the
built-in slugifier before being used as the archive directory name. The
slugifier rules are:

1. Lowercase the input.
2. Replace every run of characters outside `[a-z0-9]` with a single `-`.
3. Strip leading and trailing `-`.
4. If the result is the empty string, the name is rejected (exit 1).

Examples: `slugify("My Feature!")` → `my-feature`;
`slugify("  Foo / Bar  ")` → `foo-bar`;
`slugify("naïve")` → `na-ve` (non-ASCII letters become separators, they are
not transliterated); `slugify("!!!")` → `""` (rejected).
Output is restricted to `[a-z0-9-]` by construction, so `/`, `\`, and `..`
cannot appear in slugs.

Exit codes for `archive`:

| Code | Meaning |
|------|---------|
| `0`  | Snapshot written successfully. |
| `2`  | `.claude/state/` is missing or not a directory. |
| `1`  | Destination archive directory already exists, or `--name` slugifies to the empty string. |

#### `diff` — compare two pipeline runs

`pipeline-status diff [--against OTHER] NAME` compares two pipeline runs
artefact-by-artefact across the five tracked files (`feature-request.md`,
`requirements.md`, `adr.md`, `tasks.json`, `worktrees.json`) and prints a
per-artefact summary followed by an aggregate footer. The positional `NAME`
is always the right-hand side and is passed through the same slugifier
used by `archive` and `history`.

`diff` operates in two modes:

- **Live-vs-archive (default)** — when `--against` is omitted, the left
  side is the live `.claude/state/` directory and the right side is the
  archive at `.claude/state/archive/<slug(NAME)>/`. Use this to see what
  has changed since a snapshot was taken.
- **Archive-vs-archive (`--against OTHER`)** — when `--against` is
  supplied, both sides are archives:
  `.claude/state/archive/<slug(OTHER)>/` (left) and
  `.claude/state/archive/<slug(NAME)>/` (right). Use this to compare two
  historical snapshots without touching live state.

Each tracked artefact is categorised into exactly one of four glyphs:

- `+` — **added**: present on the right side, absent from the left.
- `-` — **removed**: present on the left side, absent from the right.
- `=` — **unchanged**: present on both sides with byte-identical content (or absent from both).
- `M` — **modified**: present on both sides but with differing content.

Artefacts absent from both sides are counted as `unchanged` in the footer
but no row is emitted for them.

```bash
# Live state vs archive named 'foo'
pipeline-status diff foo

# Archive 'bar' (left) vs archive 'foo' (right)
pipeline-status diff --against bar foo
```

Example output:

```
= feature-request.md
= requirements.md
M adr.md
+ tasks.json

Diff: 1 added, 0 removed, 3 unchanged, 1 modified.
```

The footer counts always sum to exactly 5 (the number of tracked
artefacts).

Exit codes for `diff`:

| Code | Meaning |
|------|---------|
| `0`  | Comparison completed successfully (regardless of category mix). |
| `1`  | `NAME` (or `OTHER`) is invalid (slugifies to empty), or the resolved archive directory does not exist. |
| `2`  | Live `.claude/state/` is missing or not a directory (only possible when `--against` is omitted). |

`--watch` and `--interval` are top-level flags and are **not** accepted
with `diff`; combining them with the subcommand is rejected by argparse
with exit code 2.

**Truncation caveat**: comparison reads at most `MAX_READ_BYTES` (10 MiB)
from each side of every artefact. Files that differ only beyond the first
10 MiB will compare as `=`. The 10 MiB cap is far larger than any
realistic `.claude/state/` artefact.

#### `history` — list past archives (table form)

Without arguments, `history` enumerates the immediate subdirectories of
`.claude/state/archive/` and prints a four-column table (`NAME`,
`ARCHIVED-AT`, `TASKS`, `DONE`) sorted alphabetically by name. `TASKS` and
`DONE` are read from each archive's `tasks.json`; if that file is missing or
malformed, both cells render as `-`. Columns are separated by two-space
gutters.

```bash
pipeline-status history
```

Example output:

```
NAME              ARCHIVED-AT                TASKS  DONE
pipeline-status   2026-05-20T14:32:01+02:00  3      3
watch-mode        2026-05-22T09:15:00+02:00  4      2
```

If the archive root is missing or contains no archive subdirectories,
`history` prints `No archives found.` and exits 0.

Exit codes for `history` (table form):

| Code | Meaning |
|------|---------|
| `0`  | Table rendered (including the no-archives case which prints `No archives found.`). |

#### `history NAME` — render one archived run (detail form)

When a positional `NAME` is supplied, `history` resolves it to
`.claude/state/archive/<slug>/` (passing `NAME` through the same slugifier
described above) and renders the archive using the same layout as the v1
one-shot report — header `Pipeline Status`, one row per artefact, and a
trailing stage line. Partial archives are accepted: any missing artefacts
render with their v1 `MISSING` / unfilled markers and the report still
exits 0.

```bash
# Mixed-case input is normalised by the slugifier:
pipeline-status history Watch-Mode
# -> reads .claude/state/archive/watch-mode/
```

Exit codes for `history NAME`:

| Code | Meaning |
|------|---------|
| `0`  | Archive directory exists and was rendered (even if some artefacts are missing inside it). |
| `1`  | Archive directory does not exist. |

#### `restore` — copy an archived snapshot back into live state

`pipeline-status restore NAME [--force]` is the read-write counterpart of
`archive`: it copies the canonical artefact files from
`.claude/state/archive/<slug>/` (where `<slug>` is `slugify(NAME)`, using the
same slugifier as `archive`) back into `.claude/state/`. Whatever subset of
the tracked artefacts is present in the archive directory is what gets
restored. `restore` is **additive**: it never removes or modifies live files
that are not present in the archive — any such file is left untouched
byte-for-byte.

By default, `restore` performs an **all-or-nothing collision check** before
copying anything. It enumerates the tracked artefacts present in the
archive, builds the full list of basenames whose live counterpart already
exists under `.claude/state/`, and — if that list is non-empty — refuses
the entire operation, prints the complete conflict list to stderr, and
exits 1 **without copying any file** (including non-conflicting ones).

Passing `--force` is a coarse permission grant ("yes, I accept
overwrites"). With `--force`, existing live **files** are overwritten by
their archived counterpart. `--force` never overwrites a live **directory**
sitting at a target path — if any tracked target under `.claude/state/` is
a real directory (e.g. someone manually `mkdir`'d `.claude/state/tasks.json/`),
`restore --force` aborts with a directory-overwrite error and exit code 1
before any file is copied. `--force` likewise never deletes live files that
are absent from the archive.

The `--watch` and `--interval` flags are **not** accepted on the `restore`
subcommand — they live on the top-level parser and apply only to the
no-subcommand path. Combining either with `restore` is rejected by argparse
with exit code 2 and a usage error to stderr.

```bash
# Safe restore — refuses if any live target already exists,
# listing every conflicting basename:
pipeline-status restore foo

# Roll the live state back to a snapshot, overwriting any
# existing live files (but never live directories, and
# never deleting live files absent from the archive):
pipeline-status restore foo --force
```

On success `restore` prints a single confirmation line to stdout and
exits 0:

```
Restored 5 file(s) from .claude/state/archive/foo/
```

If any live target would be overwritten and `--force` was not passed,
`restore` prints the full conflict list to stderr and exits 1 without
copying anything:

```
pipeline-status: error: refusing to overwrite existing file(s): requirements.md, adr.md, tasks.json
(use --force to overwrite)
```

Exit codes for `restore`:

| Code | Meaning |
|------|---------|
| `0`  | Restore succeeded (`N` may be `0` if the archive directory is empty); printed as `Restored N file(s) from .claude/state/archive/<slug>/`. |
| `1`  | Archive directory not found, `NAME` slugifies to the empty string, one or more live targets would be overwritten and `--force` was not passed, or `--force` was passed but a live target at a tracked path is a directory. |
| `2`  | `.claude/state/` is missing or is not a directory. |

<!-- BEGIN: docker-section (Feature A) -->

## Docker

`pipeline-status` is published as a multi-arch (`linux/amd64`, `linux/arm64`) Docker image at [`ghcr.io/asnapper/master-replicator`](https://github.com/asnapper/master-replicator/pkgs/container/master-replicator). It bundles the CLI with all v1–v4 subcommands and runs as a non-root user (UID/GID `65532:65532`).

### Quick start

Mount your repo (the one containing `.claude/state/`) at `/repo` and invoke any subcommand:

```bash
# One-shot status report (v1 behaviour)
docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest

# Subcommand pass-through (v3/v4)
docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest history
docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest history watch-mode
docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest diff watch-mode
docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest archive --name my-snapshot
docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest restore my-snapshot --force
```

### Watch mode

`--watch` works the same way as the local CLI; allocate a TTY to see the screen-clear:

```bash
docker run --rm -it -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest --watch --interval 5
```

### Suppressing colour

Pass `NO_COLOR=1` through the environment:

```bash
docker run --rm -v "$PWD":/repo -e NO_COLOR=1 ghcr.io/asnapper/master-replicator:latest
```

### Image tags

| Tag | When pushed | Use case |
|---|---|---|
| `:latest` | every push to `master` | development / always-current |
| `:sha-<short>` | every push to `master` | immutable, traceable to a commit |
| `:vX.Y.Z` | when a `v*` git tag is pushed | semver-pinned production deployments |
| `:X.Y` | when a `v*` git tag is pushed | minor-version pinned |

### Permissions / UID mapping

The image runs as UID `65532` (`pipeline:pipeline`). When mounting a host directory, ensure it's writable by that UID for the `archive` and `restore` subcommands. On Linux:

```bash
docker run --rm \
    --user "$(id -u):$(id -g)" \
    -v "$PWD":/repo \
    ghcr.io/asnapper/master-replicator:latest archive --name snapshot
```

(Or `chmod a+rwx` your `.claude/state/`.)

<!-- END: docker-section (Feature A) -->
