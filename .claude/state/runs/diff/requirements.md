# Requirements: pipeline-status diff subcommand

## Problem Statement
Now that v3 has shipped `pipeline-status archive` (snapshot) and `pipeline-status history` (list / inspect past runs), a developer or returning Claude Code session frequently needs to answer the question "what changed between two pipeline runs?" — for example, "did the ADR change since the last release?" or "which artefacts in the live state differ from the archived `foo` run?". Today the only way to answer this is to invoke an external `diff -r .claude/state/ .claude/state/archive/<NAME>/`, parse the output by hand, and reason about which of the five tracked artefacts are added, removed, or modified. This is error-prone, depends on a non-Python tool, produces verbose per-line output when only a per-artefact summary is wanted, and provides no machine-stable contract that future CI gates (e.g. "fail the build if `adr.md` changed since last tag") can build on. A first-class `pipeline-status diff` subcommand turns this into one stdlib-only command with a deterministic, four-row summary.

## Goals
- Add a `pipeline-status diff [--against OTHER] NAME` subcommand that compares two pipeline runs (live vs archive, or archive vs archive) and prints exactly one summary line per tracked artefact.
- Each artefact line uses one of four glyphs: `+` (added on the right), `-` (removed from the right), `=` (byte-identical on both sides), `M` (present on both but content differs).
- Print a single-line footer summarising the four category counts: `Diff: <added> added, <removed> removed, <unchanged> unchanged, <modified> modified.`.
- Exit non-zero with a clear stderr message if either side fails to resolve to a directory; exit 0 on any successful comparison regardless of whether differences were found.
- Reuse the existing v3 infrastructure: `archive.slugify` for archive-name resolution (lazy import, matching `history.run_history`'s pattern), the `TRACKED_ARTEFACTS` tuple as the canonical list of basenames, and `MAX_READ_BYTES` as the per-file read cap.
- Preserve byte-identical stdout for every existing invocation: `pipeline-status`, `pipeline-status --watch [--interval N]`, `pipeline-status archive`, and `pipeline-status history`.
- Ship under the stdlib-only contract; tests use stdlib `unittest` + `tempfile.TemporaryDirectory` only.
- Complete any single comparison in under 200 ms p99 on a modern laptop where each side fits within `MAX_READ_BYTES` per artefact.

## Non-Goals
- No line-by-line text diff. The output is a per-artefact summary only; `difflib.unified_diff`, `difflib.HtmlDiff`, and any character-level diff are out of scope.
- No diff for files other than the five `TRACKED_ARTEFACTS` (no recursive directory diff, no extra-files report, no `.gitignore`-style handling).
- No `--json` / machine-readable output mode (still deferred from v1).
- No three-way diff, no merge, no patch generation.
- No diff between non-adjacent revisions of the same archive (archives are immutable once written; nothing to revise).
- No `--ignore-whitespace`, `--ignore-case`, `--ignore-blank-lines`, or any other content-normalisation flag; comparison is byte-equality of the (capped) file content.
- No caching, manifest, lockfile, index, PID file, or any other auxiliary write. `diff` is strictly read-only on the filesystem.
- No `pipeline-status restore` (or any write-back from archive to live state) — owned by a sibling pipeline.
- No `--watch` / `--interval` integration for `diff`. The subcommand is one-shot only.
- No colour-coded glyphs as a hard requirement. ANSI colour is MAY-level (nice-to-have) per the feature request; the default contract is plain ASCII glyphs.
- No removal, rename, or modification of any source file under `.claude/state/` or `.claude/state/archive/`.
- No changes to v1 inspector contracts, v1 stage rules, v1/v2 filled-detection heuristics, v2 watch behaviour, or v3 `archive` / `history` behaviour. All frozen.
- No new files added inside the `pipeline_status/` package beyond at most ONE new submodule (`pipeline_status/diff.py`) plus its test file.

## User Stories

> As a developer about to cut a release, I want to run `pipeline-status diff <last-release>` so that I can immediately see which of the five tracked artefacts in `.claude/state/` have changed since the previous archived run, without leaving the terminal or reading raw file content.

Acceptance criteria:
- [ ] `pipeline-status diff foo` compares the live `.claude/state/` (right side) against `.claude/state/archive/foo/` (left side) and prints exactly five rows (one per `TRACKED_ARTEFACTS` entry, in canonical order) plus one footer line.
- [ ] Each row begins with one of `+`, `-`, `=`, `M`, followed by a single space, followed by the artefact basename verbatim.
- [ ] The footer is `Diff: <a> added, <r> removed, <u> unchanged, <m> modified.` where the four counts sum to exactly 5.
- [ ] Exit code is 0 on a successful comparison, regardless of whether the four counts are non-zero or all-`=`.

> As a developer investigating divergence between two past runs, I want to invoke `pipeline-status diff --against bar foo` so that I can compare archive `bar` (left) to archive `foo` (right) without needing the live state to be present.

Acceptance criteria:
- [ ] `pipeline-status diff --against bar foo` resolves both `<archive_root>/bar/` and `<archive_root>/foo/`, reads the five tracked artefacts from each, and prints the same five-row + footer layout.
- [ ] The `--against bar` value is the LEFT side ("old"); the positional `foo` is the RIGHT side ("new"). Added/removed glyphs reflect this orientation: `+ X` means present in `foo` and absent in `bar`.
- [ ] If `--against` resolves to a missing directory, the command exits non-zero with a stderr message naming the missing path.

> As a maintainer protecting the v1/v2/v3 contracts, I want the new `diff` subcommand to leave every existing invocation byte-identical so that prior tests and user muscle memory continue to work unchanged.

Acceptance criteria:
- [ ] `pipeline-status` with no arguments produces stdout byte-identical to the v3 release for any given filesystem state.
- [ ] `pipeline-status --watch [--interval N]` behaves byte-identically to v2.
- [ ] `pipeline-status archive [--name N]` behaves byte-identically to v3.
- [ ] `pipeline-status history` and `pipeline-status history NAME` behave byte-identically to v3.
- [ ] Every existing test under `tests/` continues to pass unchanged.

> As an orchestrator operator who supplied a mixed-case or otherwise non-slug archive name, I want `pipeline-status diff Foo-Bar` to resolve the same way `pipeline-status history Foo-Bar` does so that I do not have to remember slug normalisation rules separately for each subcommand.

Acceptance criteria:
- [ ] The positional `NAME` argument is passed through `archive.slugify(...)` (lazy-imported) before path resolution.
- [ ] The `--against OTHER` value is passed through the same slugifier before path resolution.
- [ ] `Foo Bar` and `foo-bar` resolve to the same on-disk directory (`<archive_root>/foo-bar/`).
- [ ] If either slug is empty after normalisation, the command exits non-zero with a clear stderr message and writes nothing.

> As a maintainer wanting hermetic, fast tests, I want the new subcommand tested under stdlib `unittest` + `tempfile.TemporaryDirectory` only so that the suite stays fast and reproducible.

Acceptance criteria:
- [ ] New tests live under `tests/` and use only stdlib `unittest` + `tempfile` (+ `unittest.mock` where helpful).
- [ ] No new test spawns a subprocess, opens a network socket, or touches the real `.claude/state/` directory of the repo.
- [ ] The full suite (`python -m unittest discover -s tests`) continues to complete in under 5 seconds wall time.

## Functional Requirements

### Top-level CLI surface

1. The CLI MUST accept `diff` as a recognised positional subcommand, alongside the existing `archive` and `history`. The recognised set becomes exactly `{archive, history, diff}`. Any other positional value MUST cause argparse to exit with its default non-zero status and print a usage error to stderr.
2. When no subcommand is supplied, the CLI MUST behave exactly as v1/v2/v3: the one-shot inspector, honouring `--watch` and `--interval`. Stdout for that path MUST remain byte-identical to the v3 release.
3. The `--watch` and `--interval` flags MUST be rejected (argparse error to stderr, non-zero exit) when combined with `diff`. The `diff` subparser MUST NOT declare `--watch` or `--interval`; argparse's default behaviour (subparsers do not inherit parent optionals at parse time when a subcommand is consumed) suffices.
4. `--help` at the top level MUST list `diff` as an available subcommand alongside `archive` and `history`. `pipeline-status diff --help` MUST describe the positional `NAME`, the optional `--against OTHER`, and the exit-code contract.
5. The package layout MUST add at most ONE new submodule (`pipeline_status/diff.py`) plus its test file (`tests/test_diff.py`). No other production files are introduced inside the package. The wiring in `pipeline_status/__main__.py` follows the v3 subparser-registration pattern (`add_diff_subparser(subparsers)` called from `_build_parser`).

### `diff` subcommand — argument parsing

6. The `diff` subparser MUST declare exactly two arguments:
   - one required positional `NAME` (no `nargs`, default behaviour),
   - one optional `--against OTHER` (defaulting to `None` when omitted).
   No other flags MUST be accepted (in particular: no `--watch`, no `--interval`, no `--json`, no `--colour`, no `--ignore-*`).
7. If `NAME` is missing from the command line, argparse MUST emit its standard "the following arguments are required: NAME" error to stderr and exit with its default code. No custom handling is required.

### `diff` subcommand — side resolution

8. The `diff` subcommand MUST resolve the two sides as follows:
   - `state_dir = Path.cwd() / ".claude" / "state"` (same convention as v1/v2/v3).
   - `archive_root = state_dir / "archive"`.
   - **Right side** (always the positional `NAME`): `right_dir = archive_root / slugify(NAME)`.
   - **Left side**:
     - If `--against OTHER` is supplied: `left_dir = archive_root / slugify(OTHER)`.
     - Otherwise: `left_dir = state_dir` (the live state directory itself).
9. The slugifier MUST be `archive.slugify`, imported lazily inside the action callable (same pattern as `history.run_history`'s lazy import). The `diff` module MUST NOT import `archive` at module load time.
10. If `slugify(NAME)` returns an empty string, the command MUST write a clear stderr error (e.g. `pipeline-status: error: diff name is empty after normalisation`) and exit with code 1. No comparison is performed.
11. If `--against OTHER` is supplied and `slugify(OTHER)` returns an empty string, the command MUST write a clear stderr error and exit with code 1.
12. After resolution, the command MUST verify that **`right_dir.is_dir()` is true**. If not, it MUST write a stderr error naming the missing path (e.g. `pipeline-status: error: archive 'foo' not found at <path>`) and exit with code 1.
13. After resolution, the command MUST verify that **`left_dir.is_dir()` is true** when `--against` is supplied. If not, it MUST write a stderr error naming the missing path and exit with code 1.
14. When `--against` is omitted, `left_dir` is the live `.claude/state/` directory. If that directory does not exist (`is_dir()` is false), the command MUST write a stderr error (e.g. `pipeline-status: error: .claude/state/ not found or not a directory`) and exit with code 2 (matching v1 missing-state semantics for the live path). Note that this contrasts with FR-15: a *missing* live directory is a structural error, while *missing artefacts inside* a present live directory is acceptable per the feature request ("Live state missing on the LIVE side is acceptable — those artefacts simply appear as removed").

### `diff` subcommand — per-artefact comparison

15. For each `name` in `TRACKED_ARTEFACTS` (in the canonical order defined by `pipeline_status.archive.TRACKED_ARTEFACTS`), the command MUST determine the per-artefact category using exactly the following rules:
    - `left_path = left_dir / name`; `right_path = right_dir / name`.
    - `left_present = left_path.is_file()`; `right_present = right_path.is_file()`.
    - If `right_present and not left_present`: category is `+` (added).
    - If `left_present and not right_present`: category is `-` (removed).
    - If `left_present and right_present`:
      - Read up to `MAX_READ_BYTES` from each side (using `Path.read_bytes()` then slicing, or an explicit capped read — either is acceptable as long as the cap is honoured).
      - If the two byte sequences are equal: category is `=` (unchanged).
      - Otherwise: category is `M` (modified).
    - If `not left_present and not right_present`: the artefact is **not emitted in the row list**, but is **counted as "unchanged"** in the footer counts. (Both sides agree the file does not exist; this is a no-op row.)
16. The comparison MUST be byte-equality of the (capped) file content. Implementations MUST NOT normalise line endings, trim whitespace, lowercase, or apply any content transformation prior to comparison.
17. Reads MUST be capped at `MAX_READ_BYTES` (the v1 constant, currently 10 MiB) per file per side. Files larger than the cap are compared on the first `MAX_READ_BYTES` only; the result is exact for files within the cap and best-effort for files exceeding it. This matches v1 inspector behaviour.
18. The slugifier MUST NOT be re-imported inside the per-artefact loop; one import per invocation suffices (or zero imports, if the slugifier was already pulled in during side resolution).
19. Reads MUST tolerate transient I/O errors gracefully: if `Path.read_bytes()` raises `OSError` (including `PermissionError`, `IsADirectoryError`) on either side for a given artefact, the comparison for that single artefact MUST treat the unreadable side as **absent** and continue to the next artefact. The command MUST NOT abort the overall comparison because of a single per-file read failure. (Rationale: this mirrors v1 inspector tolerance and ensures `diff` is a useful triage tool even on partial filesystems.)

### `diff` subcommand — output format

20. On a successful comparison, the command MUST write to stdout exactly the following sequence of lines (each terminated by `\n`):
    - One row per artefact where the category is `+`, `-`, `=`, or `M`. Rows MUST appear in the canonical order of `TRACKED_ARTEFACTS` (skipping artefacts that are absent from both sides per FR-15).
    - One blank line (just `\n`).
    - One footer line of the form `Diff: <a> added, <r> removed, <u> unchanged, <m> modified.` where `<a>`, `<r>`, `<u>`, `<m>` are decimal integers and the four counts sum to exactly the cardinality of `TRACKED_ARTEFACTS` (currently 5).
21. Each row MUST be formatted as `<glyph><space><basename>` where `<glyph>` is one of `+`, `-`, `=`, `M` and `<basename>` is the artefact's canonical name verbatim (e.g. `+ feature-request.md`). No leading indentation, no trailing whitespace.
22. The output MUST be byte-deterministic: identical filesystem state on both sides MUST produce byte-identical stdout (modulo any optional ANSI colour wrapping, see FR-23).
23. ANSI colour codes for the four glyphs MAY be emitted when `formatting.use_colour()` returns true (i.e. stdout is a TTY and `NO_COLOR` is unset), but the requirement is **MAY**, not MUST. The plain-ASCII path is the contract; any colour decoration MUST NOT alter the byte length of the underlying glyph or basename, and MUST NOT change the footer line.
24. Clear-screen sequences MUST NOT be emitted by `diff` under any circumstance (those are watch-mode-only primitives, frozen from v2).

### Exit codes

25. The CLI MUST adopt the following exit-code contract for `diff`:
    - Successful comparison (regardless of category mix) → 0.
    - `slugify(NAME)` or `slugify(OTHER)` produces an empty string → 1.
    - `right_dir` does not resolve to a directory → 1.
    - `left_dir` does not resolve to a directory when `--against` is supplied → 1.
    - Live `.claude/state/` directory missing when `--against` is omitted → 2 (matches v1).
    - Argparse-level argument errors (missing `NAME`, unknown subcommand, `--watch` combined with `diff`, etc.) → argparse's default (typically 2).

### Inter-module contract

26. The `diff` module MUST export a public `add_diff_subparser(subparsers) -> argparse.ArgumentParser` registration helper that `__main__._build_parser()` calls (alongside `add_archive_subparser` and `add_history_subparser`).
27. The `diff` module MUST export a public `run_diff(args: argparse.Namespace) -> int` action callable, registered via `sp.set_defaults(func=run_diff)` inside `add_diff_subparser`.
28. The `diff` module MUST NOT import `pipeline_status.archive` or `pipeline_status.history` at module load time. Where it needs `archive.slugify` or `archive.TRACKED_ARTEFACTS`, it MUST use a lazy import inside `run_diff` (and any helpers called only from `run_diff`). This preserves the v3 parallel-fan-out test seam and keeps `pipeline-status` (no args) startup free of `diff`-module costs beyond the cheap subparser registration.
29. The `__main__.py` edits MUST be additive and minimal: one new import of `add_diff_subparser`, one new call to `add_diff_subparser(subparsers)` in `_build_parser()`. The `_run_one_shot()`, `--watch` lazy import, `_interval_type`, `_locate_state_dir`, and `_EPILOG` paths MUST remain byte-identical.
30. The existing v3 modules (`archive.py`, `history.py`, `format_history.py`) MUST NOT be modified by this iteration. In particular, `archive.TRACKED_ARTEFACTS`, `archive.slugify`, and `archive.MAX_READ_BYTES`-or-equivalent constants MUST be consumed by reading, not by re-export or refactor.

## Non-Functional Requirements

- **Performance — `diff`**: a single invocation comparing two sides where each side's five tracked artefacts fit within `MAX_READ_BYTES` MUST complete in under 200 ms p99 on a modern laptop. Files at the `MAX_READ_BYTES` cap (10 MiB) per side may push this budget; the budget applies to "typical" archives (≤ 1 MiB total per side).
- **Performance — startup overhead**: invoking `pipeline-status` (no subcommand) MUST NOT trigger import of `pipeline_status.diff` beyond the cheap `add_diff_subparser` registration call (which only creates an argparse subparser and attaches a `set_defaults(func=run_diff)`). The body of `run_diff` and its helpers MUST NOT execute on the no-subcommand path.
- **Dependencies**: stdlib only. The allowed imports for `pipeline_status/diff.py` are: `argparse`, `sys`, `pathlib`, and a lazy import of `pipeline_status.archive` (for `slugify` and `TRACKED_ARTEFACTS`). No `difflib`, no `hashlib`, no `filecmp`, no third-party packages, no new entries in `pyproject.toml`.
- **Portability**: MUST run on Linux, macOS, and Windows 10+ with Python 3.10+ unchanged. All filesystem operations use `pathlib`; no POSIX-only flags.
- **Security**: read-only access to `.claude/state/` and `.claude/state/archive/`. No `eval`, no `exec`, no `subprocess`, no network. Path-traversal safety is inherited from `archive.slugify`'s output character set (`[a-z0-9-]` only); the slugifier guarantees `/`, `\`, and `..` cannot appear in slug output. A defensive assertion that `left_dir` and `right_dir` resolve under their expected parents is OPTIONAL but recommended.
- **Resource limits**: per-file reads MUST respect the v1 `MAX_READ_BYTES = 10 MiB` cap. No file is loaded fully into memory beyond that cap. The two sides MUST NOT be held in memory simultaneously longer than needed for the equality comparison (i.e. release each `(left_bytes, right_bytes)` pair after computing its category before reading the next artefact).
- **Determinism**: given identical filesystem state on both sides, `diff` stdout MUST be byte-identical across runs (modulo optional ANSI colour wrapping per FR-23).
- **Backwards compatibility**: every existing test under `tests/` MUST pass unchanged. The v1 stdout for `pipeline-status` (no args), the v2 stdout for `--watch`, and the v3 stdout for `archive` / `history` / `history NAME` MUST be byte-identical to the prior releases for any given filesystem state.
- **Test execution**: `python -m unittest discover -s tests` MUST complete in under 5 seconds wall time for the full new + existing test suite. No test MUST spawn a subprocess, open a network socket, or call `time.sleep` with a non-zero real-time argument. New tests MUST use only `tempfile.TemporaryDirectory` (and `unittest.mock` where helpful for `cwd` manipulation).
- **Test coverage targets**: new tests MUST cover at minimum:
  - all four categories observed in a single invocation (`+`, `-`, `=`, `M`) with a mixed-content fixture;
  - the "both absent" no-op case for at least one artefact (verifying it is omitted from rows but counted as unchanged);
  - the no-`--against` form: live vs archive, with live missing entirely (exit 2), with archive missing (exit 1), with both present and identical (all `=`), with live empty (all `-`);
  - the `--against` form: archive vs archive, with `--against` resolving to a missing dir (exit 1), with both archives present;
  - slug normalisation: `Foo Bar` resolves to `foo-bar` on both `NAME` and `OTHER`; empty slug after normalisation exits 1;
  - footer counts sum to exactly 5 in every successful invocation;
  - `MAX_READ_BYTES` cap: a file larger than the cap on one side and identical-up-to-cap on the other side compares as `=`;
  - per-file read tolerance: an unreadable file on one side does not abort the overall comparison;
  - argparse rejection of `--watch` combined with `diff`, and of `diff` with no `NAME`;
  - byte-identical regression: `pipeline-status`, `pipeline-status --watch`, `pipeline-status archive`, `pipeline-status history`, `pipeline-status history NAME` all produce v3-identical stdout for the same filesystem state.
- **Compliance / regulatory**: none.

## Open Questions

1. **Footer counting of "both absent" artefacts**: the feature request specifies four glyph categories (`+`, `-`, `=`, `M`) but is silent on artefacts that are absent from both sides. Should "both absent" be counted as `=` (unchanged) — both sides agree the file is not there — or should it be silently omitted from both rows AND footer counts (so the footer counts sum to less than 5 in that case)? *Proposed default*: count "both absent" as **unchanged** (incremented into the `<unchanged>` slot of the footer) but **omit the row** from the printed list. This keeps the footer counts summing to exactly 5 (= `len(TRACKED_ARTEFACTS)`) as a stable invariant, while keeping the visible row list focused on artefacts that exist somewhere. The ADR documents this rule explicitly.

2. **`MAX_READ_BYTES` cap semantics for content equality**: if either side is larger than `MAX_READ_BYTES`, the comparison reads only the first `MAX_READ_BYTES` bytes from each. Files that differ only beyond the cap will compare equal. Is this acceptable, or should the comparison fall back to "always `M` if either side is over the cap"? *Proposed default*: **accept the cap-truncated comparison and document the limitation**. The 10 MiB cap is far larger than any realistic markdown / JSON artefact under `.claude/state/`; the false-negative ("they differ beyond 10 MiB but we report `=`") is preferable to the false-positive ("they're identical but we report `M` because both are over the cap"). The ADR notes this as a known edge case.

3. **Ordering of rows when categories interleave**: the feature request implies rows appear in `TRACKED_ARTEFACTS` order (one row per artefact). Should rows instead be grouped by category (`+` first, then `-`, then `M`, then `=`) for readability? *Proposed default*: **canonical artefact order**, not category order. This matches `pipeline-status` one-shot mode (which also iterates `TRACKED_ARTEFACTS` in canonical order) and keeps the output stable under content changes (the order of `feature-request.md` vs `adr.md` does not flip based on which one happened to change).

4. **Live side resolution when `--against` is omitted and the live `.claude/state/` exists but is empty**: per FR-15, "both absent" is counted as unchanged. So a completely empty live state vs an archive with all five artefacts produces five `-` rows (and an `Diff: 0 added, 5 removed, 0 unchanged, 0 modified.` footer). Is this the desired UX, or should an empty live state trigger an exit-2 "live state is empty" error? *Proposed default*: **emit the five `-` rows and exit 0**. The feature request explicitly says "Live state missing on the LIVE side is acceptable — those artefacts simply appear as removed." We extend the same rule to "live state is present but empty": the artefacts appear as removed; no error.

5. **`diff` against a self-reference (e.g. `pipeline-status diff foo --against foo`)**: the two sides resolve to the same directory; comparison trivially produces five `=` rows. Should the command short-circuit (skip the file reads) or proceed normally? *Proposed default*: **proceed normally**. The performance cost is negligible (5 capped reads × 2 sides = ≤ 100 MiB even in the pathological case, but realistically ≤ 1 MiB), and special-casing adds complexity for no user benefit. The ADR notes this is a no-op invocation that may be useful in CI as a smoke test.

6. **Diff against a slugified `NAME` that happens to equal the literal directory name `archive`** (i.e. `pipeline-status diff archive` would resolve to `.claude/state/archive/archive/`): this is benign on the slug side (the slug is `archive`, the resolved path is a child of `archive_root`) but might confuse users. Should the command warn? *Proposed default*: **no warning**. The slug `archive` is no different from any other slug; if a user genuinely named a run "archive" it MUST work. The ADR notes this as an edge case but does not special-case it.

7. **Symlinked archive directories**: per v3 Decision 7, `Path.is_dir()` follows symlinks by default, so `history` follows them. Should `diff` follow them identically? *Proposed default*: **YES — follow symlinks**, identical to v3. This keeps behaviour consistent across subcommands and matches Python's default `Path.is_dir()` semantics. The ADR documents this as a known edge case.

8. **Footer line format — Oxford comma and period**: the feature request example reads `Diff: 1 added, 0 removed, 3 unchanged, 1 modified.`. Should the trailing period be optional? *Proposed default*: **always emit the trailing period**, matching the feature request example byte-for-byte. The four commas separate the four counts; no Oxford comma is needed (the last item is preceded by "and"-less comma). Format string: `f"Diff: {a} added, {r} removed, {u} unchanged, {m} modified."`.

## Assumptions

- The v1 + v2 + v3 architecture is in place and stable: package layout `pipeline_status/{__init__,__main__,inspectors,stage,formatting,watch,archive,history,format_history}.py`, `pyproject.toml` declares the `pipeline-status` console script, `tests/` uses stdlib `unittest`, and the existing v1/v2/v3 contracts are frozen.
- The v3 subparser-registration pattern (`add_archive_subparser`, `add_history_subparser` called from `_build_parser`) is established and is the canonical way to add a new subcommand. `add_diff_subparser` follows the same shape.
- `pipeline_status.archive.TRACKED_ARTEFACTS` is exposed as a public module-level tuple of the five canonical basenames in canonical copy order: `("feature-request.md", "requirements.md", "adr.md", "tasks.json", "worktrees.json")`.
- `pipeline_status.archive.slugify` is exposed as a public function with signature `slugify(text: str) -> str` and the slug rules documented in v3 (lowercase, replace runs of non-`[a-z0-9]` with `-`, strip leading/trailing `-`, return empty if nothing remains).
- `pipeline_status.inspectors.MAX_READ_BYTES` (or an equivalent v1 constant) is exposed and equals 10 MiB. If the `diff` module needs the constant, it imports it from `pipeline_status.inspectors` (which is master code, present in every worktree by definition).
- `pipeline_status.formatting.use_colour()` (or the equivalent v1 helper) is the single point of truth for whether ANSI codes may be emitted; `diff` consults it for the optional colour decoration (FR-23). If the helper does not exist under that exact name, `diff` falls back to plain ASCII unconditionally — the plain path is the contract.
- The repo root is the current working directory when the command is invoked; no auto-discovery via parent-directory walking is required (consistent with v1/v2/v3).
- All state and archive files use UTF-8 encoding, but `diff` does NOT decode bytes — it compares raw `bytes` from `Path.read_bytes()`. Encoding is irrelevant to the byte-equality contract.
- The sibling pipeline adding `pipeline-status restore` does NOT modify any of the modules `diff` consumes (`archive.py`, `history.py`, `__main__.py` beyond its own additive `add_restore_subparser` call). The `diff` and `restore` pipelines are independently mergeable; merge conflicts in `__main__.py` will be limited to the two-line addition near `add_archive_subparser` / `add_history_subparser` and are trivially resolvable (both registration calls land alphabetically or by feature order — the PM Agent for `diff` should not pre-suppose `restore`'s wiring placement).
- Unit tests for the new subcommand use `tempfile.TemporaryDirectory` and `unittest.mock` (e.g. for `cwd` manipulation via `os.chdir` saved/restored in `setUp`/`tearDown`); no real `.claude/state/` is touched.
- `--help` rendering is the responsibility of argparse defaults; no custom help formatter is required. The `diff` subparser's `help=...` and `description=...` strings are short prose; the exact wording is not part of the byte-identical contract.
