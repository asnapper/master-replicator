import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline_status.inspectors import ArtefactResult
from pipeline_status.formatting import (
    use_colour,
    colorize,
    format_artefact_row,
    format_stage_line,
    format_report,
)


class TestUseColour(unittest.TestCase):
    def test_no_color_env_suppresses(self):
        """colorize() suppressed when NO_COLOR set."""
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                self.assertFalse(use_colour())

    def test_non_tty_suppresses(self):
        """colorize() suppressed when not TTY."""
        env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
        with patch.dict(os.environ, env, clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                self.assertFalse(use_colour())

    def test_tty_no_no_color_enables(self):
        """colorize() emits ANSI when TTY enabled."""
        env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
        with patch.dict(os.environ, env, clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                self.assertTrue(use_colour())


class TestColorize(unittest.TestCase):
    def test_no_color_returns_plain_text(self):
        """colorize() returns plain text when NO_COLOR is set."""
        with patch("pipeline_status.formatting.use_colour", return_value=False):
            result = colorize("hello", "green")
            self.assertEqual(result, "hello")

    def test_colour_wraps_ansi(self):
        """colorize() emits ANSI when colour enabled."""
        with patch("pipeline_status.formatting.use_colour", return_value=True):
            result = colorize("hello", "green")
            self.assertIn("\033[32m", result)
            self.assertIn("hello", result)
            self.assertIn("\033[0m", result)

    def test_unknown_colour_returns_plain(self):
        """colorize() returns plain text for unknown colour names."""
        with patch("pipeline_status.formatting.use_colour", return_value=True):
            result = colorize("hello", "fuschia")
            self.assertEqual(result, "hello")

    def test_all_supported_colours(self):
        """All supported colour names produce ANSI output."""
        supported = ["green", "yellow", "red", "cyan", "bold"]
        with patch("pipeline_status.formatting.use_colour", return_value=True):
            for col in supported:
                result = colorize("x", col)
                self.assertIn("\033[", result, f"Expected ANSI for colour={col!r}")


class TestFormatArtefactRow(unittest.TestCase):
    def _make_result(self, **kwargs):
        defaults = dict(
            name="requirements.md",
            path=Path("/fake/requirements.md"),
            exists=True,
            filled=True,
            mtime_iso="2026-05-23T10:00:00",
        )
        defaults.update(kwargs)
        return ArtefactResult(**defaults)

    def test_includes_name(self):
        """format_artefact_row() includes name."""
        r = self._make_result(name="requirements.md")
        row = format_artefact_row(r, colour=False)
        self.assertIn("requirements.md", row)

    def test_includes_exists_status(self):
        """format_artefact_row() includes EXISTS when file exists."""
        r = self._make_result(exists=True)
        row = format_artefact_row(r, colour=False)
        self.assertIn("EXISTS", row)

    def test_includes_missing_status(self):
        """format_artefact_row() includes MISSING when file absent."""
        r = self._make_result(exists=False, filled=False)
        row = format_artefact_row(r, colour=False)
        self.assertIn("MISSING", row)

    def test_includes_filled_status(self):
        r = self._make_result(filled=True)
        row = format_artefact_row(r, colour=False)
        self.assertIn("FILLED", row)

    def test_includes_empty_status(self):
        r = self._make_result(exists=True, filled=False)
        row = format_artefact_row(r, colour=False)
        self.assertIn("EMPTY", row)

    def test_includes_mtime(self):
        """format_artefact_row() includes mtime."""
        r = self._make_result(mtime_iso="2026-05-23T10:00:00")
        row = format_artefact_row(r, colour=False)
        self.assertIn("2026-05-23T10:00:00", row)

    def test_includes_task_counts_when_total_present(self):
        """format_artefact_row() includes task counts when extra[total] present."""
        r = self._make_result(extra={"total": 5, "completed": 3})
        row = format_artefact_row(r, colour=False)
        self.assertIn("3/5 tasks done", row)

    def test_no_task_counts_when_total_absent(self):
        r = self._make_result(extra={})
        row = format_artefact_row(r, colour=False)
        self.assertNotIn("tasks done", row)

    def test_includes_error_when_set(self):
        r = self._make_result(error="file locked")
        row = format_artefact_row(r, colour=False)
        self.assertIn("[ERROR: file locked]", row)

    def test_dash_when_no_mtime(self):
        r = self._make_result(mtime_iso=None)
        row = format_artefact_row(r, colour=False)
        self.assertIn("\u2014", row)

    def test_deterministic(self):
        """Output deterministic — same input gives same output."""
        r = self._make_result()
        row1 = format_artefact_row(r, colour=False)
        row2 = format_artefact_row(r, colour=False)
        self.assertEqual(row1, row2)


class TestFormatStageLine(unittest.TestCase):
    def test_non_empty_for_all_stages(self):
        """format_stage_line() non-empty for all stage strings."""
        stages = ["requirements", "architecture", "planning", "implementation", ""]
        for stage in stages:
            result = format_stage_line(stage, colour=False)
            self.assertTrue(len(result) > 0, f"Expected non-empty for stage={stage!r}")

    def test_contains_stage(self):
        result = format_stage_line("implementation", colour=False)
        self.assertIn("implementation", result)

    def test_contains_label(self):
        result = format_stage_line("foo", colour=False)
        self.assertIn("Stage:", result)


class TestFormatReport(unittest.TestCase):
    def _make_results(self):
        return [
            ArtefactResult(
                name="requirements.md",
                path=Path("/fake/requirements.md"),
                exists=True,
                filled=True,
                mtime_iso="2026-05-23T10:00:00",
            ),
            ArtefactResult(
                name="adr.md",
                path=Path("/fake/adr.md"),
                exists=False,
                filled=False,
                mtime_iso=None,
            ),
        ]

    def test_report_contains_header(self):
        results = self._make_results()
        report = format_report(results, stage="planning", colour=False)
        self.assertIn("Pipeline Artefact Status", report)

    def test_report_contains_stage_line(self):
        results = self._make_results()
        report = format_report(results, stage="planning", colour=False)
        self.assertIn("planning", report)

    def test_report_contains_all_names(self):
        results = self._make_results()
        report = format_report(results, stage="planning", colour=False)
        self.assertIn("requirements.md", report)
        self.assertIn("adr.md", report)

    def test_report_deterministic(self):
        results = self._make_results()
        r1 = format_report(results, stage="planning", colour=False)
        r2 = format_report(results, stage="planning", colour=False)
        self.assertEqual(r1, r2)


if __name__ == "__main__":
    unittest.main()
