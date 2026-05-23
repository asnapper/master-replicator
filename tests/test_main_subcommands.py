"""Tests for the v3 subcommand wiring in :mod:`pipeline_status.__main__`
(task-004 / KAN-20).

Covers:

* parser-level: ``_build_parser`` adds ``archive`` and ``history`` subparsers
  with ``args.cmd`` set, ``args.func`` bound to the right action callable, and
  positional/optional flags parsed as documented.
* dispatch-order: top-level ``--watch`` combined with a subcommand is
  rejected by argparse (exit 2) thanks to the subparser positional being
  mutually-exclusive with the watch-only branch.
* unknown subcommand: argparse rejects with exit 2.
* end-to-end happy paths for ``archive``, ``history`` (table), and
  ``history NAME`` (detail), each driven via ``_build_parser().parse_args``
  followed by ``args.func(args)`` in an isolated tempdir.
* byte-identical regression: with no arguments and no ``.claude/state/``
  on disk, the one-shot path still emits the v1 error string to stderr and
  returns exit code 2; with a populated state dir it emits the v1 header.

Stdlib only: ``unittest`` + ``tempfile.TemporaryDirectory`` + ``unittest.mock``.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline_status import __main__ as cli_main
from pipeline_status import archive as archive_module
from pipeline_status import history as history_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ChdirMixin:
    """Provide a tempdir + ``os.chdir`` into it with reliable cleanup."""

    def _make_tempdir(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def _chdir(self, target: Path) -> None:
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(target)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _populate_state(state_dir: Path) -> None:
    """Write a minimal but valid set of artefacts into ``state_dir``."""
    _write(state_dir / "feature-request.md", "# My Feature\n\nbody\n")
    _write(state_dir / "requirements.md", "# Requirements\n\nrequirements body\n")
    _write(state_dir / "adr.md", "# ADR\n\nADR body\n")
    _write(
        state_dir / "tasks.json",
        json.dumps(
            [
                {"id": "t1", "title": "first", "status": "done"},
                {"id": "t2", "title": "second", "status": "in_progress"},
            ]
        ),
    )
    _write(state_dir / "worktrees.json", json.dumps([]))


# ---------------------------------------------------------------------------
# Parser-level wiring
# ---------------------------------------------------------------------------


class ParserWiringTests(unittest.TestCase):
    """``_build_parser`` registers the archive + history subparsers per ADR
    Decision 13."""

    def test_no_args_leaves_cmd_none(self) -> None:
        args = cli_main._build_parser().parse_args([])
        self.assertIsNone(getattr(args, "cmd", None))
        # v1/v2 attributes still present and at their defaults.
        self.assertFalse(args.watch)
        self.assertEqual(args.interval, 2)

    def test_watch_only_still_no_cmd(self) -> None:
        args = cli_main._build_parser().parse_args(["--watch"])
        self.assertIsNone(getattr(args, "cmd", None))
        self.assertTrue(args.watch)

    def test_watch_with_interval(self) -> None:
        args = cli_main._build_parser().parse_args(["--watch", "--interval", "5"])
        self.assertIsNone(getattr(args, "cmd", None))
        self.assertTrue(args.watch)
        self.assertEqual(args.interval, 5)

    def test_archive_no_name(self) -> None:
        args = cli_main._build_parser().parse_args(["archive"])
        self.assertEqual(args.cmd, "archive")
        self.assertIsNone(args.name)
        self.assertIs(args.func, archive_module.run_archive)

    def test_archive_with_name(self) -> None:
        args = cli_main._build_parser().parse_args(["archive", "--name", "foo"])
        self.assertEqual(args.cmd, "archive")
        self.assertEqual(args.name, "foo")
        self.assertIs(args.func, archive_module.run_archive)

    def test_history_no_name(self) -> None:
        args = cli_main._build_parser().parse_args(["history"])
        self.assertEqual(args.cmd, "history")
        self.assertIsNone(args.name)
        self.assertIs(args.func, history_module.run_history)

    def test_history_with_name(self) -> None:
        args = cli_main._build_parser().parse_args(["history", "watch-mode"])
        self.assertEqual(args.cmd, "history")
        self.assertEqual(args.name, "watch-mode")
        self.assertIs(args.func, history_module.run_history)


# ---------------------------------------------------------------------------
# Argparse rejection cases (exit code 2)
# ---------------------------------------------------------------------------


class ArgparseRejectionTests(unittest.TestCase):
    """argparse must reject ill-formed combinations with exit 2 on stderr.

    Per ADR Decision 13 (additive, minimal diff), no extra validation logic is
    added: the rejection cases are whatever argparse *naturally* enforces.
    """

    def _expect_exit_2(self, argv: list[str]) -> str:
        parser = cli_main._build_parser()
        stderr = io.StringIO()
        with mock.patch.object(sys, "stderr", stderr):
            with self.assertRaises(SystemExit) as ctx:
                parser.parse_args(argv)
        # argparse uses int code 2 for usage errors.
        self.assertEqual(ctx.exception.code, 2)
        return stderr.getvalue()

    def test_watch_flag_after_archive_subcommand_rejected(self) -> None:
        # "archive --watch" is rejected because --watch is a parser-level flag
        # and the archive subparser does not declare it -> "unrecognized
        # arguments". This is the natural CLI idiom for placing a flag after
        # the subcommand.
        err = self._expect_exit_2(["archive", "--watch"])
        self.assertTrue(err)

    def test_watch_flag_after_history_subcommand_rejected(self) -> None:
        err = self._expect_exit_2(["history", "--watch"])
        self.assertTrue(err)

    def test_unknown_subcommand_rejected(self) -> None:
        err = self._expect_exit_2(["frobnicate"])
        # argparse uses "invalid choice" for unknown subcommands.
        self.assertIn("invalid choice", err)

    def test_unknown_archive_flag_rejected(self) -> None:
        # Defensive: a typo'd flag on the archive subparser is also exit 2.
        err = self._expect_exit_2(["archive", "--no-such-flag"])
        self.assertTrue(err)

    def test_watch_before_subcommand_does_not_reach_watch_branch(self) -> None:
        # Sanity / dispatch-order documentation: ``--watch archive`` parses
        # without error (--watch is a root flag, ``archive`` is a valid
        # subcommand), but main() dispatches the subcommand first and the
        # watch branch is never reached.  Validated separately in
        # ``MainDispatchOrderTests``; here we only assert the parse succeeds.
        args = cli_main._build_parser().parse_args(["--watch", "archive"])
        self.assertEqual(args.cmd, "archive")
        self.assertTrue(args.watch)
        self.assertIs(args.func, archive_module.run_archive)


# ---------------------------------------------------------------------------
# End-to-end: archive subcommand
# ---------------------------------------------------------------------------


class ArchiveSubcommandEndToEndTests(_ChdirMixin, unittest.TestCase):
    """Drive ``args.func(args)`` against a real tempdir for the archive path."""

    def test_archive_happy_path_default_name(self) -> None:
        root = self._make_tempdir()
        state_dir = root / ".claude" / "state"
        _populate_state(state_dir)
        self._chdir(root)

        args = cli_main._build_parser().parse_args(["archive"])

        stdout = io.StringIO()
        with mock.patch.object(sys, "stdout", stdout):
            rc = args.func(args)

        self.assertEqual(rc, 0)
        # Heading "My Feature" slugifies to "my-feature".
        self.assertEqual(
            stdout.getvalue().rstrip("\n"),
            "Archived 5 file(s) to .claude/state/archive/my-feature/",
        )
        self.assertTrue((state_dir / "archive" / "my-feature").is_dir())
        # All five artefacts were copied.
        for name in archive_module.TRACKED_ARTEFACTS:
            self.assertTrue(
                (state_dir / "archive" / "my-feature" / name).is_file(),
                name,
            )

    def test_archive_with_explicit_name(self) -> None:
        root = self._make_tempdir()
        state_dir = root / ".claude" / "state"
        _populate_state(state_dir)
        self._chdir(root)

        args = cli_main._build_parser().parse_args(["archive", "--name", "Run 1!"])

        stdout = io.StringIO()
        with mock.patch.object(sys, "stdout", stdout):
            rc = args.func(args)

        self.assertEqual(rc, 0)
        # "Run 1!" slugifies to "run-1".
        self.assertEqual(
            stdout.getvalue().rstrip("\n"),
            "Archived 5 file(s) to .claude/state/archive/run-1/",
        )
        self.assertTrue((state_dir / "archive" / "run-1").is_dir())


# ---------------------------------------------------------------------------
# End-to-end: history subcommand
# ---------------------------------------------------------------------------


class HistorySubcommandEndToEndTests(_ChdirMixin, unittest.TestCase):
    """Drive ``args.func(args)`` against a real tempdir for the history path."""

    def test_history_table_empty_archive(self) -> None:
        root = self._make_tempdir()
        # No archive dir at all -> "No archives found.".
        self._chdir(root)

        args = cli_main._build_parser().parse_args(["history"])

        stdout = io.StringIO()
        with mock.patch.object(sys, "stdout", stdout):
            rc = args.func(args)

        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue(), "No archives found.\n")

    def test_history_table_with_archives(self) -> None:
        root = self._make_tempdir()
        state_dir = root / ".claude" / "state"
        # Build a populated archive subdirectory for the table to render.
        archive_dir = state_dir / "archive" / "run-1"
        _populate_state(archive_dir)
        self._chdir(root)

        args = cli_main._build_parser().parse_args(["history"])

        stdout = io.StringIO()
        with mock.patch.object(sys, "stdout", stdout):
            rc = args.func(args)

        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertTrue(output)
        # The archive name must appear in the rendered table.
        self.assertIn("run-1", output)
        # Each rendered table ends with a trailing newline per format_history.
        self.assertTrue(output.endswith("\n"))

    def test_history_detail_for_named_archive(self) -> None:
        root = self._make_tempdir()
        state_dir = root / ".claude" / "state"
        archive_dir = state_dir / "archive" / "run-1"
        _populate_state(archive_dir)
        self._chdir(root)

        args = cli_main._build_parser().parse_args(["history", "run-1"])

        stdout = io.StringIO()
        with mock.patch.object(sys, "stdout", stdout):
            rc = args.func(args)

        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertTrue(output)
        # The v1-style detail report mentions the canonical artefact basenames.
        self.assertIn("feature-request.md", output)
        self.assertIn("requirements.md", output)

    def test_history_detail_for_missing_archive_errors(self) -> None:
        root = self._make_tempdir()
        # No archive dir present at all.
        self._chdir(root)

        args = cli_main._build_parser().parse_args(["history", "ghost"])

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(sys, "stdout", stdout), mock.patch.object(
            sys, "stderr", stderr
        ):
            rc = args.func(args)

        self.assertEqual(rc, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("not found", stderr.getvalue())


# ---------------------------------------------------------------------------
# Byte-identical regression for the no-args / v1 one-shot path
# ---------------------------------------------------------------------------


class OneShotRegressionTests(_ChdirMixin, unittest.TestCase):
    """``pipeline-status`` with no arguments must keep the v1/v2 contract."""

    def test_no_args_missing_state_dir_exits_2(self) -> None:
        root = self._make_tempdir()
        # No .claude/state/ created on purpose.
        self._chdir(root)

        stderr = io.StringIO()
        with mock.patch.object(sys, "stderr", stderr):
            rc = cli_main._run_one_shot()

        self.assertEqual(rc, 2)
        self.assertEqual(
            stderr.getvalue(),
            "pipeline-status: error: .claude/state/ not found or not a directory\n",
        )

    def test_no_args_populated_state_dir_emits_v1_header(self) -> None:
        root = self._make_tempdir()
        state_dir = root / ".claude" / "state"
        _populate_state(state_dir)
        self._chdir(root)

        stdout = io.StringIO()
        with mock.patch.object(sys, "stdout", stdout):
            rc = cli_main._run_one_shot()

        self.assertEqual(rc, 0)
        # Byte-identical v1 header (kept stable across v2 and v3).
        self.assertTrue(
            stdout.getvalue().startswith("Pipeline Status\n===============\n\n"),
            f"Unexpected one-shot prefix: {stdout.getvalue()!r}",
        )

    def test_no_args_does_not_touch_subcommand_modules(self) -> None:
        """The argparse-level dispatch leaves ``args.cmd`` as ``None`` and
        ``args.func`` is absent so the v1/v2 branch is taken unmodified."""
        args = cli_main._build_parser().parse_args([])
        self.assertIsNone(getattr(args, "cmd", None))
        self.assertFalse(hasattr(args, "func"))


# ---------------------------------------------------------------------------
# main() dispatch-order smoke test (no real I/O)
# ---------------------------------------------------------------------------


class MainDispatchOrderTests(unittest.TestCase):
    """``main()`` must dispatch subcommands BEFORE the watch/one-shot branches."""

    def test_main_subcommand_dispatch_short_circuits(self) -> None:
        # Build a Namespace that looks like a subcommand parse.
        sentinel_func = mock.Mock(return_value=0)
        namespace = mock.Mock()
        namespace.cmd = "archive"
        namespace.func = sentinel_func
        namespace.watch = True  # would normally take the watch branch
        namespace.interval = 2

        fake_parser = mock.Mock()
        fake_parser.parse_args.return_value = namespace

        with mock.patch.object(cli_main, "_build_parser", return_value=fake_parser):
            with self.assertRaises(SystemExit) as ctx:
                cli_main.main()

        self.assertEqual(ctx.exception.code, 0)
        sentinel_func.assert_called_once_with(namespace)

    def test_main_no_subcommand_runs_one_shot(self) -> None:
        namespace = mock.Mock()
        namespace.cmd = None
        namespace.watch = False
        namespace.interval = 2

        fake_parser = mock.Mock()
        fake_parser.parse_args.return_value = namespace

        with mock.patch.object(
            cli_main, "_build_parser", return_value=fake_parser
        ), mock.patch.object(
            cli_main, "_run_one_shot", return_value=0
        ) as run_one_shot:
            with self.assertRaises(SystemExit) as ctx:
                cli_main.main()

        self.assertEqual(ctx.exception.code, 0)
        run_one_shot.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
