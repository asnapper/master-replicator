"""Restore archived artefacts from .claude/state/archive/<NAME>/ into .claude/state/.

Public symbols:
    run_restore(args)             -> int    (argparse action callable; returns exit code)
    add_restore_subparser(subparsers) -> argparse.ArgumentParser (registers ``restore``)

stdlib only at module scope.  ``TRACKED_ARTEFACTS`` and ``slugify`` are
lazy-imported from ``pipeline_status.archive`` INSIDE ``run_restore`` (not at
top level), to keep the non-restore CLI paths free of restore's transitive
import cost (preserves the v3 import-cost invariant).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def run_restore(args: argparse.Namespace) -> int:
    """Action callable for the ``restore`` subcommand.

    Algorithm (Decisions 3 + 5):
      1. ``state_dir = Path.cwd() / ".claude" / "state"``.  If not a directory,
         emit the missing-state error to stderr and return 2.
      2. Lazy-import ``TRACKED_ARTEFACTS`` and ``slugify`` from
         ``pipeline_status.archive`` (Decision 10).
      3. ``slug = slugify(args.name)``.  If ``""`` -> empty-slug error, return 1.
      4. ``archive_dir = state_dir / "archive" / slug``.  If not a directory,
         emit the missing-archive error and return 1.
      5. Phase 1 (enumerate): for each tracked basename ``b`` (in declaration
         order), if ``(archive_dir / b).is_file()`` then append ``(b, src, dst)``
         to ``sources``.  Classify dst conflicts:
            * If ``dst.is_dir()`` -> DIRECTORY conflict (always fatal,
              even with ``--force``).
            * Elif ``dst.exists()`` and not ``args.force`` -> FILE conflict
              (fatal without ``--force``, ignored with ``--force``).
      6. Phase 2 (gate): if any conflicts, print ALL of them to stderr
         (one per line) and return 1.  If all conflicts are file-type
         (no directory conflicts), append the hint
         ``(use --force to overwrite)`` as a final stderr line.
      7. Phase 3 (copy): for each ``(b, src, dst)`` in ``sources``,
         ``shutil.copy2(src, dst)``.
      8. Emit ``Restored N file(s) from .claude/state/archive/<slug>/`` on
         stdout and return 0.

    The two-phase design means no file is copied on any error path
    (no partial restore).  Restore is additive: live files not present in
    the archive are never touched.
    """
    state_dir = Path.cwd() / ".claude" / "state"
    if not state_dir.is_dir():
        print(
            "pipeline-status: error: .claude/state/ not found or not a directory",
            file=sys.stderr,
        )
        return 2

    # Lazy import (Decision 10): keep non-restore code paths free of the
    # archive module's transitive import cost.
    from pipeline_status.archive import TRACKED_ARTEFACTS, slugify

    slug = slugify(args.name)
    if slug == "":
        print(
            "pipeline-status: error: restore name is empty after normalisation",
            file=sys.stderr,
        )
        return 1

    archive_dir = state_dir / "archive" / slug
    if not archive_dir.is_dir():
        print(
            f"pipeline-status: error: archive {args.name!r} not found at {archive_dir}",
            file=sys.stderr,
        )
        return 1

    # Phase 1: enumerate sources + classify conflicts.
    sources: list[tuple[str, Path, Path]] = []
    file_conflicts: list[Path] = []
    dir_conflicts: list[Path] = []
    for basename in TRACKED_ARTEFACTS:
        src = archive_dir / basename
        if not src.is_file():
            continue
        dst = state_dir / basename
        sources.append((basename, src, dst))
        if dst.is_dir() and not dst.is_symlink():
            # Always fatal, even with --force.
            dir_conflicts.append(dst)
        elif dst.exists() and not args.force:
            file_conflicts.append(dst)

    # Phase 2: gate on any conflict.
    if file_conflicts or dir_conflicts:
        for dst in dir_conflicts:
            print(
                f"pipeline-status: error: cannot overwrite directory: {dst}",
                file=sys.stderr,
            )
        for dst in file_conflicts:
            print(
                f"pipeline-status: error: cannot overwrite existing file: {dst}",
                file=sys.stderr,
            )
        if file_conflicts and not dir_conflicts:
            print(
                "pipeline-status: error: (use --force to overwrite)",
                file=sys.stderr,
            )
        return 1

    # Phase 3: copy.  Single write phase, reached only when all checks pass.
    for _basename, src, dst in sources:
        shutil.copy2(src, dst)

    print(f"Restored {len(sources)} file(s) from .claude/state/archive/{slug}/")
    return 0


def add_restore_subparser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> argparse.ArgumentParser:
    """Register the ``restore`` subcommand on ``subparsers``.

    Declares one required positional ``NAME`` (string) and one optional
    boolean flag ``--force`` (``action="store_true"``, default ``False``).
    No other arguments.  Calls ``sp.set_defaults(func=run_restore)``.
    Returns the created subparser.
    """
    sp = subparsers.add_parser(
        "restore",
        help=(
            "Restore archived artefacts from .claude/state/archive/<NAME>/ "
            "into .claude/state/."
        ),
        description=(
            "Copy the five tracked artefact files from "
            ".claude/state/archive/<slugify(NAME)>/ back into .claude/state/. "
            "By default, refuses to overwrite any existing live artefact "
            "and exits 1 listing every conflicting basename. "
            "Pass --force to overwrite. Files absent from the archive are "
            "never created, modified, or deleted by restore."
        ),
    )
    sp.add_argument(
        "name",
        metavar="NAME",
        help=(
            "Archive name (slugified) to restore from "
            ".claude/state/archive/<slug>/."
        ),
    )
    sp.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite existing live files (all-or-nothing collision check "
            "otherwise)."
        ),
    )
    sp.set_defaults(func=run_restore)
    return sp
