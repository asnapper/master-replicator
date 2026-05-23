"""
Renderers for the history table and the per-archive detail view.

Public symbols:
    format_history_table(entries)         -> str   (multi-line; ends with '\\n')
    format_archive_detail(results, stage) -> str   (multi-line; ends with '\\n')

The detail renderer mirrors the inline ``print()`` sequence in
``pipeline_status.__main__._run_one_shot()`` byte-for-byte so the
``pipeline-status history NAME`` form produces output identical to v1's
one-shot view of the same artefacts.

This module does NOT import ``pipeline_status.archive`` or
``pipeline_status.history`` at runtime; ``ArchiveEntry`` is referenced only
under ``TYPE_CHECKING`` for static type-checking purposes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for static type-checking
    from pipeline_status.history import ArchiveEntry

from pipeline_status.inspectors import ArtefactResult
from pipeline_status.formatting import format_artefact_row, format_stage_line


_TABLE_HEADERS: tuple[str, str, str, str] = ("NAME", "ARCHIVED-AT", "TASKS", "DONE")
_COLUMN_SEPARATOR: str = "  "  # two spaces; FR forbids tabs / box-drawing


def _format_archived_at(mtime: float) -> str:
    """Format ``mtime`` as ISO-8601 local time at second precision.

    Mirrors ``inspectors._mtime_iso`` exactly:
        datetime.fromtimestamp(mtime, tz=datetime.now().astimezone().tzinfo)
                .isoformat(timespec="seconds")

    Re-implemented inline (rather than importing the private helper) per the
    ADR's Decision 6 implementation notes.
    """
    tz = datetime.now().astimezone().tzinfo
    return datetime.fromtimestamp(mtime, tz=tz).isoformat(timespec="seconds")


def _format_count(value: int | None) -> str:
    """Render an optional integer count as a decimal string, or '-' for None."""
    return str(value) if value is not None else "-"


def format_history_table(entries: Sequence["ArchiveEntry"]) -> str:
    """Render the ``history`` table.

    Columns, in order: NAME, ARCHIVED-AT, TASKS, DONE.
    Separator: two spaces between columns (no tabs, no Unicode box-drawing).
    One header row precedes the data rows. Column widths size to the widest
    value in each column (header included). The returned string ends with a
    trailing newline.

    Cell formatting:
        NAME         -> entry.name verbatim.
        ARCHIVED-AT  -> datetime.fromtimestamp(entry.mtime,
                            tz=datetime.now().astimezone().tzinfo)
                        .isoformat(timespec="seconds")
        TASKS        -> str(entry.total_tasks) if entry.total_tasks is not None
                        else "-"
        DONE         -> str(entry.completed_tasks) if entry.completed_tasks is
                        not None else "-"

    The function accepts any sequence of objects exposing ``.name``, ``.mtime``,
    ``.total_tasks``, ``.completed_tasks`` (structural protocol). Rows are
    emitted in input order (callers pre-sort).

    No ANSI colour is emitted; the table is metadata, not a status report.
    """
    rows: list[tuple[str, str, str, str]] = [_TABLE_HEADERS]
    for entry in entries:
        rows.append(
            (
                entry.name,
                _format_archived_at(entry.mtime),
                _format_count(entry.total_tasks),
                _format_count(entry.completed_tasks),
            )
        )

    # Size each column to the widest value (header included).
    widths = [max(len(row[i]) for row in rows) for i in range(len(_TABLE_HEADERS))]

    lines: list[str] = []
    for row in rows:
        # ljust every column except the last; the last is left unpadded to
        # avoid trailing whitespace on the line.
        padded = [
            row[i].ljust(widths[i]) if i < len(row) - 1 else row[i]
            for i in range(len(row))
        ]
        lines.append(_COLUMN_SEPARATOR.join(padded))

    return "\n".join(lines) + "\n"


def format_archive_detail(
    results: Sequence[ArtefactResult],
    stage: str,
) -> str:
    """Render the per-archive detail view for ``history NAME``.

    Output is byte-identical to the v1 one-shot body in
    ``pipeline_status.__main__._run_one_shot()``::

        Pipeline Status\\n
        ===============\\n
        \\n
        <one '  ' + format_artefact_row(r) + '\\n' per r in results>
        \\n
        '  ' + format_stage_line(stage) + '\\n'

    Returned as a single string ending with a trailing newline; callers print
    it with ``end=""`` so the trailing newline is not doubled.

    Colour follows ``pipeline_status.formatting.use_colour()`` because
    ``format_artefact_row`` and ``format_stage_line`` consult it themselves; no
    separate colour flag is accepted here.
    """
    parts: list[str] = [
        "Pipeline Status\n",
        "===============\n",
        "\n",
    ]
    for result in results:
        parts.append(f"  {format_artefact_row(result)}\n")
    parts.append("\n")
    parts.append(f"  {format_stage_line(stage)}\n")
    return "".join(parts)
