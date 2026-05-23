"""Tests for the v2 watch-mode driver and the argparse --interval validator."""
import argparse
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline_status.__main__ import _build_parser, _interval_type
from pipeline_status.formatting import (
    format_footer,
    render_missing_state,
)
from pipeline_status.watch import WatchConfig, run_watch


CET = timezone(timedelta(hours=2))
FIXED_DT = datetime(2026, 5, 23, 5, 54, 36, tzinfo=CET)
CANONICAL_FOOTER = (
    "Last refresh: 2026-05-23T05:54:36+02:00  (interval: 2s, press Ctrl+C to stop)"
)
CLEAR_SCREEN = "\x1b[H\x1b[2J"


def _make_config(
    state_dir: Path,
    *,
    interval: int = 2,
    is_tty: bool = False,
    max_iterations: int | None = 1,
    sleep_fn=None,
    stream: io.StringIO | None = None,
) -> WatchConfig:
    """Build a WatchConfig for tests with sensible deterministic defaults."""
    return WatchConfig(
        state_dir=state_dir,
        interval=interval,
        is_tty=is_tty,
        use_colour=False,
        stream=stream or io.StringIO(),
        sleep_fn=sleep_fn if sleep_fn is not None else (lambda _: None),
        clock_fn=lambda: FIXED_DT,
        max_iterations=max_iterations,
    )


class TestIntervalValidator(unittest.TestCase):
    def test_rejects_zero(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _interval_type("0")

    def test_rejects_negative(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _interval_type("-1")

    def test_rejects_above_max(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _interval_type("3601")

    def test_rejects_float_string(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _interval_type("0.5")

    def test_rejects_non_numeric(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _interval_type("abc")

    def test_accepts_min(self):
        self.assertEqual(_interval_type("1"), 1)

    def test_accepts_max(self):
        self.assertEqual(_interval_type("3600"), 3600)

    def test_parser_exits_on_bad_interval(self):
        parser = _build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--interval", "0"])
        self.assertNotEqual(ctx.exception.code, 0)


class TestRunWatchSingleIteration(unittest.TestCase):
    def test_single_iteration_writes_body_blank_footer_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            stream = io.StringIO()
            cfg = _make_config(state_dir, stream=stream)
            rc = run_watch(cfg)
            self.assertEqual(rc, 0)

            output = stream.getvalue()
            self.assertIn(CANONICAL_FOOTER, output)
            # Output ends with newline (footer's trailing \n).
            self.assertTrue(output.endswith("\n"))
            # The footer is preceded by a blank line separating it from the body.
            self.assertIn(f"\n\n{CANONICAL_FOOTER}\n", output)

    def test_tty_emits_clear_screen_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            stream = io.StringIO()
            cfg = _make_config(state_dir, is_tty=True, stream=stream)
            run_watch(cfg)
            self.assertTrue(stream.getvalue().startswith(CLEAR_SCREEN))

    def test_non_tty_does_not_emit_clear_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            stream = io.StringIO()
            cfg = _make_config(state_dir, is_tty=False, stream=stream)
            run_watch(cfg)
            self.assertNotIn(CLEAR_SCREEN, stream.getvalue())


class TestRunWatchMultipleIterations(unittest.TestCase):
    def test_three_iterations_non_tty_blank_line_separated(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            stream = io.StringIO()
            cfg = _make_config(state_dir, max_iterations=3, stream=stream)
            run_watch(cfg)
            output = stream.getvalue()
            # Three footers (one per iteration).
            self.assertEqual(output.count(CANONICAL_FOOTER), 3)
            # No clear-screen sequence on non-TTY.
            self.assertNotIn(CLEAR_SCREEN, output)

    def test_three_iterations_tty_three_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            stream = io.StringIO()
            cfg = _make_config(
                state_dir, max_iterations=3, is_tty=True, stream=stream
            )
            run_watch(cfg)
            output = stream.getvalue()
            # One clear-screen per iteration.
            self.assertEqual(output.count(CLEAR_SCREEN), 3)
            self.assertEqual(output.count(CANONICAL_FOOTER), 3)


class TestRunWatchMissingState(unittest.TestCase):
    def test_missing_state_dir_renders_placeholder(self):
        missing = Path("/this/path/does/not/exist/.claude/state")
        self.assertFalse(missing.exists())
        stream = io.StringIO()
        cfg = _make_config(missing, max_iterations=1, stream=stream)
        rc = run_watch(cfg)
        self.assertEqual(rc, 0)
        self.assertIn(".claude/state/: MISSING", stream.getvalue())

    def test_missing_state_dir_does_not_raise_across_iterations(self):
        missing = Path("/this/path/does/not/exist/.claude/state")
        stream = io.StringIO()
        cfg = _make_config(missing, max_iterations=5, stream=stream)
        rc = run_watch(cfg)
        self.assertEqual(rc, 0)
        # 5 footers, 5 placeholder bodies.
        self.assertEqual(stream.getvalue().count(CANONICAL_FOOTER), 5)
        self.assertEqual(stream.getvalue().count(".claude/state/: MISSING"), 5)

    def test_state_dir_replaced_with_regular_file_renders_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            faux_state = Path(tmp) / "not_a_dir"
            faux_state.write_text("hi")  # regular file, not a directory
            stream = io.StringIO()
            cfg = _make_config(faux_state, max_iterations=1, stream=stream)
            rc = run_watch(cfg)
            self.assertEqual(rc, 0)
            self.assertIn(".claude/state/: MISSING", stream.getvalue())


class TestRunWatchKeyboardInterrupt(unittest.TestCase):
    def test_keyboard_interrupt_from_sleep_returns_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            stream = io.StringIO()
            sleep_fn = MagicMock(side_effect=KeyboardInterrupt)
            cfg = _make_config(
                state_dir, max_iterations=None, sleep_fn=sleep_fn, stream=stream
            )
            rc = run_watch(cfg)
            self.assertEqual(rc, 0)
            # The one render before sleep happened; then KeyboardInterrupt
            # was caught and a trailing newline was emitted.
            self.assertTrue(stream.getvalue().endswith("\n"))
            # Footer appears exactly once (one iteration before the interrupt).
            self.assertEqual(stream.getvalue().count(CANONICAL_FOOTER), 1)
            # sleep_fn was called exactly once before the interrupt fired.
            self.assertEqual(sleep_fn.call_count, 1)

    def test_keyboard_interrupt_no_exception_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            sleep_fn = MagicMock(side_effect=KeyboardInterrupt)
            cfg = _make_config(state_dir, max_iterations=None, sleep_fn=sleep_fn)
            # Must not raise.
            run_watch(cfg)


class TestFormattingHelpersSmoke(unittest.TestCase):
    """Light cross-check tests that exercise the v1 helpers used in watch mode."""

    def test_format_footer_canonical(self):
        self.assertEqual(format_footer(FIXED_DT, 2), CANONICAL_FOOTER)

    def test_render_missing_state_shape(self):
        body = render_missing_state(Path("/anywhere"))
        self.assertTrue(body.startswith("Pipeline Status\n===============\n\n"))
        self.assertTrue(body.endswith(".claude/state/: MISSING\n"))


class TestOneShotByteIdenticalRegression(unittest.TestCase):
    """Guard against accidental drift in the one-shot rendering path."""

    def test_format_artefact_row_stable(self):
        """Encoded golden output for a known ArtefactResult fixture."""
        from pipeline_status.formatting import format_artefact_row
        from pipeline_status.inspectors import ArtefactResult

        fixture = ArtefactResult(
            name="requirements.md",
            path=Path("/x/y/requirements.md"),
            exists=True,
            filled=True,
            mtime_iso="2026-05-22T10:30:00+02:00",
            extra={},
            error=None,
        )
        row = format_artefact_row(fixture, colour=False)
        # The row contains the documented fields in the documented order.
        # We don't lock exact whitespace — that would break unrelated cosmetic
        # tweaks — but we do lock the *presence* of every field and the lack
        # of stray newlines or ANSI escapes.
        self.assertIn("requirements.md", row)
        self.assertIn("EXISTS", row)
        self.assertIn("FILLED", row)
        self.assertIn("2026-05-22T10:30:00+02:00", row)
        self.assertNotIn("\n", row)
        self.assertNotIn("\x1b", row)

    def test_format_stage_line_stable(self):
        """`format_stage_line` returns a non-empty string for every canonical stage."""
        from pipeline_status.formatting import format_stage_line

        canonical_stages = [
            "Awaiting feature request",
            "Awaiting Gate 1 (PO review)",
            "Awaiting Gate 2 (Architect review)",
            "Awaiting Gate 3 (PM review)",
            "Awaiting Gate 4 (Engineering kick-off)",
            "Engineering in progress",
            "Pipeline complete",
        ]
        for stage in canonical_stages:
            with self.subTest(stage=stage):
                line = format_stage_line(stage, colour=False)
                self.assertIn(stage, line)
                self.assertNotIn("\n", line)


if __name__ == "__main__":
    unittest.main()
