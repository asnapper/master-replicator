# Requirements: pipeline-status CLI

## Problem Statement
Developers and returning Claude Code sessions working with this multi-agent orchestrator scaffold have no quick way to determine the current state of the PO -> Architect -> PM -> Engineer pipeline without manually opening each artefact in `.claude/state/`. This slows down context recovery when resuming paused pipelines and prevents use of pipeline-state checks in CI or pre-commit hooks.

## Goals
- Provide a single command (`pipeline-status`) that reports pipeline progress in under 1 second on a repo of expected size.
- Inspect all five known state artefacts and report existence, fill-state, and mtime.
- Derive and print the current pipeline stage from the artefact states.
- Be embeddable in CI / pre-commit (deterministic, parseable enough, non-zero exit on error).
- Ship with unit tests covering each artefact inspector.

## Non-Goals
- No modification, repair, or migration of `.claude/state/` files.
- No JSON / machine-readable output mode in v1 (human-readable only).
- No remote state, multi-repo, or daemon mode.
- No interactive UI, TUI, or watch mode.
- No external dependencies, no virtualenv setup tooling.
- No advancing or driving the pipeline forward; this is a read-only inspector.
- No authentication, network calls, or telemetry.

## User Stories

> As a developer returning to a paused pipeline, I want to run `pipeline-status` so that I can immediately see which stage is next without opening files.
- [ ] Running the command in a repo root prints a summary block listing each artefact.
- [ ] The final line names the current pipeline stage (e.g. "Awaiting Gate 2: ADR review").
- [ ] Command exits 0 when `.claude/state/` exists.

> As a CI pipeline author, I want `pipeline-status` to exit non-zero when the state directory is missing so that I can gate jobs on pipeline readiness.
- [ ] Command exits with a non-zero status if `.claude/state/` is absent.
- [ ] An explanatory error message is written to stderr.

> As a developer in a plain terminal, I want output that is readable without ANSI colour codes so that logs and pipes remain clean.
- [ ] When stdout is not a TTY, no ANSI escape sequences are emitted.
- [ ] When stdout is a TTY, colour MAY be used to highlight stage/status.

> As a maintainer, I want unit tests for each artefact inspector so that future changes do not regress the status logic.
- [ ] At least one unit test exists per state-file inspector (5 inspectors -> >= 5 tests).
- [ ] Tests run via stdlib `unittest` with no external dependencies.

## Functional Requirements

1. The utility MUST be a single Python module/file (`pipeline_status`) using only the Python 3.10+ standard library.
2. The utility MUST be invokable as `python -m pipeline_status` from the repo root.
3. The utility MUST also be invokable via a console script entry point named `pipeline-status` (declared in `pyproject.toml` or equivalent packaging metadata).
4. The utility MUST locate `.claude/state/` relative to the current working directory.
5. If `.claude/state/` does not exist or is not a directory, the utility MUST write an error message to stderr and exit with a non-zero status (SHOULD be exit code 2).
6. The utility MUST inspect the following artefacts in `.claude/state/`:
   - `feature-request.md`
   - `requirements.md`
   - `adr.md`
   - `tasks.json`
   - `worktrees.json`
7. For each artefact, the utility MUST report:
   - existence (present / missing)
   - filled vs. placeholder/empty state (see FR-8)
   - last-modified time in ISO-8601 local-time format with timezone offset
8. The "filled" check MUST treat the following as NOT filled:
   - file missing
   - file size == 0
   - file contents consisting only of whitespace
   - markdown files whose non-whitespace, non-comment content is only a single top-level heading (e.g. `# Feature Request` with nothing else)
   - JSON files whose parsed content is an empty object `{}` or empty array `[]`
9. For `tasks.json`, the utility MUST additionally report:
   - total task count
   - count of tasks considered completed (a task is completed when it is a JSON object containing a `status` field equal to `"done"` or `"completed"`, case-insensitive, OR a boolean field `completed` / `done` set to `true`)
   - if `tasks.json` is malformed JSON, the utility MUST report the parse error inline and continue processing other artefacts (MUST NOT crash).
10. After per-artefact reporting, the utility MUST print a single line naming the current pipeline stage using the following derivation rules, evaluated in order:
    - `feature-request.md` missing or not filled -> "Awaiting feature request"
    - `requirements.md` not filled -> "Awaiting Gate 1: PO requirements"
    - `adr.md` not filled -> "Awaiting Gate 2: ADR / Architect review"
    - `tasks.json` not filled -> "Awaiting Gate 3: PM task breakdown"
    - `worktrees.json` not filled -> "Awaiting Gate 4: Engineer worktree setup"
    - all artefacts filled and `tasks.json` has at least one task and not all tasks completed -> "Engineering in progress (<done>/<total> tasks complete)"
    - all artefacts filled and all tasks completed -> "Pipeline complete"
11. The utility MUST NOT write to, create, delete, or modify any file under `.claude/state/`.
12. On successful inspection (regardless of stage), the utility MUST exit with status 0.
13. When stdout is attached to a TTY, the utility MAY emit ANSI colour codes; when stdout is not a TTY, it MUST NOT emit ANSI escape sequences.
14. The utility SHOULD respect the `NO_COLOR` environment variable and suppress colour when set.
15. The utility MUST provide `--help` output via `argparse` describing usage and exit codes.
16. The package MUST ship unit tests under a `tests/` directory using stdlib `unittest`, with at least one test per artefact inspector and one test for the stage-derivation logic.

## Non-Functional Requirements
- Performance: end-to-end runtime MUST be < 500 ms on a repo where all five artefacts exist and total under 1 MB combined, on a modern laptop.
- Portability: MUST run unchanged on Linux, macOS, and Windows (no POSIX-only syscalls; use `pathlib`).
- Python version: MUST support CPython 3.10, 3.11, 3.12; SHOULD remain compatible with 3.13.
- Dependencies: stdlib only. No `pip install`-required packages, including for tests.
- Side effects: read-only with respect to the filesystem outside of stdout/stderr.
- Security: MUST NOT execute or `eval` any content from state files; MUST parse `tasks.json` and `worktrees.json` with `json.load` only.
- Resource limits: MUST guard against pathological inputs by not loading any single artefact > 10 MB into memory for filled-detection (SHOULD stream / cap reads).
- Determinism: given identical filesystem state and a fixed timezone, output MUST be byte-identical across runs (modulo ANSI colour when TTY).
- Compliance: no PII handling; no telemetry; no network access.

## Open Questions
1. Should the console script entry point and packaging metadata live in a new `pyproject.toml` at repo root, or be added to an existing one? (No `pyproject.toml` is presently confirmed.)
2. Is the proposed stage taxonomy ("Gate 1..4") aligned with how the orchestrator labels gates elsewhere, or should the labels match an existing convention in `.claude/agents/`?
3. For `worktrees.json`, what is the canonical "filled" shape — list of worktree records, or an object keyed by task id? This affects FR-8's JSON emptiness rule for that file.
4. Should a `--json` machine-readable output mode be added now (deferred to a future iteration per Non-Goals), or is a stable text format sufficient for CI consumers?
5. Is exit code 2 acceptable for the "state dir missing" case, or is a different non-zero code preferred for CI conventions?

## Assumptions
- The repo root is the current working directory when the command is invoked; no auto-discovery via walking up parent directories is required.
- All state files use UTF-8 encoding.
- `tasks.json` is a JSON array of task objects, or a JSON object containing a top-level `tasks` array; the inspector will accept either.
- A markdown artefact containing only its template heading (e.g. `# Requirements: <Feature Name>` with `<...>` placeholders) counts as NOT filled; the heuristic is "no non-heading, non-placeholder body content."
- The pipeline gate naming ("Gate 1..4") is acceptable absent contradictory guidance; can be renamed cheaply later.
- Console script entry point will be added via a new minimal `pyproject.toml` if none exists.
- Unit tests will use `tempfile.TemporaryDirectory` fixtures and not touch the real `.claude/state/`.
