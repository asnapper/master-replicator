"""
Watch-mode driver for the pipeline-status CLI.

Provides a polling loop that re-renders the same artefact-status report
that the one-shot mode prints, plus a footer with the last-refresh
timestamp. Designed for dependency injection so unit tests can drive a
deterministic, finite number of iterations without real ``time.sleep``
or signal delivery between processes.

See the v2 ADR ("ADR: pipeline-status --watch mode") for the design
contract and the five Open Question resolutions this module embodies.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, TextIO

from pipeline_status.formatting import (
    format_artefact_row,
    format_footer,
    format_stage_line,
    render_missing_state,
)
from pipeline_status.inspectors import (
    inspect_adr,
    inspect_feature_request,
    inspect_requirements,
    inspect_tasks,
    inspect_worktrees,
)
from pipeline_status.stage import derive_stage


_CLEAR_SCREEN = "\x1b[H\x1b[2J"


@dataclass(frozen=True)
class WatchConfig:
    """Configuration for one watch session.

    All collaborators are injected so tests can run the loop without real
    time, real signals, or real stdout. In production, only ``state_dir``,
    ``interval``, ``is_tty``, ``use_colour``, and ``stream`` are typically
    overridden; the other fields default to ``time.sleep``,
    ``datetime.now().astimezone``, and ``None`` for ``max_iterations``.
    """
    state_dir: Path
    interval: int
    is_tty: bool
    use_colour: bool
    stream: TextIO
    sleep_fn: Callable[[float], None]
    clock_fn: Callable[[], datetime]
    max_iterations: Optional[int] = None


def _render_body(state_dir: Path, use_colour: bool) -> str:
    """Return the one-iteration body.

    If ``state_dir`` is missing or not a directory, returns the
    placeholder from :func:`formatting.render_missing_state`. Otherwise
    runs the five inspectors, derives the stage, and renders the body in
    the same format used by ``__main__.py``'s one-shot path.

    The returned string ends with a newline so the caller can concatenate
    a blank-line separator and the footer cleanly.
    """
    if not (state_dir.exists() and state_dir.is_dir()):
        return render_missing_state(state_dir)

    results = [
        inspect_feature_request(state_dir / "feature-request.md"),
        inspect_requirements(state_dir / "requirements.md"),
        inspect_adr(state_dir / "adr.md"),
        inspect_tasks(state_dir / "tasks.json"),
        inspect_worktrees(state_dir / "worktrees.json"),
    ]
    artefact_map = {r.name: r for r in results}
    stage = derive_stage(artefact_map)

    lines = ["Pipeline Status", "==============="]
    lines.append("")
    for result in results:
        lines.append(f"  {format_artefact_row(result, colour=use_colour)}")
    lines.append("")
    lines.append(f"  {format_stage_line(stage, colour=use_colour)}")
    return "\n".join(lines) + "\n"


def run_watch_iteration(config: WatchConfig, is_first: bool = True) -> None:
    """Write one watch-mode iteration to ``config.stream``.

    Steps (per the ADR Sequence Diagram, happy path):

    1. If ``config.is_tty`` is True, write the ANSI clear-screen escape
       ``\\x1b[H\\x1b[2J``. Otherwise, if ``is_first`` is False, write a
       single ``\\n`` as the inter-iteration separator.
    2. Compute the body via :func:`_render_body` and write it.
    3. Write ``\\n`` (blank-line separator) + the footer line + ``\\n``.
    4. Flush the stream.

    No file writes; no signal handlers; no subprocesses; no real sleeps.
    """
    if config.is_tty:
        config.stream.write(_CLEAR_SCREEN)
    elif not is_first:
        config.stream.write("\n")

    body = _render_body(config.state_dir, config.use_colour)
    config.stream.write(body)
    config.stream.write("\n")
    config.stream.write(format_footer(config.clock_fn(), config.interval))
    config.stream.write("\n")
    config.stream.flush()


def run_watch(config: WatchConfig) -> int:
    """Drive the watch loop. Returns the intended process exit code (0).

    Iterates up to ``config.max_iterations`` (or indefinitely if it is
    ``None``), calling :func:`run_watch_iteration` and then
    ``config.sleep_fn(config.interval)``. Any ``KeyboardInterrupt``
    raised anywhere in the loop is caught; a single trailing ``\\n`` is
    written to ``config.stream`` and the stream is flushed so the shell
    prompt lands on its own line after Ctrl+C.

    Does not install signal handlers and does not block any signal.
    Default Python behaviour delivers ``KeyboardInterrupt`` from
    ``time.sleep`` on SIGINT across Linux / macOS / Windows.
    """
    is_first = True
    i = 0
    try:
        while config.max_iterations is None or i < config.max_iterations:
            run_watch_iteration(config, is_first=is_first)
            is_first = False
            config.sleep_fn(config.interval)
            i += 1
    except KeyboardInterrupt:
        config.stream.write("\n")
        config.stream.flush()
    return 0
