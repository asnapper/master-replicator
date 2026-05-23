"""
Stage derivation logic for pipeline_status.

This module exports a single pure function, derive_stage(), which maps a
collection of ArtefactResult values to a human-readable pipeline stage string.

Expected mapping keys:
    "feature-request.md"  — Markdown; pipeline entry point
    "requirements.md"     — Markdown; PO output
    "adr.md"              — Markdown; Architect output
    "tasks.json"          — JSON; PM output (extra["total"], extra["completed"])
    "worktrees.json"      — JSON; Orchestrator output

ArtefactResult fields consumed:
    exists (bool)  — whether the file is present on disk
    filled (bool)  — whether the file contains substantive content
    extra  (dict)  — for tasks.json: {"total": int, "completed": int}

Stage rules (evaluated in order; first match wins):
    1. feature-request.md not filled → "Awaiting feature request"
    2. requirements.md not filled    → "Awaiting Gate 1 (PO review)"
    3. adr.md not filled             → "Awaiting Gate 2 (Architect review)"
    4. tasks.json not filled         → "Awaiting Gate 3 (PM review)"
    5. worktrees.json not filled     → "Awaiting Gate 4 (Engineering kick-off)"
    6. all filled but tasks not done → "Engineering in progress"
    7. all filled, all tasks done    → "Pipeline complete"

"All tasks done" requires extra["total"] == extra["completed"] and both > 0.
"""
from typing import Mapping

from pipeline_status.inspectors import ArtefactResult


def derive_stage(artefacts: Mapping[str, ArtefactResult]) -> str:
    """Return the current pipeline stage string given a mapping of ArtefactResult values.

    Args:
        artefacts: A mapping from artefact name (e.g. "feature-request.md") to
                   its ArtefactResult. Unknown keys are silently ignored.

    Returns:
        One of the seven stage strings defined in the module docstring.
    """

    def filled(name: str) -> bool:
        result = artefacts.get(name)
        return result is not None and result.filled

    if not filled("feature-request.md"):
        return "Awaiting feature request"
    if not filled("requirements.md"):
        return "Awaiting Gate 1 (PO review)"
    if not filled("adr.md"):
        return "Awaiting Gate 2 (Architect review)"
    if not filled("tasks.json"):
        return "Awaiting Gate 3 (PM review)"
    if not filled("worktrees.json"):
        return "Awaiting Gate 4 (Engineering kick-off)"

    # Check if all tasks are done
    tasks_result = artefacts.get("tasks.json")
    if tasks_result is not None:
        total = tasks_result.extra.get("total", 0)
        completed = tasks_result.extra.get("completed", 0)
        if total > 0 and completed == total:
            return "Pipeline complete"

    return "Engineering in progress"
