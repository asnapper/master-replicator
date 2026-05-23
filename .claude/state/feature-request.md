# Feature Request

## Feature
A small command-line utility `pipeline-status` (Python, single file, no external deps beyond the stdlib) that inspects `.claude/state/` in the current repo and prints a human-readable summary of which stage of the multi-agent pipeline the project is currently at.

For each known artefact (`feature-request.md`, `requirements.md`, `adr.md`, `tasks.json`, `worktrees.json`) it should report:
- whether the file exists
- whether it is non-empty / non-template (i.e. has been filled in vs. still placeholder)
- the last-modified time
- for `tasks.json`: a count of tasks and how many appear completed

At the end it should print the current pipeline stage (e.g. "Awaiting Gate 2: ADR review") and exit 0 on success, non-zero if `.claude/state/` is missing.

## Context
This repo is the scaffold for a Claude Code orchestrator that runs a PO → Architect → PM → Engineer pipeline. There is currently no quick way for a human (or a returning Claude session) to glance at the repo and know where in the pipeline things stand without opening each state file individually. A small status command would make resuming a paused pipeline much smoother and would also be useful in CI / pre-commit checks.

## Constraints
- Python 3.10+, stdlib only — no pip installs.
- Must work when invoked from the repo root as `python -m pipeline_status` or via a console script entry point.
- Output must be readable in a plain terminal (no required colour, but colour OK if TTY-detected).
- Must not modify any files in `.claude/state/`.
- Ship with at least one unit test per state-file inspector.
