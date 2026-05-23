# Feature Request

## Feature
Add a `--watch` mode to the existing `pipeline-status` CLI so a developer (or returning Claude Code session) can leave a terminal window open and see the pipeline state refresh automatically while the orchestrator pipeline progresses.

When `--watch` is passed, the tool MUST:
- Re-render the same status report it currently prints, every N seconds (default 2 seconds).
- Clear the previous render before each new one so the screen does not scroll.
- Append a small footer line under the report, e.g. `Last refresh: 2026-05-23T05:54:36+02:00  (interval: 2s, press Ctrl+C to stop)`.
- Exit 0 cleanly on SIGINT (Ctrl+C), printing a final newline so the shell prompt is on its own line.
- Continue running across transient changes in `.claude/state/` — i.e. a missing state dir should NOT cause `--watch` to exit (only the one-shot mode does that).

Add a `--interval SECONDS` flag to control the refresh cadence (default 2; minimum 1; maximum 3600). Negative or non-integer values must be rejected by argparse with a clear error.

## Context
The base `pipeline-status` CLI delivered in the first pipeline run is a one-shot inspector — you run it, you read the output, you forget about it. During an active multi-agent pipeline run (when state files are being written and worktrees are being created in real time), a static one-shot view goes stale almost immediately. A `--watch` mode is the smallest possible feature that turns the existing CLI into a live "dashboard" without inventing a TUI, daemon, or any HTTP surface.

This feature also pairs naturally with the new pattern of orchestrators running long fan-outs of engineer subagents: the human reviewer can leave `pipeline-status --watch` open in one pane and watch tasks tick through to completion while reviewing PRs in another.

## Constraints
- **Stdlib only.** No new pip dependencies. `time.sleep`, `signal`, and ANSI escape sequences are sufficient. No `curses`, no `rich`, no `watchdog`.
- **Existing one-shot behaviour MUST NOT change.** Running `pipeline-status` without `--watch` must produce byte-identical output to the previous release.
- **No file watching / inotify.** Polling is explicitly the chosen mechanism — it works cross-platform, has zero dependencies, and the refresh interval is small enough that latency is acceptable.
- **TTY-only by default.** If stdout is not a TTY and `--watch` is passed, argparse should accept it but the screen-clear sequences must be suppressed (the report just prints repeatedly, separated by a blank line). This keeps pipe/log usage sane.
- **Cross-platform.** Linux, macOS, Windows 10+. Use the standard ANSI clear-screen escape (`\x1b[H\x1b[2J`); modern Windows terminals support it.
- **No new external state.** `--watch` does not write any file; it does not create lockfiles, PID files, or log files.
- **Tests:** stdlib `unittest` only, no live process spawning. The watch loop must be factored so the iteration body and the sleep/loop control are testable independently (inject the sleep/clock for unit tests).
- **Backwards compatible.** All existing tests (`tests/test_inspectors.py`, `tests/test_stage.py`, `tests/test_formatting.py`) MUST continue to pass unchanged.

## Out of Scope
- No file-system event watching (inotify / FSEvents / ReadDirectoryChangesW).
- No `--json` machine-readable output mode (still deferred from the v1 ADR).
- No interactive TUI, no scrolling history, no keyboard shortcuts beyond Ctrl+C.
- No daemon mode, systemd unit, or background process management.
- No remote / multi-repo state inspection.
