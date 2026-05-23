"""Tests for :mod:`pipeline_status.restore` (task-001 / KAN-22).

stdlib-only: ``unittest`` + ``tempfile.TemporaryDirectory`` + ``io.StringIO``
+ ``unittest.mock``.  No subprocess; no real ``.claude/state/`` access; each
test isolates its filesystem state under a ``TemporaryDirectory`` and changes
into it for the duration of the test.

May import ``pipeline_status.archive`` eagerly here (test fixture setup);
production code in ``pipeline_status.restore`` keeps that import lazy.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline_status import archive
from pipeline_status.archive import TRACKED_ARTEFACTS
from pipeline_status.restore import add_restore_subparser, run_restore


# ---------------------------------------------------------------------------
# Helpers / base
# ---------------------------------------------------------------------------


class _RunRestoreBase(unittest.TestCase):
    """TemporaryDirectory + cwd swap + IO capture."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self._old_cwd)
        self.state_dir = self.tmp / ".claude" / "state"
        self.archive_root = self.state_dir / "archive"

    def _make_state(self, files: dict[str, str] | None = None) -> None:
        self.state_dir.mkdir(parents=True)
        for name, body in (files or {}).items():
            (self.state_dir / name).write_text(body, encoding="utf-8")

    def _make_archive(
        self, slug: str, files: dict[str, str] | None = None
    ) -> Path:
        """Create .claude/state/archive/<slug>/ with the given files."""
        archive_dir = self.archive_root / slug
        archive_dir.mkdir(parents=True)
        for name, body in (files or {}).items():
            (archive_dir / name).write_text(body, encoding="utf-8")
        return archive_dir

    @staticmethod
    def _ns(**kwargs: object) -> argparse.Namespace:
        kwargs.setdefault("name", "")
        kwargs.setdefault("force", False)
        return argparse.Namespace(**kwargs)

    def _capture(self, ns: argparse.Namespace) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            rc = run_restore(ns)
        return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class RestoreHappyPathTests(_RunRestoreBase):
    def test_clean_restore_all_five(self) -> None:
        """All five artefacts in archive, no live files -> exit 0, all copied."""
        self._make_state()  # state dir exists, but empty
        files = {b: f"archived-{b}\n" for b in TRACKED_ARTEFACTS}
        self._make_archive("foo-bar", files)

        ns = self._ns(name="foo-bar")
        rc, out, err = self._capture(ns)

        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertIn(
            "Restored 5 file(s) from .claude/state/archive/foo-bar/", out
        )
        for b in TRACKED_ARTEFACTS:
            self.assertTrue((self.state_dir / b).is_file())
            self.assertEqual(
                (self.state_dir / b).read_text(encoding="utf-8"),
                f"archived-{b}\n",
            )

    def test_partial_archive(self) -> None:
        """Archive has 2 of 5; only those 2 are restored."""
        self._make_state()
        self._make_archive(
            "partial",
            {
                "requirements.md": "REQS\n",
                "adr.md": "ADR\n",
            },
        )
        ns = self._ns(name="partial")
        rc, out, err = self._capture(ns)

        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertIn(
            "Restored 2 file(s) from .claude/state/archive/partial/", out
        )
        self.assertTrue((self.state_dir / "requirements.md").is_file())
        self.assertTrue((self.state_dir / "adr.md").is_file())
        self.assertFalse((self.state_dir / "feature-request.md").exists())
        self.assertFalse((self.state_dir / "tasks.json").exists())
        self.assertFalse((self.state_dir / "worktrees.json").exists())

    def test_empty_archive_zero_files(self) -> None:
        """Archive dir exists but is empty -> exit 0, Restored 0 file(s) ..."""
        self._make_state()
        self._make_archive("empty", {})
        ns = self._ns(name="empty")
        rc, out, err = self._capture(ns)

        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertIn(
            "Restored 0 file(s) from .claude/state/archive/empty/", out
        )

    def test_force_flag_on_clean_state_still_succeeds(self) -> None:
        """--force with no conflicts behaves like default."""
        self._make_state()
        self._make_archive("snap", {"adr.md": "x\n"})
        ns = self._ns(name="snap", force=True)
        rc, _out, err = self._capture(ns)
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertTrue((self.state_dir / "adr.md").is_file())


# ---------------------------------------------------------------------------
# Collision paths
# ---------------------------------------------------------------------------


class RestoreCollisionTests(_RunRestoreBase):
    def test_single_conflict_no_force(self) -> None:
        self._make_state({"adr.md": "LIVE ADR\n"})
        self._make_archive("snap", {"adr.md": "ARCHIVED ADR\n"})

        ns = self._ns(name="snap")
        rc, out, err = self._capture(ns)

        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        # exact error line for the file conflict (note: dst is state_dir/adr.md)
        expected_dst = self.state_dir / "adr.md"
        self.assertIn(
            f"pipeline-status: error: cannot overwrite existing file: {expected_dst}",
            err,
        )
        # hint added when conflicts are all file-type
        self.assertIn(
            "pipeline-status: error: (use --force to overwrite)", err
        )
        # Live file unchanged (no partial restore)
        self.assertEqual(
            (self.state_dir / "adr.md").read_text(encoding="utf-8"),
            "LIVE ADR\n",
        )

    def test_multiple_conflicts_no_force_lists_all(self) -> None:
        self._make_state(
            {
                "requirements.md": "LIVE REQS\n",
                "adr.md": "LIVE ADR\n",
            }
        )
        self._make_archive(
            "snap",
            {
                "feature-request.md": "ARCH FR\n",  # no conflict
                "requirements.md": "ARCH REQS\n",
                "adr.md": "ARCH ADR\n",
            },
        )
        ns = self._ns(name="snap")
        rc, out, err = self._capture(ns)

        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        # All conflicts present in stderr; non-conflicting file NOT created.
        err_lines = err.strip().splitlines()
        # 2 conflict lines + 1 hint line
        self.assertEqual(len(err_lines), 3)
        self.assertIn(
            f"pipeline-status: error: cannot overwrite existing file: {self.state_dir / 'requirements.md'}",
            err_lines,
        )
        self.assertIn(
            f"pipeline-status: error: cannot overwrite existing file: {self.state_dir / 'adr.md'}",
            err_lines,
        )
        self.assertIn(
            "pipeline-status: error: (use --force to overwrite)", err_lines
        )
        # Non-conflict file NOT created (no partial restore).
        self.assertFalse((self.state_dir / "feature-request.md").exists())
        # Live files unchanged.
        self.assertEqual(
            (self.state_dir / "requirements.md").read_text(encoding="utf-8"),
            "LIVE REQS\n",
        )
        self.assertEqual(
            (self.state_dir / "adr.md").read_text(encoding="utf-8"),
            "LIVE ADR\n",
        )

    def test_conflicts_ordered_in_tracked_artefacts_order(self) -> None:
        """Conflict lines respect TRACKED_ARTEFACTS declaration order."""
        self._make_state(
            {b: f"live-{b}\n" for b in TRACKED_ARTEFACTS}
        )
        self._make_archive(
            "snap",
            {b: f"arch-{b}\n" for b in TRACKED_ARTEFACTS},
        )
        ns = self._ns(name="snap")
        rc, _out, err = self._capture(ns)
        self.assertEqual(rc, 1)
        # First 5 stderr lines are conflict lines in TRACKED_ARTEFACTS order.
        err_lines = err.strip().splitlines()
        for i, b in enumerate(TRACKED_ARTEFACTS):
            self.assertEqual(
                err_lines[i],
                f"pipeline-status: error: cannot overwrite existing file: {self.state_dir / b}",
            )

    def test_force_overwrites_conflicts(self) -> None:
        self._make_state(
            {
                "requirements.md": "LIVE REQS\n",
                "adr.md": "LIVE ADR\n",
            }
        )
        self._make_archive(
            "snap",
            {
                "requirements.md": "ARCH REQS\n",
                "adr.md": "ARCH ADR\n",
            },
        )
        ns = self._ns(name="snap", force=True)
        rc, out, err = self._capture(ns)
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertIn(
            "Restored 2 file(s) from .claude/state/archive/snap/", out
        )
        self.assertEqual(
            (self.state_dir / "requirements.md").read_text(encoding="utf-8"),
            "ARCH REQS\n",
        )
        self.assertEqual(
            (self.state_dir / "adr.md").read_text(encoding="utf-8"),
            "ARCH ADR\n",
        )


# ---------------------------------------------------------------------------
# Decision 5: directory at live target
# ---------------------------------------------------------------------------


class RestoreDirectoryGuardTests(_RunRestoreBase):
    def test_live_target_is_a_directory_without_force(self) -> None:
        """Without --force, a live dir-at-target is still a conflict (exit 1)."""
        self._make_state()
        (self.state_dir / "tasks.json").mkdir()  # live target is a directory
        self._make_archive("snap", {"tasks.json": '{"x":1}\n'})

        ns = self._ns(name="snap")
        rc, out, err = self._capture(ns)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        # Even without --force, a directory is a directory conflict.
        expected_dst = self.state_dir / "tasks.json"
        self.assertIn(
            f"pipeline-status: error: cannot overwrite directory: {expected_dst}",
            err,
        )
        # Hint must NOT be present (directory conflicts are not fixable by --force).
        self.assertNotIn("(use --force to overwrite)", err)
        # Live dir untouched (still a dir).
        self.assertTrue((self.state_dir / "tasks.json").is_dir())

    def test_live_target_is_a_directory_with_force_still_fails(self) -> None:
        """Decision 5: even --force will not overwrite a directory."""
        self._make_state()
        (self.state_dir / "tasks.json").mkdir()
        self._make_archive("snap", {"tasks.json": '{"x":1}\n'})

        ns = self._ns(name="snap", force=True)
        rc, out, err = self._capture(ns)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        expected_dst = self.state_dir / "tasks.json"
        self.assertIn(
            f"pipeline-status: error: cannot overwrite directory: {expected_dst}",
            err,
        )
        # Hint suppressed when any directory conflict is present.
        self.assertNotIn("(use --force to overwrite)", err)
        # Live dir still a directory (nothing copied).
        self.assertTrue((self.state_dir / "tasks.json").is_dir())


# ---------------------------------------------------------------------------
# Missing-state / missing-archive / empty-slug
# ---------------------------------------------------------------------------


class RestoreMissingStateTests(_RunRestoreBase):
    def test_missing_state_dir_returns_2(self) -> None:
        # Do NOT create state_dir.
        ns = self._ns(name="anything")
        rc, out, err = self._capture(ns)
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertEqual(
            err.strip(),
            "pipeline-status: error: .claude/state/ not found or not a directory",
        )

    def test_state_path_is_a_file_returns_2(self) -> None:
        (self.tmp / ".claude").mkdir()
        (self.tmp / ".claude" / "state").write_text("oops")
        ns = self._ns(name="anything")
        rc, _out, err = self._capture(ns)
        self.assertEqual(rc, 2)
        self.assertIn("not found or not a directory", err)


class RestoreMissingArchiveTests(_RunRestoreBase):
    def test_archive_dir_missing_returns_1(self) -> None:
        self._make_state()
        # archive_root does not exist at all.
        ns = self._ns(name="nope")
        rc, out, err = self._capture(ns)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        expected_archive_dir = self.state_dir / "archive" / "nope"
        self.assertIn(
            f"pipeline-status: error: archive 'nope' not found at {expected_archive_dir}",
            err,
        )

    def test_archive_is_a_regular_file_returns_1(self) -> None:
        """archive_dir present but is a file -> treated as missing."""
        self._make_state()
        self.archive_root.mkdir(parents=True)
        (self.archive_root / "snap").write_text("not a dir")
        ns = self._ns(name="snap")
        rc, _out, err = self._capture(ns)
        self.assertEqual(rc, 1)
        expected_archive_dir = self.state_dir / "archive" / "snap"
        self.assertIn(
            f"pipeline-status: error: archive 'snap' not found at {expected_archive_dir}",
            err,
        )

    def test_archive_name_quoted_in_error(self) -> None:
        """The raw NAME (not the slug) appears single-quoted via !r."""
        self._make_state()
        # 'Foo Bar' slugifies to 'foo-bar'; archive dir for 'foo-bar' does not exist.
        ns = self._ns(name="Foo Bar")
        rc, _out, err = self._capture(ns)
        self.assertEqual(rc, 1)
        # !r on 'Foo Bar' yields "'Foo Bar'"
        self.assertIn("archive 'Foo Bar' not found at", err)


class RestoreEmptySlugTests(_RunRestoreBase):
    def test_empty_slug_returns_1(self) -> None:
        self._make_state()
        ns = self._ns(name="!!!")  # slugifies to ""
        rc, out, err = self._capture(ns)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertEqual(
            err.strip(),
            "pipeline-status: error: restore name is empty after normalisation",
        )

    def test_whitespace_only_name_returns_1(self) -> None:
        self._make_state()
        ns = self._ns(name="   ")
        rc, _out, err = self._capture(ns)
        self.assertEqual(rc, 1)
        self.assertIn("empty after normalisation", err)

    def test_empty_string_name_returns_1(self) -> None:
        self._make_state()
        ns = self._ns(name="")
        rc, _out, err = self._capture(ns)
        self.assertEqual(rc, 1)
        self.assertIn("empty after normalisation", err)


# ---------------------------------------------------------------------------
# Additivity: live files not in archive remain untouched
# ---------------------------------------------------------------------------


class RestoreAdditivityTests(_RunRestoreBase):
    def test_live_file_absent_from_archive_untouched(self) -> None:
        """worktrees.json not in archive must remain byte-identical after restore."""
        live_worktrees = '{"live": true}\n'
        self._make_state({"worktrees.json": live_worktrees})
        self._make_archive(
            "snap",
            {
                "feature-request.md": "FR\n",
                "requirements.md": "REQS\n",
            },
        )
        ns = self._ns(name="snap")
        rc, out, err = self._capture(ns)

        self.assertEqual(rc, 0, err)
        self.assertIn(
            "Restored 2 file(s) from .claude/state/archive/snap/", out
        )
        # Untouched.
        self.assertEqual(
            (self.state_dir / "worktrees.json").read_text(encoding="utf-8"),
            live_worktrees,
        )

    def test_force_does_not_delete_live_files_absent_from_archive(self) -> None:
        """--force does not delete live files outside the archive."""
        live_worktrees = '{"live": true}\n'
        self._make_state(
            {
                "worktrees.json": live_worktrees,
                "adr.md": "LIVE ADR\n",
            }
        )
        self._make_archive("snap", {"adr.md": "ARCH ADR\n"})
        ns = self._ns(name="snap", force=True)
        rc, _out, err = self._capture(ns)

        self.assertEqual(rc, 0, err)
        self.assertEqual(
            (self.state_dir / "worktrees.json").read_text(encoding="utf-8"),
            live_worktrees,
        )
        self.assertEqual(
            (self.state_dir / "adr.md").read_text(encoding="utf-8"),
            "ARCH ADR\n",
        )


# ---------------------------------------------------------------------------
# Argparse / subparser registration
# ---------------------------------------------------------------------------


class AddRestoreSubparserTests(unittest.TestCase):
    """``add_restore_subparser`` registers ``restore`` correctly."""

    def _make_parser(self) -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
        parser = argparse.ArgumentParser(prog="pipeline-status")
        subparsers = parser.add_subparsers(dest="cmd", required=False)
        sp = add_restore_subparser(subparsers)
        return parser, sp

    def test_returns_subparser(self) -> None:
        _parser, sp = self._make_parser()
        self.assertIsInstance(sp, argparse.ArgumentParser)

    def test_parses_required_positional_and_force(self) -> None:
        parser, _sp = self._make_parser()
        args = parser.parse_args(["restore", "foo", "--force"])
        self.assertEqual(args.cmd, "restore")
        self.assertEqual(args.name, "foo")
        self.assertTrue(args.force)
        self.assertIs(args.func, run_restore)

    def test_force_defaults_false(self) -> None:
        parser, _sp = self._make_parser()
        args = parser.parse_args(["restore", "foo"])
        self.assertFalse(args.force)

    def test_missing_positional_rejected(self) -> None:
        parser, _sp = self._make_parser()
        with self.assertRaises(SystemExit) as cm:
            with mock.patch("sys.stderr", io.StringIO()):
                parser.parse_args(["restore"])
        self.assertEqual(cm.exception.code, 2)

    def test_watch_flag_rejected(self) -> None:
        """Restore subparser does NOT accept --watch."""
        parser, _sp = self._make_parser()
        with self.assertRaises(SystemExit) as cm:
            with mock.patch("sys.stderr", io.StringIO()):
                parser.parse_args(["restore", "foo", "--watch"])
        self.assertEqual(cm.exception.code, 2)

    def test_interval_flag_rejected(self) -> None:
        parser, _sp = self._make_parser()
        with self.assertRaises(SystemExit) as cm:
            with mock.patch("sys.stderr", io.StringIO()):
                parser.parse_args(["restore", "foo", "--interval", "5"])
        self.assertEqual(cm.exception.code, 2)

    def test_no_short_force_alias(self) -> None:
        """No -f short alias (Decision 11 Q3)."""
        parser, _sp = self._make_parser()
        with self.assertRaises(SystemExit) as cm:
            with mock.patch("sys.stderr", io.StringIO()):
                parser.parse_args(["restore", "foo", "-f"])
        self.assertEqual(cm.exception.code, 2)


# ---------------------------------------------------------------------------
# Lazy-import discipline (Decision 10)
# ---------------------------------------------------------------------------


class LazyImportDisciplineTests(unittest.TestCase):
    """Importing ``pipeline_status.restore`` MUST NOT import ``archive``."""

    def test_restore_does_not_eagerly_import_archive(self) -> None:
        # Remove any cached state to observe a clean (re)import.
        saved = {}
        for mod_name in ("pipeline_status.archive", "pipeline_status.restore"):
            if mod_name in sys.modules:
                saved[mod_name] = sys.modules.pop(mod_name)
        try:
            import pipeline_status.restore  # noqa: F401
            self.assertNotIn("pipeline_status.archive", sys.modules)
        finally:
            # Restore module table to avoid leaking state into other tests.
            for mod_name, mod in saved.items():
                sys.modules[mod_name] = mod
            # Make sure pipeline_status.restore is re-imported cleanly so
            # other tests in this file (which already imported it at module
            # load time) still have the correct binding.
            if "pipeline_status.restore" not in sys.modules:
                import pipeline_status.restore  # noqa: F401


# ---------------------------------------------------------------------------
# Cross-check: shutil.copy2 (Decision 4) -> content + mtime preserved
# ---------------------------------------------------------------------------


class RestorePreservesMtimeTests(_RunRestoreBase):
    def test_restore_carries_mtime(self) -> None:
        """shutil.copy2 carries src mtime to dst."""
        self._make_state()
        archive_dir = self._make_archive("snap", {"adr.md": "X\n"})
        src = archive_dir / "adr.md"
        # Set a deterministic mtime on the src.
        target_mtime = 1_600_000_000.0
        os.utime(src, (target_mtime, target_mtime))

        ns = self._ns(name="snap")
        rc, _out, err = self._capture(ns)
        self.assertEqual(rc, 0, err)
        dst = self.state_dir / "adr.md"
        self.assertAlmostEqual(dst.stat().st_mtime, target_mtime, places=2)


# ---------------------------------------------------------------------------
# Slug-based path resolution (basic sanity)
# ---------------------------------------------------------------------------


class RestoreSlugifyPathTests(_RunRestoreBase):
    def test_name_slugified_for_archive_lookup(self) -> None:
        """``args.name`` is slugified before being used as the archive dir."""
        self._make_state()
        # Create archive under the SLUG, not the raw name.
        self._make_archive("foo-bar", {"adr.md": "x\n"})

        ns = self._ns(name="Foo Bar")  # slugify -> "foo-bar"
        rc, out, err = self._capture(ns)
        self.assertEqual(rc, 0, err)
        self.assertIn(
            "Restored 1 file(s) from .claude/state/archive/foo-bar/", out
        )

    def test_archive_module_eventually_imported_when_run(self) -> None:
        """Sanity: after run_restore executes the slugify call, archive is loaded."""
        # archive is already imported at the top of this test module for
        # fixture setup, so this asserts run_restore's lazy import simply works.
        self._make_state()
        self._make_archive("zzz", {})
        ns = self._ns(name="zzz")
        rc, _out, _err = self._capture(ns)
        self.assertEqual(rc, 0)
        self.assertIn("pipeline_status.archive", sys.modules)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
