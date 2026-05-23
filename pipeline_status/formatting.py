import os
import sys
from datetime import datetime
from pathlib import Path

from pipeline_status.inspectors import ArtefactResult


def use_colour() -> bool:
    """True when sys.stdout.isatty() AND 'NO_COLOR' not in os.environ."""
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def colorize(text: str, colour: str) -> str:
    """Wrap in ANSI if colour enabled. Supported: green, yellow, red, cyan, bold."""
    if not use_colour():
        return text
    codes = {
        "green": "\033[32m",
        "yellow": "\033[33m",
        "red": "\033[31m",
        "cyan": "\033[36m",
        "bold": "\033[1m",
    }
    return f"{codes[colour]}{text}\033[0m" if colour in codes else text


def format_artefact_row(result: ArtefactResult, colour: bool | None = None) -> str:
    """One display line: name (padded), EXISTS/MISSING, FILLED/EMPTY, mtime, optional task counts."""
    # Determine whether to use colour
    use_c = use_colour() if colour is None else colour

    def _c(text: str, col: str) -> str:
        if not use_c:
            return text
        codes = {
            "green": "\033[32m",
            "yellow": "\033[33m",
            "red": "\033[31m",
            "cyan": "\033[36m",
            "bold": "\033[1m",
        }
        return f"{codes[col]}{text}\033[0m" if col in codes else text

    name_col = f"  {result.name:<22}"

    if result.exists:
        exists_str = _c("EXISTS ", "green")
    else:
        exists_str = _c("MISSING", "red")

    if result.filled:
        filled_str = _c("FILLED", "green")
    else:
        filled_str = _c("EMPTY ", "yellow")

    mtime_str = result.mtime_iso if result.mtime_iso else "\u2014"

    row = f"{name_col}  {exists_str}  {filled_str}  {mtime_str}"

    # Append task counts if extra["total"] is present
    if "total" in result.extra:
        total = result.extra["total"]
        completed = result.extra.get("completed", 0)
        row += f"  ({completed}/{total} tasks done)"

    # Append error if set
    if result.error:
        row += f"  [ERROR: {result.error}]"

    return row


def format_stage_line(stage: str, colour: bool | None = None) -> str:
    """Return "Stage: {stage}" with optional bold."""
    use_c = use_colour() if colour is None else colour

    label = "Stage: "
    if use_c:
        text = f"\033[1m{label}{stage}\033[0m"
    else:
        text = f"{label}{stage}"
    return text


def format_report(results: list[ArtefactResult], stage: str, colour: bool | None = None) -> str:
    """Full report: header, rows, blank line, stage line."""
    use_c = use_colour() if colour is None else colour

    def _bold(text: str) -> str:
        if use_c:
            return f"\033[1m{text}\033[0m"
        return text

    lines = []
    lines.append(_bold("Pipeline Artefact Status"))
    lines.append(_bold("-" * 60))
    for result in results:
        lines.append(format_artefact_row(result, colour=use_c))
    lines.append("")
    lines.append(format_stage_line(stage, colour=use_c))
    return "\n".join(lines)


def format_local_iso(dt: datetime) -> str:
    """Format ``dt`` as ISO-8601 local time with TZ offset at second precision.

    Mirrors the v1 mtime formatter in ``inspectors._mtime_iso`` (which uses
    ``datetime.fromtimestamp(ts, tz=tz).isoformat(timespec="seconds")``). The
    helper is exposed here so v2 watch-mode footers and any future ISO-8601
    consumer share a single formatting style.

    Raises:
        ValueError: if ``dt`` is timezone-naive.
    """
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError("format_local_iso requires a timezone-aware datetime")
    return dt.isoformat(timespec="seconds")


def format_footer(now: datetime, interval: int) -> str:
    """Return the watch-mode footer line (no trailing newline).

    Format::

        Last refresh: 2026-05-23T05:54:36+02:00  (interval: 2s, press Ctrl+C to stop)

    Note the two spaces between the timestamp and ``(interval:`` per the ADR.

    Raises:
        ValueError: if ``now`` is timezone-naive or ``interval`` is not a
            positive integer.
    """
    if not isinstance(interval, int) or isinstance(interval, bool) or interval < 1:
        raise ValueError(f"format_footer requires a positive int interval, got {interval!r}")
    return f"Last refresh: {format_local_iso(now)}  (interval: {interval}s, press Ctrl+C to stop)"


def render_missing_state(state_dir: Path) -> str:
    """Return the watch-mode placeholder body when ``state_dir`` is absent.

    Format (with trailing newline)::

        Pipeline Status
        ===============

          .claude/state/: MISSING

    The ``state_dir`` argument is currently unused at the rendering level
    (the placeholder is a fixed string by design — see ADR Decision 6). It is
    accepted for symmetry with future variants that may want to surface the
    inspected path.
    """
    _ = state_dir  # reserved for future variants; see ADR Decision 6
    return (
        "Pipeline Status\n"
        "===============\n"
        "\n"
        "  .claude/state/: MISSING\n"
    )
