import os
import sys
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
