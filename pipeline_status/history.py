"""
Discover and parse archived pipeline runs under .claude/state/archive/.

Public symbols:
    ArchiveEntry                            (frozen dataclass; see ADR Data Model)
    list_archives(archive_root)             -> list[ArchiveEntry]
    inspect_archive(archive_dir)            -> dict[str, ArtefactResult]
    add_history_subparser(subparsers)       -> argparse.ArgumentParser
    run_history(args)                       -> int   (argparse action callable)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pipeline_status.inspectors import (
    ArtefactResult,
    MAX_READ_BYTES,
    inspect_adr,
    inspect_feature_request,
    inspect_requirements,
    inspect_tasks,
    inspect_worktrees,
)

# Private duplicate of archive.TRACKED_ARTEFACTS to keep this module parallel-safe.
# The duplication is intentional per ADR Decision 12; do NOT replace with an
# import from pipeline_status.archive.
_TRACKED_ARTEFACTS: Final[tuple[str, ...]] = (
    "feature-request.md",
    "requirements.md",
    "adr.md",
    "tasks.json",
    "worktrees.json",
)


@dataclass(frozen=True)
class ArchiveEntry:
    """One row in the history table.

    Attributes:
        name:            The archive directory name (already a slug on disk).
        path:            Absolute Path to the archive directory.
        mtime:           The directory's st_mtime at scan time.
        total_tasks:     Total tasks count from <archive>/tasks.json, or None
                         if tasks.json is missing or malformed.
        completed_tasks: Completed-tasks count, or None under the same conditions.
    """
    name: str
    path: Path
    mtime: float
    total_tasks: int | None
    completed_tasks: int | None


def _parse_task_counts(tasks_json_path: Path) -> tuple[int | None, int | None]:
    """Parse <archive>/tasks.json and return (total, completed).

    Never raises. Returns (None, None) on any of:
      - missing file
      - OSError / UnicodeDecodeError / JSONDecodeError
      - shape that is neither list nor {"tasks": [...]}.

    Returns (0, 0) for top-level empty `[]` or `{}`.
    """
    if not tasks_json_path.is_file():
        return (None, None)
    try:
        with tasks_json_path.open("rb") as f:
            raw = f.read(MAX_READ_BYTES)
        text = raw.decode("utf-8")
        parsed = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return (None, None)

    # Empty top-level JSON values: count as zero.
    if parsed == [] or parsed == {}:
        return (0, 0)

    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict) and isinstance(parsed.get("tasks"), list):
        items = parsed["tasks"]
    else:
        return (None, None)

    total = len(items)
    completed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "")).lower()
        if status in {"done", "completed"}:
            completed += 1
            continue
        if item.get("completed") is True or item.get("done") is True:
            completed += 1
    return (total, completed)


def list_archives(archive_root: Path) -> list[ArchiveEntry]:
    """Enumerate immediate subdirectories of ``archive_root`` and parse each.

    See ADR Task B contract and Decision 7/11. Returns [] when archive_root
    does not exist or is not a directory. Never raises.
    """
    if not archive_root.is_dir():
        return []

    entries: list[ArchiveEntry] = []
    try:
        children = list(archive_root.iterdir())
    except OSError:
        return []

    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        total, completed = _parse_task_counts(child / "tasks.json")
        entries.append(
            ArchiveEntry(
                name=child.name,
                path=child,
                mtime=mtime,
                total_tasks=total,
                completed_tasks=completed,
            )
        )

    entries.sort(key=lambda e: e.name)
    return entries


def inspect_archive(archive_dir: Path) -> dict[str, ArtefactResult]:
    """Run the v1 inspectors against ``archive_dir`` instead of .claude/state/.

    Returns a dict keyed by the five canonical artefact basenames. Each value
    is the ArtefactResult produced by the corresponding v1 inspector. Missing
    files render per v1 rules (exists=False, filled=False). Does not raise on
    partial archives.
    """
    return {
        "feature-request.md": inspect_feature_request(archive_dir / "feature-request.md"),
        "requirements.md":    inspect_requirements(archive_dir / "requirements.md"),
        "adr.md":             inspect_adr(archive_dir / "adr.md"),
        "tasks.json":         inspect_tasks(archive_dir / "tasks.json"),
        "worktrees.json":     inspect_worktrees(archive_dir / "worktrees.json"),
    }


def add_history_subparser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    """Register the `history` subcommand on the given subparsers action.

    Adds one optional positional ``name`` argument (``nargs='?'``). Calls
    ``sp.set_defaults(func=run_history)``. Returns the subparser.
    """
    sp = subparsers.add_parser(
        "history",
        help="List archived pipeline runs, or render a single archive in detail.",
        description=(
            "List archived pipeline runs under .claude/state/archive/. "
            "If NAME is given, render that archive in detail (v1-style report)."
        ),
    )
    sp.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Optional archive name to render in detail. Slugified before lookup.",
    )
    sp.set_defaults(func=run_history)
    return sp


def run_history(args: argparse.Namespace) -> int:
    """Action callable for the `history` subcommand.

    Dispatches on whether ``args.name`` is set. See ADR Task B contract.
    """
    archive_root = Path.cwd() / ".claude" / "state" / "archive"

    if getattr(args, "name", None) is None:
        # Table form.
        entries = list_archives(archive_root)
        if not entries:
            print("No archives found.")
            return 0
        from pipeline_status.format_history import format_history_table
        print(format_history_table(entries), end="")
        return 0

    # Detail form.
    from pipeline_status.archive import slugify  # lazy import; see ADR Decision 2
    slug = slugify(args.name)
    archive_dir = archive_root / slug
    if not archive_dir.is_dir():
        print(
            f"pipeline-status: error: archive {args.name!r} not found at {archive_dir}",
            file=sys.stderr,
        )
        return 1

    from pipeline_status.format_history import format_archive_detail
    from pipeline_status.stage import derive_stage

    results = inspect_archive(archive_dir)
    stage = derive_stage(results)
    print(format_archive_detail(list(results.values()), stage), end="")
    return 0
