"""Tests for the v2 formatting helpers: format_local_iso, format_footer, render_missing_state."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline_status.formatting import (
    format_local_iso,
    format_footer,
    render_missing_state,
)


CET = timezone(timedelta(hours=2))
FIXED_DT = datetime(2026, 5, 23, 5, 54, 36, tzinfo=CET)


class TestFormatLocalIso(unittest.TestCase):
    def test_aware_datetime_returns_iso_with_offset(self):
        self.assertEqual(format_local_iso(FIXED_DT), "2026-05-23T05:54:36+02:00")

    def test_second_precision(self):
        dt = datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=timezone.utc)
        result = format_local_iso(dt)
        self.assertEqual(result, "2026-01-01T00:00:00+00:00")
        self.assertNotIn(".", result)

    def test_naive_datetime_raises(self):
        with self.assertRaises(ValueError):
            format_local_iso(datetime(2026, 5, 23, 5, 54, 36))

    def test_utc_offset(self):
        dt = datetime(2026, 5, 23, 5, 54, 36, tzinfo=timezone.utc)
        self.assertEqual(format_local_iso(dt), "2026-05-23T05:54:36+00:00")


class TestFormatFooter(unittest.TestCase):
    def test_canonical_interval_2(self):
        expected = "Last refresh: 2026-05-23T05:54:36+02:00  (interval: 2s, press Ctrl+C to stop)"
        self.assertEqual(format_footer(FIXED_DT, 2), expected)

    def test_no_trailing_newline(self):
        result = format_footer(FIXED_DT, 2)
        self.assertFalse(result.endswith("\n"))

    def test_two_spaces_between_timestamp_and_paren(self):
        result = format_footer(FIXED_DT, 2)
        self.assertIn("+02:00  (interval:", result)

    def test_interval_1(self):
        self.assertIn("(interval: 1s,", format_footer(FIXED_DT, 1))

    def test_interval_60(self):
        self.assertIn("(interval: 60s,", format_footer(FIXED_DT, 60))

    def test_interval_3600(self):
        self.assertIn("(interval: 3600s,", format_footer(FIXED_DT, 3600))

    def test_naive_datetime_raises(self):
        with self.assertRaises(ValueError):
            format_footer(datetime(2026, 5, 23, 5, 54, 36), 2)

    def test_zero_interval_raises(self):
        with self.assertRaises(ValueError):
            format_footer(FIXED_DT, 0)

    def test_negative_interval_raises(self):
        with self.assertRaises(ValueError):
            format_footer(FIXED_DT, -1)

    def test_non_int_interval_raises(self):
        with self.assertRaises(ValueError):
            format_footer(FIXED_DT, 2.5)

    def test_bool_rejected_as_interval(self):
        # Guard: bool is a subclass of int in Python; explicitly reject.
        with self.assertRaises(ValueError):
            format_footer(FIXED_DT, True)


class TestRenderMissingState(unittest.TestCase):
    def test_header_prefix(self):
        body = render_missing_state(Path("/anywhere/.claude/state"))
        self.assertTrue(body.startswith("Pipeline Status\n===============\n"))

    def test_contains_missing_line(self):
        body = render_missing_state(Path("/anywhere/.claude/state"))
        self.assertIn("  .claude/state/: MISSING", body)

    def test_trailing_newline(self):
        body = render_missing_state(Path("/anywhere/.claude/state"))
        self.assertTrue(body.endswith("\n"))

    def test_full_structure(self):
        body = render_missing_state(Path("/anywhere/.claude/state"))
        self.assertEqual(
            body,
            "Pipeline Status\n"
            "===============\n"
            "\n"
            "  .claude/state/: MISSING\n",
        )

    def test_independent_of_state_dir_value(self):
        a = render_missing_state(Path("/a"))
        b = render_missing_state(Path("/b/c"))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
