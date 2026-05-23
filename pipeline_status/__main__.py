"""
Entry point for the pipeline-status CLI.

Locates ``.claude/state/`` relative to CWD, runs all five artefact
inspectors, formats and prints a status table, derives the current
pipeline stage, and exits 0 on success or 2 if the state directory is
absent.

v2 adds ``--watch`` for continuous re-rendering and ``--interval
SECONDS`` for the refresh cadence. The one-shot path (no ``--watch``)
is byte-identical to v1.

This module is importable without side effects; all file I/O and
``sys.exit`` calls happen inside :func:`main`.
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from pipeline_status.archive import add_archive_subparser
from pipeline_status.formatting import (
    format_artefact_row,
    format_stage_line,
    use_colour,
)
from pipeline_status.history import add_history_subparser
from pipeline_status.inspectors import (
    inspect_adr,
    inspect_feature_request,
    inspect_requirements,
    inspect_tasks,
    inspect_worktrees,
)
from pipeline_status.stage import derive_stage


_STATE_DIR = Path(".claude") / "state"

_EPILOG = (
    "Watch mode notes:\n"
    "  --watch clears the screen between renders using the ANSI escape\n"
    "  \\x1b[H\\x1b[2J. On terminals shorter than the report, the top of\n"
    "  the report will scroll off — accepted limitation for v2. When\n"
    "  stdout is not a TTY (pipe, redirect), the clear-screen sequence\n"
    "  is suppressed and renders are separated by a single blank line.\n"
    "  Watch mode tolerates a missing .claude/state/ directory and\n"
    "  shows a placeholder until it appears."
)


def _interval_type(raw: str) -> int:
    """Custom argparse converter: accept only integers in ``[1, 3600]``.

    Rejects floats (e.g. ``"0.5"``), non-numeric strings, signs other
    than an optional leading ``+``, and out-of-range values. The
    ``str(value) != raw.lstrip("+")`` check rejects strings whose
    numeric value parses but whose textual form is not the canonical
    decimal integer (such as ``"0.5"`` after ``int("0.5")`` would have
    failed — this catches the few cases ``int()`` would accept that we
    don't want, like ``"007"``).
    """
    try:
        value = int(raw)
        if str(value) != raw.lstrip("+"):
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


def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser. Extracted for unit-testability."""
    parser = argparse.ArgumentParser(
        prog="pipeline-status",
        description="Inspect .claude/state/ pipeline artefacts and report current stage.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously re-render the status report (press Ctrl+C to stop).",
    )
    parser.add_argument(
        "--interval",
        type=_interval_type,
        default=2,
        metavar="SECONDS",
        help=(
            "Refresh cadence in integer seconds for --watch "
            "(default: 2, min: 1, max: 3600; ignored without --watch)."
        ),
    )
    subparsers = parser.add_subparsers(dest="cmd", required=False)
    add_archive_subparser(subparsers)
    add_history_subparser(subparsers)
    return parser


def _locate_state_dir() -> Path | None:
    """Return the .claude/state/ path relative to CWD, or None if absent."""
    candidate = Path.cwd() / _STATE_DIR
    if candidate.is_dir():
        return candidate
    return None


def _run_one_shot() -> int:
    """Run the v1 one-shot inspection path. Returns the intended exit code.

    Output is byte-identical to v1. Exits 2 with a stderr message when
    ``.claude/state/`` is missing.
    """
    state_dir = _locate_state_dir()
    if state_dir is None:
        print(
            "pipeline-status: error: .claude/state/ not found or not a directory",
            file=sys.stderr,
        )
        return 2

    results = [
        inspect_feature_request(state_dir / "feature-request.md"),
        inspect_requirements(state_dir / "requirements.md"),
        inspect_adr(state_dir / "adr.md"),
        inspect_tasks(state_dir / "tasks.json"),
        inspect_worktrees(state_dir / "worktrees.json"),
    ]
    artefact_map = {r.name: r for r in results}
    stage = derive_stage(artefact_map)

    print("Pipeline Status")
    print("===============")
    print()
    for result in results:
        print(f"  {format_artefact_row(result)}")
    print()
    print(f"  {format_stage_line(stage)}")
    return 0


def main() -> None:
    """Run the pipeline-status CLI.

    Dispatches between the v1 one-shot path and v2 watch mode based on
    ``--watch``. ``--interval`` is parsed unconditionally (so argparse
    validates it early), but is only consumed by watch mode.
    """
    args = _build_parser().parse_args()

    # v3: subcommand dispatch FIRST. When a subcommand was supplied, hand off
    # to its registered action callable via args.func and exit with its return
    # code; the v1/v2 paths below are untouched.
    if getattr(args, "cmd", None) is not None:
        sys.exit(args.func(args))

    if args.watch:
        # Lazy import keeps the one-shot path independent of watch.py at
        # import time and matches the ADR's "main() importable without
        # side effects" constraint.
        from pipeline_status.watch import WatchConfig, run_watch

        state_dir = Path.cwd() / _STATE_DIR
        config = WatchConfig(
            state_dir=state_dir,
            interval=args.interval,
            is_tty=sys.stdout.isatty(),
            use_colour=use_colour(),
            stream=sys.stdout,
            sleep_fn=time.sleep,
            clock_fn=lambda: datetime.now().astimezone(),
            max_iterations=None,
        )
        sys.exit(run_watch(config))

    sys.exit(_run_one_shot())


if __name__ == "__main__":
    main()
