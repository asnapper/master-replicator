# Requirements: `pipeline-status restore` Subcommand

## Problem Statement

The `pipeline-status` CLI (v3) added `archive` (snapshot live `.claude/state/` into `.claude/state/archive/<slug>/`) and `history` (read past archives). There is no first-class counterpart that goes the other way. Today, when the orchestrator (or a developer recovering a corrupted local state) wants to restore an archived run's artefact files back into the live `.claude/state/` directory, they must `cp` files by hand from `.claude/state/archive/<NAME>/` into `.claude/state/`. This is error-prone (silently overwriting a live in-progress run, leaving partial state, or typoing the archive name) and undiscoverable (no entry in `--help`). v4 adds a `restore NAME [--force]` subcommand that performs the same five-artefact copy in the reverse direction, with safe-by-default per-file collision detection and an opt-in `--force` overwrite.

## Goals

- Add a new `pipeline-status restore NAME [--force]` subcommand that copies the five tracked artefact files from `.claude/state/archive/<slug>/` into `.claude/state/`.
- Reuse `pipeline_status.archive.TRACKED_ARTEFACTS` (the canonical five-basename tuple) and `pipeline_status.archive.slugify` (the canonical slug rules) via **lazy imports** inside the `restore` action, so the no-args, `--watch`, `archive`, and `history` paths pay no extra import cost.
- Collision detection is **per-file** and **all-or-nothing**: if any live target file already exists and `--force` is not set, the subcommand exits 1, lists all conflicting basenames on stderr, and copies nothing.
- `--force` overwrites every existing live target that is also present in the archive. It does NOT delete live files that are absent from the archive. Restore is purely additive / overwriting, never removing.
- On success, print `Restored N file(s) from .claude/state/archive/<slug>/` to stdout and exit 0, where `N` is the count of files actually copied (0..5).
- If the archive directory does not exist after slugifying `NAME`, exit 1 with a stderr error matching the v3 `history NAME` style: `pipeline-status: error: archive '<name>' not found at <path>`.
- v1/v2/v3 stdout paths (no-args one-shot, `--watch`, `archive`, `history`, `history NAME`) remain **byte-identical**; the only `--help` delta is the addition of `restore` to the `{archive,history,restore}` subparser list.
- All new code is stdlib-only and tested with `unittest` + `tempfile.TemporaryDirectory`, with no subprocess and no network.

## Non-Goals

- No restore across repositories or remote sources. Source is always `.claude/state/archive/<slug>/` under the current working directory.
- No partial restore (e.g. "only `tasks.json`"). Restore always considers the same five tracked basenames; whichever subset exists in the archive is what gets copied.
- No `--dry-run` mode. Deferred to a later increment if requested.
- No backup-before-overwrite mechanism. If a user wants a safety net before `restore --force`, they run `pipeline-status archive` first.
- No deletion of live files that are absent from the archive. Restore never removes.
- No interactive conflict-resolution prompt. Behaviour is batch only: either succeed, or hard-error.
- No `--json` machine-readable output.
- No `--watch` / `--interval` on the `restore` subparser.
- No changes to `archive.py`, `history.py`, `format_history.py`, `inspectors.py`, `stage.py`, `formatting.py`, or `watch.py` other than (at most) re-exporting already-public symbols. The v3 `archive` and `history` behaviours are frozen.
- No changes to the `pipeline-status` no-args one-shot output or `--watch` output. Byte-identical regression is mandatory.
- No support for nested archive directories. Archives remain flat under `.claude/state/archive/`.

## User Stories

> As an orchestrator running an autonomous pipeline, I want to restore the last archived run's artefacts into `.claude/state/` so that I can resume work from a known-good snapshot.
>
> Acceptance criteria:
> - Given an existing archive directory `.claude/state/archive/foo-bar/` containing some subset of the five tracked artefacts AND no live files of the same names in `.claude/state/`,
> - When I run `pipeline-status restore foo-bar`,
> - Then every artefact present in the archive is copied to `.claude/state/<basename>`, the count `N` of copied files (0..5) is printed as `Restored N file(s) from .claude/state/archive/foo-bar/`, and the exit code is 0.

> As a developer recovering a corrupted local state, I want `restore` to refuse by default when any live file already exists, so that I do not silently overwrite uncommitted work.
>
> Acceptance criteria:
> - Given an archive containing (at least) `requirements.md` and a live `.claude/state/requirements.md` that already exists,
> - When I run `pipeline-status restore foo-bar` (no `--force`),
> - Then nothing is copied (all live files unchanged byte-for-byte), exit code is 1, and stderr contains a single line listing the conflicting basename(s): `pipeline-status: error: refusing to overwrite existing file(s): requirements.md` (multiple conflicts are listed comma-separated in `TRACKED_ARTEFACTS` order).

> As an orchestrator that deliberately wants to roll the live state back to an archived snapshot, I want `--force` to overwrite existing live files without prompting.
>
> Acceptance criteria:
> - Given an archive containing a subset of the five artefacts AND any number of pre-existing live files,
> - When I run `pipeline-status restore foo-bar --force`,
> - Then every artefact present in the archive overwrites the corresponding live file (or is created if absent), live files that are NOT in the archive remain untouched, `N` reflects the files actually copied, and exit code is 0.

> As a user typing the wrong archive name, I want a clear error pointing at the resolved path so that I can fix the typo.
>
> Acceptance criteria:
> - Given that no directory exists at `.claude/state/archive/<slug>/` (where `<slug>` is `slugify(NAME)`),
> - When I run `pipeline-status restore NAME` (with or without `--force`),
> - Then stdout is empty, stderr contains `pipeline-status: error: archive '<NAME>' not found at .claude/state/archive/<slug>` (the user-supplied name is quoted with `!r`-style single quotes; the path is the resolved slug path), and exit code is 1.

> As a user who supplied a name that slugifies to the empty string (e.g. `restore "!!!"`), I want a clear error so that I do not silently target the archive root.
>
> Acceptance criteria:
> - Given that `slugify(NAME)` returns `""`,
> - When I run `pipeline-status restore NAME [--force]`,
> - Then stdout is empty, stderr contains `pipeline-status: error: archive name is empty after normalisation`, and exit code is 1.

> As a maintainer of the v3 byte-identical stdout contract, I want `pipeline-status restore --help` to add itself to the subparser list without changing any existing output.
>
> Acceptance criteria:
> - `pipeline-status` (no args), `pipeline-status --watch`, `pipeline-status archive ...`, `pipeline-status history`, and `pipeline-status history NAME` produce byte-identical stdout to v3 master for identical filesystem state.
> - `pipeline-status --help` lists `restore` alongside `archive` and `history` in the subcommand usage line (this is the only `--help` change).
> - `pipeline-status restore --help` shows the positional `NAME` argument, the `--force` flag, and a one-line description of the all-or-nothing collision semantics.

## Functional Requirements

1. **FR-1** — The `restore` subcommand MUST be registered on the existing `argparse` subparsers action in `pipeline_status/__main__.py`'s `_build_parser()`, alongside `archive` and `history`. Unknown subcommands MUST continue to be rejected by argparse with exit code 2 and a usage error on stderr.

2. **FR-2** — The `restore` subparser MUST declare exactly one required positional argument `NAME` (string) and exactly one optional boolean flag `--force` (default `False`). It MUST NOT declare `--watch`, `--interval`, `--name`, `--json`, `--dry-run`, or any other flag.

3. **FR-3** — If a user combines `restore` with `--watch` or `--interval` (e.g. `pipeline-status restore foo --watch`), argparse MUST reject the invocation with its default behaviour (exit code 2, usage error on stderr). No code in the `restore` action is responsible for this; it falls out of subparsers not inheriting the parent's optionals at the subcommand-consumption step.

4. **FR-4** — `pipeline-status --help` MUST list `restore` alongside `archive` and `history` in the subcommand line (argparse's default rendering). `pipeline-status restore --help` MUST display the positional `NAME` and the `--force` flag with non-empty help text.

5. **FR-5** — The `restore` action callable MUST live in a new module `pipeline_status/restore.py` (Task ownership; see ADR). Its public symbols MUST be:
   - `add_restore_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser`
   - `run_restore(args: argparse.Namespace) -> int`
   The subparser registration helper calls `sp.set_defaults(func=run_restore)`.

6. **FR-6** — Inside `run_restore`, the implementation MUST perform a **lazy import** of `TRACKED_ARTEFACTS` and `slugify` from `pipeline_status.archive` (not at module top level). This preserves the v3 import-cost invariant for the no-args, `--watch`, `archive`, and `history` paths and avoids any test-time dependency from `restore`'s unit tests on `archive.py`.

7. **FR-7** — `run_restore` MUST locate the state directory as `state_dir = Path.cwd() / ".claude" / "state"`. If `state_dir` is not a directory, it MUST print `pipeline-status: error: .claude/state/ not found or not a directory` to stderr and return exit code 2 (matching v1's missing-state exit code).

8. **FR-8** — `run_restore` MUST compute `slug = slugify(args.name)`. If `slug == ""`, it MUST print `pipeline-status: error: archive name is empty after normalisation` to stderr and return exit code 1. (The slugifier's path-traversal safety properties — output is `[a-z0-9-]` only, so `/`, `\`, and `..` cannot survive — are inherited from `archive.slugify` and require no additional defensive code in `restore`.)

9. **FR-9** — `run_restore` MUST compute `archive_dir = state_dir / "archive" / slug`. If `archive_dir.is_dir()` is `False` (including the cases where `state_dir / "archive"` itself does not exist, or `archive_dir` is a regular file, or it is a broken symlink), it MUST print `pipeline-status: error: archive '<NAME>' not found at <archive_dir>` to stderr (with `<NAME>` being the user-supplied raw `args.name`, single-quoted as via `!r`; `<archive_dir>` is the resolved path string) and return exit code 1.

10. **FR-10** — `run_restore` MUST iterate `TRACKED_ARTEFACTS` (in declaration order) and build two ordered lists:
    - `present`: basenames `b` for which `(archive_dir / b).is_file()` is `True`.
    - `conflicts`: basenames `b` from `present` for which `(state_dir / b).exists()` is also `True`.
    The collision check uses `Path.exists()` (which follows symlinks); a live symlink that points at a missing target is treated as non-existent. Files outside `TRACKED_ARTEFACTS` are never inspected.

11. **FR-11** — If `--force` is not set and `conflicts` is non-empty, `run_restore` MUST print `pipeline-status: error: refusing to overwrite existing file(s): <list>` to stderr, where `<list>` is the comma-separated (`", "` separator) list of conflicting basenames in `TRACKED_ARTEFACTS` declaration order. It MUST then return exit code 1 **without copying any file**. This is the all-or-nothing collision contract: not even non-conflicting files are restored in this branch.

12. **FR-12** — If `--force` is set, or if `conflicts` is empty, `run_restore` MUST copy each file in `present` from `archive_dir / b` to `state_dir / b` using `shutil.copy2` (content + mtime + permissions). The copy loop MUST process basenames in `TRACKED_ARTEFACTS` declaration order. Each successful copy increments a counter `n` that begins at zero.

13. **FR-13** — `--force` MUST overwrite existing live files that are also present in the archive (this is the explicit `shutil.copy2` overwrite semantic — destination is replaced with the source content + mtime + permissions). `--force` MUST NOT delete or modify any live file under `.claude/state/` whose basename is not in `present` (i.e. files absent from the archive remain untouched, including files that are not in `TRACKED_ARTEFACTS` at all, such as `worktrees.json` when the archive lacks it).

14. **FR-14** — After the copy loop completes, `run_restore` MUST print `Restored N file(s) from .claude/state/archive/<slug>/` to stdout (using the literal slug as resolved, not the raw `args.name`) and return exit code 0. `N` is the integer `n` accumulated in FR-12 and falls in the range 0..5 inclusive. The trailing newline on the printed line is `print()`'s default.

15. **FR-15** — `N = 0` is a valid success case: it occurs when `archive_dir` exists but contains none of the five tracked basenames as regular files (or as symlinks to regular files). `run_restore` MUST still print `Restored 0 file(s) from .claude/state/archive/<slug>/` and return exit code 0. No directory is created or modified in this case.

16. **FR-16** — `run_restore` MUST NOT create the `.claude/state/` directory if it is missing (FR-7 returns 2 before any write). It MUST NOT create the `.claude/state/archive/` directory or the `archive_dir` itself (those are read-only inputs; missing inputs are FR-9 errors). It MUST NOT create any sub-directory under `.claude/state/`. It MUST only write to `.claude/state/<basename>` for `<basename>` in `present`.

17. **FR-17** — `run_restore` MUST NOT read, write, or stat any file outside `state_dir` and `archive_dir`. No lockfile, no PID file, no manifest, no index, no cache, no backup file is created anywhere in the filesystem.

18. **FR-18** — The `pipeline_status.archive` module MUST NOT be modified for this feature. The `restore` module MUST consume `archive.TRACKED_ARTEFACTS` and `archive.slugify` only as a read-only import; no monkey-patching, no shadowing, no re-export from `archive.py`.

19. **FR-19** — `pipeline_status/__main__.py` MUST register the `restore` subparser via an eager (or lazy — implementer's choice) import of `add_restore_subparser` from `pipeline_status.restore`, alongside the existing `add_archive_subparser` and `add_history_subparser` calls in `_build_parser()`. The dispatch line in `main()` (`sys.exit(args.func(args))` when `args.cmd is not None`) is already in place from v3 and MUST NOT be altered.

20. **FR-20** — All five other production modules (`__init__.py`, `inspectors.py`, `stage.py`, `formatting.py`, `watch.py`, `archive.py`, `history.py`, `format_history.py`) MUST NOT be edited for this feature. The only production-code changes are: (a) new file `pipeline_status/restore.py`; (b) three lines added to `pipeline_status/__main__.py` (one import, one `add_restore_subparser(subparsers)` call, no other changes).

21. **FR-21** — A new test file `tests/test_restore.py` MUST be added covering, at minimum: (a) happy path with all five artefacts in the archive and no live state files; (b) happy path with a partial archive (e.g. only 2 of 5 artefacts); (c) `N = 0` happy path with an empty archive directory; (d) collision detection without `--force`, single conflict; (e) collision detection without `--force`, multiple conflicts (assert ordering matches `TRACKED_ARTEFACTS`); (f) collision present, `--force` set, all conflicting files overwritten with the archive's content; (g) `--force` does NOT remove live files absent from the archive; (h) archive directory missing — exit 1 with the FR-9 stderr message; (i) `state_dir` missing — exit 2 with the FR-7 stderr message; (j) empty-slug input (e.g. `"!!!"`) — exit 1 with the FR-8 stderr message; (k) the subparser rejects `--watch` and `--interval` (argparse `SystemExit` with code 2). All tests MUST use `unittest` + `tempfile.TemporaryDirectory` and MUST NOT spawn subprocesses or open network connections.

22. **FR-22** — The byte-content of a restored live file MUST be byte-identical to the corresponding archived file (modulo whatever `shutil.copy2` already guarantees: full content copy, mtime copy, permission copy, where the platform supports it). No transformation, normalisation, line-ending conversion, or encoding sniff is performed.

23. **FR-23** — The `restore` subparser's `--force` flag MUST be a boolean store-true argument (`action="store_true"`, default `False`). It MUST NOT be abbreviated (`-f` is not declared; only the long form `--force` is accepted). This keeps the CLI surface explicit and avoids collision with any future short-form flag.

24. **FR-24** — `run_restore` MUST be deterministic given identical filesystem state: same archive contents + same live state + same `args` produce the same exit code, the same stdout bytes, and the same stderr bytes (modulo ANSI colour, which is not used by `restore`).

25. **FR-25** — `run_restore` MUST NOT use ANSI colour escapes in any of its output. The output is operational (a confirmation line and error lines), not a status report; the v3 colour helpers in `pipeline_status.formatting` are not consumed.

## Non-Functional Requirements

### Performance

- **NFR-P1** — End-to-end wall time for `pipeline-status restore NAME` against an archive whose five tracked files each fit within `MAX_READ_BYTES` (10 MiB; v1 inspector cap) MUST be under 200 ms p99 on a developer laptop with a warm filesystem cache. `shutil.copy2` of five files totalling under 50 MiB easily satisfies this; no measurement infrastructure is added.
- **NFR-P2** — `restore`'s import cost MUST be paid only when the user actually invokes the `restore` subcommand. Specifically, `pipeline-status` (no args), `pipeline-status --watch ...`, `pipeline-status archive ...`, and `pipeline-status history ...` MUST NOT cause `pipeline_status.restore` to be imported beyond what `add_restore_subparser` itself requires (which is cheap: just `argparse` subparser registration; no `shutil`, no `Path.cwd()` until `run_restore` is invoked). The lazy import of `archive.TRACKED_ARTEFACTS` and `archive.slugify` inside `run_restore` reinforces this guarantee.

### Security

- **NFR-S1** — `restore` MUST NOT escape the `.claude/state/` directory. Because the only writes happen at `state_dir / basename` for `basename in TRACKED_ARTEFACTS` (a hard-coded tuple of five plain filenames with no `/`, `\`, or `..`), and because the only read happens at `archive_dir / basename` where `archive_dir = state_dir / "archive" / slug` and `slug` is the output of `archive.slugify` (whose output character set is `[a-z0-9-]` only), no path-traversal write or read is possible. No additional defensive check is required; the constructive proof is the type signature of the inputs.
- **NFR-S2** — `restore` MUST NOT follow symlinks **out of** `state_dir` when writing. The default `shutil.copy2` behaviour writes through any existing symlink at the destination, which means a malicious actor who can replace `.claude/state/requirements.md` with a symlink to `/etc/passwd` could redirect the overwrite. This is consistent with the v3 `archive` write-side stance (which uses `mkdir(exist_ok=False)` but otherwise relies on `.claude/state/` being trusted local state). The threat model treats `.claude/state/` as a trusted directory; users who do not trust their local checkout MUST NOT run `restore --force` from it. This is documented in the README change (FR-21's docs counterpart, see Open Questions).
- **NFR-S3** — `restore` MUST NOT execute any code from the archive. The archive contains only data files (`*.md`, `*.json`); they are copied byte-for-byte. No `eval`, no `exec`, no `subprocess`, no dynamic import based on archive contents.

### Compatibility & Regression

- **NFR-C1** — Python 3.10+ only (inherited from v1).
- **NFR-C2** — Stdlib only: `argparse`, `pathlib`, `shutil`, `sys`. No `os.path` (use `pathlib`), no `os.replace`/`os.rename` (`shutil.copy2` already overwrites), no `tomllib`, no third-party libraries, no new packaging.
- **NFR-C3** — Byte-identical stdout regression: all v1, v2, and v3 stdout-producing invocations MUST produce identical bytes after v4 lands, given identical filesystem state. The CI/test suite MUST include at least one regression test asserting this for `pipeline-status` (no args) and one for `pipeline-status history` (no args).
- **NFR-C4** — `--help` regression: `pipeline-status --help` MUST gain exactly one delta — `restore` is listed alongside `archive` and `history` in the `{archive,history,restore}` subparser usage line. No other change to the top-level help text.

### Testability

- **NFR-T1** — `tests/test_restore.py` MUST run in under 2 seconds total on a developer laptop, including all FR-21 scenarios.
- **NFR-T2** — Tests MUST NOT depend on the working directory beyond what each test sets up via `os.chdir(tmpdir)` (with `addCleanup` to restore). Tests MUST NOT touch the real `.claude/state/`.
- **NFR-T3** — Tests MUST NOT import `pipeline_status.history` or `pipeline_status.format_history` (the restore feature is independent of history rendering). They MAY import `pipeline_status.archive` to construct fixtures or to assert the canonical `TRACKED_ARTEFACTS` tuple — but the production code path remains lazy.

## Open Questions

1. **Q: When `--force` overwrites a live file, should the success line distinguish "overwrote 3 + created 2" vs. just "Restored 5 file(s)"?**
   - **Proposed default**: No. The success line is `Restored N file(s) from .claude/state/archive/<slug>/` regardless of how many were overwrites vs. creations. Adding an "overwrote X / created Y" breakdown is a usability nice-to-have but bloats the CLI contract and complicates byte-equality assertions in tests. Defer to v5 if requested. Architect proceeds with the simple single-N format.

2. **Q: Should `restore` warn (on stderr, exit 0) when `N == 0` because the archive is empty? It currently exits silently with `Restored 0 file(s) ...`.**
   - **Proposed default**: No warning. `N = 0` is a legitimate (if unusual) success: the archive directory exists and was scanned, and zero tracked files were found. Treating it as a warning would require a second stderr line that complicates the byte-identical-output contract. The single stdout line `Restored 0 file(s) ...` is sufficient signal. Architect proceeds without a warning.

3. **Q: Should `restore` accept `-f` as a short alias for `--force`?**
   - **Proposed default**: No (`--force` only). FR-23 explicitly forbids the short form to keep the CLI explicit, reserve `-f` for any future feature, and match the v3 `archive --name` convention (no short form there either). Architect proceeds long-form-only.

4. **Q: When the archive contains a tracked basename that is a directory (not a file) — e.g. someone manually `mkdir`'d `.claude/state/archive/foo/tasks.json/` — should `restore` skip it silently, error out, or treat it as a conflict?**
   - **Proposed default**: Skip silently. FR-10's `present` list is built from `(archive_dir / b).is_file()`, which returns `False` for directories. The directory is treated as "not present in the archive", so it is not copied and does not contribute to `N` or to `conflicts`. This matches the v3 `archive` stance, where `(state_dir / name).is_file()` is the gate. Architect proceeds with the `.is_file()` filter.

5. **Q: When `state_dir / basename` is a directory (not a file) and that basename is in `present` from the archive — e.g. the live state has a directory `.claude/state/tasks.json/` — does the collision check trigger?**
   - **Proposed default**: Yes, treat as a conflict. FR-10 uses `(state_dir / b).exists()`, which returns `True` for any existing inode (file, directory, symlink to either). Without `--force`, the conflict is reported and restore aborts. With `--force`, `shutil.copy2` will raise (it cannot copy a file onto a directory). To avoid a confusing exception, `run_restore` MUST handle this case explicitly: if `--force` is set AND `(state_dir / b).is_dir()` AND `not (state_dir / b).is_symlink()`, print `pipeline-status: error: cannot overwrite directory: <state_dir>/<basename>` to stderr and return exit code 1 **before any copy begins** (so the all-or-nothing contract holds — no partial writes occur). This requires a small pre-flight scan after the `conflicts` check passes. Architect proceeds with this guard; if rejected, the simpler alternative is to let `shutil.copy2` raise and translate the exception, which has worse error messages but less code.

6. **Q: Where exactly does `restore`'s subparser registration sit in `_build_parser()` — before or after `archive` and `history`?**
   - **Proposed default**: After `add_history_subparser(subparsers)`. The argparse subparser order affects only the `--help` rendering (`{archive,history,restore}`), not behaviour. Listing `restore` last preserves the v3 reading order and matches the chronological feature addition. Architect proceeds with `archive → history → restore` order.

7. **Q: Should `tests/test_restore.py` import `pipeline_status.archive` to verify that `restore`'s lazy-imported symbols (`TRACKED_ARTEFACTS`, `slugify`) actually exist and have the expected shape, or should it mock them?**
   - **Proposed default**: Import `pipeline_status.archive` directly in tests for the integration-style assertions (e.g. "calling `run_restore` against a real archive built by `archive.run_archive` round-trips correctly"). The production-code lazy import is preserved; the test-time eager import does not affect runtime cost on the non-`restore` paths. This keeps tests realistic and avoids brittle `unittest.mock` setup. Architect proceeds with direct imports in tests.

8. **Q: Does the README (Task E equivalent for v4) need to be updated in this iteration, or can it be deferred?**
   - **Proposed default**: Yes, update README in this iteration. Add one short subsection under `## CLI` (or the v3 equivalent) describing `pipeline-status restore NAME [--force]`: one paragraph of behaviour, one example without `--force`, one example with `--force`, exit-code matrix. This mirrors the v3 README task and keeps the docs in lockstep. Architect proceeds with a README delta as part of v4.

9. **Q: How is `--force` reported when there are no conflicts to overwrite — should the success line note "no overwrites occurred"?**
   - **Proposed default**: No special notation. `Restored N file(s) from .claude/state/archive/<slug>/` is emitted whether or not any overwrites occurred. `--force` is a permission grant, not a status indicator. Architect proceeds with the unified success message.

10. **Q: When the archive contains a tracked file as a symlink (`.claude/state/archive/foo/tasks.json -> ../tasks.json`), should `restore` copy the symlink target's content (current `shutil.copy2` default — follows symlinks) or preserve the symlink (`follow_symlinks=False`)?**
    - **Proposed default**: Follow symlinks (i.e. `shutil.copy2` default). This matches v3 `archive`'s stance (`shutil.copy2` follows symlinks when reading sources) and the inspector's `is_file()` check (which follows symlinks). The result is that `restore` writes a regular file at the destination containing the symlink target's bytes; the symlink itself is not preserved. Architect proceeds with default `shutil.copy2`.

## Assumptions

- The v3 `pipeline_status.archive` module exposes `TRACKED_ARTEFACTS: Final[tuple[str, ...]]` and `slugify(text: str) -> str` as public symbols (as documented in the v3 ADR, `.claude/state/archive/v3-final/adr.md`, sections "Decision 2" and "Task A — `pipeline_status/archive.py`"). `restore` reuses these unchanged.
- The v3 `_build_parser()` function in `pipeline_status/__main__.py` already creates a subparsers action (`subparsers = parser.add_subparsers(dest="cmd", required=False)`) and registers `archive` and `history`. `restore` registers itself on the same subparsers action.
- The v3 `main()` dispatch (`if getattr(args, "cmd", None) is not None: sys.exit(args.func(args))`) is already in place. `restore` plugs into `args.func` via `sp.set_defaults(func=run_restore)` and requires no change to `main()`.
- The `.claude/state/` directory and `.claude/state/archive/` directory layout are unchanged from v3: flat, no nesting beyond `archive/<slug>/<basename>`.
- The five canonical artefact basenames (`feature-request.md`, `requirements.md`, `adr.md`, `tasks.json`, `worktrees.json`) and their declaration order in `TRACKED_ARTEFACTS` are stable; if v3's `archive.py` were to change them, `restore`'s tests would catch the drift on the next master build (because `restore` lazy-imports the same tuple).
- The orchestrator and engineers run Python 3.10+ with no third-party packages installed beyond what v1/v2/v3 required (which is: none).
- A sibling pipeline is concurrently adding `pipeline-status diff A B` (Feature A). `restore` does NOT depend on `diff` and does NOT pre-declare any flag or behaviour that `diff` would own. Both features can land in any order; merge conflicts (if any) are limited to `__main__.py`'s `_build_parser()` (one new `add_*_subparser` call each) and the README (one new subsection each) and are mechanical to resolve.
- The `MAX_READ_BYTES` cap from `pipeline_status.inspectors` is not directly consumed by `restore` (we only copy files, not inspect their content), so `restore` has no opinion on artefact size. `shutil.copy2` streams the file regardless of size.
- The user's working directory when invoking `pipeline-status restore` is the repository root (where `.claude/state/` lives). This matches the v1/v2/v3 assumption; `_locate_state_dir`-equivalent logic in `run_restore` uses `Path.cwd()`.
- The git worktree on which the engineer implements `restore.py` is forked from a master tip that already contains the v3 `archive.py` and `history.py`. `restore` is not parallel-fan-out with v3 tasks; it follows them.
- No locale-specific behaviour is assumed beyond what `shutil.copy2` and `pathlib` already do on the host OS.
