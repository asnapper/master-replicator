"""
Compare two pipeline runs (live vs archive, or archive vs archive) on the
five tracked artefacts and report per-artefact + aggregate categories.

Public symbols:
    CATEGORY_ADDED, CATEGORY_REMOVED, CATEGORY_UNCHANGED, CATEGORY_MODIFIED:
                                              Final[str] one-character glyphs
    ArtefactDiff                              (frozen dataclass)
    DiffReport                                (frozen dataclass)
    compute_diff(left_dir, right_dir)         -> DiffReport
    add_diff_subparser(subparsers)            -> argparse.ArgumentParser
    run_diff(args)                            -> int   (argparse action callable)

Stdlib only. Lazy import of ``pipeline_status.archive`` (for ``slugify``)
and ``pipeline_status.format_diff`` (for ``format_diff_report``) inside
``run_diff``; no top-level import of sibling tasks.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pipeline_status.inspectors import MAX_READ_BYTES


# Public glyph constants (ADR Decision 6).
CATEGORY_ADDED:     Final[str] = "+"
CATEGORY_REMOVED:   Final[str] = "-"
CATEGORY_UNCHANGED: Final[str] = "="
CATEGORY_MODIFIED:  Final[str] = "M"


# Private duplicate of archive.TRACKED_ARTEFACTS to keep this module
# parallel-safe at test time (mirrors history.py's pattern; ADR v3
# Decision 12 / v4 Decision 2). Do NOT replace with a top-level import
# from pipeline_status.archive.
_TRACKED_ARTEFACTS: Final[tuple[str, ...]] = (
    "feature-request.md",
    "requirements.md",
    "adr.md",
    "tasks.json",
    "worktrees.json",
)


@dataclass(frozen=True)
class ArtefactDiff:
    """The per-artefact result of comparing one tracked basename.

    Attributes:
        name:     The artefact basename (one of ``_TRACKED_ARTEFACTS``).
        category: One of ``"+"``, ``"-"``, ``"="``, ``"M"``.
        emit_row: True if this artefact should be rendered as a row;
                  False for the both-absent special case (ADR Decision 10).
                  Both-absent entries still contribute to the footer
                  ``unchanged`` count.
    """
    name: str
    category: str
    emit_row: bool


@dataclass(frozen=True)
class DiffReport:
    """The full result of one diff invocation.

    Attributes:
        artefacts: One entry per ``_TRACKED_ARTEFACTS`` basename, in
                   canonical order. Length is always
                   ``len(_TRACKED_ARTEFACTS) == 5``.
        added, removed, unchanged, modified:
                   Footer counts. Their sum always equals
                   ``len(artefacts)``.
    """
    artefacts: tuple[ArtefactDiff, ...]
    added: int
    removed: int
    unchanged: int
    modified: int


def _read_capped(path: Path) -> bytes | None:
    """Read up to ``MAX_READ_BYTES`` bytes from ``path``.

    Returns the bytes on success, or ``None`` on any ``OSError``
    (``PermissionError``, ``IsADirectoryError``, etc.). Callers treat
    ``None`` as "absent" for the equality test, per FR-19 / Decision 9.
    """
    try:
        with path.open("rb") as f:
            return f.read(MAX_READ_BYTES)
    except OSError:
        return None


def compute_diff(left_dir: Path, right_dir: Path) -> DiffReport:
    """Compute the four-category diff of two pipeline runs.

    Side semantics:
        ``left_dir``  -> the "old" side (``--against OTHER`` value, or the
                         live state when ``--against`` is omitted).
        ``right_dir`` -> the "new" side (the positional ``NAME`` archive).

    Iterates ``_TRACKED_ARTEFACTS`` in canonical order. For each name:

    - right present, left absent          -> ``"+"`` (emit_row=True)
    - left  present, right absent         -> ``"-"`` (emit_row=True)
    - both present:
        read both via ``_read_capped``; if either side returns ``None``
        treat it as "absent" for the comparison (FR-19 / Decision 9):
            both None                     -> ``"="`` (emit_row=False)
            left None, right bytes        -> ``"+"`` (emit_row=True)
            right None, left bytes        -> ``"-"`` (emit_row=True)
            bytes equal                   -> ``"="`` (emit_row=True)
            bytes differ                  -> ``"M"`` (emit_row=True)
    - both absent                         -> ``"="`` (emit_row=False)

    Returns a ``DiffReport``. ``len(artefacts) == len(_TRACKED_ARTEFACTS)``.
    Footer counts sum to ``len(_TRACKED_ARTEFACTS)``.

    Never raises. Does not import ``pipeline_status.archive`` (slugification
    has already been done by the caller).
    """
    diffs: list[ArtefactDiff] = []
    added = 0
    removed = 0
    unchanged = 0
    modified = 0

    for name in _TRACKED_ARTEFACTS:
        left_path = left_dir / name
        right_path = right_dir / name
        left_present = left_path.is_file()
        right_present = right_path.is_file()

        if right_present and not left_present:
            diffs.append(ArtefactDiff(name, CATEGORY_ADDED, True))
            added += 1
        elif left_present and not right_present:
            diffs.append(ArtefactDiff(name, CATEGORY_REMOVED, True))
            removed += 1
        elif left_present and right_present:
            left_bytes = _read_capped(left_path)
            right_bytes = _read_capped(right_path)
            if left_bytes is None and right_bytes is None:
                diffs.append(ArtefactDiff(name, CATEGORY_UNCHANGED, False))
                unchanged += 1
            elif left_bytes is None:
                diffs.append(ArtefactDiff(name, CATEGORY_ADDED, True))
                added += 1
            elif right_bytes is None:
                diffs.append(ArtefactDiff(name, CATEGORY_REMOVED, True))
                removed += 1
            elif left_bytes == right_bytes:
                diffs.append(ArtefactDiff(name, CATEGORY_UNCHANGED, True))
                unchanged += 1
            else:
                diffs.append(ArtefactDiff(name, CATEGORY_MODIFIED, True))
                modified += 1
        else:  # both absent
            diffs.append(ArtefactDiff(name, CATEGORY_UNCHANGED, False))
            unchanged += 1

    return DiffReport(tuple(diffs), added, removed, unchanged, modified)


def add_diff_subparser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> argparse.ArgumentParser:
    """Register the ``diff`` subcommand on ``subparsers``.

    Declares exactly one required positional ``NAME`` and one optional
    ``--against OTHER`` (default ``None``). No other flags (FR-6). Calls
    ``sp.set_defaults(func=run_diff)``. Returns the subparser.
    """
    sp = subparsers.add_parser(
        "diff",
        help=(
            "Compare two pipeline runs (live vs archive, or archive vs "
            "archive) on the five tracked artefacts."
        ),
        description=(
            "Compare two pipeline runs and print a per-artefact summary. "
            "The positional NAME is the right (new) side, slugified and "
            "resolved under .claude/state/archive/. With --against OTHER "
            "the left (old) side is another archive; without --against the "
            "left side is the live .claude/state/ directory. "
            "Glyphs: '+' added (present only on the right), '-' removed "
            "(present only on the left), '=' unchanged (bytes equal), 'M' "
            "modified (bytes differ). "
            "Exit codes: 0 on a successful comparison; 1 if NAME or OTHER "
            "slugifies to empty or resolves to a missing archive; 2 if the "
            "live .claude/state/ directory is missing (no --against)."
        ),
    )
    sp.add_argument(
        "name",
        metavar="NAME",
        help="Archive name for the right (new) side. Slugified before lookup.",
    )
    sp.add_argument(
        "--against",
        metavar="OTHER",
        default=None,
        help=(
            "Archive name for the left (old) side. Slugified before "
            "lookup. If omitted, the left side is the live .claude/state/ "
            "directory."
        ),
    )
    sp.set_defaults(func=run_diff)
    return sp


def run_diff(args: argparse.Namespace) -> int:
    """Action callable for the ``diff`` subcommand.

    See ADR sequence diagrams. Resolves both sides under
    ``Path.cwd()/.claude/state[/archive]``, calls ``compute_diff``, and
    prints the rendered report via
    ``pipeline_status.format_diff.format_diff_report`` (lazy import).
    Returns 0 / 1 / 2 per ADR Decision 8.
    """
    state_dir = Path.cwd() / ".claude" / "state"
    archive_root = state_dir / "archive"

    # Lazy import: keeps the no-subcommand path free of archive's cost.
    from pipeline_status.archive import slugify

    # Resolve right side first (matches sequence diagrams + edge case 12).
    right_slug = slugify(args.name)
    if right_slug == "":
        print(
            "pipeline-status: error: diff name is empty after normalisation",
            file=sys.stderr,
        )
        return 1
    right_dir = archive_root / right_slug
    if not right_dir.is_dir():
        print(
            f"pipeline-status: error: archive {args.name!r} not found at {right_dir}",
            file=sys.stderr,
        )
        return 1

    # Resolve left side.
    if args.against is not None:
        left_slug = slugify(args.against)
        if left_slug == "":
            print(
                "pipeline-status: error: diff --against value is empty after normalisation",
                file=sys.stderr,
            )
            return 1
        left_dir = archive_root / left_slug
        if not left_dir.is_dir():
            print(
                f"pipeline-status: error: archive {args.against!r} not found at {left_dir}",
                file=sys.stderr,
            )
            return 1
    else:
        if not state_dir.is_dir():
            print(
                "pipeline-status: error: .claude/state/ not found or not a directory",
                file=sys.stderr,
            )
            return 2
        left_dir = state_dir

    report = compute_diff(left_dir, right_dir)

    # Lazy import: format_diff is a sibling task in the parallel fan-out.
    from pipeline_status.format_diff import format_diff_report
    print(format_diff_report(report), end="")
    return 0
