"""Unit tests for ``pipeline_status.diff_archives``.

Stdlib-only: ``unittest`` + ``tempfile`` + ``unittest.mock``. Does NOT import
``pipeline_status.format_diff`` (sibling fan-out task; may not exist on this
worktree) — ``run_diff`` end-to-end tests install a stub module in
``sys.modules`` for the duration of the test only.
"""
from __future__ import annotations

import argparse
import io
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from pipeline_status.diff_archives import (
    CATEGORY_ADDED,
    CATEGORY_MODIFIED,
    CATEGORY_REMOVED,
    CATEGORY_UNCHANGED,
    ArtefactDiff,
    DiffReport,
    _read_capped,
    _TRACKED_ARTEFACTS,
    add_diff_subparser,
    compute_diff,
    run_diff,
)
from pipeline_status.inspectors import MAX_READ_BYTES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _make_dir(parent: Path, name: str) -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    return d


class _StubFormatDiffModule:
    """Installable ``pipeline_status.format_diff`` shim for run_diff tests.

    Stores the last ``DiffReport`` passed to ``format_diff_report`` so tests
    can assert on the value that ``run_diff`` computed and forwarded.
    """

    def __init__(self) -> None:
        self.last_report: DiffReport | None = None

    def install(self) -> mock._patch:
        stub = types.ModuleType("pipeline_status.format_diff")

        def format_diff_report(report):
            self.last_report = report
            return "STUBBED_OUTPUT\n"

        stub.format_diff_report = format_diff_report  # type: ignore[attr-defined]
        return mock.patch.dict(sys.modules, {"pipeline_status.format_diff": stub})


# ---------------------------------------------------------------------------
# Constants / module-level invariants
# ---------------------------------------------------------------------------

class TestModuleConstants(unittest.TestCase):
    def test_glyph_constants(self):
        self.assertEqual(CATEGORY_ADDED, "+")
        self.assertEqual(CATEGORY_REMOVED, "-")
        self.assertEqual(CATEGORY_UNCHANGED, "=")
        self.assertEqual(CATEGORY_MODIFIED, "M")

    def test_tracked_artefacts_canonical_order(self):
        self.assertEqual(
            _TRACKED_ARTEFACTS,
            (
                "feature-request.md",
                "requirements.md",
                "adr.md",
                "tasks.json",
                "worktrees.json",
            ),
        )
        self.assertEqual(len(_TRACKED_ARTEFACTS), 5)

    def test_no_top_level_archive_import(self):
        # diff_archives must not have eagerly imported pipeline_status.archive.
        # Note: other tests (or prior test runs) may have imported archive on
        # their own; this assertion guards only against diff_archives doing so
        # itself, by inspecting its module dict.
        import pipeline_status.diff_archives as m
        self.assertNotIn("archive", vars(m))


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

class TestDataclasses(unittest.TestCase):
    def test_artefact_diff_is_frozen(self):
        a = ArtefactDiff("adr.md", "=", True)
        with self.assertRaises(Exception):
            a.name = "x"  # type: ignore[misc]

    def test_diff_report_is_frozen(self):
        r = DiffReport(artefacts=tuple(), added=0, removed=0, unchanged=0, modified=0)
        with self.assertRaises(Exception):
            r.added = 5  # type: ignore[misc]

    def test_diff_report_artefacts_is_tuple(self):
        r = DiffReport(artefacts=tuple(), added=0, removed=0, unchanged=0, modified=0)
        self.assertIsInstance(r.artefacts, tuple)


# ---------------------------------------------------------------------------
# _read_capped
# ---------------------------------------------------------------------------

class TestReadCapped(unittest.TestCase):
    def test_returns_bytes_for_normal_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.bin"
            p.write_bytes(b"hello world")
            self.assertEqual(_read_capped(p), b"hello world")

    def test_returns_none_for_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "nope.bin"
            self.assertIsNone(_read_capped(p))

    def test_returns_none_for_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "d"
            d.mkdir()
            self.assertIsNone(_read_capped(d))

    def test_caps_at_max_read_bytes(self):
        # Patch MAX_READ_BYTES via a small file to keep the test fast.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "big.bin"
            p.write_bytes(b"A" * (MAX_READ_BYTES + 1024))
            data = _read_capped(p)
            self.assertIsNotNone(data)
            assert data is not None  # for type-checkers
            self.assertEqual(len(data), MAX_READ_BYTES)

    def test_returns_none_on_oserror_from_open(self):
        # Mock Path.open to raise OSError.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.bin"
            p.write_bytes(b"x")
            with mock.patch.object(Path, "open", side_effect=PermissionError("nope")):
                self.assertIsNone(_read_capped(p))

    def test_returns_none_on_oserror_from_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.bin"
            p.write_bytes(b"x")

            class _RaisingFile:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False

                def read(self_inner, n):
                    raise OSError("boom")

            with mock.patch.object(Path, "open", return_value=_RaisingFile()):
                self.assertIsNone(_read_capped(p))


# ---------------------------------------------------------------------------
# compute_diff — happy paths
# ---------------------------------------------------------------------------

class TestComputeDiffHappyPaths(unittest.TestCase):
    def _make_sides(self, tmp: str) -> tuple[Path, Path]:
        return _make_dir(Path(tmp), "left"), _make_dir(Path(tmp), "right")

    def _assert_invariants(self, report: DiffReport) -> None:
        self.assertEqual(len(report.artefacts), len(_TRACKED_ARTEFACTS))
        self.assertEqual(
            tuple(a.name for a in report.artefacts),
            _TRACKED_ARTEFACTS,
        )
        self.assertEqual(
            report.added + report.removed + report.unchanged + report.modified,
            len(_TRACKED_ARTEFACTS),
        )

    def test_all_equal(self):
        with tempfile.TemporaryDirectory() as tmp:
            left, right = self._make_sides(tmp)
            for name in _TRACKED_ARTEFACTS:
                _write(left / name, b"same")
                _write(right / name, b"same")
            r = compute_diff(left, right)
            self._assert_invariants(r)
            self.assertEqual(r.unchanged, 5)
            self.assertEqual(r.added, 0)
            self.assertEqual(r.removed, 0)
            self.assertEqual(r.modified, 0)
            for a in r.artefacts:
                self.assertEqual(a.category, CATEGORY_UNCHANGED)
                self.assertTrue(a.emit_row)

    def test_all_added(self):
        # left empty, right has everything.
        with tempfile.TemporaryDirectory() as tmp:
            left, right = self._make_sides(tmp)
            for name in _TRACKED_ARTEFACTS:
                _write(right / name, b"x")
            r = compute_diff(left, right)
            self._assert_invariants(r)
            self.assertEqual(r.added, 5)
            for a in r.artefacts:
                self.assertEqual(a.category, CATEGORY_ADDED)
                self.assertTrue(a.emit_row)

    def test_all_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            left, right = self._make_sides(tmp)
            for name in _TRACKED_ARTEFACTS:
                _write(left / name, b"x")
            r = compute_diff(left, right)
            self._assert_invariants(r)
            self.assertEqual(r.removed, 5)
            for a in r.artefacts:
                self.assertEqual(a.category, CATEGORY_REMOVED)
                self.assertTrue(a.emit_row)

    def test_all_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            left, right = self._make_sides(tmp)
            for name in _TRACKED_ARTEFACTS:
                _write(left / name, b"old")
                _write(right / name, b"new")
            r = compute_diff(left, right)
            self._assert_invariants(r)
            self.assertEqual(r.modified, 5)
            for a in r.artefacts:
                self.assertEqual(a.category, CATEGORY_MODIFIED)
                self.assertTrue(a.emit_row)

    def test_mixed(self):
        # feature-request.md: equal
        # requirements.md:    modified
        # adr.md:             removed (left only)
        # tasks.json:         added (right only)
        # worktrees.json:     both absent
        with tempfile.TemporaryDirectory() as tmp:
            left, right = self._make_sides(tmp)
            _write(left / "feature-request.md", b"same")
            _write(right / "feature-request.md", b"same")
            _write(left / "requirements.md", b"old")
            _write(right / "requirements.md", b"new")
            _write(left / "adr.md", b"only-left")
            _write(right / "tasks.json", b"only-right")
            # worktrees.json absent on both sides
            r = compute_diff(left, right)
            self._assert_invariants(r)
            self.assertEqual(r.added, 1)
            self.assertEqual(r.removed, 1)
            self.assertEqual(r.unchanged, 2)  # feature-request + both-absent worktrees
            self.assertEqual(r.modified, 1)

            by_name = {a.name: a for a in r.artefacts}
            self.assertEqual(by_name["feature-request.md"].category, CATEGORY_UNCHANGED)
            self.assertTrue(by_name["feature-request.md"].emit_row)
            self.assertEqual(by_name["requirements.md"].category, CATEGORY_MODIFIED)
            self.assertEqual(by_name["adr.md"].category, CATEGORY_REMOVED)
            self.assertEqual(by_name["tasks.json"].category, CATEGORY_ADDED)
            # Both-absent worktrees: unchanged, no row.
            self.assertEqual(by_name["worktrees.json"].category, CATEGORY_UNCHANGED)
            self.assertFalse(by_name["worktrees.json"].emit_row)

    def test_canonical_order_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            left, right = self._make_sides(tmp)
            # Write in arbitrary order; result should still be canonical.
            for name in reversed(_TRACKED_ARTEFACTS):
                _write(left / name, b"x")
                _write(right / name, b"x")
            r = compute_diff(left, right)
            self.assertEqual(
                tuple(a.name for a in r.artefacts),
                _TRACKED_ARTEFACTS,
            )


# ---------------------------------------------------------------------------
# compute_diff — both-absent and unreadable edge cases
# ---------------------------------------------------------------------------

class TestComputeDiffEdges(unittest.TestCase):
    def test_both_absent_unchanged_no_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            left = _make_dir(Path(tmp), "L")
            right = _make_dir(Path(tmp), "R")
            r = compute_diff(left, right)
            self.assertEqual(r.unchanged, 5)
            for a in r.artefacts:
                self.assertEqual(a.category, CATEGORY_UNCHANGED)
                self.assertFalse(a.emit_row)

    def test_max_read_bytes_truncation_reports_equal(self):
        # Both files identical for the first MAX_READ_BYTES bytes; differ
        # beyond the cap. Expected: category "=".
        with tempfile.TemporaryDirectory() as tmp:
            left = _make_dir(Path(tmp), "L")
            right = _make_dir(Path(tmp), "R")
            name = "feature-request.md"
            prefix = b"A" * MAX_READ_BYTES
            _write(left / name, prefix + b"left-suffix")
            _write(right / name, prefix + b"right-suffix")
            # Leave other artefacts both-absent.
            r = compute_diff(left, right)
            by_name = {a.name: a for a in r.artefacts}
            self.assertEqual(by_name[name].category, CATEGORY_UNCHANGED)
            self.assertTrue(by_name[name].emit_row)

    def test_both_unreadable_treated_as_both_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            left = _make_dir(Path(tmp), "L")
            right = _make_dir(Path(tmp), "R")
            name = "adr.md"
            _write(left / name, b"x")
            _write(right / name, b"y")

            real_open = Path.open

            def fake_open(self, *args, **kwargs):
                if self.name == name:
                    raise PermissionError("no")
                return real_open(self, *args, **kwargs)

            with mock.patch.object(Path, "open", autospec=True, side_effect=fake_open):
                r = compute_diff(left, right)

            by_name = {a.name: a for a in r.artefacts}
            self.assertEqual(by_name[name].category, CATEGORY_UNCHANGED)
            self.assertFalse(by_name[name].emit_row)
            # Sum invariant still holds.
            self.assertEqual(
                r.added + r.removed + r.unchanged + r.modified, 5
            )

    def test_left_unreadable_one_side_present_becomes_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            left = _make_dir(Path(tmp), "L")
            right = _make_dir(Path(tmp), "R")
            name = "tasks.json"
            _write(left / name, b"x")
            _write(right / name, b"y")

            real_open = Path.open

            def fake_open(self, *args, **kwargs):
                # Make the LEFT side unreadable for this artefact only.
                if self == left / name:
                    raise PermissionError("no")
                return real_open(self, *args, **kwargs)

            with mock.patch.object(Path, "open", autospec=True, side_effect=fake_open):
                r = compute_diff(left, right)

            by_name = {a.name: a for a in r.artefacts}
            self.assertEqual(by_name[name].category, CATEGORY_ADDED)
            self.assertTrue(by_name[name].emit_row)

    def test_right_unreadable_one_side_present_becomes_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            left = _make_dir(Path(tmp), "L")
            right = _make_dir(Path(tmp), "R")
            name = "tasks.json"
            _write(left / name, b"x")
            _write(right / name, b"y")

            real_open = Path.open

            def fake_open(self, *args, **kwargs):
                if self == right / name:
                    raise PermissionError("no")
                return real_open(self, *args, **kwargs)

            with mock.patch.object(Path, "open", autospec=True, side_effect=fake_open):
                r = compute_diff(left, right)

            by_name = {a.name: a for a in r.artefacts}
            self.assertEqual(by_name[name].category, CATEGORY_REMOVED)
            self.assertTrue(by_name[name].emit_row)

    @unittest.skipIf(os.name == "nt", "POSIX permission semantics required")
    def test_permission_stripped_file_treated_as_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            left = _make_dir(Path(tmp), "L")
            right = _make_dir(Path(tmp), "R")
            name = "adr.md"
            lp = left / name
            rp = right / name
            _write(lp, b"x")
            _write(rp, b"y")
            # Strip all permissions on the left file. Running as root would
            # defeat this; skip when uid==0.
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                self.skipTest("root bypasses POSIX permission bits")
            os.chmod(lp, 0)
            try:
                r = compute_diff(left, right)
            finally:
                os.chmod(lp, stat.S_IRUSR | stat.S_IWUSR)

            by_name = {a.name: a for a in r.artefacts}
            # left unreadable, right readable -> added.
            self.assertEqual(by_name[name].category, CATEGORY_ADDED)

    def test_self_comparison_all_equal(self):
        # left_dir == right_dir; every file is trivially equal to itself.
        with tempfile.TemporaryDirectory() as tmp:
            d = _make_dir(Path(tmp), "only")
            for name in _TRACKED_ARTEFACTS:
                _write(d / name, b"content-" + name.encode())
            r = compute_diff(d, d)
            self.assertEqual(r.unchanged, 5)
            for a in r.artefacts:
                self.assertEqual(a.category, CATEGORY_UNCHANGED)
                self.assertTrue(a.emit_row)

    def test_footer_count_invariant_random_mix(self):
        with tempfile.TemporaryDirectory() as tmp:
            left = _make_dir(Path(tmp), "L")
            right = _make_dir(Path(tmp), "R")
            # feature-request.md: equal
            _write(left / "feature-request.md", b"x")
            _write(right / "feature-request.md", b"x")
            # adr.md: modified
            _write(left / "adr.md", b"a")
            _write(right / "adr.md", b"b")
            # tasks.json: added (right only)
            _write(right / "tasks.json", b"a")
            # requirements.md and worktrees.json absent on both sides.
            r = compute_diff(left, right)
            self.assertEqual(
                r.added + r.removed + r.unchanged + r.modified,
                len(_TRACKED_ARTEFACTS),
            )


# ---------------------------------------------------------------------------
# add_diff_subparser — argparse smoke
# ---------------------------------------------------------------------------

class TestAddDiffSubparser(unittest.TestCase):
    def _build(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="pipeline-status")
        subparsers = parser.add_subparsers(dest="cmd", required=False)
        add_diff_subparser(subparsers)
        return parser

    def test_returns_subparser(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="cmd")
        sp = add_diff_subparser(subparsers)
        self.assertIsInstance(sp, argparse.ArgumentParser)

    def test_parse_positional_name(self):
        parser = self._build()
        args = parser.parse_args(["diff", "foo"])
        self.assertEqual(args.cmd, "diff")
        self.assertEqual(args.name, "foo")
        self.assertIsNone(args.against)
        self.assertIs(args.func, run_diff)

    def test_parse_against_option(self):
        parser = self._build()
        args = parser.parse_args(["diff", "--against", "bar", "foo"])
        self.assertEqual(args.name, "foo")
        self.assertEqual(args.against, "bar")

    def test_missing_positional_name_exits_2(self):
        parser = self._build()
        # argparse writes to stderr; silence it.
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as ctx:
                parser.parse_args(["diff"])
        self.assertEqual(ctx.exception.code, 2)

    def test_watch_flag_not_accepted_on_subparser(self):
        parser = self._build()
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as ctx:
                parser.parse_args(["diff", "foo", "--watch"])
        self.assertEqual(ctx.exception.code, 2)

    def test_no_extra_flags(self):
        # Specifically reject --interval, --json, --brief — any unknown flag.
        parser = self._build()
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as ctx:
                parser.parse_args(["diff", "foo", "--json"])
        self.assertEqual(ctx.exception.code, 2)


# ---------------------------------------------------------------------------
# run_diff — end-to-end
# ---------------------------------------------------------------------------

class TestRunDiffEndToEnd(unittest.TestCase):
    def _layout(self, root: Path) -> tuple[Path, Path]:
        """Create .claude/state and .claude/state/archive under ``root``.

        Returns (state_dir, archive_root).
        """
        state_dir = root / ".claude" / "state"
        archive_root = state_dir / "archive"
        archive_root.mkdir(parents=True)
        return state_dir, archive_root

    def _populate(self, d: Path, contents: dict[str, bytes] | None = None) -> None:
        d.mkdir(parents=True, exist_ok=True)
        if contents:
            for name, data in contents.items():
                (d / name).write_bytes(data)

    def _ns(self, name: str, against: str | None = None) -> argparse.Namespace:
        return argparse.Namespace(name=name, against=against)

    def test_happy_live_vs_archive_exit_0(self):
        stub = _StubFormatDiffModule()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir, archive_root = self._layout(root)
            # Populate live state and archive "foo".
            self._populate(state_dir, {"adr.md": b"new"})
            self._populate(archive_root / "foo", {"adr.md": b"old"})
            captured = io.StringIO()
            with stub.install(), \
                    mock.patch.object(Path, "cwd", return_value=root), \
                    mock.patch("sys.stdout", captured):
                rc = run_diff(self._ns("foo"))
            self.assertEqual(rc, 0)
            self.assertEqual(captured.getvalue(), "STUBBED_OUTPUT\n")
            self.assertIsNotNone(stub.last_report)
            assert stub.last_report is not None
            # adr.md was "new" vs "old" -> modified.
            by_name = {a.name: a for a in stub.last_report.artefacts}
            self.assertEqual(by_name["adr.md"].category, CATEGORY_MODIFIED)

    def test_happy_archive_vs_archive_exit_0(self):
        stub = _StubFormatDiffModule()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir, archive_root = self._layout(root)
            self._populate(archive_root / "foo", {"adr.md": b"a"})
            self._populate(archive_root / "bar", {"adr.md": b"b"})
            with stub.install(), \
                    mock.patch.object(Path, "cwd", return_value=root), \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                rc = run_diff(self._ns("foo", against="bar"))
            self.assertEqual(rc, 0)
            self.assertIsNotNone(stub.last_report)

    def test_empty_name_slug_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._layout(root)
            captured_err = io.StringIO()
            with mock.patch.object(Path, "cwd", return_value=root), \
                    mock.patch("sys.stderr", captured_err):
                rc = run_diff(self._ns("   "))  # slugifies to ""
            self.assertEqual(rc, 1)
            self.assertIn(
                "pipeline-status: error: diff name is empty after normalisation",
                captured_err.getvalue(),
            )

    def test_empty_against_slug_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir, archive_root = self._layout(root)
            self._populate(archive_root / "foo", {"adr.md": b"x"})
            captured_err = io.StringIO()
            with mock.patch.object(Path, "cwd", return_value=root), \
                    mock.patch("sys.stderr", captured_err):
                rc = run_diff(self._ns("foo", against="!!!"))
            self.assertEqual(rc, 1)
            self.assertIn(
                "pipeline-status: error: diff --against value is empty after normalisation",
                captured_err.getvalue(),
            )

    def test_right_archive_missing_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._layout(root)
            captured_err = io.StringIO()
            with mock.patch.object(Path, "cwd", return_value=root), \
                    mock.patch("sys.stderr", captured_err):
                rc = run_diff(self._ns("ghost"))
            self.assertEqual(rc, 1)
            err = captured_err.getvalue()
            self.assertIn("pipeline-status: error: archive 'ghost' not found at", err)

    def test_left_archive_missing_with_against_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir, archive_root = self._layout(root)
            self._populate(archive_root / "foo", {"adr.md": b"x"})
            captured_err = io.StringIO()
            with mock.patch.object(Path, "cwd", return_value=root), \
                    mock.patch("sys.stderr", captured_err):
                rc = run_diff(self._ns("foo", against="ghost"))
            self.assertEqual(rc, 1)
            err = captured_err.getvalue()
            self.assertIn("pipeline-status: error: archive 'ghost' not found at", err)

    def test_live_state_missing_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # archive_root present so the right side resolves, but no
            # .claude/state directory beyond that.
            archive_root = root / ".claude" / "state" / "archive"
            archive_root.mkdir(parents=True)
            # Right archive exists.
            (archive_root / "foo").mkdir()
            # But we want state_dir to NOT be a directory. Replace it with a
            # file: rmdir the empty parent chain first.
            #
            # Actually .claude/state IS a directory (archive_root's parent).
            # To exercise the exit-2 path we need state_dir.is_dir() to be
            # False *after* archive_root.is_dir() is True. We swap state_dir
            # by removing it and creating a sentinel: that's impossible while
            # archive_root exists. Instead, simulate via mock: patch
            # Path.is_dir to return False for state_dir specifically.
            state_dir = archive_root.parent
            real_is_dir = Path.is_dir

            def fake_is_dir(self):
                if self == state_dir:
                    return False
                return real_is_dir(self)

            captured_err = io.StringIO()
            with mock.patch.object(Path, "cwd", return_value=root), \
                    mock.patch.object(Path, "is_dir", autospec=True, side_effect=fake_is_dir), \
                    mock.patch("sys.stderr", captured_err):
                rc = run_diff(self._ns("foo"))
            self.assertEqual(rc, 2)
            self.assertIn(
                "pipeline-status: error: .claude/state/ not found or not a directory",
                captured_err.getvalue(),
            )

    def test_against_supplied_live_state_missing_is_irrelevant(self):
        # With --against, live state existence is not consulted; exit 0.
        stub = _StubFormatDiffModule()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_root = root / ".claude" / "state" / "archive"
            archive_root.mkdir(parents=True)
            (archive_root / "foo").mkdir()
            (archive_root / "bar").mkdir()
            state_dir = archive_root.parent
            real_is_dir = Path.is_dir

            def fake_is_dir(self):
                if self == state_dir:
                    return False
                return real_is_dir(self)

            with stub.install(), \
                    mock.patch.object(Path, "cwd", return_value=root), \
                    mock.patch.object(Path, "is_dir", autospec=True, side_effect=fake_is_dir), \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                rc = run_diff(self._ns("foo", against="bar"))
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
