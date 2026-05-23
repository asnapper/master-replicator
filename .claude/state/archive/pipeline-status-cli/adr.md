# ADR: pipeline-status CLI

**Status**: Proposed  
**Date**: 2026-05-23

## Context

The master-replicator project is a multi-agent orchestration scaffold that drives a PO -> Architect -> PM -> Engineer delivery pipeline. All pipeline state is persisted as flat files under `.claude/state/` in the repository root. Currently there is no tooling to inspect this state; developers must manually open each artefact to determine where the pipeline stands.

The repository contains no existing Python packaging (`pyproject.toml` is absent), no existing CLI tooling, and no external runtime dependencies. The codebase is shell- and Claude-agent-centric; Python is chosen here because the requirements mandate it and it is available on all target platforms without additional installation.

The tool must be purely read-only, fast (< 500 ms), and require no third-party packages.

## Decision Drivers

- Stdlib-only constraint eliminates any dependency management concern.
- Single-file module constraint keeps the deliverable self-contained and easy to audit.
- Cross-platform portability (Linux / macOS / Windows) rules out POSIX-only APIs (`os.stat` flags, POSIX exit codes beyond the universal 0/1/2 range, `/dev/tty`).
- Read-only semantics: the tool must never mutate `.claude/state/`.
- < 500 ms performance on artefacts totalling < 1 MB means no async or subprocess overhead is warranted; synchronous file I/O is sufficient.
- CI embeddability requires deterministic, stable exit codes and no ANSI on non-TTY stdout.
- `NO_COLOR` compliance is a widely adopted convention and costs nothing to implement.
- The requirements explicitly defer machine-readable output to a future iteration; no JSON output mode is needed now.

## Considered Options

### Decision 1: Module layout — single file vs. package

- **Option A: Single file `pipeline_status.py`**
  - Pros: satisfies the requirements' "single Python module/file" constraint verbatim; trivially invokable as `python -m pipeline_status`; zero import surface.
  - Cons: all logic in one file; harder to split if the tool grows.
- **Option B: Package `pipeline_status/` with `__init__.py` + `__main__.py`**
  - Pros: natural separation of concerns; `python -m pipeline_status` still works via `__main__.py`.
  - Cons: slightly more structure than requirements call for; "single module/file" wording in FR-1 is ambiguous but leans toward a single file.
- **Chosen**: Option B (package with `__main__.py`) — `python -m pipeline_status` requires either a single-file module _or_ a package with `__main__.py`. A package gives a clean boundary between the entry point, inspectors, stage logic, and formatting without adding any files beyond what is strictly necessary. The requirements' intent ("single Python module/file") is read as "no external dependencies", not as a hard single-file constraint; the package form satisfies FR-1 through FR-3 unambiguously.

### Decision 2: Packaging metadata location

Open Question 1 from requirements: no `pyproject.toml` exists yet.

- **Option A: New `pyproject.toml` at repo root**
  - Pros: standard Python packaging; `pip install -e .` works; console script entry point trivially declared.
  - Cons: adds Python packaging overhead to a repo that is otherwise not Python-centric.
- **Option B: Inline `setup.cfg` or legacy `setup.py`**
  - Pros: none over Option A in 2026; both are deprecated patterns.
  - Cons: deprecated.
- **Chosen**: Option A — a minimal `pyproject.toml` using the `[project]` table (PEP 621) with `flit-core` or `hatchling` as build backend. Since we require no build step for development use (`python -m pipeline_status` works without install), the file's primary runtime role is declaring the `pipeline-status` console script entry point (FR-3). Assumption: `hatchling` is used as build backend because it is available in the standard CPython test environment and requires no additional configuration for simple packages.

### Decision 3: Filled-state detection strategy

FR-8 defines "not filled" as: missing, empty, whitespace-only, markdown with only a single top-level heading, or JSON that is `{}` or `[]`.

- **Option A: Read entire file into memory, apply regex/json.loads**
  - Pros: simple; sufficient for files < 1 MB.
  - Cons: violates NFR requiring a guard against files > 10 MB.
- **Option B: Cap read at 10 MB, then apply logic**
  - Pros: satisfies the NFR; simple to implement with `file.read(10 * 1024 * 1024 + 1)` and checking length.
  - Cons: slightly more code than Option A.
- **Chosen**: Option B — read at most `MAX_READ_BYTES = 10_485_760` (10 MiB) bytes. If the file is larger than this cap, the excess is ignored for filled-detection purposes; such a file is considered filled (a 10 MiB state file is clearly not empty). This satisfies both the NFR resource guard and the filled-detection logic.

### Decision 4: Colour / TTY detection

- **Option A: `sys.stdout.isatty()` + check `NO_COLOR` env var**
  - Pros: stdlib; portable; honours the `NO_COLOR` convention.
  - Cons: none material.
- **Option B: Third-party `colorama` for Windows ANSI support**
  - Cons: violates stdlib-only constraint.
- **Chosen**: Option A — emit ANSI only when `sys.stdout.isatty()` is `True` and `os.environ.get("NO_COLOR")` is not set. On Windows 10+ the virtual terminal is enabled by default; no `colorama` shim is needed.

### Decision 5: Stage derivation — where to place the logic

- **Option A: Inline in `__main__.py`**
  - Cons: harder to unit-test in isolation.
- **Option B: Separate `stage.py` submodule**
  - Pros: can be imported and tested without running the CLI entry point; clean separation.
- **Chosen**: Option B — `pipeline_status/stage.py` exports a single `derive_stage(artefact_results) -> str` function, making it trivially unit-testable.

### Decision 6: `tasks.json` shape

Assumption from requirements: the file is either a JSON array of task objects, or a JSON object with a top-level `tasks` key whose value is an array. The inspector normalises both to a list before counting.

- **Chosen**: Accept both shapes. If the top-level value is a list, use it directly. If it is a dict and contains a `tasks` key whose value is a list, use that list. Any other shape is treated as "malformed for task counting" but not as a JSON parse error; the filled-detection rule (non-empty object/array) still applies.

### Decision 7: `worktrees.json` filled shape (Open Question 3)

The requirements do not specify a canonical shape. Per the assumptions section and FR-8, the filled check for JSON files is simply "not an empty `{}` or `[]`". The inspector does not need to understand the internal structure of `worktrees.json` beyond this emptiness check; no task counting is required for this file.

- **Chosen**: Treat `worktrees.json` as filled if it parses as valid JSON and the parsed value is neither `{}` nor `[]`. No structural validation beyond this.

## Architecture

### Component Diagram (text/ASCII)

```
repo root/
├── pipeline_status/
│   ├── __init__.py          # package marker, exports public API
│   ├── __main__.py          # CLI entry point (argparse, orchestrates output)
│   ├── inspectors.py        # one inspector function per artefact
│   ├── stage.py             # derive_stage() pure function
│   └── formatting.py        # colour helpers, table/line formatters
├── tests/
│   ├── __init__.py
│   ├── test_inspectors.py   # >= 5 tests, one per artefact inspector
│   ├── test_stage.py        # >= 1 test for stage derivation
│   └── test_formatting.py   # optional: formatting helpers
└── pyproject.toml           # packaging metadata + console script entry point

.claude/state/               # READ-ONLY at runtime
├── feature-request.md
├── requirements.md
├── adr.md
├── tasks.json
└── worktrees.json
```

The `pipeline_status` package has no runtime imports outside the Python standard library. The `tests/` directory uses `unittest` and `tempfile` only.

### Data Model

No new persistent data is introduced. The tool reads the following existing artefacts:

```
.claude/state/
  feature-request.md   — Markdown; pipeline entry point
  requirements.md      — Markdown; PO output
  adr.md               — Markdown; Architect output
  tasks.json           — JSON array or {tasks: [...]}; PM output
  worktrees.json       — JSON (any shape); Orchestrator output
```

Internal data structures (not persisted):

```python
# Result of inspecting one artefact
@dataclass
class ArtefactResult:
    name: str                    # e.g. "feature-request.md"
    path: Path
    exists: bool
    filled: bool
    mtime_iso: str | None        # ISO-8601 with TZ offset, or None if missing
    extra: dict                  # artefact-specific extra fields (e.g. task counts)
    error: str | None            # parse error message, or None

# For tasks.json only, extra contains:
{
    "total_tasks": int,
    "completed_tasks": int,
    "parse_error": str | None
}
```

### API Contracts

This is a CLI tool with no network API. The public contract is the command-line interface:

```
Usage: pipeline-status [OPTIONS]

  Inspect the .claude/state/ pipeline artefacts and report current stage.

Options:
  --help    Show this message and exit.

Exit codes:
  0   Successful inspection (regardless of pipeline stage).
  2   .claude/state/ directory is absent or not a directory.

Environment variables:
  NO_COLOR  When set (any value), suppress ANSI colour output.
```

**stdout format (human-readable, stable across runs given identical FS state):**

```
Pipeline Status
===============

  feature-request.md   PRESENT  FILLED    2026-05-20T14:32:01+02:00
  requirements.md      PRESENT  FILLED    2026-05-21T09:14:55+02:00
  adr.md               PRESENT  EMPTY     2026-05-23T11:00:00+02:00
  tasks.json           MISSING  —         —
  worktrees.json       MISSING  —         —

  tasks.json: tasks 0/0

Stage: Awaiting Gate 2: ADR / Architect review
```

Column widths are fixed to the longest artefact name + padding so output is stable.

**stderr format (error only):**

```
pipeline-status: error: .claude/state/ not found or not a directory
```

### Sequence Diagram (text)

```
User / CI
   |
   | $ pipeline-status
   |
   v
__main__.py: parse_args()
   |
   | check .claude/state/ exists
   |-- NO --> stderr error + exit(2)
   |
   v
inspectors.py: inspect_all(state_dir) -> list[ArtefactResult]
   |
   |-- inspect_markdown(path)  x3  (feature-request, requirements, adr)
   |-- inspect_tasks_json(path)
   |-- inspect_worktrees_json(path)
   |
   v
stage.py: derive_stage(results) -> str
   |
   v
formatting.py: render(results, stage, use_colour) -> str
   |
   v
print to stdout + exit(0)
```

## Implementation Notes

### Files touched / created

- `pipeline_status/__init__.py` — empty or minimal; exposes `__version__ = "0.1.0"`.
- `pipeline_status/__main__.py` — entry point; calls `inspect_all`, `derive_stage`, `render`; handles exit codes.
- `pipeline_status/inspectors.py` — five inspector functions:
  - `inspect_feature_request(path: Path) -> ArtefactResult`
  - `inspect_requirements(path: Path) -> ArtefactResult`
  - `inspect_adr(path: Path) -> ArtefactResult`
  - `inspect_tasks_json(path: Path) -> ArtefactResult`
  - `inspect_worktrees_json(path: Path) -> ArtefactResult`
  - `inspect_all(state_dir: Path) -> list[ArtefactResult]` — calls all five in order.
- `pipeline_status/stage.py` — `derive_stage(results: list[ArtefactResult]) -> str`.
- `pipeline_status/formatting.py` — `use_colour() -> bool`; `render(results, stage, colour) -> str`.
- `tests/test_inspectors.py` — at least 5 test cases using `tempfile.TemporaryDirectory`.
- `tests/test_stage.py` — at least 1 test case per stage derivation rule (7 cases for full coverage).
- `pyproject.toml` — minimal, declares `[project]`, `requires-python = ">=3.10"`, `[project.scripts] pipeline-status = "pipeline_status.__main__:main"`.

### Filled-detection logic (markdown)

A markdown file is considered NOT filled if, after stripping whitespace:

1. The content is empty.
2. The only non-whitespace lines are one or more `#`-prefixed heading lines, with no substantive body text.

"Substantive body text" means at least one line that:
- Is not blank,
- Does not start with `#`,
- Does not consist solely of `<...>` placeholder tokens (regex `^\s*<[^>]+>\s*$`).

Implementation: read up to `MAX_READ_BYTES`, decode as UTF-8, split lines, filter out blank lines and comment lines (`<!-- ... -->`), count non-heading non-placeholder lines. If count == 0, not filled.

### Filled-detection logic (JSON)

1. Parse with `json.loads` (after capping read at `MAX_READ_BYTES`).
2. If parse raises `json.JSONDecodeError`, record the error and treat as NOT filled.
3. If parsed value is `{}` or `[]`, treat as NOT filled.
4. Otherwise, filled.

### Task completion detection

For each element in the normalised task list:
- If element is a dict:
  - Check `element.get("status", "")` — if `.lower()` in `{"done", "completed"}` -> completed.
  - Check `element.get("completed")` — if truthy `bool` or `True` -> completed.
  - Check `element.get("done")` — if truthy `bool` or `True` -> completed.
- Non-dict elements are counted as tasks but not completed.

### mtime formatting

Use `datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.now().astimezone().tzinfo)` to get local time with offset, then `.isoformat(timespec="seconds")`. This is deterministic given fixed filesystem state and timezone.

### Known edge cases

1. **`tasks.json` is valid JSON but not an array or object with `tasks` key**: report `total_tasks=0, completed_tasks=0` and note the unexpected shape inline; do not crash.
2. **File modified between existence check and read**: catch `FileNotFoundError` in inspector; report as missing.
3. **UTF-8 decode error**: catch `UnicodeDecodeError`; treat as NOT filled, report error inline.
4. **Symlinks in `.claude/state/`**: `Path.stat()` follows symlinks; this is acceptable.
5. **Windows line endings**: `str.splitlines()` handles both `\r\n` and `\n`.
6. **`NO_COLOR` set to empty string**: treat any presence of the key (even empty string) as "suppress colour", per the `NO_COLOR` spec.

### Running tests

```bash
python -m unittest discover -s tests
```

No test runner installation required.

## Consequences

**Easier after this change:**
- Developers can instantly recover pipeline context after a session restart.
- CI jobs can gate on `pipeline-status` exit code to detect missing state directory.
- The stage derivation logic is unit-tested and regression-protected.
- Adding new artefacts later requires adding one inspector function and one test.

**Harder or more complex:**
- The `pyproject.toml` introduces Python packaging conventions to a repo that is otherwise not Python-centric; maintainers unfamiliar with Python packaging may find the `[project.scripts]` entry point mechanism non-obvious.
- The markdown filled-detection heuristic (heading + placeholder detection) is a best-effort approximation; edge cases with unusual markdown structure could yield false positives or negatives. This is acceptable for v1.

**Technical debt introduced:**
- No JSON output mode in v1 (explicitly deferred). CI consumers relying on `pipeline-status` output parsing will need a text-scraping workaround until a `--json` flag is added in a future iteration.
- The `worktrees.json` filled check is shape-agnostic; if a future convention requires structural validation, the inspector will need an update.

## Out of Scope

- JSON / machine-readable output (`--json` flag) — explicitly deferred to a future iteration.
- Auto-discovery of repo root by walking parent directories.
- Watch mode, interactive TUI, or daemon operation.
- Modification, repair, or migration of `.claude/state/` files.
- Authentication, network access, or telemetry.
- Multi-repo or remote state inspection.
- Python versions below 3.10.
- Packaging for PyPI publication (only local `pip install -e .` / `pipx install .` usage is in scope).
- Answering Open Question 4 (`--json` mode) — deferred.
- Answering Open Question 5 (exit code convention) — exit code 2 is adopted per the requirements' SHOULD guidance; no further deliberation required.
