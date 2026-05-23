# ADR: pipeline-status `--watch` mode

**Status**: Proposed
**Date**: 2026-05-23

## Context

The `pipeline-status` CLI was delivered in v1 as a one-shot, read-only inspector of `.claude/state/`. Its architecture is documented in the v1 ADR (`/home/matt/src/master-replicator/.claude/state/archive/pipeline-status-cli/adr.md`). In short:

- Package layout is `pipeline_status/{__init__,__main__,inspectors,stage,formatting}.py` with `tests/` next to it and a minimal `pyproject.toml` declaring a `hatchling` build backend and a `pipeline-status` console script.
- Runtime dependencies: Python 3.10+ stdlib only (`argparse`, `json`, `pathlib`, `datetime`, `sys`, `os`).
- `__main__.py` is the entry point: it parses args, calls `inspectors.inspect_all(state_dir)`, derives a stage via `stage.derive_stage(results)`, renders with `formatting.render(results, stage, colour)`, prints to stdout, and exits 0 (or stderr-error + exit 2 if `.claude/state/` is missing).
- `formatting.render(...)` is reused as-is wherever possible; the v1 ADR explicitly anticipates minor signature tweaks if needed.
- `NO_COLOR` and `sys.stdout.isatty()` govern colour rendering; nothing else in v1 cares about TTY detection.

This v2 increment adds a live-refresh mode (`--watch`) and a `--interval SECONDS` cadence flag, without touching inspector, stage, or formatting semantics. The one-shot stdout must remain byte-identical for any given filesystem state. No new third-party dependencies; no `curses`, `rich`, `watchdog`, `asyncio`, `threading`, or `multiprocessing`. The mechanism is a synchronous polling loop with `time.sleep` and a `KeyboardInterrupt`-based SIGINT path.

The five Open Questions in `requirements.md` are resolved here by adopting each proposed default; each is called out in the relevant Considered Options sub-section below.

## Decision Drivers

- **Stdlib-only, boring tech**: `time.sleep` + `KeyboardInterrupt` + ANSI escape `\x1b[H\x1b[2J` is the smallest viable surface. No new modules outside the standard library.
- **Byte-identical one-shot stdout**: the v1 contract is frozen. The watch implementation must not perturb the one-shot rendering path.
- **Testability without subprocesses or real sleeps**: NFR mandates `python -m unittest discover -s tests` completes in under 5 s and no test may sleep with a non-zero real-time argument. The watch loop must therefore expose injectable seams for `sleep`, `clock`, and an iteration-count / continue-predicate.
- **Cross-platform ANSI**: Windows 10+ terminals have Virtual Terminal Processing on by default; no `colorama`, no `os.system("")`, no kernel32 calls.
- **TTY-aware rendering**: clear-screen sequences must be suppressed when stdout is not a TTY so pipes and log files stay readable.
- **Resilience to transient state**: missing `.claude/state/` mid-loop must not kill the watcher; one-shot semantics on missing state must NOT change.
- **Read-only filesystem access**: no lockfile, no PID file, no log file, no cache.
- **Minimal new public surface**: extend the existing five-module package with at most one new submodule (`pipeline_status/watch.py`) to keep the audit boundary tight.

## Considered Options

### Decision 1: Refresh mechanism — polling vs. filesystem events

- **Option A: Synchronous polling with `time.sleep(interval)`**
  - Pros: zero dependencies; identical behaviour on Linux/macOS/Windows; the polling cadence (≥ 1 s) is so coarse that latency is irrelevant; trivially testable with an injected sleep function.
  - Cons: tiny wasted work between real state changes; not "event-driven".
- **Option B: Filesystem events (`inotify` / `FSEvents` / `ReadDirectoryChangesW`)**
  - Pros: instant response to state changes.
  - Cons: explicitly forbidden by the feature request and requirements (no `watchdog`, no platform-specific event APIs); cross-platform implementation in stdlib is non-trivial.
- **Chosen**: Option A — synchronous polling. The requirements rule out Option B explicitly; polling at a 2 s default cadence has negligible CPU overhead and zero dependency cost.

### Decision 2: Loop control — `while True` vs. dependency-injected iteration

- **Option A: `while True: render(); time.sleep(interval)` directly in `__main__.py`**
  - Pros: minimal code.
  - Cons: violates the testability NFR — tests would have to monkeypatch `time.sleep` and break out of an infinite loop with an exception; brittle.
- **Option B: Extract `run_watch(...)` into `pipeline_status/watch.py` with injected `sleep_fn`, `clock_fn`, and `max_iterations` (or `should_continue` predicate)**
  - Pros: drives a finite number of deterministic iterations from tests with `sleep_fn=lambda _: None`; clock is mockable for stable footer timestamps in assertions; no subprocess; no real wall-clock sleep.
  - Cons: one extra module file; slightly more wiring.
- **Chosen**: Option B — see "Testability seam" below for the exact signature. This is the testability seam the requirements demand (FR-15, NFR test-execution clause).

### Decision 3: SIGINT handling — explicit `signal.signal` vs. `KeyboardInterrupt`

- **Option A: Install a custom `signal.signal(signal.SIGINT, handler)`**
  - Pros: explicit control over the exit path.
  - Cons: Python's default already raises `KeyboardInterrupt` on SIGINT on all three platforms; installing a handler complicates Windows behaviour (where `signal.SIGINT` is delivered on the main thread only) and is unnecessary; FR-13 forbids trapping any other signal, and reusing the default keeps the surface minimal.
- **Option B: Catch `KeyboardInterrupt` in `__main__.main()` around `run_watch(...)`**
  - Pros: stdlib-idiomatic; works identically on Linux/macOS/Windows; trivially testable (raise the exception in a fake `sleep_fn`); satisfies FR-12 and FR-13.
  - Cons: a partial render is possible if Ctrl+C lands mid-`print` — acceptable per Open Question 5 default.
- **Chosen**: Option B (Open Question 5 resolved → accept partial render; emit a single trailing `\n` to stdout in the `except KeyboardInterrupt:` arm so the shell prompt lands on its own line).

### Decision 4: Clear-screen mechanism on TTY

- **Option A: ANSI `\x1b[H\x1b[2J` (cursor home, then erase entire screen) written to stdout**
  - Pros: stdlib-only; works on Linux, macOS, Windows 10+ (VT processing on by default); FR-5 mandates this exact sequence.
  - Cons: terminals with height < report height will still scroll the top off — accepted per Open Question 3 default; documented in `--help` epilog.
- **Option B: `os.system("cls" if os.name == "nt" else "clear")`**
  - Cons: spawns a subprocess, violates NFR "MUST NOT spawn subprocesses"; introduces a fork on POSIX.
- **Chosen**: Option A. Open Question 3 resolved → accept the terminal-height limitation; mention it in the `--help` epilog ("clear-screen relies on the terminal supporting ANSI; small terminals will scroll").

### Decision 5: Non-TTY rendering of consecutive renders

- **Option A: Suppress `\x1b[H\x1b[2J` when `not sys.stdout.isatty()`; separate renders with exactly one blank line**
  - Pros: pipes, redirections, and `tee` produce clean, greppable output; FR-6 mandates this; aligns with `pipeline-status --watch | tee log.txt` use case.
  - Cons: none material.
- **Option B: Always emit the clear-screen sequence regardless of TTY**
  - Cons: pollutes logs with `\x1b[H\x1b[2J` literal bytes; FR-6 forbids it.
- **Chosen**: Option A.

### Decision 6: Missing `.claude/state/` behaviour in watch mode

- **Option A: Render a minimal placeholder body (header + one indented `.claude/state/: MISSING` line) plus the standard footer, then continue the loop**
  - Pros: keeps the screen alive while the orchestrator initialises or briefly removes state; matches FR-10 and the user story for returning Claude Code sessions; minimal new formatting code.
  - Cons: the placeholder differs from the one-shot stderr error — but that is the whole point.
- **Option B: Render the full v1-style report with every artefact line marked MISSING (i.e. run inspectors against a non-existent `state_dir`)**
  - Pros: richer placeholder.
  - Cons: requires the inspectors to gracefully handle a non-existent parent dir, expanding their contract beyond v1.
- **Chosen**: Option A (Open Question 1 resolved → minimal placeholder is sufficient). The placeholder body is generated by a new `formatting.render_missing_state(state_dir: Path) -> str` helper (or equivalent inline in `watch.py`) and contains exactly:

  ```
  Pipeline Status
  ===============

    .claude/state/: MISSING
  ```

  followed by the standard footer line written by the watch loop. One-shot mode (FR-11) continues to use the existing stderr error + exit(2) path unchanged.

### Decision 7: Footer separator from body

- **Option A: One blank line between the body's last line and the footer line**
  - Pros: visually separates report from metadata; FR-7 reads naturally as "body, blank line, footer, trailing newline"; matches the feature request wording ("a small footer line under the report"); Open Question 2 default.
  - Cons: none.
- **Option B: Footer directly on the line immediately after the report**
  - Cons: visually crowded; harder to scan.
- **Chosen**: Option A (Open Question 2 resolved). The exact emitted sequence per iteration is: `<body><\n><\n><footer><\n>` (where `<body>` is the existing one-shot render which already ends in a newline — see "Migration steps" below for the precise contract).

### Decision 8: `--interval` without `--watch`

- **Option A: Silent ignore (no stderr, no behaviour change)**
  - Pros: preserves byte-identical one-shot stdout (the v1 contract); avoids surprising stderr noise for scripts; documented in `--help`; Open Question 4 default.
  - Cons: a user passing `--interval 5` and seeing a single render might be momentarily confused — mitigated by the `--help` text.
- **Option B: Emit a soft warning to stderr**
  - Cons: violates the "byte-identical one-shot stdout" constraint loosely (stderr is not stdout, but the noise is still a behavioural change for scripts capturing stderr).
- **Chosen**: Option A (Open Question 4 resolved). `--help` text for `--interval` ends with "(ignored without --watch)".

### Decision 9: SIGINT mid-render

- **Option A: Accept partial render; rely on Python's default `KeyboardInterrupt` arriving wherever the interpreter happens to be; emit a single `\n` to stdout in the `except KeyboardInterrupt:` arm so the shell prompt is clean**
  - Pros: simplest; FR-12 satisfied; Open Question 5 default; no signal-masking gymnastics required.
  - Cons: a single render may print only halfway — acceptable for v2.
- **Option B: Mask SIGINT during render with `signal.pthread_sigmask` / `signal.set_wakeup_fd`**
  - Cons: not portable to Windows; complex; not justified by the requirement.
- **Chosen**: Option A (Open Question 5 resolved).

### Decision 10: Footer timestamp source

- **Option A: Reuse the v1 mtime-formatting helper to produce ISO-8601 local time with offset at second precision**
  - Pros: single helper, single style across the codebase; FR-8 mandates the same mechanism; matches the format already shown in v1 sample output (`2026-05-20T14:32:01+02:00`).
  - Cons: the v1 helper takes an mtime float; the footer takes a "now" value. Either (a) expose the underlying formatter as a public helper that accepts a `datetime` and call it from `watch.py` with `clock_fn()`, or (b) make `clock_fn` return an ISO-8601 string directly.
- **Option B: Call `datetime.now().astimezone().isoformat(timespec="seconds")` inline in `watch.py`**
  - Cons: duplicates the v1 formatting style — fragile if the format ever changes.
- **Chosen**: Option A, sub-option (a). A new public helper `formatting.format_local_iso(dt: datetime) -> str` is extracted (the v1 mtime-formatting code is refactored to call it internally, preserving its current output byte-for-byte). `watch.py` calls `format_local_iso(clock_fn())`. The injected `clock_fn` is `Callable[[], datetime]` returning a timezone-aware local-time `datetime`.

## Architecture

### Component Diagram (text/ASCII)

```
repo root/
├── pipeline_status/
│   ├── __init__.py          # unchanged: __version__
│   ├── __main__.py          # MODIFIED: argparse gains --watch + --interval;
│   │                        #   dispatches to run_watch() or one-shot path
│   ├── inspectors.py        # UNCHANGED
│   ├── stage.py             # UNCHANGED
│   ├── formatting.py        # MODIFIED: extract format_local_iso() helper;
│   │                        #   add render_missing_state() helper;
│   │                        #   add format_footer() helper.
│   │                        #   Existing render(...) stays byte-identical
│   │                        #   for the one-shot path.
│   └── watch.py             # NEW: run_watch(...) and run_watch_iteration(...)
├── tests/
│   ├── test_inspectors.py   # UNCHANGED
│   ├── test_stage.py        # UNCHANGED
│   ├── test_formatting.py   # UNCHANGED (existing assertions hold);
│   │                        #   may grow with tests for new helpers
│   ├── test_watch.py        # NEW: covers argparse, run_watch_iteration,
│   │                        #   loop driver, TTY vs non-TTY,
│   │                        #   missing-state behaviour
│   └── test_cli.py          # NEW (optional): end-to-end argparse smoke tests
└── pyproject.toml           # UNCHANGED — same console script entry point
```

Flow at runtime:

```
                +-------------------+
                |   __main__.main   |
                |  parse_args()     |
                +---------+---------+
                          |
              --watch? ---+--- no ---> existing one-shot path (UNCHANGED)
                          |              inspect_all -> derive_stage
                          |              -> render -> print -> exit(0|2)
                          | yes
                          v
                +-------------------+
                |  watch.run_watch  |
                |  (sleep_fn,       |
                |   clock_fn,       |
                |   max_iterations) |
                +---------+---------+
                          |
                          | loop (deterministic in tests)
                          v
                +-------------------+
                | run_watch_        |
                | iteration(...)    |  <-- pure-ish: deps injected
                |   - check state   |
                |   - clear screen  |
                |     OR blank line |
                |   - render body   |
                |     (or missing)  |
                |   - render footer |
                |   - write stdout  |
                +---------+---------+
                          |
                          v
                +-------------------+
                |  sleep_fn(interval)|
                +---------+---------+
                          |
                          v
                  KeyboardInterrupt? --- yes ---> print("\n"); exit(0)
                          | no
                          v
                       (loop)
```

### Data Model

No new persistent data is introduced. One new internal config object is added:

```python
# pipeline_status/watch.py
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, TextIO

@dataclass(frozen=True)
class WatchConfig:
    state_dir: Path
    interval: int                         # validated in [1, 3600]
    is_tty: bool                          # True if stdout is a TTY
    use_colour: bool                      # passed through to formatting.render
    stream: TextIO                        # stdout (or test buffer)
    sleep_fn: Callable[[float], None]     # default: time.sleep
    clock_fn: Callable[[], datetime]      # default: lambda: datetime.now().astimezone()
    max_iterations: Optional[int]         # default: None (infinite); test override: small int
```

No file format changes. No new on-disk artefacts. No new fields added to `ArtefactResult` from the v1 ADR.

### API Contracts

#### CLI surface (additions to v1)

```
Usage: pipeline-status [OPTIONS]

  Inspect the .claude/state/ pipeline artefacts and report current stage.

Options:
  --watch                Continuously re-render the status report. Polls
                         every --interval seconds until Ctrl+C.
  --interval SECONDS     Refresh cadence in integer seconds (default: 2,
                         min: 1, max: 3600). Ignored without --watch.
  --help                 Show this message and exit.

Exit codes:
  0   Successful inspection (one-shot) or clean Ctrl+C exit (watch).
  2   .claude/state/ directory is absent or not a directory (one-shot only;
      watch mode tolerates a missing state directory and continues).

Environment variables:
  NO_COLOR  When set (any value), suppress ANSI colour output in the
            report body. Does NOT affect the clear-screen escape used
            by --watch (which is a layout primitive, not a colour primitive).

Notes:
  --watch uses the ANSI sequence \x1b[H\x1b[2J to clear the screen. On
  terminals shorter than the report, the top of the report will scroll
  off; this is accepted for v2. When stdout is not a TTY, the clear-
  screen sequence is suppressed and consecutive renders are separated
  by exactly one blank line.
```

#### Argparse contract

```python
# pipeline_status/__main__.py (excerpt)
parser = argparse.ArgumentParser(prog="pipeline-status", description="...")
parser.add_argument(
    "--watch",
    action="store_true",
    help="Continuously re-render the status report (Ctrl+C to stop).",
)
parser.add_argument(
    "--interval",
    type=_interval_type,            # custom converter, see below
    default=2,
    metavar="SECONDS",
    help="Refresh cadence in integer seconds (default: 2, min: 1, max: 3600). "
         "Ignored without --watch.",
)

def _interval_type(raw: str) -> int:
    """Custom argparse converter: accept only integers in [1, 3600]."""
    try:
        value = int(raw)                            # rejects '0.5', 'abc'
        if str(value) != raw.lstrip("+"):           # rejects '0.5' specifically
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--interval must be an integer in [1, 3600], got {raw!r}"
        )
    if value < 1 or value > 3600:
        raise argparse.ArgumentTypeError(
            f"--interval must be an integer in [1, 3600], got {value}"
        )
    return value
```

Argparse rejection produces exit code 2 (argparse default) with a clear error to stderr; this rejection happens before any inspector runs, satisfying FR-3. Note: argparse's default exit code on parse error is 2, which coincidentally matches v1's missing-state exit code — both are "user / environment error", so the overload is acceptable.

#### New public symbols

```python
# pipeline_status/watch.py
def run_watch_iteration(config: WatchConfig) -> None:
    """
    Execute exactly one watch-mode iteration:
      1. If config.is_tty, write '\x1b[H\x1b[2J' to config.stream.
         Otherwise, if this is not the first iteration, write '\n' to
         config.stream (the blank-line separator).
      2. If config.state_dir is missing or not a directory, write the
         missing-state placeholder body to config.stream.
         Otherwise, call inspectors.inspect_all(config.state_dir),
         stage.derive_stage(results), formatting.render(results, stage,
         config.use_colour) and write the result to config.stream.
      3. Write '\n' (blank line separator), then the footer line, then
         '\n' to config.stream.
      4. config.stream.flush().
    Pure with respect to its inputs (config); does not sleep; does not
    install signal handlers; does not touch the filesystem beyond reading
    config.state_dir.
    """

def run_watch(config: WatchConfig) -> int:
    """
    Drive the watch loop. Returns the intended exit code (0).
    For each iteration up to (config.max_iterations or infinity):
      1. Call run_watch_iteration(config_for_this_iteration).
      2. config.sleep_fn(config.interval).
    A KeyboardInterrupt anywhere in the loop is caught: a single '\n' is
    written to config.stream, the stream is flushed, and 0 is returned.
    Note: 'first iteration' state needed by run_watch_iteration for the
    non-TTY blank-line separator is tracked inside run_watch (e.g. by
    passing an explicit `is_first: bool` argument to a private helper, or
    by maintaining the separator state in run_watch itself and prepending
    it before each call except the first).
    """
```

```python
# pipeline_status/formatting.py (new helpers)
def format_local_iso(dt: datetime) -> str:
    """
    Format `dt` as ISO-8601 local time with timezone offset at second
    precision, e.g. '2026-05-23T05:54:36+02:00'. `dt` MUST be timezone-
    aware. Used by both v1 mtime formatting (refactored internally) and
    the v2 watch footer.
    """

def format_footer(now: datetime, interval: int) -> str:
    """
    Return the watch footer line (no trailing newline), e.g.
    'Last refresh: 2026-05-23T05:54:36+02:00  (interval: 2s, press Ctrl+C to stop)'.
    `now` MUST be timezone-aware; `interval` MUST be a positive int.
    """

def render_missing_state(state_dir: Path) -> str:
    """
    Return the placeholder body for watch mode when state_dir is absent
    or not a directory. Format:

      Pipeline Status
      ===============

        .claude/state/: MISSING

    Trailing newline included to match the contract of the existing
    formatting.render(...) function (which v1 callers concatenate with
    a print() that adds a final newline; preserve byte-identical
    behaviour for the one-shot path).
    """
```

#### Modified one-shot path

`formatting.render(...)`'s observable output is **unchanged**. Internally it now calls `format_local_iso(...)` for the mtime column; the produced string MUST be byte-identical to v1 for the same `ArtefactResult` inputs. The existing `tests/test_formatting.py` assertions catch any regression.

### Sequence Diagram (text)

**Happy path — watch loop:**

```
User: $ pipeline-status --watch --interval 2

__main__.main()
  parse_args()                 ; argparse validates --interval (1..3600 int)
  state_dir = repo_root / ".claude" / "state"
  is_tty = sys.stdout.isatty()
  use_colour = formatting.use_colour()      ; honours NO_COLOR + isatty
  config = WatchConfig(
      state_dir=state_dir, interval=2, is_tty=is_tty,
      use_colour=use_colour, stream=sys.stdout,
      sleep_fn=time.sleep,
      clock_fn=lambda: datetime.now().astimezone(),
      max_iterations=None,
  )
  exit_code = watch.run_watch(config)

watch.run_watch(config):
  is_first = True
  i = 0
  try:
    while config.max_iterations is None or i < config.max_iterations:
      run_watch_iteration_internal(config, is_first)
      is_first = False
      config.sleep_fn(config.interval)
      i += 1
  except KeyboardInterrupt:
    config.stream.write("\n")
    config.stream.flush()
  return 0

run_watch_iteration_internal(config, is_first):
  if config.is_tty:
    config.stream.write("\x1b[H\x1b[2J")
  elif not is_first:
    config.stream.write("\n")

  if not (config.state_dir.exists() and config.state_dir.is_dir()):
    body = formatting.render_missing_state(config.state_dir)
  else:
    results = inspectors.inspect_all(config.state_dir)
    stage = stage_mod.derive_stage(results)
    body = formatting.render(results, stage, config.use_colour)

  config.stream.write(body)                  ; body ends with '\n'
  config.stream.write("\n")                  ; blank line separator
  config.stream.write(formatting.format_footer(config.clock_fn(), config.interval))
  config.stream.write("\n")
  config.stream.flush()

sys.exit(exit_code)
```

**Ctrl+C path:**

```
User: <Ctrl+C> during sleep_fn(2)

  Python's default SIGINT handler raises KeyboardInterrupt in the main
  thread, interrupting time.sleep(2).
  -> run_watch's `except KeyboardInterrupt` arm fires.
  -> stream.write("\n"); stream.flush(); return 0.
  -> __main__.main() returns 0; process exits 0.
  -> Shell prompt appears on its own line.
```

**Ctrl+C mid-render:** same path; the iteration may be partially flushed; the trailing `\n` from the exception handler still lands cleanly. Accepted per Decision 9.

**Missing state mid-loop:** `run_watch_iteration_internal` re-checks `config.state_dir` on every iteration; transient absence yields the placeholder, transient reappearance yields the full report on the very next iteration. No restart required.

## Implementation Notes

### Modules touched

- `pipeline_status/__main__.py`: add `--watch` and `--interval` to argparse; add `_interval_type` validator; branch on `args.watch` to call either the existing one-shot path (unchanged) or `watch.run_watch(...)`.
- `pipeline_status/formatting.py`:
  - Extract `format_local_iso(dt: datetime) -> str` from the existing mtime formatter. The existing mtime formatter now reads `format_local_iso(datetime.fromtimestamp(mtime, tz=datetime.now().astimezone().tzinfo))` (or equivalent). Output MUST be byte-identical to v1.
  - Add `format_footer(now: datetime, interval: int) -> str`.
  - Add `render_missing_state(state_dir: Path) -> str`.
  - `render(...)` signature unchanged; output byte-identical for v1 inputs.
- `pipeline_status/watch.py` (NEW): export `WatchConfig`, `run_watch`, `run_watch_iteration` (or keep `run_watch_iteration` private and only test `run_watch` with `max_iterations`; both are acceptable as long as the testability seam holds).
- `pipeline_status/inspectors.py`, `pipeline_status/stage.py`: **untouched**.
- `pipeline_status/__init__.py`: optionally re-export `run_watch` at the package level; not required.

### New modules

Exactly one new submodule: `pipeline_status/watch.py`. This is the maximum permitted by FR-17.

### Migration steps

1. Refactor `formatting.format_local_iso(...)` out of the existing mtime path; run `tests/test_formatting.py` to confirm byte-identical output.
2. Add `format_footer(...)` and `render_missing_state(...)` to `formatting.py`; cover with new unit tests in `tests/test_watch.py` (or `tests/test_formatting.py` — either is acceptable).
3. Add `pipeline_status/watch.py` with `WatchConfig`, `run_watch`, and the iteration internal helper.
4. Add argparse changes in `__main__.py`; route to `run_watch` when `args.watch` is true.
5. Add `tests/test_watch.py` covering FR-15, FR-3 (argparse), FR-5/6 (TTY vs non-TTY), FR-10 (missing state), FR-12 (Ctrl+C — by raising `KeyboardInterrupt` from a fake `sleep_fn`), and the byte-identical-one-shot regression (assert `formatting.render(...)` output unchanged).
6. Confirm `python -m unittest discover -s tests` runs in under 5 s with no real sleeps. The default `sleep_fn=time.sleep` is only used in production; tests use `sleep_fn=lambda _: None` or `sleep_fn=MagicMock()`.

### Testability seam (called out per the brief)

The seam is **dependency injection of `sleep_fn`, `clock_fn`, and `max_iterations` into `run_watch` via a `WatchConfig` dataclass**, combined with **a stream parameter** (`config.stream`, an `io.TextIO`-compatible object) so tests can capture output into `io.StringIO`. This satisfies the three independently-required test scenarios:

- **One-iteration body+footer assertion**: construct `WatchConfig(..., max_iterations=1, clock_fn=lambda: FIXED_DT, sleep_fn=lambda _: None, stream=io.StringIO())`, call `run_watch`, assert the buffer contents match a known string.
- **N-iteration deterministic loop**: same as above with `max_iterations=N`; assert the buffer contains exactly N body+footer blocks separated correctly.
- **Ctrl+C exit**: pass `sleep_fn=lambda _: (_ for _ in ()).throw(KeyboardInterrupt())` (or a `MagicMock(side_effect=KeyboardInterrupt)`); assert `run_watch` returns 0 and the buffer ends with a `\n`.
- **TTY vs non-TTY**: flip `config.is_tty`; assert presence/absence of `\x1b[H\x1b[2J` and the blank-line separator.
- **Missing state**: pass a `state_dir` that does not exist; assert the placeholder body appears and the loop does NOT raise.

No subprocess. No real `time.sleep`. No signal delivery from another process. All assertions are pure string comparisons against `StringIO.getvalue()`.

### Known edge cases

1. **ANSI clear on non-TTY**: handled by Decision 5; suppressed when `not sys.stdout.isatty()`. Tests cover both branches by setting `config.is_tty` directly.
2. **Mid-render SIGINT**: accepted per Decision 9; the trailing `\n` in the `except KeyboardInterrupt:` arm ensures the shell prompt is on its own line even after a partial render.
3. **Missing state dir mid-loop**: `run_watch_iteration_internal` re-evaluates `state_dir.exists() and state_dir.is_dir()` on every iteration; no state caching across iterations.
4. **State dir replaced with a regular file**: the `is_dir()` check fails → placeholder body. Same code path as "missing".
5. **Permission error reading `.claude/state/`**: not explicitly required by FR-10. The inspectors already handle per-file `FileNotFoundError`; a `PermissionError` on the parent directory will propagate as an uncaught exception, producing a Python traceback and a non-zero exit (FR-20 allows this). Engineers MAY add a defensive `try/except OSError` around the dir check if trivial; this is not mandatory for v2.
6. **`stream.flush()` failure** (e.g. `BrokenPipeError` when piped to `head`): allow the exception to propagate; Python's default `BrokenPipeError` handling is acceptable for a CLI tool. Not a required test case.
7. **Windows + non-VT terminal (legacy `cmd.exe` without VT)**: `\x1b[H\x1b[2J` may render as literal characters. The requirements (and assumption #4 in `requirements.md`) state that Windows 10+ terminals have VT processing enabled by default; legacy `cmd.exe` is explicitly out of scope. No `colorama` shim is added.
8. **`--interval 1` with a slow inspector**: at interval=1 s and a worst-case 200 ms render (NFR p99), the effective cadence is ~1.2 s. FR-9 explicitly accepts "at least `interval` seconds between renders"; no compensation logic is needed.
9. **Footer timestamp during DST transition**: `datetime.now().astimezone()` returns the local-time offset at call time; an offset change between iterations is rendered as observed. Acceptable; no special handling.
10. **`NO_COLOR` and clear-screen**: per Assumption 5 in `requirements.md`, `NO_COLOR` does NOT suppress the clear-screen escape. Only the body's colour rendering inside `formatting.render(...)` is affected.
11. **Resident memory across long runs**: each iteration constructs and discards its `ArtefactResult` list and render strings; no caching. NFR-memory satisfied by construction.

### Running tests

```bash
python -m unittest discover -s tests
```

Expected: existing tests pass byte-identically. New tests in `tests/test_watch.py` (and any additions to `tests/test_formatting.py`) bring total wall time under the 5 s NFR budget.

## Consequences

**Easier after this change:**

- A developer or returning Claude Code session can keep `pipeline-status --watch` running in a terminal pane and watch the pipeline advance through gates without re-invoking the CLI.
- The watch-loop seam (`WatchConfig` with injected `sleep_fn` / `clock_fn` / `max_iterations`) is reusable if future iterations add more long-running modes (e.g. a `--follow` mode for a specific artefact).
- `format_local_iso(...)` is now a public helper; future features that need ISO-8601 local-time formatting (e.g. JSON output mode) will reuse it.
- Non-TTY output is now first-class: piping `pipeline-status --watch` to `tee` or `grep` produces clean, deterministic logs.

**Harder or more complex:**

- The package now has six modules instead of five; engineers must locate watch-loop logic in `watch.py` rather than `__main__.py`.
- Argparse validation has grown a custom converter (`_interval_type`); test coverage of boundary cases (`0`, `3601`, `-1`, `0.5`, `abc`) is required to prevent regressions.
- The placeholder body in watch mode introduces a second "rendering style" alongside `formatting.render(...)`. Future formatting changes must remember to update both.

**Technical debt introduced:**

- No JSON output mode in v2 (still deferred from v1).
- No filesystem-event watching; polling at 1 s minimum cadence is a coarse view of state changes. If a future user reports unacceptable latency, the architecture supports replacing the `sleep_fn` driver with a `select`/`poll`-based one without changing the public CLI; the testability seam holds.
- The placeholder body is minimal (one MISSING line). If users request a richer placeholder (per Open Question 1 alt), it is a localised change to `render_missing_state(...)` only.
- `_interval_type` overlap with argparse's exit code 2 means a user passing a bad `--interval` and a user missing `.claude/state/` both exit 2; scripts cannot distinguish them via exit code alone. Acceptable; documented behaviour.

## Out of Scope

- Filesystem-event watching (`inotify`, `FSEvents`, `ReadDirectoryChangesW`, `watchdog`). Explicitly excluded.
- `--json` machine-readable output. Still deferred from v1.
- Interactive TUI, scrolling history, keyboard shortcuts beyond Ctrl+C. Explicitly excluded.
- Daemon mode, systemd unit, PID file, lockfile, log file. Explicitly excluded.
- Remote, multi-repo, or networked state inspection. Explicitly excluded.
- Filesystem writes of any kind during a watch session. Explicitly excluded.
- Changes to inspector logic, stage derivation, filled-detection heuristics, or one-shot exit codes. Explicitly excluded.
- Legacy Windows `cmd.exe` without Virtual Terminal Processing. Excluded per assumption in requirements.
- `colorama` or any other third-party Windows-ANSI shim. Excluded.
- Python versions below 3.10. Excluded (inherited from v1).
- A soft stderr warning for `--interval` without `--watch`. Resolved silent-ignore (Open Question 4).
- Detection of terminal height shorter than the report. Accepted limitation (Open Question 3); documented in `--help` epilog.
- A richer "missing state" placeholder that enumerates the five expected artefacts. Resolved minimal placeholder (Open Question 1).
- SIGINT masking during a render. Resolved partial render accepted (Open Question 5).
