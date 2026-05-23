"""Unit tests for pipeline_status.history.

Covers list_archives and inspect_archive directly with Path inputs. The
table-form / detail-form behaviour of run_history (which triggers lazy imports
of pipeline_status.archive and pipeline_status.format_history) is intentionally
left to task-004's end-to-end CLI tests.

Tests use only stdlib unittest + tempfile and do NOT import
pipeline_status.archive or pipeline_status.format_history.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline_status.history import (
    ArchiveEntry,
    _TRACKED_ARTEFACTS,
    inspect_archive,
    list_archives,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TrackedArtefactsTests(unittest.TestCase):
    """The private _TRACKED_ARTEFACTS tuple must match the canonical list."""

    def test_tracked_artefacts_exact_values_and_order(self) -> None:
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

    def test_tracked_artefacts_is_tuple(self) -> None:
        self.assertIsInstance(_TRACKED_ARTEFACTS, tuple)


class ArchiveEntryTests(unittest.TestCase):
    """ArchiveEntry is a frozen dataclass with five fields."""

    def test_construct_with_all_fields(self) -> None:
        entry = ArchiveEntry(
            name="foo",
            path=Path("/tmp/foo"),
            mtime=1.0,
            total_tasks=3,
            completed_tasks=1,
        )
        self.assertEqual(entry.name, "foo")
        self.assertEqual(entry.path, Path("/tmp/foo"))
        self.assertEqual(entry.mtime, 1.0)
        self.assertEqual(entry.total_tasks, 3)
        self.assertEqual(entry.completed_tasks, 1)

    def test_frozen_assignment_raises(self) -> None:
        entry = ArchiveEntry(
            name="foo",
            path=Path("/tmp/foo"),
            mtime=1.0,
            total_tasks=None,
            completed_tasks=None,
        )
        with self.assertRaises(Exception):
            entry.name = "bar"  # type: ignore[misc]

    def test_none_counts_allowed(self) -> None:
        entry = ArchiveEntry(
            name="x",
            path=Path("/tmp/x"),
            mtime=0.0,
            total_tasks=None,
            completed_tasks=None,
        )
        self.assertIsNone(entry.total_tasks)
        self.assertIsNone(entry.completed_tasks)


class ListArchivesMissingOrEmptyTests(unittest.TestCase):
    """list_archives returns [] when the root is missing, empty, or a file."""

    def test_missing_root_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "no-such-dir"
            self.assertEqual(list_archives(missing), [])

    def test_root_is_a_regular_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "iam-a-file"
            f.write_text("hi", encoding="utf-8")
            self.assertEqual(list_archives(f), [])

    def test_empty_root_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(list_archives(Path(td)), [])

    def test_root_with_only_files_returns_empty(self) -> None:
        # Files directly under archive_root must be ignored.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / ".DS_Store").write_text("junk", encoding="utf-8")
            (Path(td) / "stray.txt").write_text("more junk", encoding="utf-8")
            self.assertEqual(list_archives(Path(td)), [])


class ListArchivesSortingTests(unittest.TestCase):
    """list_archives sorts by name ascending using byte-order key."""

    def test_multiple_archives_sorted_alphabetically(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("zebra", "apple", "mango"):
                (root / name).mkdir()
            entries = list_archives(root)
            self.assertEqual([e.name for e in entries], ["apple", "mango", "zebra"])

    def test_byte_order_sort_uppercase_before_lowercase(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("zeta", "Alpha", "beta"):
                (root / name).mkdir()
            entries = list_archives(root)
            # ASCII byte-order: 'A' (0x41) < 'b' (0x62) < 'z' (0x7a).
            self.assertEqual([e.name for e in entries], ["Alpha", "beta", "zeta"])

    def test_archive_entry_path_is_correct(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "first").mkdir()
            entries = list_archives(root)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].path, root / "first")
            self.assertEqual(entries[0].name, "first")

    def test_mtime_is_directory_stat_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "a1"
            archive.mkdir()
            expected = archive.stat().st_mtime
            entries = list_archives(root)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].mtime, expected)


class ListArchivesFilesIgnoredTests(unittest.TestCase):
    """Files (not directories) directly under archive_root are skipped."""

    def test_mixed_files_and_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "dir_one").mkdir()
            (root / "dir_two").mkdir()
            (root / "file.txt").write_text("ignored", encoding="utf-8")
            entries = list_archives(root)
            self.assertEqual([e.name for e in entries], ["dir_one", "dir_two"])


class ListArchivesTasksJsonTests(unittest.TestCase):
    """Coverage of tasks.json parsing edge cases."""

    def test_missing_tasks_json_yields_none_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "no-tasks").mkdir()
            entries = list_archives(root)
            self.assertEqual(len(entries), 1)
            self.assertIsNone(entries[0].total_tasks)
            self.assertIsNone(entries[0].completed_tasks)

    def test_malformed_json_yields_none_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "broken"
            archive.mkdir()
            (archive / "tasks.json").write_text("{not json", encoding="utf-8")
            entries = list_archives(root)
            self.assertEqual(len(entries), 1)
            self.assertIsNone(entries[0].total_tasks)
            self.assertIsNone(entries[0].completed_tasks)

    def test_empty_object_json_yields_zero_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "empty-obj"
            archive.mkdir()
            (archive / "tasks.json").write_text("{}", encoding="utf-8")
            entries = list_archives(root)
            self.assertEqual(entries[0].total_tasks, 0)
            self.assertEqual(entries[0].completed_tasks, 0)

    def test_empty_array_json_yields_zero_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "empty-arr"
            archive.mkdir()
            (archive / "tasks.json").write_text("[]", encoding="utf-8")
            entries = list_archives(root)
            self.assertEqual(entries[0].total_tasks, 0)
            self.assertEqual(entries[0].completed_tasks, 0)

    def test_list_shape_counts_tasks_and_done(self) -> None:
        tasks = [
            {"id": "t1", "status": "done"},
            {"id": "t2", "status": "in_progress"},
            {"id": "t3", "status": "completed"},
            {"id": "t4", "completed": True},
            {"id": "t5", "done": True},
            {"id": "t6"},
        ]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "listy"
            archive.mkdir()
            (archive / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
            entries = list_archives(root)
            self.assertEqual(entries[0].total_tasks, 6)
            self.assertEqual(entries[0].completed_tasks, 4)

    def test_dict_with_tasks_key_counts_correctly(self) -> None:
        tasks_doc = {
            "tasks": [
                {"id": "t1", "status": "DONE"},        # case-insensitive
                {"id": "t2", "status": "Completed"},  # case-insensitive
                {"id": "t3", "status": "todo"},
                {"id": "t4", "completed": False},
                {"id": "t5", "done": True},
            ]
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "tasks-dict"
            archive.mkdir()
            (archive / "tasks.json").write_text(json.dumps(tasks_doc), encoding="utf-8")
            entries = list_archives(root)
            self.assertEqual(entries[0].total_tasks, 5)
            self.assertEqual(entries[0].completed_tasks, 3)

    def test_unexpected_shape_yields_none_counts(self) -> None:
        # E.g. a top-level string or number, or a dict without a "tasks" list.
        for payload in ("\"hello\"", "42", json.dumps({"items": [1, 2, 3]})):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                archive = root / "weird"
                archive.mkdir()
                (archive / "tasks.json").write_text(payload, encoding="utf-8")
                entries = list_archives(root)
                self.assertEqual(len(entries), 1, f"payload={payload!r}")
                self.assertIsNone(entries[0].total_tasks, f"payload={payload!r}")
                self.assertIsNone(entries[0].completed_tasks, f"payload={payload!r}")

    def test_tasks_with_non_dict_items_are_skipped_for_completed(self) -> None:
        # total counts every entry; completed only counts dict items.
        tasks = [
            "string-item",
            42,
            None,
            {"status": "done"},
            {"status": "todo"},
        ]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "mixed"
            archive.mkdir()
            (archive / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
            entries = list_archives(root)
            self.assertEqual(entries[0].total_tasks, 5)
            self.assertEqual(entries[0].completed_tasks, 1)

    def test_tasks_json_unreadable_yields_none_counts(self) -> None:
        # A tasks.json that is a *directory* will raise OSError on open; the
        # function must silently swallow it. (is_file() returns False for a
        # directory, so the early return at the top handles it.)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "dir-tasks"
            archive.mkdir()
            (archive / "tasks.json").mkdir()  # tasks.json is a directory
            entries = list_archives(root)
            self.assertEqual(len(entries), 1)
            self.assertIsNone(entries[0].total_tasks)
            self.assertIsNone(entries[0].completed_tasks)

    def test_invalid_utf8_yields_none_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "bad-utf8"
            archive.mkdir()
            # Invalid UTF-8 byte sequence.
            (archive / "tasks.json").write_bytes(b"\xff\xfe\xff\xfe")
            entries = list_archives(root)
            self.assertEqual(len(entries), 1)
            self.assertIsNone(entries[0].total_tasks)
            self.assertIsNone(entries[0].completed_tasks)


class ListArchivesNeverRaisesTests(unittest.TestCase):
    """list_archives must never raise regardless of input pathology."""

    def test_returns_list_for_pathological_inputs(self) -> None:
        # Use a path with strange characters but on a real tmp dir.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "no" / "such" / "deep" / "path"
            # Should not raise; should return [].
            self.assertEqual(list_archives(root), [])


class InspectArchiveTests(unittest.TestCase):
    """inspect_archive returns the 5 canonical keys mapped to ArtefactResults."""

    EXPECTED_KEYS = (
        "feature-request.md",
        "requirements.md",
        "adr.md",
        "tasks.json",
        "worktrees.json",
    )

    def test_keys_are_exactly_the_five_canonical_names(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            results = inspect_archive(Path(td))
            self.assertEqual(set(results.keys()), set(self.EXPECTED_KEYS))

    def test_empty_archive_dir_marks_all_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            results = inspect_archive(Path(td))
            for key in self.EXPECTED_KEYS:
                self.assertFalse(results[key].exists, f"{key} should not exist")
                self.assertFalse(results[key].filled, f"{key} should not be filled")

    def test_partial_archive_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td)
            _write(archive / "feature-request.md", "# A feature\n\nBody.\n")
            _write(archive / "tasks.json", json.dumps([{"id": "t1", "status": "done"}]))
            results = inspect_archive(archive)
            self.assertTrue(results["feature-request.md"].exists)
            self.assertTrue(results["feature-request.md"].filled)
            self.assertTrue(results["tasks.json"].exists)
            self.assertTrue(results["tasks.json"].filled)
            self.assertFalse(results["requirements.md"].exists)
            self.assertFalse(results["adr.md"].exists)
            self.assertFalse(results["worktrees.json"].exists)

    def test_missing_archive_dir_does_not_raise(self) -> None:
        # The function must dispatch to the v1 inspectors even if archive_dir
        # itself doesn't exist; the inspectors handle missing-file cases.
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "does-not-exist"
            results = inspect_archive(missing)
            self.assertEqual(set(results.keys()), set(self.EXPECTED_KEYS))
            for key in self.EXPECTED_KEYS:
                self.assertFalse(results[key].exists)

    def test_full_archive_populates_tasks_extra(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td)
            _write(archive / "feature-request.md", "# X\n\nbody\n")
            _write(archive / "requirements.md", "# R\n\nbody\n")
            _write(archive / "adr.md", "# A\n\nbody\n")
            _write(
                archive / "tasks.json",
                json.dumps([
                    {"id": "t1", "status": "done"},
                    {"id": "t2", "status": "todo"},
                ]),
            )
            _write(archive / "worktrees.json", json.dumps({"foo": "bar"}))
            results = inspect_archive(archive)
            for key in self.EXPECTED_KEYS:
                self.assertTrue(results[key].exists, f"{key} should exist")
                self.assertTrue(results[key].filled, f"{key} should be filled")
            self.assertEqual(results["tasks.json"].extra.get("total"), 2)
            self.assertEqual(results["tasks.json"].extra.get("completed"), 1)


class AddHistorySubparserTests(unittest.TestCase):
    """add_history_subparser registers the expected positional + dispatcher."""

    def _build_parser(self):
        import argparse
        from pipeline_status.history import add_history_subparser, run_history

        parser = argparse.ArgumentParser(prog="pipeline-status")
        subparsers = parser.add_subparsers(dest="cmd")
        sp = add_history_subparser(subparsers)
        return parser, sp, run_history

    def test_name_is_optional_default_none(self) -> None:
        parser, _sp, run_history = self._build_parser()
        args = parser.parse_args(["history"])
        self.assertEqual(args.cmd, "history")
        self.assertIsNone(args.name)
        self.assertIs(args.func, run_history)

    def test_name_is_accepted_positional(self) -> None:
        parser, _sp, run_history = self._build_parser()
        args = parser.parse_args(["history", "watch-mode"])
        self.assertEqual(args.cmd, "history")
        self.assertEqual(args.name, "watch-mode")
        self.assertIs(args.func, run_history)


if __name__ == "__main__":
    unittest.main()
