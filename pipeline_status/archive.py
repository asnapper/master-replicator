"""Snapshot the live ``.claude/state/`` artefacts into ``.claude/state/archive/<NAME>/``.

Public symbols:
    TRACKED_ARTEFACTS:                Final[tuple[str, ...]] of the five basenames.
    slugify(text)                     -> str    (FR-11 rules; "" if empty after normalisation)
    derive_default_name(...)          -> str    (FR-9/FR-10 heading-or-date fallback)
    run_archive(args)                 -> int    (argparse action callable; returns exit code)
    add_archive_subparser(subparsers) -> argparse.ArgumentParser (registers `archive`)

stdlib only.  No imports from sibling tasks (``pipeline_status.history`` /
``pipeline_status.format_history``).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Final

# Per Decision 10 / FR-11: the exact five tracked basenames, in copy order.
TRACKED_ARTEFACTS: Final[tuple[str, ...]] = (
    "feature-request.md",
    "requirements.md",
    "adr.md",
    "tasks.json",
    "worktrees.json",
)

# Cap reads of feature-request.md when extracting its first heading.  Matches
# the v1 inspector cap (10 MiB) so we never balloon memory on a runaway file.
_MAX_READ_BYTES: Final[int] = 10 * 1024 * 1024

# Compiled once: any run of non-[a-z0-9] characters (after lowercasing).
_SLUG_SEPARATOR_RE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")

# ATX heading: a non-blank line whose first non-whitespace characters are one
# or more ``#`` followed by whitespace.  Captures the heading text.
_ATX_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^\s*#+\s+(.*\S)\s*$")


def slugify(text: str) -> str:
    """Slugify per FR-11 / Decision 10.

    Lowercases the input, replaces every run of characters outside
    ``[a-z0-9]`` with a single ``-``, then strips leading/trailing ``-``.
    Returns ``""`` if the result is empty after stripping.

    The output character set is ``[a-z0-9-]`` by construction, so ``/``,
    ``\\``, and ``..`` cannot appear and the function is path-traversal safe.
    Non-ASCII letters are treated as separators (not transliterated).
    """
    if not isinstance(text, str):  # defensive; argparse hands us strings
        text = str(text)
    lowered = text.lower()
    replaced = _SLUG_SEPARATOR_RE.sub("-", lowered)
    return replaced.strip("-")


def derive_default_name(
    feature_request_path: Path,
    *,
    today: datetime | None = None,
) -> str:
    """Return the default archive name per FR-9/FR-10.

    Reads ``feature_request_path``, finds the first ATX-style markdown
    heading (a line whose first non-whitespace characters are one or more
    ``#`` followed by whitespace), slugifies its text, and returns the slug
    if non-empty.

    Otherwise — missing file, unreadable file, no heading, or heading that
    slugifies to empty — returns ``today`` formatted as ``YYYY-MM-DD``.
    ``today`` defaults to ``datetime.now().astimezone()`` (local time).

    This function never raises.
    """
    heading_slug = ""
    try:
        with open(feature_request_path, "rb") as fh:
            raw = fh.read(_MAX_READ_BYTES)
        text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if not line.strip():
                continue
            match = _ATX_HEADING_RE.match(line)
            if match is None:
                # Non-blank, non-heading line: per the contract we only look
                # at the *first* heading; we keep scanning lines until we
                # either find one or run out.  Other non-heading content
                # before the first heading is ignored.
                continue
            heading_slug = slugify(match.group(1))
            break
    except (OSError, ValueError):
        heading_slug = ""

    if heading_slug:
        return heading_slug

    if today is None:
        today = datetime.now().astimezone()
    return today.strftime("%Y-%m-%d")


def _emit_error(message: str) -> None:
    """Single stderr write used by run_archive; isolated for testability."""
    print(message, file=sys.stderr)


def run_archive(args: argparse.Namespace) -> int:
    """Action callable for the ``archive`` subcommand.

    See ADR Decision 3/4/5/8 and ``Implementation Notes -> archive.py`` for
    the precise algorithm and error-message strings.
    """
    state_dir = Path.cwd() / ".claude" / "state"
    if not state_dir.is_dir():
        _emit_error(
            "pipeline-status: error: .claude/state/ not found or not a directory"
        )
        return 2

    raw_name = getattr(args, "name", None)
    if raw_name is not None:
        slug = slugify(raw_name)
        if slug == "":
            _emit_error(
                "pipeline-status: error: archive name is empty after normalisation"
            )
            return 1
    else:
        # derive_default_name never raises and always returns a non-empty slug
        # (date fallback is non-empty by construction).
        slug = derive_default_name(state_dir / "feature-request.md")

    dest_root = state_dir / "archive"
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / slug

    try:
        dest.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        _emit_error(
            f"pipeline-status: error: archive {slug!r} already exists at {dest}"
        )
        return 1

    copied = 0
    for name in TRACKED_ARTEFACTS:
        src = state_dir / name
        if src.is_file():
            shutil.copy2(src, dest / name)
            copied += 1

    now = time.time()
    os.utime(dest, (now, now))

    print(f"Archived {copied} file(s) to .claude/state/archive/{slug}/")
    return 0


def add_archive_subparser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> argparse.ArgumentParser:
    """Register the ``archive`` subcommand on ``subparsers``.

    Declares one optional ``--name NAME`` flag whose help text documents the
    slugifier rules (FR-12).  Calls ``sp.set_defaults(func=run_archive)`` so
    ``__main__.main()`` can dispatch via ``args.func(args)``.
    """
    sp = subparsers.add_parser(
        "archive",
        help="Snapshot the live .claude/state/ artefacts into "
             ".claude/state/archive/<NAME>/.",
        description=(
            "Snapshot the live .claude/state/ artefacts into "
            ".claude/state/archive/<NAME>/.  When --name is omitted, the "
            "archive name is derived from the first markdown heading in "
            ".claude/state/feature-request.md, falling back to today's date "
            "(YYYY-MM-DD) when no usable heading is found."
        ),
    )
    sp.add_argument(
        "--name",
        metavar="NAME",
        default=None,
        help=(
            "Name for the archive directory.  Normalised by lowercasing, "
            "replacing any run of characters outside [a-z0-9] with a single "
            "'-', and stripping leading/trailing '-'.  Must produce a "
            "non-empty slug after normalisation."
        ),
    )
    sp.set_defaults(func=run_archive)
    return sp
