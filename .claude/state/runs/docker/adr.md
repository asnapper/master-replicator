# ADR: Docker image + GHCR publish

**Status**: Proposed
**Date**: 2026-05-23

## Context

`pipeline-status` (v1 one-shot, v2 `--watch`, v3 `archive`/`history`, v4 `diff`/`restore`) is today shipped as a pip-installable package whose only consumption path is `pip install -e .` inside a clone of this repo. The package is stdlib-only at runtime; its single console-script entry point is declared in `pyproject.toml`:

```
[project.scripts]
pipeline-status = "pipeline_status.__main__:main"
```

This v5 iteration wraps that package — with **zero changes to `pipeline_status/*`** and zero changes to `pyproject.toml` — into a Docker image published to `ghcr.io/asnapper/master-replicator`. A sibling pipeline (Feature B) is concurrently producing a Helm chart that consumes this image; the two pipelines coordinate only through the **image contract** documented in `requirements.md` §NFR-16: a multi-arch image at the GHCR path above, tagged `:latest`, `:sha-<short>`, and `:vX.Y.Z`.

The deliverables are three production artefacts (`Dockerfile`, `.dockerignore`, `.github/workflows/docker-publish.yml`) plus one new test file (`tests/test_dockerfile.py`) and a new self-contained section in `README.md`. None of the Python source tree is touched.

This ADR is organised so the PM can decompose Feature A into exactly **3 parallel-fan-out engineer tasks** (A1, A2, A3). Each task owns one primary file and at most one new test file; **no two tasks edit the same file**. The pattern matches v4's parallel-fan-out ADR (which used 5 tasks); we use 3 here because there is no Python-package code change to spread across modules.

## Decision Drivers

- **D-1. Parallel-fan-out within Feature A.** The PM dispatches A1/A2/A3 to three Engineer subagents using `Agent(isolation: "worktree")`. Each owns ONE primary file. The ADR pins exact paths and exact line-shaped contents (e.g. `FROM python:3.13-slim-bookworm AS builder`, `USER 65532:65532`) so engineers do not re-derive them and do not need to read each other's worktrees.
- **D-2. Cross-feature coordination with Feature B (Helm).** Both Feature A and Feature B add a new README section. Task A3's section is **self-contained** (lives between two unique HTML-comment anchor markers — see Decision 8) so the merge with Feature B's Helm README section is mechanical: keep both sections, no resolution needed.
- **D-3. Reuse v1–v4 contracts.** The Dockerfile relies on the existing `[project.scripts]` declaration. The `ENTRYPOINT` is `["pipeline-status"]` (the console script); the v1–v4 CLI surface (`archive`, `history`, `diff`, `restore`, `--watch`, no-subcommand) is reachable through it for free.
- **D-4. Zero source changes.** No edits to `pipeline_status/`, no edits to existing `tests/test_*.py`, no edits to `pyproject.toml`. Only the NEW `tests/test_dockerfile.py` is created on the test side.
- **D-5. Stdlib-only, no-`docker`-in-tests parser.** `tests/test_dockerfile.py` reads `Dockerfile`, `.dockerignore`, and `.github/workflows/docker-publish.yml` as **text**; it asserts via `re` and line scans only. No `subprocess`, no `docker` CLI, no third-party YAML parser. This keeps the test suite < 1 second (NFR-14) and portable to any machine.
- **D-6. Reproducible CI.** Every `uses:` in the workflow is pinned to a major version (`@v3`, `@v5`). No `@latest` anywhere. Multi-arch uses `docker/setup-qemu-action@v3` + `docker/setup-buildx-action@v3` + `docker/build-push-action@v5`.
- **D-7. PR-build-only path.** PR events trigger a build with `push: false`; `master` pushes and `v*` tag pushes build **and** push to GHCR. The login step is gated by `github.event_name != 'pull_request'`.
- **D-8. Adopt the PO's proposed defaults for every Open Question (Q1–Q10).** The PO's `requirements.md` lists ten Open Questions with proposed defaults; this ADR explicitly accepts every default and pins the exact strings engineers will write. The PO is the source of truth for "should we"; this ADR is the source of truth for "what literal characters go in the file".

## Considered Options

### Decision 1: Base image tag (Open Question Q1)

The base image of both stages must match `^python:3\.(12|13)-slim(-[a-z0-9]+)?$` (FR-2). Three sub-choices: Python version, Debian suffix, digest-pinning.

- **Option A**: `python:3.13-slim` — pure `:slim` (Debian default tracks upstream).
- **Option B**: `python:3.12-slim-bookworm` — older Python, explicit Debian release.
- **Option C (chosen)**: **`python:3.13-slim-bookworm`** for both `builder` and `runtime` stages.

**Rationale**: Python 3.13 is GA (Oct 2024); `pyproject.toml` declares `requires-python = ">=3.10"`. The `-bookworm` suffix pins the Debian release so transitive `apt-get` upgrades inside the base stay predictable. Digest-pinning is rejected for v5 (NFR-9 defers it); upgrading to `-trixie` is a future-PR decision. If 3.13 ever exhibits a cold-start issue in CI, the documented fallback is `python:3.12-slim-bookworm`, and only the `FROM` lines change.

**Pinned literal**: every `FROM` directive uses exactly `python:3.13-slim-bookworm`.

### Decision 2: Multi-stage build strategy

FR-1 requires at least two `FROM` stages; FR-3 forbids `gcc`/`g++`/`make`/`git`/`build-essential`/`cargo` in the final stage; FR-10 requires the builder to `pip install` the local context. Two implementation patterns are common.

- **Option A**: `pip install --prefix /install .` in the builder, then `COPY --from=builder /install /usr/local` in the runtime stage. Copies the installed site-packages + the console-script shim into the final image. Slim, no wheel intermediate.
- **Option B**: `pip wheel --wheel-dir /wheels .` in the builder, then `pip install --no-cache-dir --no-index --find-links=/wheels pipeline-status` in the runtime. Requires `pip` in the runtime; produces a wheel.
- **Option C**: Single-stage `pip install .` (rejected — FR-1 mandates multi-stage).

**Chosen**: **Option A (`pip install --prefix /install .` then `COPY --from=builder /install /usr/local`).** Reasons:

1. The runtime stage does NOT need `pip`. The console-script shim (`/usr/local/bin/pipeline-status`) and the `pipeline_status` package both land via the `COPY --from=builder` line. Slimmer image.
2. `pipeline-status` is **stdlib-only at runtime**; there are no transitive wheels to compile. The builder stage's `pip install` is essentially a metadata-driven copy of `pipeline_status/` into `/install/lib/python3.13/site-packages/` plus a generated shim in `/install/bin/pipeline-status`.
3. The pattern matches the de-facto multi-stage idiom for Python apps and is easy to read.

**Pinned literal** (builder stage):

```
RUN pip install --no-cache-dir --prefix=/install .
```

**Pinned literal** (runtime stage):

```
COPY --from=builder /install /usr/local
```

The `/usr/local` target is the conventional Python install prefix inside `python:3.X-slim` images; `/usr/local/bin/pipeline-status` ends up on the default `PATH`.

### Decision 3: Non-root user (Open Question Q2)

FR-7 forbids `root`/`0`/`0:0` as the final `USER`. The PO proposes the named-`pipeline:pipeline`-at-UID-`65532:65532` form for the "best of both" property: readable in `docker inspect`, numeric-pinned to the widely-used `nonroot` UID convention so Helm `securityContext.runAsUser: 65532` works downstream.

- **Option A**: `USER 65532:65532` (numeric only). Smallest Dockerfile; `docker inspect` shows the bare UID/GID.
- **Option B**: `USER nonroot` (no explicit group). The `nonroot` username convention from distroless. Requires `useradd`.
- **Option C (chosen)**: **Create a named user/group `pipeline:pipeline` with UID/GID `65532:65532`, then `USER pipeline:pipeline`.** Both forms (named in the Dockerfile, numeric on disk) are present; downstream tooling can use either.

**Rationale**: matches the PO's Q2 default verbatim. The named form is human-readable; the numeric pin guarantees the UID survives chowning across hosts.

**Pinned literals** (runtime stage):

```
RUN groupadd --system --gid 65532 pipeline \
 && useradd --system --uid 65532 --gid 65532 --create-home --home-dir /home/pipeline --shell /usr/sbin/nologin pipeline
```

```
USER pipeline:pipeline
```

The home directory `/home/pipeline` is created by `useradd --create-home` (FR-12). It is owned by `pipeline:pipeline` (the conventional `useradd` behaviour with `--create-home`).

The parser test (FR-7) accepts numeric `<uid>` / `<uid>:<gid>` OR a named form; our literal `pipeline:pipeline` satisfies the "non-numeric username" branch.

### Decision 4: `ENTRYPOINT` / `CMD` shape (Open Question Q5)

FR-5 permits either `ENTRYPOINT ["pipeline-status"]` or `ENTRYPOINT ["python", "-m", "pipeline_status"]`. FR-6 mandates `CMD []` so `docker run … :tag` runs `pipeline-status` with no subcommand (the v1 one-shot path), `docker run … :tag history` runs `pipeline-status history`, etc.

- **Option A (chosen)**: **`ENTRYPOINT ["pipeline-status"]` + `CMD []`.** Uses the console script declared in `pyproject.toml`.
- **Option B**: `ENTRYPOINT ["python", "-m", "pipeline_status"]` + `CMD []`. A fallback if the console script's `bin/` location were not on `PATH` for the non-root user.

**Chosen Option A**. The `pip install --prefix=/install .` step produces `/install/bin/pipeline-status`, which the `COPY --from=builder /install /usr/local` step lands at `/usr/local/bin/pipeline-status`. That path is on the default `PATH` of `python:3.X-slim` for the `pipeline` user (the user inherits the image-wide `PATH` env). Simpler, faster (one fewer `runpy` indirection).

**Pinned literals** (runtime stage):

```
ENTRYPOINT ["pipeline-status"]
CMD []
```

The `CMD []` is **exact**: an empty exec-form JSON array. The parser test asserts `CMD []` literally (FR-38).

### Decision 5: OCI labels & build-args (FR-11 + FR-30)

FR-11 mandates four `LABEL` lines in the final stage: `org.opencontainers.image.source`, `.title`, `.description`, `.licenses`. FR-30 mandates the workflow passes `VERSION`, `REVISION`, `CREATED` build-args; the Dockerfile receives them as `ARG`s and propagates them into additional `LABEL` lines for `.version`, `.revision`, `.created`.

**Pinned literals** (runtime stage, before `USER pipeline:pipeline`):

```
ARG VERSION="0.0.0+local"
ARG REVISION="unknown"
ARG CREATED="1970-01-01T00:00:00Z"

LABEL org.opencontainers.image.source="https://github.com/asnapper/master-replicator"
LABEL org.opencontainers.image.title="pipeline-status"
LABEL org.opencontainers.image.description="Inspect .claude/state/ pipeline artefacts and report current stage."
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.revision="${REVISION}"
LABEL org.opencontainers.image.created="${CREATED}"
```

**Note on `.licenses`**: the repo has no `LICENSE` file today; `MIT` is the documented placeholder pending an explicit licence decision (out of scope for v5). The PO requirements do not specify a licence value, only the presence of the label; `MIT` is a benign default and matches the OCI `licenses` spec (SPDX expression).

**Note on `ARG` defaults**: harmless local-build defaults (`0.0.0+local`, `unknown`, `1970-01-01T00:00:00Z`) ensure `docker build .` works without `--build-arg`. The GHA workflow always supplies real values (Decision 7).

The parser test (FR-38 bullet 11) asserts the presence of the four mandatory `LABEL` lines (`.source`, `.title`, `.description`, `.licenses`) and MAY assert the presence of `ARG VERSION`/`ARG REVISION`/`ARG CREATED` (it will).

### Decision 6: Multi-arch via buildx + QEMU (NFR-3/NFR-4)

The workflow MUST publish a manifest list with both `linux/amd64` and `linux/arm64` variants. The standard idiom is `docker/setup-qemu-action@v3` → `docker/setup-buildx-action@v3` → `docker/build-push-action@v5` with `platforms: linux/amd64,linux/arm64`.

- **Option A**: A single job that builds both arches via QEMU emulation. Simpler workflow YAML; longer wall clock (arm64 emulation is ~3-5x slower than native).
- **Option B**: Matrix job per arch, manifest merged in a third job. More complex; faster wall clock with native arm64 runners (which are not generally available on GitHub-hosted free-tier today).

**Chosen Option A** — single job, QEMU emulation. Within NFR-12's 10-minute budget for `pipeline-status` (a stdlib-only pure-Python install).

### Decision 7: Tag matrix via `docker/metadata-action@v5` (Open Question Q7)

FR-29 lists four tag classes:
- `:latest` on push-to-`master`,
- `:sha-<7-char-short>` on push-to-`master`,
- `:<tag>` on `v*` tag pushes,
- `:latest` on `v*` tag pushes.

`docker/metadata-action@v5` generates exactly this matrix from a single config block AND emits the OCI `created` label automatically. The PO Q7 default adopts it; we do too.

**Pinned literal** (`.github/workflows/docker-publish.yml`, in the build job):

```yaml
      - name: Compute image metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/asnapper/master-replicator
          tags: |
            type=raw,value=latest,enable=${{ github.event_name == 'push' && (github.ref == 'refs/heads/master' || startsWith(github.ref, 'refs/tags/v')) }}
            type=sha,prefix=sha-,format=short,enable=${{ github.event_name == 'push' && github.ref == 'refs/heads/master' }}
            type=ref,event=tag,enable=${{ github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v') }}
```

`metadata-action` emits `linux/amd64`-style `org.opencontainers.image.created` and `.revision` labels automatically (via its `labels` output); the workflow passes both `tags: ${{ steps.meta.outputs.tags }}` and `labels: ${{ steps.meta.outputs.labels }}` to `docker/build-push-action@v5`. The Dockerfile's hard-coded `LABEL` lines (Decision 5) and the workflow-level dynamic labels co-exist; `build-push-action` honours the dynamic ones at build time.

### Decision 8: README section anchors for mechanical merge with Feature B (D-2)

Both Feature A and Feature B add a new top-level section to `README.md`. To make the merge mechanical, Task A3 inserts its section between two unique HTML-comment anchor markers. Feature B's sibling architect is expected to follow the same convention with a different anchor name. Both sections then land in `README.md` in independent text spans that 3-way-merge cleanly.

**Pinned anchors** (Task A3):

```
<!-- BEGIN: docker-section (Feature A) -->
## Docker

...content...
<!-- END: docker-section (Feature A) -->
```

Task A3's section MUST be inserted at the **end** of `README.md` (append). Feature B's sibling will likely also append; the order between them is irrelevant because each is self-contained.

### Decision 9: Parser-test strategy (FR-34..FR-41)

The test parses three files as text. The choice between regex and stdlib YAML parsing is moot — there is no stdlib YAML loader. We use `re` and line scans.

- **Option A (chosen)**: One `unittest.TestCase` with one test method per assertion category. Helpers (`_read_dockerfile_lines`, `_split_stages`, `_workflow_text`) keep the test methods small.
- **Option B**: One test method per FR. Too granular; ~40 methods.

**Chosen Option A** — ~12-15 test methods, all in `tests/test_dockerfile.py`. The file uses only `unittest`, `re`, `pathlib`, and `os` from the stdlib. No `subprocess`, no `docker`, no `yaml`, no `pytest`.

**Stage-splitting helper** (used by tests that need "final stage only"):

```python
def _split_stages(lines: list[str]) -> list[list[str]]:
    """Return each stage as a list of lines. A stage starts at `FROM`."""
    stages = []
    cur = None
    for line in lines:
        if re.match(r"^\s*FROM\s+", line):
            if cur is not None:
                stages.append(cur)
            cur = [line]
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        stages.append(cur)
    return stages
```

The "final stage" is `stages[-1]`. Stage 0 is the builder.

### Decision 10: `.dockerignore` scope (Open Question Q3)

FR-18 mandates excluding `.git/`, `.github/`, `.claude/state/archive/`. FR-19 recommends Python noise. Q3 asks whether `.dockerignore` excludes all of `.claude/` or only `.claude/state/archive/`.

**Chosen**: **Exclude all of `.claude/`** (Q3 proposed default). `/repo` is bind-mounted at runtime, so the image never uses its baked-in `.claude/`; excluding the whole directory keeps the build context smaller and avoids accidentally baking in feature-request / requirements / ADR files from the building branch.

**Pinned `.dockerignore` content** (Task A1):

```
# Version control & CI metadata
.git
.gitignore
.github

# Pipeline state (mounted at runtime under /repo; image never uses its own copy)
.claude

# Python build noise
__pycache__
*.pyc
*.pyo
*.pyd
.pytest_cache
.mypy_cache
.ruff_cache
.venv
venv
dist
build
*.egg-info

# Editor / OS noise
.idea
.vscode
.DS_Store
```

The parser test asserts (FR-39): `.git`, `.github`, `.claude/state/archive` (each matched by at least one line — `.claude` covers `.claude/state/archive` as a prefix; `_dockerignore_matches(".claude/state/archive")` returns True when any line is a prefix), `__pycache__`, `*.pyc` present; `pyproject.toml`, `README.md`, `pipeline_status` absent.

## Architecture

### File ownership table (parallel-fan-out contract)

| Task | Owns (production) | Owns (test) | Reads (from master) | Reads (from sibling tasks) |
|---|---|---|---|---|
| **A1** | `Dockerfile`, `.dockerignore` | `tests/test_dockerfile.py` | `pyproject.toml` (path only; tests resolve repo root via walking up) | none |
| **A2** | `.github/workflows/docker-publish.yml` | (assertions for this file live in A1's `test_dockerfile.py`; A2 does NOT own a test file) | none | none |
| **A3** | `README.md` (append a self-contained section) | (none) | none | none |

**Critical**: A1 owns the parser tests for **all three** files (`Dockerfile`, `.dockerignore`, `.github/workflows/docker-publish.yml`), per the PO's FR-34..FR-41 mandate that the test live at `tests/test_dockerfile.py` (singular). This is the **one** test file ownership rule and it places the workflow-asserting test methods inside A1's test file.

To keep A2 fully parallel with A1: A2's worktree contains ONLY the workflow YAML. A1's worktree contains the Dockerfile, the `.dockerignore`, AND the parser test. A1's parser test asserts properties of a workflow file A1 itself does not write — but the **assertions are pinned in this ADR** (every literal substring/regex the test must check is enumerated below in §"Test strategy"). A1's engineer writes the assertions against this ADR's pinned literals; A2's engineer writes the workflow against this ADR's pinned literals. The two converge on the same byte sequence.

**Cross-task imports**: none. No file in any task references any file from another task. The three worktrees can run truly in parallel.

### Dockerfile structure (annotated)

The full Dockerfile content for `Dockerfile` (Task A1) — annotated; the engineer writes this verbatim, comments included, except as noted:

```dockerfile
# syntax=docker/dockerfile:1.7

# ---- builder stage ---------------------------------------------------------
# Compiles/installs the pipeline-status package into /install, isolated from
# the runtime image. Build tools live here only; they never enter the final
# image (FR-3).
FROM python:3.13-slim-bookworm AS builder

WORKDIR /src

# Copy the minimum set required for `pip install .`. NOTE: .dockerignore
# already excludes .git, .github, .claude. Listing the targeted COPYs
# defensively narrows the layer and guarantees FR-9 compliance even if a
# future .dockerignore drift would let bigger paths through.
COPY pyproject.toml ./
COPY README.md ./
COPY pipeline_status ./pipeline_status

# pipeline-status is stdlib-only at runtime; no native compilation needed.
# --prefix=/install installs into a relocatable tree that the runtime stage
# COPYs as a single layer (Decision 2).
RUN pip install --no-cache-dir --prefix=/install .

# ---- runtime stage ---------------------------------------------------------
# Final image: no build tools, no pip cache, non-root user, /repo as the
# mount point for the host's checkout.
FROM python:3.13-slim-bookworm AS runtime

# Build-time metadata, supplied by the GHA workflow (FR-30). Harmless local
# defaults so `docker build .` works without --build-arg.
ARG VERSION="0.0.0+local"
ARG REVISION="unknown"
ARG CREATED="1970-01-01T00:00:00Z"

# OCI labels (FR-11). The four mandatory labels are static; the three
# dynamic labels resolve from the ARGs above.
LABEL org.opencontainers.image.source="https://github.com/asnapper/master-replicator"
LABEL org.opencontainers.image.title="pipeline-status"
LABEL org.opencontainers.image.description="Inspect .claude/state/ pipeline artefacts and report current stage."
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.revision="${REVISION}"
LABEL org.opencontainers.image.created="${CREATED}"

# Non-root user 'pipeline' at UID/GID 65532 (Decision 3, PO Q2). The home
# directory is created so subcommands that write under HOME work; /repo is
# created here too so the bind-mount target exists with the right ownership.
RUN groupadd --system --gid 65532 pipeline \
 && useradd --system --uid 65532 --gid 65532 --create-home --home-dir /home/pipeline --shell /usr/sbin/nologin pipeline \
 && mkdir -p /repo \
 && chown -R pipeline:pipeline /repo /home/pipeline

# Copy the installed package + console script from the builder stage. This
# is the only layer that brings application code into the runtime image.
COPY --from=builder /install /usr/local

# Run as 'pipeline' under /repo, the conventional bind-mount target.
WORKDIR /repo
USER pipeline:pipeline

# Entry point is the console script declared by pyproject.toml's
# [project.scripts] table (Decision 4, PO Q5). CMD is the empty exec-form
# array so `docker run … :tag` runs `pipeline-status` with no subcommand
# (the v1 one-shot path), and `docker run … :tag <args>` appends <args>.
ENTRYPOINT ["pipeline-status"]
CMD []
```

#### Required textual properties (what the parser test asserts)

The Dockerfile MUST contain (as line-shaped text; comments and whitespace allowed between):

| Property | Exact match / regex |
|---|---|
| Two `FROM` directives, the first labelled `AS builder` | `FROM python:3.13-slim-bookworm AS builder` and `FROM python:3.13-slim-bookworm AS runtime` |
| Every `FROM` line matches FR-2 regex | `^FROM\s+python:3\.(12\|13)-slim(-[a-z0-9]+)?(\s+AS\s+\w+)?\s*$` |
| Builder stage contains `pip install` referencing local context | `pip install --no-cache-dir --prefix=/install .` (the trailing `.` is the local-context token; regex `pip install [^#\n]*\s\.\s*$` matches) |
| Runtime stage forbids build tools | None of `gcc`, `g++`, `clang`, `make`, `git`, `build-essential`, `cargo` appears as a `RUN apt-get install` token in the runtime stage (the `groupadd`/`useradd` line MUST NOT install any of these) |
| `WORKDIR /repo` appears exactly once | `^WORKDIR /repo\s*$` |
| `USER pipeline:pipeline` appears exactly once in runtime stage | literal match; passes FR-7 non-root check |
| `ENTRYPOINT ["pipeline-status"]` in runtime stage | literal match (exec-form, double quotes) |
| `CMD []` in runtime stage | literal match (empty exec-form array) |
| Four mandatory `LABEL` lines in runtime stage | each of `LABEL org.opencontainers.image.{source,title,description,licenses}=` present |
| Three `ARG` lines for `VERSION`, `REVISION`, `CREATED` | each declared at runtime-stage top |
| No `ENV NO_COLOR=`, `ENV FORCE_COLOR=`, `ENV TERM=` in runtime stage | absent (FR-8) |
| No `EXPOSE`, `HEALTHCHECK`, `VOLUME` anywhere | absent (FR-13/14/15) |
| No `COPY`/`ADD` source equals `.git`, `.github`, or `.claude/state/archive` | absent (FR-9) |
| `COPY --from=builder /install /usr/local` | literal match (the runtime-stage handoff from the builder) |

### `.dockerignore` content (annotated)

Owned by Task A1. Full content listed in Decision 10 above. Parser test asserts:

- File exists at repo root.
- Includes patterns for `.git`, `.github`, `.claude/state/archive` (any prefix-matching line counts; `.claude` covers `.claude/state/archive`).
- Includes `__pycache__` and `*.pyc`.
- Does NOT include `pyproject.toml`, `README.md`, or `pipeline_status` as bare-token exclude lines.

### GHA workflow shape (annotated)

The full workflow content for `.github/workflows/docker-publish.yml` (Task A2) — annotated; engineer writes this verbatim:

```yaml
name: Publish Docker image

on:
  push:
    branches: [master]
    tags: ['v*']
  pull_request:
  workflow_dispatch:

permissions:
  contents: read
  packages: write

jobs:
  build-and-push:
    name: Build and (conditionally) publish image
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Set up QEMU (multi-arch)
        uses: docker/setup-qemu-action@v3

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        if: ${{ github.event_name != 'pull_request' }}
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Compute image metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/asnapper/master-replicator
          tags: |
            type=raw,value=latest,enable=${{ github.event_name == 'push' && (github.ref == 'refs/heads/master' || startsWith(github.ref, 'refs/tags/v')) }}
            type=sha,prefix=sha-,format=short,enable=${{ github.event_name == 'push' && github.ref == 'refs/heads/master' }}
            type=ref,event=tag,enable=${{ github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v') }}

      - name: Build (and conditionally push) image
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./Dockerfile
          platforms: linux/amd64,linux/arm64
          push: ${{ github.event_name != 'pull_request' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          build-args: |
            VERSION=${{ steps.meta.outputs.version }}
            REVISION=${{ github.sha }}
            CREATED=${{ steps.meta.outputs.json && fromJSON(steps.meta.outputs.json).labels['org.opencontainers.image.created'] || '' }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

#### Required textual properties (asserted in A1's parser test)

The workflow MUST contain (as substring / regex matches; YAML structure is not parsed):

| Property | Regex / substring |
|---|---|
| `on:` block with `push:` → `branches: [master]` | `branches:\s*\[\s*master\s*\]` |
| `on:` block with `push:` → `tags: ['v*']` | `tags:\s*\[\s*'v\*'\s*\]` |
| `on:` block with `pull_request:` | `\bpull_request:` |
| Permissions: `contents: read` and `packages: write` | both substrings present |
| `docker/login-action@v3` | literal substring |
| Login step gated by `github.event_name != 'pull_request'` | regex `if:\s*\$\{\{\s*github\.event_name\s*!=\s*'pull_request'\s*\}\}` somewhere within ~5 lines of `docker/login-action@v3` (or anywhere — A1's test uses the looser anywhere-in-file form) |
| `docker/setup-qemu-action@v3` | literal substring |
| `docker/setup-buildx-action@v3` | literal substring |
| `docker/build-push-action@v5` | literal substring |
| `docker/metadata-action@v5` | literal substring |
| `ghcr.io/asnapper/master-replicator` | literal substring |
| `platforms: linux/amd64,linux/arm64` | literal substring (may be on a single line; regex tolerates whitespace) |
| `push:` directive references `github.event_name != 'pull_request'` | regex `push:\s*\$\{\{\s*github\.event_name\s*!=\s*'pull_request'\s*\}\}` |
| `actions/checkout@v4` | literal substring |
| No `@latest` anywhere | regex `@latest` must NOT match |
| `runs-on: ubuntu-latest` | literal substring (FR-32) |

### Test strategy (parser-based, no real `docker build`)

Owned by Task A1. File: `tests/test_dockerfile.py`. Stdlib-only (`unittest`, `re`, `pathlib`, `os`). No `yaml`, no `subprocess`, no `docker`.

**Repo-root discovery** (FR-41): walk up from `Path(__file__).resolve()` until a directory containing `pyproject.toml` is found. Cache the result in a module-level constant after first use.

**Test class layout** (~12-15 methods):

```python
class DockerfileTests(unittest.TestCase):
    def test_dockerfile_exists(self): ...
    def test_two_stages_with_builder_alias(self): ...
    def test_every_from_matches_base_image_regex(self): ...
    def test_no_build_tools_in_runtime_stage(self): ...
    def test_workdir_repo_present_once(self): ...
    def test_user_non_root_in_runtime_stage(self): ...
    def test_entrypoint_exec_form_in_runtime_stage(self): ...
    def test_cmd_empty_array_in_runtime_stage(self): ...
    def test_oci_labels_in_runtime_stage(self): ...
    def test_arg_declarations_in_runtime_stage(self): ...
    def test_no_color_env_absent_in_runtime_stage(self): ...
    def test_no_expose_healthcheck_volume(self): ...
    def test_no_copy_of_excluded_paths(self): ...
    def test_pip_install_local_context_in_builder_stage(self): ...

class DockerignoreTests(unittest.TestCase):
    def test_dockerignore_exists(self): ...
    def test_excludes_git_github_and_claude_archive(self): ...
    def test_excludes_python_noise(self): ...
    def test_does_not_exclude_required_source(self): ...

class WorkflowTests(unittest.TestCase):
    def test_workflow_exists_at_expected_path(self): ...
    def test_on_triggers_present(self): ...
    def test_permissions_block(self): ...
    def test_login_action_pinned(self): ...
    def test_buildx_and_qemu_actions_pinned(self): ...
    def test_build_push_action_pinned(self): ...
    def test_metadata_action_pinned(self): ...
    def test_image_path_present(self): ...
    def test_platforms_multi_arch(self): ...
    def test_push_gated_by_event_name(self): ...
    def test_no_at_latest_anywhere(self): ...
    def test_runs_on_ubuntu(self): ...
```

**Total assertion count**: ~30 individual `self.assert*` calls across the methods.

**Stage-splitting helper**: as in Decision 9.

**`.dockerignore`-matching helper**: strip comments (`# …`) and blank lines; the remaining lines are exclude patterns. To check "does `<path>` match any pattern", check whether `<path>` equals a pattern OR starts with `<pattern>/`. Belt-and-braces simple.

## Implementation Notes

### Per-task notes for engineers

#### Task A1 — `Dockerfile`, `.dockerignore`, `tests/test_dockerfile.py`

- **Write the Dockerfile verbatim from §"Dockerfile structure (annotated)" above.** The comment lines are part of the file; they help future maintainers without affecting the parser test (which ignores `#` lines except where explicitly asserted to be absent).
- **Write the `.dockerignore` verbatim from Decision 10.**
- **Write `tests/test_dockerfile.py` against the assertion table in §"Required textual properties (what the parser test asserts)" and §"Required textual properties (asserted in A1's parser test)" above.** Use the stage-splitter helper for "final stage only" assertions. Use module-level constants for the file paths once `_repo_root()` resolves them.
- **The parser test MUST run in < 1 second** (NFR-14). All assertions are pure-text; achievable trivially.
- **Run `python -m unittest discover -s tests`** locally before opening the PR. The new test must pass; no existing test must regress (no existing test touches the Dockerfile / `.dockerignore` / workflow paths, so regression is structurally impossible — but verify).
- **Do NOT add any third-party imports** to the test (NFR FR-35). `yaml` is forbidden.
- **Do NOT modify any existing test file.** The new file is `tests/test_dockerfile.py` (singular, exactly that path) per FR-41 and the PO Q9 default.
- **Do NOT touch `pipeline_status/*` or `pyproject.toml`.**

#### Task A2 — `.github/workflows/docker-publish.yml`

- **Write the workflow YAML verbatim from §"GHA workflow shape (annotated)" above.** Mind YAML indentation (2 spaces, no tabs).
- **No additional files.** A2 owns only this one file.
- **Do NOT add a separate test file**; A1's `tests/test_dockerfile.py` already asserts every textual property of this workflow. A2's PR will pass A1's test as soon as both PRs are present in the merge tree.
- **Do NOT touch `Dockerfile`, `.dockerignore`, `README.md`, or `pipeline_status/*`.**
- **Verify locally** (optional) with `gh workflow view` or YAML-lint; a syntactic mistake will surface when GHA picks up the file. Functional correctness (the build itself working) is verified by the first push to a branch with this file; that verification is out of A2's responsibility — A2 ships the YAML; CI runs it.

#### Task A3 — `README.md`

- **Append a new section to the END of `README.md`** (after every existing section, including any blank trailing lines). Use the anchor markers from Decision 8:

  ```markdown
  <!-- BEGIN: docker-section (Feature A) -->
  ## Docker

  `pipeline-status` is available as a Docker image at `ghcr.io/asnapper/master-replicator`.

  ### Quick start

  Run against the current directory's `.claude/state/`:

  ```sh
  docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest
  ```

  The image's working directory is `/repo`; mount your repository (the
  directory containing `.claude/state/`) there. The default entry point is
  `pipeline-status`, and the default `CMD` is empty, so the invocation above
  runs the one-shot status report.

  ### Subcommands

  Pass any `pipeline-status` subcommand after the image name:

  ```sh
  # History of past archives
  docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest history

  # Snapshot the live state into .claude/state/archive/<name>/
  docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest archive --name my-snapshot

  # Diff two archived states
  docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest diff name-a name-b

  # Continuous watch mode (Ctrl+C to stop)
  docker run --rm -it -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest --watch
  ```

  ### Tags

  - `:latest` — the most recent build from `master` or a `v*` release tag.
  - `:sha-<short>` — immutable per-`master`-commit tag (7-char short SHA).
  - `:vX.Y.Z` — immutable per-release tag, published on `v*` git tags.

  Pin `:sha-<short>` or `:vX.Y.Z` for reproducible deployments.

  ### Colour output

  The image does not set `NO_COLOR`, `FORCE_COLOR`, or `TERM`. To disable ANSI
  escapes (e.g. in CI logs), set `NO_COLOR` in the invocation:

  ```sh
  docker run --rm -e NO_COLOR=1 -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest
  ```

  ### Non-root user and file ownership

  The container runs as the non-root user `pipeline` (UID/GID `65532:65532`).
  Subcommands that write under `/repo` (e.g. `archive`, `restore`) create
  files owned by UID 65532 on the host. If you need the files to be owned by
  your host user, override the runtime UID:

  ```sh
  docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest archive
  ```

  The image also has no `EXPOSE`, no `HEALTHCHECK`, and no declared `VOLUME`
  — it is a one-shot CLI, not a long-running service.

  ### Multi-architecture

  Images are published as a multi-arch manifest covering `linux/amd64` and
  `linux/arm64`. Docker selects the correct variant automatically based on
  the host architecture.

  ### Building locally

  To build the image from a checkout:

  ```sh
  docker build -t pipeline-status:dev .
  docker run --rm -v "$PWD":/repo pipeline-status:dev
  ```

  The Dockerfile is multi-stage; the final image contains only the Python
  runtime and the installed `pipeline-status` package — no build tools, no
  pip cache, no repository metadata.
  <!-- END: docker-section (Feature A) -->
  ```

- **DO NOT modify any existing section of `README.md`.** Only append.
- **DO NOT touch any other file.** No code, no tests, no workflow, no Dockerfile.
- **Verify the markdown renders** (any markdown previewer; e.g. `glow README.md` or VS Code preview). Code fences must close properly; HTML comments must round-trip through GitHub's renderer (they do — both `<!-- … -->` blocks render as invisible markers).

### Known edge cases

1. **Local build without `--build-arg`**: `docker build .` runs without supplying `VERSION`/`REVISION`/`CREATED`. The `ARG` defaults (`0.0.0+local`, `unknown`, `1970-01-01T00:00:00Z`) take effect. The resulting image has placeholder OCI labels but is otherwise fully functional. The GHA workflow always supplies real values; locally-built images are not published.
2. **Forked-PR `GITHUB_TOKEN` lacks `packages: write`**: irrelevant — the login step is conditional on `github.event_name != 'pull_request'`, so forked PRs never attempt the login. The PR-event build runs with `push: false` and succeeds without registry access. (Requirements §A9.)
3. **`docker run … :latest --help`**: the CLI prints argparse help and exits 0. No `/repo` mount needed for `--help` (FR-42 last bullet). The fact that `WORKDIR /repo` is empty in this case does not matter because argparse never reads `.claude/state/`.
4. **`docker run … :latest` without `-v` mount**: `WORKDIR /repo` is empty inside the container. `pipeline-status` exits 2 with the v1 message `pipeline-status: error: .claude/state/ not found or not a directory`. This is the documented v1 behaviour; PO Open Question Q10 explicitly rejects adding image-side detection (NG7). README's `### Quick start` mentions the bind-mount requirement.
5. **`docker run … archive` without writable `/repo`**: the bind mount is read-only or the host UID does not match `65532`. `pipeline-status archive` falls back to its v3 collision/permission error. README documents the `--user "$(id -u):$(id -g)"` workaround.
6. **`docker run -it … --watch` in a non-TTY** (e.g. `docker run -i … --watch` piped to `tee`): v2's watch mode detects non-TTY via `sys.stdout.isatty()` and suppresses the clear-screen sequence. This is the v2 behaviour; no image-level handling needed.
7. **arm64 build via QEMU is slow**: ~3-5x amd64 wall-clock. Acceptable per NFR-12 (10-minute soft target for both arches combined). If wall-clock becomes an issue, future work can split into a matrix-per-arch job with a third merge job, but v5 ships the single-job form.
8. **`docker/metadata-action@v5` and the empty-tag-list edge case**: if neither `push:` nor `pull_request:` matches (e.g. a manually-dispatched run on a non-`master` branch), the `tags:` filter rules all yield empty. `docker/build-push-action@v5` with `push: false` and empty tags will still build but emit a warning. This is acceptable — `workflow_dispatch` is a developer convenience, not a publish path.
9. **Repository transfer / rename**: the `org.opencontainers.image.source` label, the image path in `metadata-action`'s `images:` field, and any documentation references in `README.md` all reference `asnapper/master-replicator`. Requirements §A1 mandates these update in lockstep in a single PR if the namespace changes. Out of scope for v5.
10. **README anchor collision with Feature B**: if Feature B's sibling architect uses the literal string `<!-- BEGIN: docker-section` (i.e. the same anchor name as A3), 3-way merge will conflict. The convention is that each feature uses a unique anchor name; Feature A uses `docker-section`, Feature B is expected to use `helm-section` or similar. If they collide despite the convention, the merge resolution is trivial: keep both sections.

## Consequences

**Easier after this change:**

- `pipeline-status` is consumable from any host with Docker and no Python toolchain. Bind-mount the repo, run the image, done.
- A CI job (anywhere — GitHub, GitLab, Bitbucket, Buildkite) can call `docker run ghcr.io/asnapper/master-replicator:vX.Y.Z` in a step without environment setup.
- The sibling Helm chart (Feature B) has a concrete image reference to consume — no placeholder needed.
- Releases gain a clean immutable artefact: tag `v1.2.3` in git, the workflow publishes `:v1.2.3` to GHCR, and downstream consumers pin to it.

**Harder or more complex:**

- The repo now has CI infrastructure (the workflow) that wasn't there before. Any future change to the Dockerfile, base-image version, or tagging matrix touches the workflow YAML; engineers must learn the GHA action ecosystem.
- Three new top-level artefacts (`Dockerfile`, `.dockerignore`, `.github/workflows/docker-publish.yml`) widen the surface for reviewers. Mitigated by the parser test catching textual drift.
- README grows by ~80 lines. The new section is anchored with HTML comments so structural edits remain straightforward.

**Technical debt introduced:**

- The `org.opencontainers.image.licenses` label is `MIT` as a documented placeholder; the repo has no `LICENSE` file today. Adding one is a future-PR concern (out of scope for v5).
- The base-image is not digest-pinned (NFR-9 defers this). A future PR can convert `FROM python:3.13-slim-bookworm` to `FROM python:3.13-slim-bookworm@sha256:…` once the org adopts a digest-tracking workflow.
- The CI cache (`cache-from: type=gha`, `cache-to: type=gha,mode=max`) is a soft-state convenience. If GHA's cache backend churns or fills, build times degrade gracefully but visibly. Not a correctness concern.
- Local builds (`docker build .`) get placeholder OCI labels. Acceptable; documented in the Dockerfile comment alongside the `ARG` declarations.
- The `_TRACKED_ARTEFACTS`-style internal-duplication argument from v3/v4 does NOT apply here: this iteration adds no Python code.

## Out of Scope

- Cosign / sigstore image signing. Deferred to v6 (Requirements NG1).
- SBOM generation (Syft / Trivy / `docker/scout` / `provenance: true`). Deferred (NG2).
- Distroless or `scratch` base image. `python:slim` is the chosen base (NG3).
- Docker Hub mirroring. GHCR only (NG4).
- Architectures beyond `linux/amd64` + `linux/arm64`. (NG5).
- A `docker-compose.yml` example file. README example only (NG6).
- Modifying any file under `pipeline_status/` or any existing `tests/test_*.py`. (NG7, D-4.)
- Helm chart packaging or Kubernetes manifests. Owned by sibling Feature B (NG8).
- A registry-write integration test (no test pushes to GHCR or pulls back). (NG9.)
- A `:nightly` or scheduled-rebuild tag. (NG11.)
- Any image-side handling for "user forgot `-v /repo` mount" — the v1 exit-2 behaviour is documented (Q10, NG7).
- Refactoring the existing `pipeline_status` package to reduce module count or consolidate `_TRACKED_ARTEFACTS`. (Inherited from v3/v4 out-of-scope lists.)
- Coordinating the merge order between Feature A and Feature B. The two pipelines develop in isolation; the Orchestrator handles merge sequencing.
