# Requirements: pipeline-status `--watch` mode

## Problem Statement
The existing `pipeline-status` CLI is a one-shot inspector of `.claude/state/`: a developer runs it, reads the snapshot, and the view is immediately stale once the multi-agent orchestrator starts writing new artefacts or registering worktrees. During an active pipeline run (PO → Architect → PM → Engineer fan-out) the human reviewer and any returning Claude Code session have no way to keep a live view of where the pipeline stands without manually re-invoking the CLI. This forces noisy shell history, race-prone polling scripts, or context-switching out of the review terminal. The problem affects orchestrator operators, returning Claude Code sessions resuming a paused pipeline, and humans reviewing engineer-agent PRs in a parallel pane.

## Goals
- Provide a `--watch` flag that re-renders the existing status report on a fixed interval, in-place, in a single terminal pane.
- Provide a `--interval SECONDS` flag (default 2, min 1, max 3600, integer-only) to control refresh cadence.
- Keep the existing one-shot invocation (`pipeline-status` with no flags) byte-for-byte identical to the current release on stdout.
- Survive transient absences of `.claude/state/` during a watch session without exiting.
- Exit cleanly on SIGINT (Ctrl+C) with exit code 0 and a trailing newline so the shell prompt lands on its own line.
- Degrade gracefully when stdout is not a TTY: suppress ANSI clear-screen sequences, separate consecutive renders with a single blank line.
- Add zero new runtime dependencies; remain stdlib-only across Linux, macOS, and Windows 10+.
- Keep the watch-loop body and its sleep/clock control independently unit-testable via dependency injection; `unittest` only, no live subprocess spawning.

## Non-Goals
- No filesystem-event watching (no `inotify`, `FSEvents`, `ReadDirectoryChangesW`, `watchdog`).
- No `--json` or other machine-readable output mode (still deferred from the v1 ADR).
- No interactive TUI, no `curses`/`rich`, no keyboard shortcuts beyond Ctrl+C, no scrolling history navigation.
- No daemon, background process, PID file, lockfile, log file, or systemd unit.
- No remote, multi-repo, or networked state inspection.
- No persistence of any kind: `--watch` MUST NOT create, modify, or delete any file.
- No change to the existing inspectors, stage-derivation logic, filled-detection heuristics, exit codes for one-shot mode, or formatting layout of the per-run report.
- No new third-party packaging or PyPI publication work.
- No support for Python < 3.10 (inherited from v1).

## User Stories

> As an orchestrator operator, I want to leave `pipeline-status --watch` running in a terminal pane so that I can see the pipeline advance through gates in real time without re-running the CLI.

Acceptance criteria:
- [ ] Running `pipeline-status --watch` re-renders the same report as the one-shot mode every 2 seconds by default.
- [ ] Each re-render replaces the previous render in place (screen does not scroll under normal TTY conditions).
- [ ] A footer line appears immediately below the report showing the last refresh timestamp, the interval, and the Ctrl+C hint.
- [ ] Pressing Ctrl+C terminates the process with exit code 0 and leaves the shell prompt on its own line.

> As a returning Claude Code session, I want the watch mode to keep running when `.claude/state/` is temporarily missing so that I do not have to restart the watcher every time the orchestrator briefly removes or recreates state files.

Acceptance criteria:
- [ ] If `.claude/state/` is absent at the start of a `--watch` session, the watcher MUST display a placeholder report indicating the directory is missing and continue polling.
- [ ] If `.claude/state/` disappears mid-session, the next render MUST show the missing-directory placeholder and the loop MUST continue.
- [ ] When `.claude/state/` reappears, the next render MUST show the normal report without restart.
- [ ] One-shot mode (no `--watch`) preserves its existing behaviour: stderr error and exit code 2 on missing state directory.

> As a developer scripting around the CLI, I want `--interval` to reject invalid values via argparse so that I get a clear error before any output is produced.

Acceptance criteria:
- [ ] `--interval 0`, `--interval -1`, `--interval 0.5`, `--interval abc`, `--interval 3601` all exit non-zero with an argparse error on stderr.
- [ ] `--interval 1`, `--interval 2`, `--interval 60`, `--interval 3600` are accepted.
- [ ] `--interval` without `--watch` is accepted by argparse but has no effect on the single render (documented behaviour).

> As a CI/log consumer redirecting `pipeline-status --watch` to a file or pipe, I want the output to remain readable so that I can tail logs without ANSI noise.

Acceptance criteria:
- [ ] When `sys.stdout.isatty()` is false, no `\x1b[H\x1b[2J` (or equivalent) clear-screen sequence is emitted.
- [ ] Consecutive renders in non-TTY mode are separated by exactly one blank line.
- [ ] The footer line is still emitted in non-TTY mode.

> As a test author, I want the watch loop factored so that I can unit-test one iteration body and the loop control independently without spawning a subprocess.

Acceptance criteria:
- [ ] A pure function or callable produces the single-iteration output string (report + footer) given an injected clock.
- [ ] The loop driver accepts an injected sleep function and an injected "should-continue" predicate (or iteration count) so tests can run a finite number of iterations deterministically.
- [ ] All existing tests under `tests/test_inspectors.py`, `tests/test_stage.py`, `tests/test_formatting.py` continue to pass unchanged.

## Functional Requirements

1. The CLI MUST accept a new boolean flag `--watch` (no short form). When absent, behaviour MUST be byte-identical to the previous release on stdout for any given filesystem state.
2. The CLI MUST accept a new flag `--interval SECONDS` where `SECONDS` is an integer in the closed range `[1, 3600]`. The default value MUST be `2`.
3. Argparse MUST reject `--interval` values that are non-integer, negative, zero, or greater than 3600 by exiting with a non-zero status and printing an argparse error to stderr. The rejection MUST occur before any inspector runs.
4. `--interval` MAY be passed without `--watch`; in that case it MUST be silently ignored (one-shot render still happens exactly once).
5. When `--watch` is passed and stdout is a TTY, the CLI MUST clear the terminal before each render using the ANSI sequence `\x1b[H\x1b[2J` (cursor home, then erase entire screen).
6. When `--watch` is passed and stdout is NOT a TTY, the CLI MUST NOT emit any clear-screen sequence; instead it MUST emit exactly one blank line between consecutive renders.
7. Each render in watch mode MUST consist of (in order): the same body that one-shot mode prints, then a footer line, then a trailing newline. The footer line MUST match the format `Last refresh: <ISO-8601 local time with offset>  (interval: <N>s, press Ctrl+C to stop)` where `<N>` is the resolved integer interval.
8. The ISO-8601 timestamp in the footer MUST use the local timezone offset and second precision (e.g. `2026-05-23T05:54:36+02:00`), produced via the same mechanism already used for artefact mtime formatting in v1.
9. In watch mode, the loop MUST sleep for `interval` seconds between iterations using `time.sleep`. Any clock drift across iterations is acceptable; the cadence is "at least `interval` seconds between renders", not a strict scheduler.
10. In watch mode, if `.claude/state/` is absent or not a directory at iteration time, the CLI MUST NOT exit. It MUST instead render a placeholder body that clearly states the directory is missing (e.g. `Pipeline Status` header followed by a single line indicating the missing path) plus the standard footer, then continue to the next iteration.
11. In one-shot mode (no `--watch`), behaviour on a missing `.claude/state/` directory MUST remain unchanged: stderr error and exit code 2.
12. The CLI MUST install a SIGINT handler (or rely on the default `KeyboardInterrupt`) such that Ctrl+C in watch mode causes a clean exit with status code 0, after emitting a single trailing newline to stdout so the shell prompt is not concatenated with the last footer.
13. The CLI MUST NOT install any other signal handlers and MUST NOT trap SIGTERM, SIGHUP, or any other signal beyond what is needed for SIGINT cleanup.
14. The CLI MUST NOT create, modify, or delete any file or directory at any point during a watch session. This includes lockfiles, PID files, cache files, and log files.
15. The watch loop body MUST be factored into a function or callable that takes injected dependencies for: (a) the clock used to render the footer timestamp, (b) the sleep function, and (c) a loop-control predicate or iteration count. The factoring MUST allow unit tests to run N >= 1 deterministic iterations without spawning a subprocess and without real wall-clock sleep.
16. The CLI MUST support the cross-platform clear-screen escape on Windows 10+ without requiring `colorama` or any third-party shim. The implementation MAY rely on Windows 10+ Virtual Terminal Processing being enabled by default in modern terminals; no explicit `os.system("")` or kernel32 calls are required.
17. The set of new public symbols exported by the `pipeline_status` package MAY grow but MUST remain confined to the existing package layout (`pipeline_status/{__init__,__main__,inspectors,stage,formatting}.py`) plus at most one new submodule (e.g. `pipeline_status/watch.py`) if needed for clean separation.
18. New unit tests MUST be added under `tests/` using stdlib `unittest` only. The tests MUST cover at minimum: (a) argparse rejection of invalid `--interval` values, (b) acceptance of boundary values 1 and 3600, (c) a single watch iteration producing the expected body+footer string, (d) the loop driver running N iterations deterministically with an injected sleep, (e) TTY vs non-TTY rendering branches, (f) watch-mode behaviour when `.claude/state/` is missing.
19. The `--help` output MUST document `--watch` and `--interval`, including the default value, min/max, and the fact that `--interval` is ignored without `--watch`.
20. The CLI MUST NOT change its current exit-code contract for one-shot mode: 0 on success, 2 on missing `.claude/state/`. Watch mode MUST exit 0 on Ctrl+C and MAY exit non-zero only on unhandled exceptions (which SHOULD propagate with their default Python tracebacks).

## Non-Functional Requirements

- **Performance — render latency**: A single watch-iteration render (inspectors + stage + format + write to stdout) MUST complete in under 200 ms p99 on a state directory totalling ≤ 1 MiB across all five artefacts, on Python 3.10+ on a modern laptop (Linux/macOS/Windows). This bound MUST hold at the default `--interval 2` cadence with no observable lag.
- **Performance — startup overhead**: Time from process start to first render in watch mode MUST be no more than 100 ms slower than the existing one-shot mode start-to-render time.
- **Performance — CPU at idle**: Between renders, the process MUST sleep (no busy wait). CPU usage averaged over a 60-second window at default interval MUST be ≤ 1 % on a single core on a modern laptop.
- **Memory**: Resident memory in watch mode MUST NOT grow without bound across iterations. There MUST be no per-iteration accumulation of inspector results, render strings, or timestamps beyond a single iteration's working set.
- **Dependencies**: No new third-party packages. Only the Python standard library (and the existing `pipeline_status` package internals).
- **Portability**: MUST run on Linux, macOS, and Windows 10+ with Python 3.10+, without `colorama` or any platform-specific wheel.
- **Security**: Read-only filesystem access. MUST NOT open network sockets, MUST NOT spawn subprocesses, MUST NOT exec other binaries, MUST NOT read any file outside `.claude/state/` beyond what v1 already reads.
- **Backwards compatibility**: All existing tests (`tests/test_inspectors.py`, `tests/test_stage.py`, `tests/test_formatting.py`) MUST pass unchanged. One-shot stdout MUST be byte-identical to the v1 release for any given filesystem state.
- **Test execution**: `python -m unittest discover -s tests` MUST complete in under 5 seconds wall time for the full new + existing test suite on a modern laptop. No test MUST call `time.sleep` with a non-zero real-time argument, spawn a subprocess, or depend on signal delivery from another process.
- **Compliance / regulatory**: None.

## Open Questions

1. **Placeholder body for missing `.claude/state/` in watch mode**: The feature request requires that watch mode survive a missing state directory but does not specify the exact placeholder text. Proposed default: a header `Pipeline Status` followed by a single indented line `.claude/state/: MISSING` and the standard footer. Confirm whether a richer placeholder (e.g. listing the five expected artefacts as MISSING) is desired, or whether the proposed minimal placeholder is sufficient.
2. **Footer separator from body**: Should the footer be separated from the report body by a blank line, or appear directly on the line after the last report line? The feature request shows it as a "small footer line under the report" — proposed default: one blank line, then the footer line.
3. **Behaviour when terminal height < report height**: If the terminal is shorter than the report, the ANSI `\x1b[H\x1b[2J` sequence will still clear the screen but the top of the report will scroll off. Is this acceptable for v2, or should the tool detect terminal height and refuse to enter watch mode below a threshold? Proposed default: accept the limitation; document it in `--help`.
4. **`--interval` accepted but unused without `--watch`**: FR-4 silently ignores `--interval` in one-shot mode. Should the CLI instead emit a soft warning on stderr (e.g. `pipeline-status: --interval has no effect without --watch`)? Proposed default: silent ignore, to preserve byte-identical stdout for one-shot mode and avoid stderr noise.
5. **SIGINT timing during a render**: If Ctrl+C arrives mid-render (between inspector reads and stdout flush), is a partially printed report acceptable? Proposed default: yes, accept partial render; the trailing newline is still emitted in the SIGINT handler so the shell prompt is clean. Confirm.

## Assumptions

- The existing package layout from the v1 ADR (`pipeline_status/{__init__,__main__,inspectors,stage,formatting}.py`, `pyproject.toml`, `tests/` with `unittest`) is in place and stable. The watch-mode work extends this layout; it does not refactor it.
- The existing `render(results, stage, colour) -> str` function in `pipeline_status/formatting.py` can be reused unchanged to produce the report body for both one-shot and watch modes. If a minor signature adjustment is needed (e.g. to suppress the trailing newline so the watch footer can be appended cleanly), it is permitted provided one-shot stdout remains byte-identical.
- The existing mtime-formatting helper produces ISO-8601 local time with offset at second precision; the same helper (or its underlying call) is reused to produce the footer's `Last refresh:` timestamp.
- Windows 10+ terminals (Windows Terminal, modern conhost) have Virtual Terminal Processing enabled by default; no explicit enablement call is required for ANSI sequences to render correctly.
- The `NO_COLOR` environment variable convention from v1 affects only the colour/highlight rendering inside the report body; it does NOT suppress the clear-screen escape, which is a layout primitive rather than a colour primitive. (If a future ticket asks for `NO_COLOR` to also disable screen clearing, that is a separate change.)
- Engineers implementing this feature have access to `unittest.mock` and stdlib `io.StringIO` for capturing stdout in tests; no third-party test utilities are needed.
- `tasks.json` and `worktrees.json` schemas and the filled-detection heuristics from v1 are unchanged; this iteration does not touch inspector or stage logic.
- The `--watch` flag is mutually compatible with all current and future flags except where a future flag explicitly conflicts; no such conflict exists today.
