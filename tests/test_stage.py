"""
Unit tests for pipeline_status.stage.derive_stage().

Tests each of the seven pipeline stage boundary conditions using directly
constructed ArtefactResult instances — no file I/O required.
"""
import unittest
from pathlib import Path

from pipeline_status.inspectors import ArtefactResult
from pipeline_status.stage import derive_stage


def _make_result(name: str, filled: bool, total: int = 0, completed: int = 0) -> ArtefactResult:
    """Helper: construct an ArtefactResult without touching the filesystem."""
    extra = {}
    if name == "tasks.json" and (total > 0 or completed > 0):
        extra = {"total": total, "completed": completed}
    return ArtefactResult(
        name=name,
        path=Path("/fake") / name,
        exists=filled,
        filled=filled,
        extra=extra,
    )


def _all_filled(tasks_total: int = 3, tasks_completed: int = 1) -> dict[str, ArtefactResult]:
    """Return a mapping where every artefact is filled."""
    return {
        "feature-request.md": _make_result("feature-request.md", True),
        "requirements.md": _make_result("requirements.md", True),
        "adr.md": _make_result("adr.md", True),
        "tasks.json": _make_result("tasks.json", True,
                                   total=tasks_total, completed=tasks_completed),
        "worktrees.json": _make_result("worktrees.json", True),
    }


class TestDeriveStageAwaitingFeatureRequest(unittest.TestCase):
    """Stage 1: feature-request.md not filled."""

    def test_feature_request_not_filled(self):
        artefacts = {
            "feature-request.md": _make_result("feature-request.md", False),
            "requirements.md": _make_result("requirements.md", True),
            "adr.md": _make_result("adr.md", True),
            "tasks.json": _make_result("tasks.json", True, total=3, completed=1),
            "worktrees.json": _make_result("worktrees.json", True),
        }
        self.assertEqual(derive_stage(artefacts), "Awaiting feature request")

    def test_empty_mapping_yields_awaiting_feature_request(self):
        self.assertEqual(derive_stage({}), "Awaiting feature request")


class TestDeriveStageAwaitingGate1(unittest.TestCase):
    """Stage 2: feature-request filled but requirements not."""

    def test_requirements_not_filled(self):
        artefacts = {
            "feature-request.md": _make_result("feature-request.md", True),
            "requirements.md": _make_result("requirements.md", False),
            "adr.md": _make_result("adr.md", True),
            "tasks.json": _make_result("tasks.json", True, total=3, completed=1),
            "worktrees.json": _make_result("worktrees.json", True),
        }
        self.assertEqual(derive_stage(artefacts), "Awaiting Gate 1 (PO review)")


class TestDeriveStageAwaitingGate2(unittest.TestCase):
    """Stage 3: feature-request and requirements filled, adr not."""

    def test_adr_not_filled(self):
        artefacts = {
            "feature-request.md": _make_result("feature-request.md", True),
            "requirements.md": _make_result("requirements.md", True),
            "adr.md": _make_result("adr.md", False),
            "tasks.json": _make_result("tasks.json", True, total=3, completed=1),
            "worktrees.json": _make_result("worktrees.json", True),
        }
        self.assertEqual(derive_stage(artefacts), "Awaiting Gate 2 (Architect review)")


class TestDeriveStageAwaitingGate3(unittest.TestCase):
    """Stage 4: through adr filled, tasks not."""

    def test_tasks_not_filled(self):
        artefacts = {
            "feature-request.md": _make_result("feature-request.md", True),
            "requirements.md": _make_result("requirements.md", True),
            "adr.md": _make_result("adr.md", True),
            "tasks.json": _make_result("tasks.json", False),
            "worktrees.json": _make_result("worktrees.json", True),
        }
        self.assertEqual(derive_stage(artefacts), "Awaiting Gate 3 (PM review)")


class TestDeriveStageAwaitingGate4(unittest.TestCase):
    """Stage 5: through tasks filled, worktrees not."""

    def test_worktrees_not_filled(self):
        artefacts = {
            "feature-request.md": _make_result("feature-request.md", True),
            "requirements.md": _make_result("requirements.md", True),
            "adr.md": _make_result("adr.md", True),
            "tasks.json": _make_result("tasks.json", True, total=3, completed=1),
            "worktrees.json": _make_result("worktrees.json", False),
        }
        self.assertEqual(derive_stage(artefacts), "Awaiting Gate 4 (Engineering kick-off)")


class TestDeriveStageEngineeringInProgress(unittest.TestCase):
    """Stage 6: all filled but tasks not complete."""

    def test_all_filled_tasks_not_done(self):
        artefacts = _all_filled(tasks_total=5, tasks_completed=3)
        self.assertEqual(derive_stage(artefacts), "Engineering in progress")

    def test_all_filled_zero_tasks(self):
        """Zero tasks (total=0) should NOT count as all done."""
        artefacts = _all_filled(tasks_total=0, tasks_completed=0)
        self.assertEqual(derive_stage(artefacts), "Engineering in progress")

    def test_completed_less_than_total(self):
        artefacts = _all_filled(tasks_total=8, tasks_completed=7)
        self.assertEqual(derive_stage(artefacts), "Engineering in progress")


class TestDeriveStageComplete(unittest.TestCase):
    """Stage 7: all filled and all tasks done."""

    def test_all_filled_all_tasks_done(self):
        artefacts = _all_filled(tasks_total=5, tasks_completed=5)
        self.assertEqual(derive_stage(artefacts), "Pipeline complete")

    def test_single_task_done(self):
        artefacts = _all_filled(tasks_total=1, tasks_completed=1)
        self.assertEqual(derive_stage(artefacts), "Pipeline complete")

    def test_exact_equality_required(self):
        """completed == total AND both > 0 is required."""
        artefacts = _all_filled(tasks_total=3, tasks_completed=3)
        self.assertEqual(derive_stage(artefacts), "Pipeline complete")
        # One short must NOT be complete
        artefacts2 = _all_filled(tasks_total=3, tasks_completed=2)
        self.assertNotEqual(derive_stage(artefacts2), "Pipeline complete")


if __name__ == "__main__":
    unittest.main()
