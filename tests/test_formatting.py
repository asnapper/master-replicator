"""Tests for pipeline_status.formatting module."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline_status.inspectors import ArtefactResult
from pipeline_status.formatting import (
    colorize,
    format_artefact_row,
    format_stage_line,
    STAGES,
)


class TestColorize(unittest.TestCase):
    """Tests for colorize()."""

    def test_no_color_env_suppresses(self):
        """Any presence of NO_COLOR (even empty string) suppresses colour."""
        with patch.dict(os.environ, {"NO_COLOR": ""}):
            self.assertEqual(colorize("text", "green"), "text")

    def test_no_color_env_non_empty_suppresses(self):
        """NO_COLOR=1 also suppresses colour."""
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertEqual(colorize("text", "green"), "text")

    def test_non_tty_suppresses(self):
        """Non-TTY stdout suppresses colour even when NO_COLOR is absent."""
        with patch("pipeline_status.formatting.sys") as m:
            m.stdout.isatty.return_value = False
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(colorize("text", "green"), "text")

    def test_tty_emits_ansi(self):
        """TTY stdout without NO_COLOR produces ANSI escape sequences."""
        with patch("pipeline_status.formatting.sys") as m:
            m.stdout.isatty.return_value = True
            with patch.dict(os.environ, {}, clear=True):
                result = colorize("text", "green")
                self.assertIn("[", result)
                self.assertIn("text", result)

    def test_unknown_colour_returns_plain(self):
        """An unrecognised colour name returns the plain text."""
        with patch("pipeline_status.formatting.sys") as m:
            m.stdout.isatty.return_value = True
            with patch.dict(os.environ, {}, clear=True):
                result = colorize("text", "ultraviolet")
                self.assertEqual(result, "text")


class TestFormatArtefactRow(unittest.TestCase):
    """Tests for format_artefact_row()."""

    def _make_result(self, **kwargs):
        defaults = dict(
            name="requirements.md",
            path=Path(".claude/state/requirements.md"),
            exists=True,
            filled=True,
        )
        defaults.update(kwargs)
        return ArtefactResult(**defaults)

    def test_contains_name(self):
        """Row output contains the artefact name."""
        r = self._make_result(name="requirements.md")
        row = format_artefact_row(r)
        self.assertIn("requirements.md", row)

    def test_exists_label(self):
        r = self._make_result(exists=True)
        self.assertIn("EXISTS", format_artefact_row(r))

    def test_missing_label(self):
        r = self._make_result(exists=False, filled=False)
        self.assertIn("MISSING", format_artefact_row(r))

    def test_filled_label(self):
        r = self._make_result(filled=True)
        self.assertIn("FILLED", format_artefact_row(r))

    def test_empty_label(self):
        r = self._make_result(filled=False)
        self.assertIn("EMPTY", format_artefact_row(r))

    def test_includes_task_counts(self):
        """Row includes task count when extra[total] is set."""
        r = self._make_result(extra={"total": 5, "done": 3})
        row = format_artefact_row(r)
        self.assertIn("3/5", row)
        self.assertIn("tasks", row)

    def test_task_counts_absent_without_total(self):
        r = self._make_result(extra={})
        self.assertNotIn("tasks", format_artefact_row(r))

    def test_includes_mtime(self):
        r = self._make_result(mtime_iso="2026-05-23T12:00:00")
        self.assertIn("2026-05-23", format_artefact_row(r))

    def test_em_dash_when_no_mtime(self):
        r = self._make_result(mtime_iso=None)
        self.assertIn("—", format_artefact_row(r))

    def test_includes_error(self):
        r = self._make_result(error="permission denied")
        row = format_artefact_row(r)
        self.assertIn("ERROR", row)
        self.assertIn("permission denied", row)


class TestFormatStageLine(unittest.TestCase):
    """Tests for format_stage_line()."""

    def test_all_stages_non_empty(self):
        """format_stage_line returns a non-empty string for all 7 stage names."""
        for stage in STAGES:
            with self.subTest(stage=stage):
                line = format_stage_line(stage)
                self.assertTrue(line, f"Expected non-empty for stage {stage!r}")
                self.assertIn("[", line)

    def test_feature_request_stage(self):
        self.assertIn("Feature Request", format_stage_line("feature-request"))

    def test_requirements_stage(self):
        self.assertIn("Requirements", format_stage_line("requirements"))

    def test_adr_stage(self):
        self.assertIn("ADR", format_stage_line("adr"))

    def test_tasks_stage(self):
        self.assertIn("Tasks", format_stage_line("tasks"))

    def test_implementation_stage(self):
        self.assertIn("Implementation", format_stage_line("implementation"))

    def test_review_stage(self):
        self.assertIn("Review", format_stage_line("review"))

    def test_done_stage(self):
        self.assertIn("Done", format_stage_line("done"))

    def test_unknown_stage_falls_back(self):
        """An unrecognised stage name falls back gracefully."""
        line = format_stage_line("mystery-stage")
        self.assertIn("mystery-stage", line)


if __name__ == "__main__":
    unittest.main()
