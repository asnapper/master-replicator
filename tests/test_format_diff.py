"""Tests for pipeline_status.format_diff.format_diff_report.

These tests use local namedtuple shims for ArtefactDiff and DiffReport so
that this test file has zero import dependency on
pipeline_status.diff_archives (Task A2 stays parallel-safe with Task A1).
"""
from __future__ import annotations

import unittest
from collections import namedtuple

from pipeline_status.format_diff import format_diff_report


# Local structural-typed shims (do NOT import from diff_archives).
_FakeArtefactDiff = namedtuple("_FakeArtefactDiff", ("name", "category", "emit_row"))
_FakeReport = namedtuple(
    "_FakeReport", ("artefacts", "added", "removed", "unchanged", "modified")
)


def _make_report(artefacts, added=0, removed=0, unchanged=0, modified=0):
    return _FakeReport(
        artefacts=tuple(artefacts),
        added=added,
        removed=removed,
        unchanged=unchanged,
        modified=modified,
    )


class SingleRowTests(unittest.TestCase):
    """Single-row of each category, byte-exact verification."""

    def test_single_added_row(self):
        report = _make_report(
            [_FakeArtefactDiff("tasks.json", "+", True)],
            added=1,
            removed=0,
            unchanged=4,
            modified=0,
        )
        expected = (
            "+ tasks.json\n"
            "\n"
            "Diff: 1 added, 0 removed, 4 unchanged, 0 modified.\n"
        )
        self.assertEqual(format_diff_report(report), expected)

    def test_single_removed_row(self):
        report = _make_report(
            [_FakeArtefactDiff("adr.md", "-", True)],
            added=0,
            removed=1,
            unchanged=4,
            modified=0,
        )
        expected = (
            "- adr.md\n"
            "\n"
            "Diff: 0 added, 1 removed, 4 unchanged, 0 modified.\n"
        )
        self.assertEqual(format_diff_report(report), expected)

    def test_single_unchanged_row(self):
        report = _make_report(
            [_FakeArtefactDiff("requirements.md", "=", True)],
            added=0,
            removed=0,
            unchanged=5,
            modified=0,
        )
        expected = (
            "= requirements.md\n"
            "\n"
            "Diff: 0 added, 0 removed, 5 unchanged, 0 modified.\n"
        )
        self.assertEqual(format_diff_report(report), expected)

    def test_single_modified_row(self):
        report = _make_report(
            [_FakeArtefactDiff("feature-request.md", "M", True)],
            added=0,
            removed=0,
            unchanged=4,
            modified=1,
        )
        expected = (
            "M feature-request.md\n"
            "\n"
            "Diff: 0 added, 0 removed, 4 unchanged, 1 modified.\n"
        )
        self.assertEqual(format_diff_report(report), expected)


class MultiRowMixedCategoryTests(unittest.TestCase):
    """Multi-row mixed-category output, canonical input order preserved."""

    def test_mixed_categories_in_input_order(self):
        # The ADR example: live state has modified adr.md and a new tasks.json;
        # archive lacks tasks.json and worktrees.json (both-absent for the
        # latter, suppressed from row output but counted as unchanged).
        report = _make_report(
            [
                _FakeArtefactDiff("feature-request.md", "=", True),
                _FakeArtefactDiff("requirements.md", "=", True),
                _FakeArtefactDiff("adr.md", "M", True),
                _FakeArtefactDiff("tasks.json", "+", True),
                _FakeArtefactDiff("worktrees.json", "=", False),
            ],
            added=1,
            removed=0,
            unchanged=3,
            modified=1,
        )
        expected = (
            "= feature-request.md\n"
            "= requirements.md\n"
            "M adr.md\n"
            "+ tasks.json\n"
            "\n"
            "Diff: 1 added, 0 removed, 3 unchanged, 1 modified.\n"
        )
        self.assertEqual(format_diff_report(report), expected)

    def test_order_preserves_input_sequence_not_category_grouping(self):
        # Deliberately scramble categories to assert rows follow input order,
        # not category-sorted order.
        report = _make_report(
            [
                _FakeArtefactDiff("a.md", "M", True),
                _FakeArtefactDiff("b.md", "+", True),
                _FakeArtefactDiff("c.md", "-", True),
                _FakeArtefactDiff("d.md", "=", True),
                _FakeArtefactDiff("e.md", "+", True),
            ],
            added=2,
            removed=1,
            unchanged=1,
            modified=1,
        )
        expected = (
            "M a.md\n"
            "+ b.md\n"
            "- c.md\n"
            "= d.md\n"
            "+ e.md\n"
            "\n"
            "Diff: 2 added, 1 removed, 1 unchanged, 1 modified.\n"
        )
        self.assertEqual(format_diff_report(report), expected)

    def test_emit_row_false_entries_are_skipped(self):
        # emit_row=False entries must be suppressed even if their position is
        # between two emit_row=True entries.
        report = _make_report(
            [
                _FakeArtefactDiff("a.md", "=", True),
                _FakeArtefactDiff("b.md", "=", False),  # both-absent, no row
                _FakeArtefactDiff("c.md", "M", True),
            ],
            added=0,
            removed=0,
            unchanged=4,
            modified=1,
        )
        expected = (
            "= a.md\n"
            "M c.md\n"
            "\n"
            "Diff: 0 added, 0 removed, 4 unchanged, 1 modified.\n"
        )
        self.assertEqual(format_diff_report(report), expected)


class AllRowsSuppressedTests(unittest.TestCase):
    """Degenerate cases where no row is emitted."""

    def test_all_rows_suppressed_produces_blank_plus_footer(self):
        # Every artefact is both-absent => emit_row=False => zero rows.
        # Output must be exactly "\n" + footer.
        report = _make_report(
            [
                _FakeArtefactDiff("feature-request.md", "=", False),
                _FakeArtefactDiff("requirements.md", "=", False),
                _FakeArtefactDiff("adr.md", "=", False),
                _FakeArtefactDiff("tasks.json", "=", False),
                _FakeArtefactDiff("worktrees.json", "=", False),
            ],
            added=0,
            removed=0,
            unchanged=5,
            modified=0,
        )
        expected = "\nDiff: 0 added, 0 removed, 5 unchanged, 0 modified.\n"
        self.assertEqual(format_diff_report(report), expected)

    def test_empty_artefacts_tuple_still_emits_blank_and_footer(self):
        # Degenerate edge: artefacts tuple is empty. The blank separator must
        # still be unconditionally emitted.
        report = _make_report([], added=0, removed=0, unchanged=0, modified=0)
        expected = "\nDiff: 0 added, 0 removed, 0 unchanged, 0 modified.\n"
        self.assertEqual(format_diff_report(report), expected)


class FooterWordingTests(unittest.TestCase):
    """Footer wording byte-for-byte across several count combinations."""

    def test_footer_all_zero(self):
        report = _make_report([], 0, 0, 0, 0)
        result = format_diff_report(report)
        self.assertTrue(
            result.endswith("Diff: 0 added, 0 removed, 0 unchanged, 0 modified.\n")
        )

    def test_footer_typical_v4_example_counts(self):
        report = _make_report([], 1, 0, 3, 1)
        result = format_diff_report(report)
        self.assertTrue(
            result.endswith("Diff: 1 added, 0 removed, 3 unchanged, 1 modified.\n")
        )

    def test_footer_all_five_unchanged(self):
        report = _make_report([], 0, 0, 5, 0)
        result = format_diff_report(report)
        self.assertTrue(
            result.endswith("Diff: 0 added, 0 removed, 5 unchanged, 0 modified.\n")
        )

    def test_footer_all_five_modified(self):
        report = _make_report([], 0, 0, 0, 5)
        result = format_diff_report(report)
        self.assertTrue(
            result.endswith("Diff: 0 added, 0 removed, 0 unchanged, 5 modified.\n")
        )

    def test_footer_mixed_double_digits(self):
        # Even though TRACKED_ARTEFACTS only has 5 entries today, the
        # renderer must not assume single-digit counts.
        report = _make_report([], 12, 7, 23, 4)
        result = format_diff_report(report)
        self.assertTrue(
            result.endswith(
                "Diff: 12 added, 7 removed, 23 unchanged, 4 modified.\n"
            )
        )

    def test_footer_exact_words_and_punctuation(self):
        # Spot-check the exact words "added", "removed", "unchanged",
        # "modified" plus separating commas and the trailing period.
        report = _make_report([], 1, 2, 3, 4)
        result = format_diff_report(report)
        self.assertIn("Diff: 1 added, 2 removed, 3 unchanged, 4 modified.", result)


class TrailingNewlineTests(unittest.TestCase):
    """The returned string always ends in a single '\\n'."""

    def test_with_rows_ends_in_newline(self):
        report = _make_report(
            [_FakeArtefactDiff("foo", "+", True)], added=1, unchanged=4
        )
        result = format_diff_report(report)
        self.assertTrue(result.endswith("\n"))
        # Single trailing newline (not two).
        self.assertFalse(result.endswith("\n\n\n"))

    def test_without_rows_ends_in_newline(self):
        report = _make_report([], unchanged=0)
        result = format_diff_report(report)
        self.assertTrue(result.endswith("\n"))


class NoAnsiEscapeTests(unittest.TestCase):
    """No ANSI escape codes anywhere in the rendered output (Decision 7)."""

    def test_no_ansi_in_single_row(self):
        report = _make_report(
            [_FakeArtefactDiff("foo", "M", True)],
            added=0,
            removed=0,
            unchanged=4,
            modified=1,
        )
        result = format_diff_report(report)
        self.assertNotIn("\x1b", result)

    def test_no_ansi_in_mixed_output(self):
        report = _make_report(
            [
                _FakeArtefactDiff("a", "+", True),
                _FakeArtefactDiff("b", "-", True),
                _FakeArtefactDiff("c", "=", True),
                _FakeArtefactDiff("d", "M", True),
            ],
            added=1,
            removed=1,
            unchanged=1,
            modified=1,
        )
        result = format_diff_report(report)
        self.assertNotIn("\x1b", result)

    def test_no_ansi_in_all_suppressed_output(self):
        report = _make_report(
            [_FakeArtefactDiff("x", "=", False)], unchanged=1
        )
        result = format_diff_report(report)
        self.assertNotIn("\x1b", result)


class DeterminismTests(unittest.TestCase):
    """Identical input must produce identical output across calls."""

    def test_same_input_twice_same_output(self):
        artefacts = [
            _FakeArtefactDiff("feature-request.md", "=", True),
            _FakeArtefactDiff("requirements.md", "M", True),
            _FakeArtefactDiff("adr.md", "+", True),
            _FakeArtefactDiff("tasks.json", "-", True),
            _FakeArtefactDiff("worktrees.json", "=", False),
        ]
        report = _make_report(
            artefacts, added=1, removed=1, unchanged=2, modified=1
        )
        first = format_diff_report(report)
        second = format_diff_report(report)
        self.assertEqual(first, second)

    def test_two_structurally_equal_reports_same_output(self):
        artefacts_a = [_FakeArtefactDiff("foo", "+", True)]
        artefacts_b = [_FakeArtefactDiff("foo", "+", True)]
        report_a = _make_report(artefacts_a, added=1, unchanged=4)
        report_b = _make_report(artefacts_b, added=1, unchanged=4)
        self.assertEqual(format_diff_report(report_a), format_diff_report(report_b))


class PurityTests(unittest.TestCase):
    """The renderer must not mutate its input or perform side effects."""

    def test_artefacts_tuple_unmodified(self):
        artefacts = (
            _FakeArtefactDiff("foo", "+", True),
            _FakeArtefactDiff("bar", "=", False),
        )
        report = _make_report(artefacts, added=1, unchanged=4)
        before = tuple(artefacts)
        format_diff_report(report)
        self.assertEqual(tuple(report.artefacts), before)


if __name__ == "__main__":
    unittest.main()
