"""
Unit tests for pipeline_status.inspectors.

Uses stdlib unittest and tempfile only; no real .claude/state/ directory is touched.
"""
import json
import tempfile
import unittest
from pathlib import Path

from pipeline_status.inspectors import (
    ArtefactResult,
    inspect_adr,
    inspect_feature_request,
    inspect_requirements,
    inspect_tasks,
    inspect_worktrees,
)


class TestInspectFeatureRequest(unittest.TestCase):
    def test_missing_file_yields_not_exists_not_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feature-request.md"
            result = inspect_feature_request(path)
        self.assertFalse(result.exists)
        self.assertFalse(result.filled)
        self.assertIsNone(result.mtime_iso)

    def test_zero_byte_file_yields_filled_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feature-request.md"
            path.write_bytes(b"")
            result = inspect_feature_request(path)
        self.assertTrue(result.exists)
        self.assertFalse(result.filled)

    def test_whitespace_only_yields_filled_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feature-request.md"
            path.write_text("   \n\t\n  ", encoding="utf-8")
            result = inspect_feature_request(path)
        self.assertTrue(result.exists)
        self.assertFalse(result.filled)

    def test_heading_only_yields_filled_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feature-request.md"
            path.write_text("# Feature Request\n## Sub-heading\n", encoding="utf-8")
            result = inspect_feature_request(path)
        self.assertTrue(result.exists)
        self.assertFalse(result.filled)

    def test_heading_plus_body_yields_filled_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feature-request.md"
            path.write_text("# Feature Request\n\nSome real content here.\n", encoding="utf-8")
            result = inspect_feature_request(path)
        self.assertTrue(result.exists)
        self.assertTrue(result.filled)

    def test_placeholder_only_yields_filled_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feature-request.md"
            path.write_text("# Title\n<placeholder text>\n<another placeholder>\n",
                            encoding="utf-8")
            result = inspect_feature_request(path)
        self.assertFalse(result.filled)

    def test_mtime_is_set_for_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feature-request.md"
            path.write_text("# Title\n\nBody content.\n", encoding="utf-8")
            result = inspect_feature_request(path)
        self.assertIsNotNone(result.mtime_iso)
        # ISO-8601 with timezone offset
        self.assertRegex(result.mtime_iso, r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}')


class TestInspectRequirements(unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.md"
            result = inspect_requirements(path)
        self.assertFalse(result.exists)
        self.assertFalse(result.filled)

    def test_substantive_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.md"
            path.write_text("# Requirements\n\n- FR-1: The system must do X.\n", encoding="utf-8")
            result = inspect_requirements(path)
        self.assertTrue(result.filled)

    def test_name_is_requirements_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requirements.md"
            path.write_text("content", encoding="utf-8")
            result = inspect_requirements(path)
        self.assertEqual(result.name, "requirements.md")


class TestInspectAdr(unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "adr.md"
            result = inspect_adr(path)
        self.assertFalse(result.exists)
        self.assertFalse(result.filled)

    def test_substantive_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "adr.md"
            path.write_text("# ADR\n\n## Decision\n\nWe chose option A.\n", encoding="utf-8")
            result = inspect_adr(path)
        self.assertTrue(result.filled)


class TestInspectTasks(unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            result = inspect_tasks(path)
        self.assertFalse(result.exists)
        self.assertFalse(result.filled)

    def test_empty_json_array_yields_filled_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text("[]", encoding="utf-8")
            result = inspect_tasks(path)
        self.assertFalse(result.filled)

    def test_empty_json_object_yields_filled_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text("{}", encoding="utf-8")
            result = inspect_tasks(path)
        self.assertFalse(result.filled)

    def test_valid_array_yields_correct_counts(self):
        tasks = [
            {"id": "t1", "status": "done"},
            {"id": "t2", "status": "in_progress"},
            {"id": "t3", "status": "completed"},
            {"id": "t4"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text(json.dumps(tasks), encoding="utf-8")
            result = inspect_tasks(path)
        self.assertTrue(result.filled)
        self.assertEqual(result.extra["total"], 4)
        self.assertEqual(result.extra["completed"], 2)

    def test_object_shape_with_tasks_key(self):
        data = {"tasks": [{"id": "t1", "status": "done"}, {"id": "t2"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = inspect_tasks(path)
        self.assertTrue(result.filled)
        self.assertEqual(result.extra["total"], 2)
        self.assertEqual(result.extra["completed"], 1)

    def test_completed_boolean_field(self):
        tasks = [{"id": "t1", "completed": True}, {"id": "t2", "completed": False}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text(json.dumps(tasks), encoding="utf-8")
            result = inspect_tasks(path)
        self.assertEqual(result.extra["total"], 2)
        self.assertEqual(result.extra["completed"], 1)

    def test_malformed_json_sets_error_no_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text("{not valid json}", encoding="utf-8")
            result = inspect_tasks(path)
        self.assertIsNotNone(result.error)
        self.assertFalse(result.filled)

    def test_zero_byte_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_bytes(b"")
            result = inspect_tasks(path)
        self.assertFalse(result.filled)
        self.assertIsNotNone(result.error)  # JSON parse error on empty string

    def test_name_is_tasks_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text("[{}]", encoding="utf-8")
            result = inspect_tasks(path)
        self.assertEqual(result.name, "tasks.json")


class TestInspectWorktrees(unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worktrees.json"
            result = inspect_worktrees(path)
        self.assertFalse(result.exists)
        self.assertFalse(result.filled)

    def test_empty_array_yields_filled_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worktrees.json"
            path.write_text("[]", encoding="utf-8")
            result = inspect_worktrees(path)
        self.assertFalse(result.filled)

    def test_empty_object_yields_filled_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worktrees.json"
            path.write_text("{}", encoding="utf-8")
            result = inspect_worktrees(path)
        self.assertFalse(result.filled)

    def test_non_empty_array_yields_filled_true(self):
        data = [{"task_id": "t1", "branch": "feature/t1", "path": "/tmp/t1"}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worktrees.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = inspect_worktrees(path)
        self.assertTrue(result.filled)

    def test_malformed_json_sets_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worktrees.json"
            path.write_text("not json at all", encoding="utf-8")
            result = inspect_worktrees(path)
        self.assertIsNotNone(result.error)
        self.assertFalse(result.filled)

    def test_name_is_worktrees_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worktrees.json"
            path.write_text('[{"task_id":"t1"}]', encoding="utf-8")
            result = inspect_worktrees(path)
        self.assertEqual(result.name, "worktrees.json")


if __name__ == "__main__":
    unittest.main()
