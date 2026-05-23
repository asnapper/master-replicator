"""
Render a DiffReport as the per-artefact summary + footer.

Public symbols:
    format_diff_report(report) -> str    (multi-line; ends with '\\n')

Stdlib only. Does NOT import pipeline_status.archive, pipeline_status.history,
pipeline_status.format_history, or pipeline_status.diff_archives at runtime.
The DiffReport dataclass is referenced under TYPE_CHECKING only to keep
test-time imports clean and parallel-safe.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for static type-checking
    from pipeline_status.diff_archives import DiffReport


def format_diff_report(report: "DiffReport") -> str:
    """Render a DiffReport as the per-artefact summary + footer.

    Output format (ADR Decision 6):

        <one '{glyph} {basename}\\n' per ArtefactDiff with emit_row=True,
         in input order (which is canonical TRACKED_ARTEFACTS order)>
        \\n
        f'Diff: {report.added} added, {report.removed} removed, '
        f'{report.unchanged} unchanged, {report.modified} modified.\\n'

    Notes:
        - No leading indentation on rows.
        - No trailing whitespace on any line.
        - The returned string always ends with a single '\\n' (the footer's).
        - The blank-line separator is emitted UNCONDITIONALLY, even when no
          artefact has emit_row=True.
        - No ANSI colour is emitted (Decision 7); glyphs are plain ASCII.

    The function accepts any object exposing the attributes of DiffReport
    (structural protocol).
    """
    parts: list[str] = []
    for artefact in report.artefacts:
        if artefact.emit_row:
            parts.append(f"{artefact.category} {artefact.name}\n")
    parts.append("\n")
    parts.append(
        f"Diff: {report.added} added, {report.removed} removed, "
        f"{report.unchanged} unchanged, {report.modified} modified.\n"
    )
    return "".join(parts)
