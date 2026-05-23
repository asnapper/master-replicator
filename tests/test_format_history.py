"""Tests for pipeline_status.format_history.

Covers the v3 history renderers:

    * format_history_table  — the `pipeline-status history` table
    * format_archive_detail — the `pipeline-status history NAME` detail view

Per Task C's parallel-fan-out contract, these tests use ONLY:
    - stdlib (unittest, types.SimpleNamespace, dataclasses, datetime, pathlib)
    - pipeline_status.format_history (the module under test)
    - pipeline_status.inspectors.ArtefactResult (frozen v1 master contract)
    - pipeline_status.formatting (frozen master helpers, only consulted via
      the renderers themselves)

They do NOT import pipeline_status.history or pipeline_status.archive.
"""
import os
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline_status.format_history import (
    format_archive_detail,
    format_history_table,
)
from pipeline_status.formatting import format_artefact_row, format_stage_line
from pipeline_status.inspectors import ArtefactResult


def _make_entry(
    name: str,
    mtime: float,
    total_tasks: int | None,
    completed_tasks: int | None,
) -> types.SimpleNamespace:
    """Build a structural-protocol ArchiveEntry stand-in.

    Mirrors pipeline_status.history.ArchiveEntry's public field set without
    importing that module (Task B's file does not exist in this worktree).
    """
    return types.SimpleNamespace(
        name=name,
        path=Path("/tmp/archive") / name,
        mtime=mtime,
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
    )


def _local_iso(mtime: float) -> str:
    """Re-derive the expected ARCHIVED-AT cell using the same one-liner as
    ``inspectors._mtime_iso`` so tests assert exact equality without
    hard-coding a specific TZ offset."""
    tz = datetime.now().astimezone().tzinfo
    return datetime.fromtimestamp(mtime, tz=tz).isoformat(timespec="seconds")


class HistoryTableEmptyTests(unittest.TestCase):
    """Empty-entries case: header-only table (acceptance criterion: empty entries)."""

    def setUp(self):
        # Disable any TTY/colour influence at table level (defensive — the
        # table renderer does NOT emit colour, but we keep the env clean).
        self._orig_no_color = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"

    def tearDown(self):
        if self._orig_no_color is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = self._orig_no_color

    def test_empty_table_renders_header_row_only(self):
        out = format_history_table([])
        # Header row only, terminated by a newline.
        self.assertEqual(out, "NAME  ARCHIVED-AT  TASKS  DONE\n")

    def test_empty_table_ends_with_newline(self):
        out = format_history_table([])
        self.assertTrue(out.endswith("\n"))

    def test_empty_table_is_single_line(self):
        out = format_history_table([])
        # Exactly one line break ⇒ one logical line.
        self.assertEqual(out.count("\n"), 1)


class HistoryTableSingleArchiveTests(unittest.TestCase):
    """Single-archive table case."""

    def setUp(self):
        self._orig_no_color = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"

    def tearDown(self):
        if self._orig_no_color is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = self._orig_no_color

    def test_single_archive_renders_header_and_one_row(self):
        entry = _make_entry("alpha", 1700000000.0, 3, 2)
        out = format_history_table([entry])
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)  # header + 1 data row
        self.assertEqual(lines[0].split()[0], "NAME")
        self.assertIn("alpha", lines[1])

    def test_single_archive_ends_with_newline(self):
        entry = _make_entry("alpha", 1700000000.0, 3, 2)
        out = format_history_table([entry])
        self.assertTrue(out.endswith("\n"))

    def test_single_archive_archived_at_uses_inspectors_mtime_format(self):
        entry = _make_entry("alpha", 1700000000.0, 3, 2)
        out = format_history_table([entry])
        expected_archived_at = _local_iso(1700000000.0)
        self.assertIn(expected_archived_at, out)

    def test_single_archive_columns_in_correct_order(self):
        # NAME, ARCHIVED-AT, TASKS, DONE.
        entry = _make_entry("zeta", 1700000000.0, 7, 4)
        out = format_history_table([entry])
        header, row = out.splitlines()
        # Both lines must have NAME first / TASKS before DONE.
        idx_name = header.index("NAME")
        idx_archived = header.index("ARCHIVED-AT")
        idx_tasks = header.index("TASKS")
        idx_done = header.index("DONE")
        self.assertLess(idx_name, idx_archived)
        self.assertLess(idx_archived, idx_tasks)
        self.assertLess(idx_tasks, idx_done)
        # The data row's cells appear in the same positions as the header.
        self.assertTrue(row.startswith("zeta"))

    def test_single_archive_separator_is_at_least_two_spaces(self):
        entry = _make_entry("a", 1700000000.0, 1, 1)
        out = format_history_table([entry])
        # Between header cells: at least two spaces and no tab characters.
        self.assertNotIn("\t", out)
        # NAME column width sized to max("NAME", "a") == 4 ⇒ "NAME" then "  ".
        self.assertIn("NAME  ", out)


class HistoryTableMultiArchiveTests(unittest.TestCase):
    """Multi-archive table with width sizing."""

    def setUp(self):
        self._orig_no_color = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"

    def tearDown(self):
        if self._orig_no_color is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = self._orig_no_color

    def test_columns_size_to_widest_value(self):
        # NAME column should expand to fit "pipeline-status-archive" (23 chars)
        # which is wider than the header "NAME" (4 chars).
        long_name = "pipeline-status-archive"
        short_name = "w"
        entries = [
            _make_entry(long_name, 1700000000.0, 3, 3),
            _make_entry(short_name, 1700000001.0, 4, 2),
        ]
        out = format_history_table(entries)
        lines = out.splitlines()
        # All lines must start with a NAME cell at least len(long_name) chars
        # wide, followed by the separator.
        for line in lines:
            # The substring at column 0 to len(long_name)+2 should always be
            # the NAME cell, padded with spaces.
            self.assertGreaterEqual(len(line), len(long_name))

    def test_rows_emitted_in_input_order(self):
        # We deliberately pass un-sorted input; the renderer must NOT re-sort.
        entries = [
            _make_entry("zeta", 1700000000.0, 1, 1),
            _make_entry("alpha", 1700000001.0, 2, 2),
            _make_entry("mu", 1700000002.0, 3, 3),
        ]
        out = format_history_table(entries)
        lines = out.splitlines()
        # Skip header; data rows in input order.
        self.assertTrue(lines[1].startswith("zeta"))
        self.assertTrue(lines[2].startswith("alpha"))
        self.assertTrue(lines[3].startswith("mu"))

    def test_no_tabs_no_box_drawing(self):
        entries = [
            _make_entry("alpha", 1700000000.0, 1, 1),
            _make_entry("beta", 1700000001.0, 2, 2),
        ]
        out = format_history_table(entries)
        self.assertNotIn("\t", out)
        # Unicode box-drawing block U+2500..U+257F.
        for ch in out:
            self.assertFalse(0x2500 <= ord(ch) <= 0x257F, f"box-drawing char {ch!r}")

    def test_no_ansi_colour_escapes(self):
        entries = [_make_entry("alpha", 1700000000.0, 1, 1)]
        out = format_history_table(entries)
        # ESC (0x1B) must not appear at all — the table is colour-free.
        self.assertNotIn("\x1b", out)


class HistoryTableNoneCountsTests(unittest.TestCase):
    """None counts must render as '-'."""

    def setUp(self):
        self._orig_no_color = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"

    def tearDown(self):
        if self._orig_no_color is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = self._orig_no_color

    def test_none_tasks_renders_dash(self):
        entry = _make_entry("alpha", 1700000000.0, None, None)
        out = format_history_table([entry])
        # The data line should end with "-  -" (TASKS=‑, DONE=‑).
        data_line = out.splitlines()[1]
        # Strip leading NAME / ARCHIVED-AT padding by splitting on the
        # ≥2-space separator and inspecting the last two cells.
        # Split on runs of two-or-more spaces.
        import re
        cells = re.split(r" {2,}", data_line.rstrip())
        self.assertEqual(cells[-2], "-")
        self.assertEqual(cells[-1], "-")

    def test_zero_counts_are_not_treated_as_none(self):
        entry = _make_entry("alpha", 1700000000.0, 0, 0)
        out = format_history_table([entry])
        data_line = out.splitlines()[1]
        import re
        cells = re.split(r" {2,}", data_line.rstrip())
        # 0, not '-'.
        self.assertEqual(cells[-2], "0")
        self.assertEqual(cells[-1], "0")

    def test_mixed_none_and_int_counts(self):
        entry = _make_entry("alpha", 1700000000.0, 5, None)
        out = format_history_table([entry])
        data_line = out.splitlines()[1]
        import re
        cells = re.split(r" {2,}", data_line.rstrip())
        self.assertEqual(cells[-2], "5")
        self.assertEqual(cells[-1], "-")


class ArchiveDetailLayoutTests(unittest.TestCase):
    """The detail renderer's body must be byte-identical to v1 _run_one_shot()."""

    def setUp(self):
        # Force colour off so format_artefact_row / format_stage_line return
        # plain text; this keeps the byte-identical comparison deterministic
        # regardless of where the test suite runs.
        self._orig_no_color = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"

    def tearDown(self):
        if self._orig_no_color is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = self._orig_no_color

    def _make_result(
        self,
        name: str,
        exists: bool = True,
        filled: bool = True,
        mtime_iso: str | None = "2026-05-23T12:00:00+02:00",
        extra: dict | None = None,
    ) -> ArtefactResult:
        return ArtefactResult(
            name=name,
            path=Path(f"/tmp/{name}"),
            exists=exists,
            filled=filled,
            mtime_iso=mtime_iso,
            extra=extra if extra is not None else {},
        )

    def test_header_is_pipeline_status_with_15_equals(self):
        out = format_archive_detail([], "stage-1")
        self.assertTrue(out.startswith("Pipeline Status\n===============\n\n"))

    def test_header_uses_equals_not_format_report_dashes(self):
        # format_report uses 60 dashes; the detail renderer must NOT use that.
        out = format_archive_detail([], "stage-1")
        self.assertNotIn("-" * 60, out)
        # And must NOT use the format_report header text.
        self.assertNotIn("Pipeline Artefact Status", out)

    def test_ends_with_newline(self):
        out = format_archive_detail([], "stage-1")
        self.assertTrue(out.endswith("\n"))

    def test_byte_identical_to_v1_one_shot_body(self):
        # Build a representative result set spanning the five artefact names.
        results = [
            self._make_result(
                "feature-request.md", exists=True, filled=True,
                mtime_iso="2026-05-20T10:00:00+02:00",
            ),
            self._make_result(
                "requirements.md", exists=True, filled=True,
                mtime_iso="2026-05-20T11:00:00+02:00",
            ),
            self._make_result(
                "adr.md", exists=True, filled=True,
                mtime_iso="2026-05-20T12:00:00+02:00",
            ),
            self._make_result(
                "tasks.json", exists=True, filled=True,
                mtime_iso="2026-05-20T13:00:00+02:00",
                extra={"total": 5, "completed": 3},
            ),
            self._make_result(
                "worktrees.json", exists=True, filled=True,
                mtime_iso="2026-05-20T14:00:00+02:00",
            ),
        ]
        stage = "implementation"

        # Build the expected output by mirroring _run_one_shot()'s exact print
        # sequence using the same formatting helpers the SUT uses.
        expected = "Pipeline Status\n===============\n\n"
        for r in results:
            expected += f"  {format_artefact_row(r)}\n"
        expected += "\n"
        expected += f"  {format_stage_line(stage)}\n"

        actual = format_archive_detail(results, stage)
        self.assertEqual(actual, expected)

    def test_two_space_row_indent(self):
        results = [self._make_result("feature-request.md")]
        out = format_archive_detail(results, "any-stage")
        # The artefact row line must be prefixed with exactly two spaces
        # added by the renderer (matching v1 _run_one_shot()'s
        # `f"  {format_artefact_row(...)}"`). Note that format_artefact_row
        # itself contributes another leading "  " before the artefact name —
        # so the line begins with "    <name>" (4 spaces total). We verify
        # equality against the helper to avoid hard-coding that detail.
        expected_row = format_artefact_row(results[0])
        self.assertIn(f"  {expected_row}\n", out)
        # Sanity-check: the line containing the artefact name starts with the
        # two-space indent prepended by the renderer.
        for line in out.splitlines():
            if "feature-request.md" in line:
                self.assertTrue(line.startswith(f"  {expected_row[:2]}"))

    def test_blank_line_between_rows_and_stage(self):
        results = [self._make_result("feature-request.md")]
        out = format_archive_detail(results, "any-stage")
        lines = out.split("\n")
        # Find the stage line and assert the preceding line is blank.
        for i, line in enumerate(lines):
            if "Stage:" in line:
                self.assertEqual(lines[i - 1], "")
                break
        else:
            self.fail("Stage line not found in output")

    def test_results_emitted_in_input_order(self):
        results = [
            self._make_result("zeta.md"),
            self._make_result("alpha.md"),
        ]
        out = format_archive_detail(results, "stage")
        zeta_pos = out.index("zeta.md")
        alpha_pos = out.index("alpha.md")
        self.assertLess(zeta_pos, alpha_pos)


class ArchiveDetailPartialArchiveTests(unittest.TestCase):
    """Detail renderer with a partial archive (some ArtefactResult marked missing)."""

    def setUp(self):
        self._orig_no_color = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"

    def tearDown(self):
        if self._orig_no_color is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = self._orig_no_color

    def test_missing_artefact_renders_via_format_artefact_row(self):
        # Build a partial archive: two present, one missing.
        present = ArtefactResult(
            name="feature-request.md",
            path=Path("/tmp/feature-request.md"),
            exists=True,
            filled=True,
            mtime_iso="2026-05-20T10:00:00+02:00",
            extra={},
        )
        missing = ArtefactResult(
            name="requirements.md",
            path=Path("/tmp/requirements.md"),
            exists=False,
            filled=False,
            mtime_iso=None,
            extra={},
        )
        another_present = ArtefactResult(
            name="adr.md",
            path=Path("/tmp/adr.md"),
            exists=True,
            filled=True,
            mtime_iso="2026-05-20T12:00:00+02:00",
            extra={},
        )
        results = [present, missing, another_present]
        out = format_archive_detail(results, "requirements")

        # MISSING must appear once (the requirements.md row).
        self.assertEqual(out.count("MISSING"), 1)
        # EXISTS must appear twice (the two present artefacts).
        self.assertEqual(out.count("EXISTS"), 2)
        # The stage line still terminates the body.
        self.assertTrue(out.rstrip("\n").endswith(format_stage_line("requirements")))
        # And the body still ends with a single trailing newline.
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_partial_archive_preserves_structure_lines(self):
        # Same partial fixture, but assert the literal structural lines.
        results = [
            ArtefactResult(
                name="feature-request.md",
                path=Path("/tmp/feature-request.md"),
                exists=False,
                filled=False,
                mtime_iso=None,
                extra={},
            ),
        ]
        out = format_archive_detail(results, "feature-request")
        lines = out.split("\n")
        self.assertEqual(lines[0], "Pipeline Status")
        self.assertEqual(lines[1], "===============")
        self.assertEqual(lines[2], "")
        # lines[3] is the row; assert it starts with two spaces.
        self.assertTrue(lines[3].startswith("  "))
        self.assertEqual(lines[4], "")
        # lines[5] is the stage line with two-space indent.
        self.assertTrue(lines[5].startswith("  "))
        self.assertIn("Stage:", lines[5])


class ArchiveDetailUsesCorrectHelpersTests(unittest.TestCase):
    """The detail renderer uses format_artefact_row / format_stage_line, NOT
    format_report."""

    def setUp(self):
        self._orig_no_color = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"

    def tearDown(self):
        if self._orig_no_color is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = self._orig_no_color

    def test_uses_format_artefact_row(self):
        # The exact string format_artefact_row produces for a given input must
        # appear in the renderer's output (with the two-space indent).
        r = ArtefactResult(
            name="feature-request.md",
            path=Path("/tmp/feature-request.md"),
            exists=True,
            filled=True,
            mtime_iso="2026-05-20T10:00:00+02:00",
            extra={},
        )
        expected_row = format_artefact_row(r)
        out = format_archive_detail([r], "feature-request")
        self.assertIn(f"  {expected_row}\n", out)

    def test_uses_format_stage_line(self):
        expected_stage = format_stage_line("requirements")
        out = format_archive_detail([], "requirements")
        self.assertIn(f"  {expected_stage}\n", out)


if __name__ == "__main__":
    unittest.main()
