"""Tests for :mod:`pipeline_status.archive` (task-001 / KAN-17).

stdlib-only: ``unittest`` + ``tempfile.TemporaryDirectory`` + ``io.StringIO``
+ ``unittest.mock``.  No subprocess; no real ``.claude/state/`` access; each
test isolates its filesystem state under a ``TemporaryDirectory`` and changes
into it for the duration of the test.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from pipeline_status import archive
from pipeline_status.archive import (
    TRACKED_ARTEFACTS,
    add_archive_subparser,
    derive_default_name,
    run_archive,
    slugify,
)


# ---------------------------------------------------------------------------
# TRACKED_ARTEFACTS contract
# ---------------------------------------------------------------------------


class TrackedArtefactsTests(unittest.TestCase):
    """The constant is part of the public ADR contract."""

    def test_tracked_artefacts_is_tuple_of_strings(self) -> None:
        self.assertIsInstance(TRACKED_ARTEFACTS, tuple)
        for name in TRACKED_ARTEFACTS:
            self.assertIsInstance(name, str)

    def test_tracked_artefacts_exact_order_and_contents(self) -> None:
        # The ADR pins copy order; any drift breaks parallel-fan-out parity
        # with the duplicate _TRACKED_ARTEFACTS that history.py owns.
        self.assertEqual(
            TRACKED_ARTEFACTS,
            (
                "feature-request.md",
                "requirements.md",
                "adr.md",
                "tasks.json",
                "worktrees.json",
            ),
        )


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


class SlugifyTests(unittest.TestCase):
    """slugify() rules per FR-11 / Decision 10."""

    def test_empty_string(self) -> None:
        self.assertEqual(slugify(""), "")

    def test_whitespace_only(self) -> None:
        self.assertEqual(slugify("   "), "")
        self.assertEqual(slugify("\t\n  "), "")

    def test_mixed_case_lowercased(self) -> None:
        self.assertEqual(slugify("Foo Bar"), "foo-bar")
        self.assertEqual(slugify("HELLO"), "hello")

    def test_runs_of_separators_collapse(self) -> None:
        self.assertEqual(slugify("foo   bar"), "foo-bar")
        self.assertEqual(slugify("foo___bar"), "foo-bar")
        self.assertEqual(slugify("a!@#$%b"), "a-b")

    def test_strips_leading_and_trailing_separators(self) -> None:
        self.assertEqual(slugify("---foo---"), "foo")
        self.assertEqual(slugify("  foo  "), "foo")
        self.assertEqual(slugify("!foo!"), "foo")

    def test_unicode_letters_become_separators(self) -> None:
        # FR-11: ASCII-only output by construction; no transliteration.
        self.assertEqual(slugify("naïve"), "na-ve")
        self.assertEqual(slugify("café"), "caf")
        self.assertEqual(slugify("Ωmega"), "mega")

    def test_path_traversal_safe(self) -> None:
        # The crown jewel of the safety property: no slug may contain '/', '\',
        # or '..'.  '../../etc/passwd' collapses to 'etc-passwd'.
        out = slugify("../../etc/passwd")
        self.assertEqual(out, "etc-passwd")
        self.assertNotIn("/", out)
        self.assertNotIn("\\", out)
        self.assertNotIn("..", out)

    def test_backslash_and_dots_alone_collapse_to_empty(self) -> None:
        for value in ("../..", "\\\\", "....", "/", "\\", "."):
            with self.subTest(value=value):
                out = slugify(value)
                self.assertNotIn("/", out)
                self.assertNotIn("\\", out)
                self.assertNotIn("..", out)
                self.assertEqual(out, "")

    def test_all_separator_input(self) -> None:
        self.assertEqual(slugify("!!!"), "")
        self.assertEqual(slugify("???---"), "")

    def test_backticks_brackets_punctuation(self) -> None:
        self.assertEqual(slugify("`pipeline-status archive`"), "pipeline-status-archive")
        self.assertEqual(slugify("(foo) [bar] {baz}"), "foo-bar-baz")
        self.assertEqual(slugify("`pipeline`"), "pipeline")

    def test_alphanumerics_preserved(self) -> None:
        self.assertEqual(slugify("abc123"), "abc123")
        self.assertEqual(slugify("v3-final-2026"), "v3-final-2026")

    def test_output_charset_only_lowercase_alphanumeric_and_hyphen(self) -> None:
        # Property check across a mix of nasty inputs.
        for raw in (
            "Foo Bar!!",
            "../../etc",
            "naïve café",
            "`# Heading: with colons & ampersands`",
            "MiXeD Case 123",
        ):
            with self.subTest(raw=raw):
                out = slugify(raw)
                self.assertRegex(out, r"^[a-z0-9-]*$")
                if out:
                    self.assertFalse(out.startswith("-"))
                    self.assertFalse(out.endswith("-"))


# ---------------------------------------------------------------------------
# derive_default_name
# ---------------------------------------------------------------------------


class DeriveDefaultNameTests(unittest.TestCase):
    """derive_default_name(): FR-9/FR-10 heading-or-date fallback."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        # Fixed datetime for deterministic date-fallback assertions.
        self.fixed_today = datetime(2026, 5, 23, 14, 30, 0, tzinfo=timezone.utc)

    def _write(self, name: str, body: str) -> Path:
        p = self.tmp / name
        p.write_text(body, encoding="utf-8")
        return p

    def test_first_h1_heading_used(self) -> None:
        path = self._write(
            "feature-request.md",
            "# Add pipeline-status archive\n\nSome body\n",
        )
        self.assertEqual(
            derive_default_name(path, today=self.fixed_today),
            "add-pipeline-status-archive",
        )

    def test_deeper_heading_levels_accepted(self) -> None:
        path = self._write(
            "feature-request.md",
            "### Deeper Heading\n\nbody\n",
        )
        self.assertEqual(
            derive_default_name(path, today=self.fixed_today),
            "deeper-heading",
        )

    def test_first_heading_wins_over_later_headings(self) -> None:
        path = self._write(
            "feature-request.md",
            "# First\n\n## Second\n",
        )
        self.assertEqual(
            derive_default_name(path, today=self.fixed_today),
            "first",
        )

    def test_blank_lines_before_heading_skipped(self) -> None:
        path = self._write(
            "feature-request.md",
            "\n\n\n# Real Heading\n",
        )
        self.assertEqual(
            derive_default_name(path, today=self.fixed_today),
            "real-heading",
        )

    def test_non_heading_lines_before_heading_skipped(self) -> None:
        # Paragraphs / metadata before the first heading do not derail us.
        path = self._write(
            "feature-request.md",
            "Some intro paragraph.\n\nMore text.\n\n# The Heading\n",
        )
        self.assertEqual(
            derive_default_name(path, today=self.fixed_today),
            "the-heading",
        )

    def test_heading_with_backticks_and_punctuation(self) -> None:
        path = self._write(
            "feature-request.md",
            "# Add `pipeline-status archive`!\n",
        )
        self.assertEqual(
            derive_default_name(path, today=self.fixed_today),
            "add-pipeline-status-archive",
        )

    def test_missing_file_falls_back_to_date(self) -> None:
        missing = self.tmp / "does-not-exist.md"
        self.assertEqual(
            derive_default_name(missing, today=self.fixed_today),
            "2026-05-23",
        )

    def test_unreadable_file_falls_back_to_date(self) -> None:
        # A directory-where-a-file-is-expected forces OSError in open().
        bogus = self.tmp / "not-a-file"
        bogus.mkdir()
        self.assertEqual(
            derive_default_name(bogus, today=self.fixed_today),
            "2026-05-23",
        )

    def test_no_heading_falls_back_to_date(self) -> None:
        path = self._write(
            "feature-request.md",
            "Just a paragraph.\n\nNo headings here.\n",
        )
        self.assertEqual(
            derive_default_name(path, today=self.fixed_today),
            "2026-05-23",
        )

    def test_setext_heading_not_recognised_falls_back_to_date(self) -> None:
        # Setext-style headings are explicitly out of scope (Assumptions).
        path = self._write(
            "feature-request.md",
            "Heading\n=======\n",
        )
        self.assertEqual(
            derive_default_name(path, today=self.fixed_today),
            "2026-05-23",
        )

    def test_hash_without_space_is_not_a_heading(self) -> None:
        # ATX requires whitespace after the hashes.
        path = self._write(
            "feature-request.md",
            "#NotAHeading\n",
        )
        self.assertEqual(
            derive_default_name(path, today=self.fixed_today),
            "2026-05-23",
        )

    def test_heading_that_slugifies_to_empty_falls_back_to_date(self) -> None:
        path = self._write(
            "feature-request.md",
            "# !!!---???\n",
        )
        self.assertEqual(
            derive_default_name(path, today=self.fixed_today),
            "2026-05-23",
        )

    def test_empty_file_falls_back_to_date(self) -> None:
        path = self._write("feature-request.md", "")
        self.assertEqual(
            derive_default_name(path, today=self.fixed_today),
            "2026-05-23",
        )

    def test_default_today_uses_now(self) -> None:
        # When today is omitted, the function calls datetime.now().astimezone().
        # Easiest verifiable property: the fallback for a missing file produces
        # a YYYY-MM-DD string that parses back into a datetime.
        missing = self.tmp / "missing.md"
        result = derive_default_name(missing)
        # Will raise ValueError if the format is wrong.
        datetime.strptime(result, "%Y-%m-%d")

    def test_function_never_raises_on_various_bad_inputs(self) -> None:
        # Exhaustive: nothing under derive_default_name's contract may raise.
        bogus_dir = self.tmp / "subdir"
        bogus_dir.mkdir()
        broken_link = self.tmp / "broken-link"
        try:
            os.symlink(self.tmp / "no-such-target", broken_link)
            symlink_ok = True
        except (OSError, NotImplementedError):
            symlink_ok = False

        candidates = [
            self.tmp / "does-not-exist.md",
            bogus_dir,  # is a directory
        ]
        if symlink_ok:
            candidates.append(broken_link)

        for cand in candidates:
            with self.subTest(cand=str(cand)):
                # Must not raise; must return the fixed-date fallback.
                self.assertEqual(
                    derive_default_name(cand, today=self.fixed_today),
                    "2026-05-23",
                )


# ---------------------------------------------------------------------------
# run_archive
# ---------------------------------------------------------------------------


class _RunArchiveBase(unittest.TestCase):
    """Mixin providing a TemporaryDirectory + cwd swap + stderr capture."""

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
        """Create .claude/state/ and optionally populate it with given files."""
        self.state_dir.mkdir(parents=True)
        for name, body in (files or {}).items():
            (self.state_dir / name).write_text(body, encoding="utf-8")

    @staticmethod
    def _ns(**kwargs: object) -> argparse.Namespace:
        kwargs.setdefault("name", None)
        return argparse.Namespace(**kwargs)


class RunArchiveMissingStateTests(_RunArchiveBase):

    def test_missing_state_dir_returns_2(self) -> None:
        # Note: state dir is not created.
        ns = self._ns()
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            rc = run_archive(ns)
        self.assertEqual(rc, 2)
        self.assertIn("not found or not a directory", buf.getvalue())
        self.assertIn(".claude/state", buf.getvalue())

    def test_state_path_is_a_file_returns_2(self) -> None:
        (self.tmp / ".claude").mkdir()
        (self.tmp / ".claude" / "state").write_text("oops")
        ns = self._ns()
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            rc = run_archive(ns)
        self.assertEqual(rc, 2)
        self.assertFalse((self.tmp / ".claude" / "state" / "archive").exists())


class RunArchiveEmptyNameTests(_RunArchiveBase):

    def test_empty_name_returns_1(self) -> None:
        self._make_state({"feature-request.md": "# Real Heading\n"})
        ns = self._ns(name="!!!---???")  # slugifies to ""
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            rc = run_archive(ns)
        self.assertEqual(rc, 1)
        self.assertIn("empty after normalisation", buf.getvalue())
        # Nothing should have been created under archive/.
        self.assertFalse(self.archive_root.exists())

    def test_whitespace_name_returns_1(self) -> None:
        self._make_state()
        ns = self._ns(name="   ")
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            rc = run_archive(ns)
        self.assertEqual(rc, 1)
        self.assertFalse(self.archive_root.exists())


class RunArchiveCollisionTests(_RunArchiveBase):

    def test_existing_destination_returns_1_no_partial_write(self) -> None:
        self._make_state({"feature-request.md": "hi"})
        # Pre-create the destination directory with a sentinel file.
        existing = self.archive_root / "foo-bar"
        existing.mkdir(parents=True)
        (existing / "sentinel.txt").write_text("preserved")

        ns = self._ns(name="foo-bar")
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            rc = run_archive(ns)
        self.assertEqual(rc, 1)
        err = buf.getvalue()
        # ADR Decision 8: terse error mentioning slug + path.
        self.assertIn("pipeline-status: error: archive ", err)
        self.assertIn("'foo-bar'", err)
        self.assertIn("already exists at ", err)
        # Sentinel preserved; no partial write of feature-request.md happened.
        self.assertEqual(
            (existing / "sentinel.txt").read_text(), "preserved"
        )
        self.assertFalse((existing / "feature-request.md").exists())


class RunArchiveHappyPathTests(_RunArchiveBase):

    def test_all_five_files_copied(self) -> None:
        bodies = {
            "feature-request.md": "# Add pipeline-status archive\n",
            "requirements.md": "reqs body\n",
            "adr.md": "adr body\n",
            "tasks.json": '{"tasks": []}\n',
            "worktrees.json": "{}\n",
        }
        self._make_state(bodies)
        ns = self._ns(name="my-archive")
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = run_archive(ns)
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.getvalue(),
            "Archived 5 file(s) to .claude/state/archive/my-archive/\n",
        )
        dest = self.archive_root / "my-archive"
        self.assertTrue(dest.is_dir())
        for name, body in bodies.items():
            self.assertTrue((dest / name).is_file(), f"missing {name}")
            self.assertEqual(
                (dest / name).read_text(encoding="utf-8"),
                body,
                f"content mismatch in {name}",
            )
        # No source file removed or modified.
        for name in bodies:
            self.assertTrue((self.state_dir / name).is_file())

    def test_partial_source_three_of_five(self) -> None:
        bodies = {
            "feature-request.md": "x",
            "adr.md": "y",
            "tasks.json": "[]",
        }
        self._make_state(bodies)
        ns = self._ns(name="part")
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = run_archive(ns)
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.getvalue(),
            "Archived 3 file(s) to .claude/state/archive/part/\n",
        )
        dest = self.archive_root / "part"
        self.assertTrue((dest / "feature-request.md").is_file())
        self.assertTrue((dest / "adr.md").is_file())
        self.assertTrue((dest / "tasks.json").is_file())
        self.assertFalse((dest / "requirements.md").exists())
        self.assertFalse((dest / "worktrees.json").exists())

    def test_zero_files_still_creates_dir_and_exits_0(self) -> None:
        # FR-14: N=0 must still leave the archive directory in place.
        self._make_state()  # empty state dir
        ns = self._ns(name="empty-run")
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = run_archive(ns)
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.getvalue(),
            "Archived 0 file(s) to .claude/state/archive/empty-run/\n",
        )
        dest = self.archive_root / "empty-run"
        self.assertTrue(dest.is_dir())
        # The directory contains nothing.
        self.assertEqual(sorted(p.name for p in dest.iterdir()), [])

    def test_source_files_not_modified_or_deleted(self) -> None:
        self._make_state({
            "feature-request.md": "fr",
            "requirements.md": "rq",
        })
        before = {
            name: (self.state_dir / name).read_text()
            for name in ("feature-request.md", "requirements.md")
        }
        ns = self._ns(name="snapshot")
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(run_archive(ns), 0)
        for name, body in before.items():
            self.assertEqual((self.state_dir / name).read_text(), body)
            self.assertTrue((self.state_dir / name).is_file())

    def test_dest_mtime_stamped_via_utime(self) -> None:
        # Decision 5: os.utime(dest, (now, now)) is called once after all
        # copies.  We verify it by intercepting time.time() to return a known
        # value, then reading the dest mtime back.
        self._make_state({"feature-request.md": "fr"})
        fixed = 1_700_000_000.0
        ns = self._ns(name="stamped")
        with mock.patch("sys.stdout", io.StringIO()), \
                mock.patch("pipeline_status.archive.time.time",
                           return_value=fixed):
            rc = run_archive(ns)
        self.assertEqual(rc, 0)
        dest = self.archive_root / "stamped"
        st = dest.stat()
        # Allow tiny float jitter from FS rounding.
        self.assertAlmostEqual(st.st_mtime, fixed, delta=1.0)

    def test_uses_shutil_copy2(self) -> None:
        # Decision 4: archive uses shutil.copy2 (preserves content + mtime).
        self._make_state({"feature-request.md": "hi", "adr.md": "x"})
        ns = self._ns(name="copy2-check")
        with mock.patch("pipeline_status.archive.shutil.copy2",
                        wraps=archive.shutil.copy2) as spy, \
                mock.patch("sys.stdout", io.StringIO()):
            rc = run_archive(ns)
        self.assertEqual(rc, 0)
        # Called once per existing tracked source (2 here).
        self.assertEqual(spy.call_count, 2)

    def test_default_name_derived_from_feature_request_heading(self) -> None:
        # No --name supplied; archive name comes from the first heading.
        self._make_state({"feature-request.md": "# My Heading\n"})
        ns = self._ns(name=None)
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = run_archive(ns)
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.getvalue(),
            "Archived 1 file(s) to .claude/state/archive/my-heading/\n",
        )
        self.assertTrue((self.archive_root / "my-heading").is_dir())

    def test_default_name_falls_back_to_date_when_no_heading(self) -> None:
        # FR-10: missing or headless feature-request -> YYYY-MM-DD slug.
        self._make_state({"feature-request.md": "no headings here\n"})
        ns = self._ns(name=None)
        out = io.StringIO()
        fake_today = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        # Patch datetime.now() inside the archive module so the fallback is
        # deterministic.
        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):  # type: ignore[override]
                return fake_today

        with mock.patch("pipeline_status.archive.datetime", _FrozenDatetime), \
                mock.patch("sys.stdout", out):
            rc = run_archive(ns)
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.getvalue(),
            "Archived 1 file(s) to .claude/state/archive/2026-01-02/\n",
        )
        self.assertTrue((self.archive_root / "2026-01-02").is_dir())

    def test_supplied_name_normalised_via_slugifier(self) -> None:
        # FR-12: --name passes through slugify before path resolution.
        self._make_state({"feature-request.md": "x"})
        ns = self._ns(name="Foo Bar BAZ!!")
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = run_archive(ns)
        self.assertEqual(rc, 0)
        self.assertIn("foo-bar-baz", out.getvalue())
        self.assertTrue((self.archive_root / "foo-bar-baz").is_dir())

    def test_creates_dest_root_when_missing(self) -> None:
        # archive/ directory does not exist before the call.
        self._make_state({"feature-request.md": "x"})
        self.assertFalse(self.archive_root.exists())
        ns = self._ns(name="fresh")
        with mock.patch("sys.stdout", io.StringIO()):
            rc = run_archive(ns)
        self.assertEqual(rc, 0)
        self.assertTrue(self.archive_root.is_dir())
        self.assertTrue((self.archive_root / "fresh").is_dir())


# ---------------------------------------------------------------------------
# add_archive_subparser
# ---------------------------------------------------------------------------


class AddArchiveSubparserTests(unittest.TestCase):
    """Subparser registration: shape + defaults + flag wiring."""

    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="pipeline-status")
        subparsers = parser.add_subparsers(dest="cmd", required=False)
        add_archive_subparser(subparsers)
        return parser

    def test_returns_argument_parser(self) -> None:
        parser = argparse.ArgumentParser(prog="pipeline-status")
        subparsers = parser.add_subparsers(dest="cmd", required=False)
        sp = add_archive_subparser(subparsers)
        self.assertIsInstance(sp, argparse.ArgumentParser)

    def test_archive_subcommand_wires_set_defaults_func(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(["archive"])
        self.assertEqual(args.cmd, "archive")
        # Decision 13 / API contract: dispatch via args.func(args).
        self.assertIs(args.func, run_archive)

    def test_name_flag_optional_and_defaults_to_none(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(["archive"])
        self.assertIsNone(args.name)

    def test_name_flag_accepts_value(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(["archive", "--name", "my-snapshot"])
        self.assertEqual(args.name, "my-snapshot")

    def test_name_flag_help_documents_slugifier_rules(self) -> None:
        # FR-12: --help must describe the slugifier normalisation rules.
        parser = self._build_parser()
        # Locate the archive subparser via its _SubParsersAction.
        sub_action = None
        for action in parser._actions:  # type: ignore[attr-defined]
            if isinstance(action, argparse._SubParsersAction):
                sub_action = action
                break
        self.assertIsNotNone(sub_action)
        archive_sp = sub_action.choices["archive"]  # type: ignore[union-attr]
        help_text = archive_sp.format_help().lower()
        # Be lenient on phrasing; require the key normalisation concepts.
        self.assertIn("--name", help_text)
        for keyword in ("lowercas", "a-z0-9"):
            self.assertIn(keyword, help_text)

    def test_subcommand_does_not_accept_watch_or_interval(self) -> None:
        # FR-3: `--watch` / `--interval` are top-level only; the archive
        # subparser must reject them.
        parser = self._build_parser()
        with self.assertRaises(SystemExit), \
                mock.patch("sys.stderr", io.StringIO()):
            parser.parse_args(["archive", "--watch"])


# ---------------------------------------------------------------------------
# Forbidden-import guard
# ---------------------------------------------------------------------------


class NoSiblingTaskImportsTests(unittest.TestCase):
    """archive.py must not import history.py or format_history.py.

    Parses the module's AST and walks every Import / ImportFrom node, so
    docstring references to those module names (allowed) do not trip the
    check.
    """

    def test_archive_module_does_not_import_history(self) -> None:
        import ast

        src = Path(archive.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = {
            "pipeline_status.history",
            "pipeline_status.format_history",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name, forbidden)
            elif isinstance(node, ast.ImportFrom):
                if node.module is not None:
                    self.assertNotIn(node.module, forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
