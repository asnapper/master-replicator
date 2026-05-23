"""
Entry point for the pipeline-status CLI.

Locates .claude/state/ relative to CWD, runs all five artefact inspectors,
formats and prints a status table, derives the current pipeline stage, and
exits 0 on success or 2 if the state directory is absent.

This module is importable without side effects; all file I/O and sys.exit
calls happen inside main().
"""
import argparse
import sys
from pathlib import Path

from pipeline_status.formatting import format_artefact_row, format_stage_line
from pipeline_status.inspectors import (
    inspect_adr,
    inspect_feature_request,
    inspect_requirements,
    inspect_tasks,
    inspect_worktrees,
)
from pipeline_status.stage import derive_stage

_STATE_DIR = Path(".claude") / "state"


def _locate_state_dir() -> Path | None:
    """Return the .claude/state/ path relative to CWD, or None if absent."""
    candidate = Path.cwd() / _STATE_DIR
    if candidate.is_dir():
        return candidate
    return None


def main() -> None:
    """Run the pipeline-status CLI. Exits 0 on success, 2 on missing state dir."""
    parser = argparse.ArgumentParser(
        prog="pipeline-status",
        description="Inspect .claude/state/ pipeline artefacts and report current stage.",
    )
    parser.parse_args()

    state_dir = _locate_state_dir()
    if state_dir is None:
        print(
            "pipeline-status: error: .claude/state/ not found or not a directory",
            file=sys.stderr,
        )
        sys.exit(2)

    # Run all inspectors
    results = [
        inspect_feature_request(state_dir / "feature-request.md"),
        inspect_requirements(state_dir / "requirements.md"),
        inspect_adr(state_dir / "adr.md"),
        inspect_tasks(state_dir / "tasks.json"),
        inspect_worktrees(state_dir / "worktrees.json"),
    ]

    # Build artefact map for stage derivation
    artefact_map = {r.name: r for r in results}

    # Determine stage
    stage = derive_stage(artefact_map)

    # Print report
    print("Pipeline Status")
    print("===============")
    print()
    for result in results:
        print(f"  {format_artefact_row(result)}")
    print()
    print(f"  {format_stage_line(stage)}")


if __name__ == "__main__":
    main()
