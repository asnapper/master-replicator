# Requirements: Dockerise `pipeline-status` + GHCR publish workflow

**Owner**: PO Agent (Feature A — Docker image + CI workflow)
**Sibling feature** (out of scope here): Helm chart referencing this image. This document MUST NOT pre-suppose Helm packaging details; it only guarantees that the image will be available at `ghcr.io/asnapper/master-replicator` with `:latest` and immutable tags so a downstream Helm chart can pin it.
**Date**: 2026-05-23

---

## Problem Statement

The `pipeline-status` CLI (v1–v4: `archive`, `history`, `diff`, `restore` subcommands plus the v2 `--watch` mode and the default one-shot path) is distributed today as an editable pip install (`pip install -e .`) against the in-tree `pyproject.toml`. Anyone who wants to inspect a `.claude/state/` tree on a CI runner, a developer workstation, or a teammate's machine has to clone the repo, set up a Python 3.10+ environment, and install the package locally. That friction blocks the wider goal of making `pipeline-status` a drop-in tool across the org and a building block for a Helm-deployed cron / job that periodically reports pipeline state.

The deliverable is therefore a Docker image that wraps the existing package without modifying it, plus a GitHub Actions workflow that publishes that image to the **GitHub Container Registry** at `ghcr.io/asnapper/master-replicator`. The image must be invokable as `docker run --rm -v <repo>:/repo ghcr.io/asnapper/master-replicator[:tag] [SUBCOMMAND] [ARGS...]`, run as a non-root user, support both `linux/amd64` and `linux/arm64`, and never bundle build toolchains, secrets, or repo history into the final layer. All Dockerfile-level guarantees must be testable from a stdlib-only `unittest` parser test that reads `Dockerfile` as text — no real `docker build` in the test suite.

---

## Goals

- G1. Ship a multi-stage `Dockerfile` at repo root that produces a runnable `pipeline-status` image from the existing `pyproject.toml` with no source code changes to the `pipeline_status/` package.
- G2. Ship a `.github/workflows/docker-publish.yml` workflow that publishes the image to `ghcr.io/asnapper/master-replicator` on push to `master` and on `v*` tag pushes, builds (but does not push) on pull requests, and builds multi-arch (`linux/amd64,linux/arm64`).
- G3. Ship a `.dockerignore` at repo root that excludes `.git/`, `.claude/state/archive/`, `.github/`, and other build noise (caches, virtualenvs, test artefacts) from the build context.
- G4. Ship a `tests/test_dockerfile.py` `unittest` module that parses `Dockerfile` and `.dockerignore` as text and asserts every Dockerfile-level guarantee in this document — without invoking `docker build`, `docker`, or any network.
- G5. Preserve the existing CLI surface: the v1 no-args one-shot path, v2 `--watch`, and the v3/v4 subcommands (`archive`, `history`, `diff`, `restore`) must all be reachable from `docker run`.
- G6. Publish two tag classes: a mutable `:latest` on every `master` push, and immutable `:sha-<short>` (master pushes) and `:vX.Y.Z` (tag pushes) tags so a Helm chart, a CI job, or a developer can pin a specific build.

---

## Non-Goals

- NG1. **Cosign / sigstore image signing.** Deferred (the feature-request explicitly defers to v6).
- NG2. **SBOM generation** (Syft / Trivy SBOM, `docker/scout`, `provenance: true` attestations). Deferred.
- NG3. **Distroless or `scratch` base image.** `python:slim` is the chosen base for this iteration.
- NG4. **Docker Hub mirroring.** GHCR only.
- NG5. **Architectures beyond `linux/amd64` and `linux/arm64`** (no Windows containers, no macOS, no `s390x`, no `linux/arm/v7`).
- NG6. **A `docker-compose.yml` example file.** README example only; no compose file.
- NG7. **Any change to `pipeline_status/` source code or its `pyproject.toml`.** The image wraps the existing package as-is.
- NG8. **Helm chart packaging, Kubernetes manifests, or `values.yaml`.** Owned by the sibling pipeline.
- NG9. **A registry-write integration test** (no test that pushes to GHCR or pulls back). The unit-test suite is Dockerfile-text parsing only.
- NG10. **Pre-built binaries / standalone executables** (PyInstaller, Nuitka). The image runs CPython.
- NG11. **A `:nightly` / scheduled-rebuild tag.** Only push-to-`master` and tag-push triggers.
- NG12. **An override mechanism for the entrypoint** beyond Docker's standard `--entrypoint` flag (which works for free). We do not invent our own indirection.

---

## User Stories

### US-1 — Run `pipeline-status` on any host without installing Python
**As** a developer or a CI operator with Docker but no local Python toolchain,
**I want** to point a container at my repo and get the live pipeline status,
**So that** I can inspect `.claude/state/` without installing anything else.

Acceptance criteria:
- AC-1.1 `docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest` prints the same one-shot report as `pipeline-status` run natively from `$PWD`, with the same exit code (0 if `.claude/state/` exists, 2 if missing).
- AC-1.2 `docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest history` prints the `history` table (or `No archives found.`), exit 0.
- AC-1.3 `docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest archive --name foo-bar` creates `.claude/state/archive/foo-bar/` on the **host** (because `/repo` is a bind mount) and prints the v3 confirmation line, exit 0.
- AC-1.4 `docker run --rm -v "$PWD":/repo ghcr.io/asnapper/master-replicator:latest history foo-bar` renders the per-archive detail view byte-identically to the native CLI.

### US-2 — Honour `NO_COLOR` from the user's environment
**As** a CI operator,
**I want** `NO_COLOR=1 docker run ... ghcr.io/asnapper/master-replicator:latest` to render without ANSI escapes,
**So that** logs in non-TTY CI jobs are readable.

Acceptance criteria:
- AC-2.1 The image does NOT set `NO_COLOR`, `FORCE_COLOR`, `TERM`, or `COLORTERM` itself.
- AC-2.2 When the user runs `docker run -e NO_COLOR=1 ... :latest`, the report contains no `\x1b[` sequences (delegated to the existing `pipeline_status.formatting.use_colour()` logic — no code change needed; the image just lets the env var through).

### US-3 — Reproducible pinning for downstream consumers
**As** the maintainer of the sibling Helm chart (or any CI job depending on this image),
**I want** to reference an immutable tag like `ghcr.io/asnapper/master-replicator:v0.1.0` or `:sha-abc1234`,
**So that** my deployment is not silently changed by a new `:latest` push.

Acceptance criteria:
- AC-3.1 Every push to `master` produces both `:latest` and `:sha-<7-char-short>` tags pointing at the same multi-arch manifest.
- AC-3.2 Every tag push matching `v*` (e.g. `v0.1.0`, `v1.2.3`) produces `:<tag>` (e.g. `:v0.1.0`) **and** updates `:latest`. The tag `:v0.1.0` is immutable thereafter (the workflow does not force-push tags).
- AC-3.3 PR-triggered builds produce no GHCR-side artefacts (no `:pr-N` tags, no `:latest` update).

### US-4 — Non-root execution for least-privilege deployments
**As** a security-conscious platform engineer,
**I want** the container's main process to run as a non-root user,
**So that** a vulnerability in the CLI cannot escalate within the container.

Acceptance criteria:
- AC-4.1 The Dockerfile contains a final `USER` directive whose value is **not** `root`, **not** `0`, and **not** `0:0`.
- AC-4.2 The chosen UID and GID are documented in the README (so users can `chown` the mounted `/repo` accordingly).
- AC-4.3 The home directory and `/repo` are writable by the chosen UID **inside the container** (i.e. file permissions inside the image allow it; host-side `chown` is a documented caveat, not an image bug).

### US-5 — PR builds validate the Dockerfile without polluting GHCR
**As** a reviewer of a PR that changes the Dockerfile,
**I want** CI to build the image to confirm it still builds, **without** publishing,
**So that** in-flight Dockerfile changes are exercised but the registry stays clean.

Acceptance criteria:
- AC-5.1 The workflow's `on:` block declares both `push:` and `pull_request:` triggers.
- AC-5.2 The `pull_request:` job sets `push: false` on `docker/build-push-action`.
- AC-5.3 The `push:` job sets `push: true` on `docker/build-push-action` (only when the event is `push`, gated by a workflow conditional or by being a separate job).

### US-6 — The image stays slim
**As** a CI operator pulling on every job,
**I want** the compressed image to be under 150 MB on `linux/amd64`,
**So that** pulls are fast and storage costs stay reasonable.

Acceptance criteria:
- AC-6.1 The Dockerfile is **multi-stage**: a `builder` stage installs the package (and any build dependency wheel-compilation), and a final `runtime` stage copies only what's needed.
- AC-6.2 The final image does NOT contain `gcc`, `g++`, `make`, `git`, build wheels' caches, or pip's HTTP cache.
- AC-6.3 The final image's base is `python:3.X-slim` (X ∈ {12, 13}; see Open Questions).

### US-7 — The image is auditable via standard OCI labels
**As** a downstream operator or scanner,
**I want** the image to declare its source repo, version, revision, and creation time via OCI labels,
**So that** `docker inspect` and standard scanners can resolve provenance.

Acceptance criteria:
- AC-7.1 The image declares OCI labels: `org.opencontainers.image.source`, `.title`, `.description`, `.licenses`, `.version`, `.revision`, `.created`.
- AC-7.2 `.source` resolves to `https://github.com/asnapper/master-replicator` (or the canonical repo URL — see Open Question Q4).
- AC-7.3 `.version` and `.revision` are filled by the GHA workflow at build time (not baked into the Dockerfile as constants).

---

## Functional Requirements

### Dockerfile (`Dockerfile`)

- FR-1. **MUST** declare at least two `FROM` stages. The first is the builder; the final stage is the runtime. The builder stage **MUST** use an alias (`AS builder`).
- FR-2. The base image of **both** stages **MUST** match the regex `^python:3\.(12|13)-slim(-[a-z0-9]+)?$`. (Bullseye / bookworm tag suffixes are allowed; pure `:slim` is allowed.)
- FR-3. The final stage **MUST NOT** install any of: `gcc`, `g++`, `clang`, `make`, `git`, `build-essential`, `cargo`. (Absent in the final image; allowed in the builder stage where they may be needed for compiling C extensions of transitive deps.)
- FR-4. The final stage **MUST** declare `WORKDIR /repo`.
- FR-5. The final stage **MUST** declare `ENTRYPOINT` such that the entry point invokes the `pipeline-status` console script. Acceptable forms (any one): `ENTRYPOINT ["pipeline-status"]`, `ENTRYPOINT ["python", "-m", "pipeline_status"]`. The Dockerfile parser test **MUST** accept either form. (See Open Question Q1 for the chosen default.)
- FR-6. The final stage **MUST** declare `CMD []` (an empty exec-form array). This makes `docker run … :tag` (no args) invoke `pipeline-status` with no subcommand — the v1 one-shot path — and `docker run … :tag history` invoke `pipeline-status history`.
- FR-7. The final stage **MUST** declare `USER` with a value that is not `root`, not `0`, and not `0:0`. The value **MUST** be one of: a numeric `<uid>` (e.g. `65532`), a numeric `<uid>:<gid>` (e.g. `65532:65532`), or a non-numeric username (e.g. `pipeline`, optionally `pipeline:pipeline`). Recommended default: `pipeline:pipeline`; chosen UID:GID `65532:65532` (the widely-used `nonroot` UID; see Open Question Q2).
- FR-8. The Dockerfile **MUST NOT** contain any `ENV NO_COLOR=…` directive, any `ENV FORCE_COLOR=…` directive, or any `ENV TERM=…` directive in the final stage. (US-2 / AC-2.1.)
- FR-9. The Dockerfile **MUST NOT** contain any `COPY` or `ADD` instruction whose source pattern explicitly resolves to `.git/`, `.github/`, or `.claude/state/archive/`. Build-context exclusion is enforced by `.dockerignore` (FR-19); the Dockerfile itself **MUST NOT** reach into excluded paths even if they were present (e.g. no `COPY .git /tmp/git`).
- FR-10. The builder stage **MUST** install the `pipeline-status` package from the source in the build context (`pip install .` or equivalent: `pip install --prefix /install .`, `pip install --target …`, `pip wheel . && pip install …`). The exact mechanism is left to the implementer; the parser test **MUST** assert that some form of `pip install` referencing the local context appears in the builder stage.
- FR-11. The final stage **MUST** include OCI image labels via `LABEL` directives covering: `org.opencontainers.image.source`, `org.opencontainers.image.title`, `org.opencontainers.image.description`, `org.opencontainers.image.licenses`. The remaining labels (`.version`, `.revision`, `.created`) **SHOULD** be supplied via build-args set by the workflow (FR-29) and propagated into `LABEL` lines with `ARG`-derived values; the parser test **MUST** assert the presence of the four mandatory `LABEL` lines and **MAY** assert the presence of `ARG VERSION`, `ARG REVISION`, `ARG CREATED` declarations.
- FR-12. The Dockerfile **MUST** ensure the home directory of the non-root user exists and is owned by that user inside the image, so `pipeline-status archive` (which writes under `/repo/.claude/state/archive/`) and `pipeline-status restore` can run. The mechanism is implementer's choice (`useradd -m`, `adduser --disabled-password`, `chown -R`, or a manual `mkdir -p /home/pipeline && chown` line).
- FR-13. The Dockerfile **MUST NOT** contain `EXPOSE` for any port. (`pipeline-status` is a CLI, not a network service.)
- FR-14. The Dockerfile **MUST NOT** contain `HEALTHCHECK`. (Not meaningful for a one-shot CLI.)
- FR-15. The Dockerfile **MUST NOT** contain `VOLUME`. Bind-mounting `/repo` is documented in the README, not declared as an anonymous volume.
- FR-16. The Dockerfile **SHOULD** pin its base image by digest **OR** by a `python:3.X-slim-<distro>` form that includes the Debian distro suffix (e.g. `python:3.13-slim-bookworm`). Either form is acceptable for the parser test; pure `python:3.13-slim` is also acceptable (FR-2 regex covers all three). Open Question Q1 chooses the default.

### `.dockerignore`

- FR-17. The repo **MUST** contain a `.dockerignore` file at root.
- FR-18. `.dockerignore` **MUST** include patterns that exclude `.git/`, `.github/`, and `.claude/state/archive/` from the build context. The exact pattern syntax is up to the implementer; the parser test **MUST** assert that each of the three paths is matched by at least one rule.
- FR-19. `.dockerignore` **SHOULD** also exclude common Python noise: `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.venv/`, `venv/`, `dist/`, `build/`, `*.egg-info/`. Parser test **SHOULD** assert at least `__pycache__` and `*.pyc` are excluded.
- FR-20. `.dockerignore` **MAY** include `.claude/state/` in its entirety (i.e. exclude **all** live state too, not just the archive subdir), since `/repo` will be bind-mounted at runtime and the image's own copy of state is never used. Recommended default: exclude **all** of `.claude/`. See Open Question Q3.
- FR-21. `.dockerignore` **MUST NOT** exclude `pyproject.toml`, `README.md`, or `pipeline_status/` — these are required by the builder stage's `pip install .`.

### GitHub Actions workflow (`.github/workflows/docker-publish.yml`)

- FR-22. The workflow file **MUST** live at `.github/workflows/docker-publish.yml` (exact path).
- FR-23. The workflow **MUST** declare `on:` triggers for:
  - `push` to `branches: [master]`,
  - `push` to `tags: ['v*']`,
  - `pull_request:` (no branch filter — covers PRs against any branch).
- FR-24. The workflow **MUST** declare `permissions: contents: read, packages: write` at the workflow or job level.
- FR-25. The workflow **MUST** use `docker/login-action@v3` with `registry: ghcr.io`, `username: ${{ github.actor }}`, `password: ${{ secrets.GITHUB_TOKEN }}`. The login step **MUST** be conditional on `github.event_name != 'pull_request'` (we don't log in for PR-build-only jobs).
- FR-26. The workflow **MUST** use `docker/setup-qemu-action@v3` and `docker/setup-buildx-action@v3` so that multi-arch builds work.
- FR-27. The workflow **MUST** use `docker/build-push-action@v5` with `platforms: linux/amd64,linux/arm64`.
- FR-28. The `docker/build-push-action@v5` step **MUST** set `push: ${{ github.event_name != 'pull_request' }}`. (Equivalently: a separate PR-only job that hard-codes `push: false` and a non-PR job that hard-codes `push: true` — implementer's choice. The parser test asserts that the workflow contains `push: ${{ github.event_name != 'pull_request' }}` OR contains two distinct steps with `push: true` / `push: false` clearly guarded by event filters.)
- FR-29. The workflow **MUST** compute tags via `docker/metadata-action@v5` OR via an inline `tags:` block, producing at minimum:
  - `ghcr.io/asnapper/master-replicator:latest` on push-to-`master`,
  - `ghcr.io/asnapper/master-replicator:sha-<7-char-short>` on push-to-`master`,
  - `ghcr.io/asnapper/master-replicator:<tag>` on `v*` tag pushes (e.g. `:v0.1.0`),
  - `ghcr.io/asnapper/master-replicator:latest` on `v*` tag pushes (latest follows the newest release).
- FR-30. The workflow **MUST** pass `VERSION`, `REVISION`, and `CREATED` as `build-args` to the Dockerfile so OCI labels (FR-11) can be filled at build time. Values:
  - `VERSION`: `${{ github.ref_name }}` for tag events, `${{ github.sha }}` (or its short form) for branch events.
  - `REVISION`: `${{ github.sha }}`.
  - `CREATED`: an RFC 3339 timestamp set by the workflow (e.g. via `metadata-action`'s `org.opencontainers.image.created` label, which it emits automatically).
- FR-31. The workflow **MUST** pin every `uses:` reference to a major-version tag (`@v3`, `@v5`) or a commit SHA. **MUST NOT** use `@latest` or unversioned references. Parser test **MUST** assert no `@latest` appears anywhere in the workflow.
- FR-32. The workflow **MUST** declare `runs-on: ubuntu-latest` (or a specific Ubuntu pinned version — implementer's choice; `ubuntu-latest` is acceptable for now).
- FR-33. The workflow **MUST NOT** print `secrets.GITHUB_TOKEN` or any other secret to logs (default GHA behaviour redacts these; no explicit `echo` of secret values).

### Python parser test (`tests/test_dockerfile.py`)

- FR-34. The test module **MUST** be runnable as `python -m unittest tests.test_dockerfile` (and via the existing test discovery on `python -m unittest discover -s tests`).
- FR-35. The test module **MUST NOT** import third-party packages — stdlib only (`unittest`, `re`, `pathlib`, `os`, `json`, `yaml` is NOT stdlib so it is forbidden — see FR-37 for the workflow assertion strategy).
- FR-36. The test module **MUST NOT** invoke `docker`, `subprocess.run(["docker", ...])`, or any external binary. It parses files as text only.
- FR-37. For asserting the YAML workflow's structure, the test **MUST** either (a) use `re` against the raw text (recommended — sufficient for the assertions listed) or (b) use a stdlib-only YAML loader. **Note**: there is no stdlib YAML loader; (a) is therefore the only practical option. The parser test treats the workflow as line-oriented text and checks for substring / regex patterns rather than parsing the full YAML tree.
- FR-38. Assertions on `Dockerfile` (one test method per category):
  - At least two `FROM` lines and at least one `AS builder` (FR-1).
  - Every `FROM` line matches `^FROM\s+python:3\.(12|13)-slim(-[a-z0-9]+)?(\s+AS\s+\w+)?\s*$` (FR-2).
  - No `gcc`/`g++`/`make`/`build-essential` install in the **final** stage. (Splits the file by `FROM` boundaries and only inspects the final-stage chunk. FR-3.)
  - Exactly one `WORKDIR /repo` directive (FR-4).
  - Exactly one `ENTRYPOINT` directive in the final stage, matching the FR-5 alternatives.
  - Exactly one `CMD []` directive in the final stage (FR-6).
  - Exactly one `USER` directive in the final stage; value passes the FR-7 non-root check.
  - No `ENV NO_COLOR=`, `ENV FORCE_COLOR=`, `ENV TERM=` lines in the final stage (FR-8).
  - No `COPY` or `ADD` source token equals `.git`, `.github`, or `.claude/state/archive` (FR-9).
  - At least one `pip install` line in the builder stage referencing the local context (FR-10): pattern `pip install [^#]*\.($|\s)` or `pip install -e \.` or `pip wheel \.`.
  - Four `LABEL org.opencontainers.image.{source,title,description,licenses}` lines in the final stage (FR-11).
  - No `EXPOSE`, `HEALTHCHECK`, or `VOLUME` directives anywhere (FR-13/14/15).
- FR-39. Assertions on `.dockerignore`:
  - File exists (FR-17).
  - Lines (after stripping comments and blanks) include patterns matching `.git`, `.github`, and `.claude/state/archive` (FR-18).
  - Lines include `__pycache__` and `*.pyc` (FR-19).
  - Lines do NOT include `pyproject.toml`, `README.md`, or `pipeline_status` (FR-21).
- FR-40. Assertions on `.github/workflows/docker-publish.yml`:
  - File exists at the exact path (FR-22).
  - File contains substrings/regex for: `branches:` line listing `master`, `tags:` line including `v*`, `pull_request`, `permissions:` block with `contents: read` and `packages: write`, `docker/login-action@v3`, `docker/setup-qemu-action@v3`, `docker/setup-buildx-action@v3`, `docker/build-push-action@v5`, `ghcr.io/asnapper/master-replicator`, `platforms: linux/amd64,linux/arm64` (or split across two lines — test uses a permissive regex), no `@latest`.
  - The workflow text contains a `push:` directive whose value references `github.event_name != 'pull_request'` OR splits push/build into two clearly-guarded steps/jobs (FR-28).
- FR-41. The test module **MUST** locate `Dockerfile`, `.dockerignore`, and `.github/workflows/docker-publish.yml` relative to the repo root, discovered via walking up from `__file__` until a `pyproject.toml` is found. **MUST NOT** hard-code an absolute path.

### General

- FR-42. The image **MUST** support invocation forms:
  - `docker run --rm -v <repo>:/repo <image>` → `pipeline-status` (no subcommand, one-shot path).
  - `docker run --rm -v <repo>:/repo <image> --watch` → `pipeline-status --watch`.
  - `docker run --rm -v <repo>:/repo <image> archive [--name N]` → `pipeline-status archive [--name N]`.
  - `docker run --rm -v <repo>:/repo <image> history [N]` → `pipeline-status history [N]`.
  - `docker run --rm -v <repo>:/repo <image> diff A B` → `pipeline-status diff A B` (per v4 ADR).
  - `docker run --rm -v <repo>:/repo <image> restore N` → `pipeline-status restore N` (per v4 ADR).
  - `docker run --rm <image> --help` → argparse help (no `/repo` mount needed for help).
- FR-43. The image **MUST** propagate the host process's exit code: a non-zero exit from `pipeline-status` (e.g. exit 2 when `.claude/state/` is absent) is the container's exit code.

---

## Non-Functional Requirements

### Size

- NFR-1. Compressed image size **SHOULD** be < 150 MB on `linux/amd64` (target). **MUST** be < 250 MB (hard ceiling). Measured by GHCR's manifest size for the `linux/amd64` variant.
- NFR-2. The number of layers in the final stage **SHOULD** be < 15 (i.e. `RUN`/`COPY`/`ENV`/`LABEL`/`USER`/etc.). No hard limit, but engineers should consolidate where reasonable.

### Multi-arch

- NFR-3. The published manifest list **MUST** include both `linux/amd64` and `linux/arm64` variants. No fallback or single-arch release path.
- NFR-4. Multi-arch builds **MUST** use Docker buildx (configured by `docker/setup-buildx-action@v3`) with QEMU emulation (configured by `docker/setup-qemu-action@v3`).

### Security

- NFR-5. The container's main process **MUST** run as a non-root user (`USER` directive in final stage; FR-7).
- NFR-6. The image **MUST NOT** bundle the repo's `.git/` directory, `.github/` workflows, or `.claude/state/archive/` historical state. Enforced by `.dockerignore` (FR-18) + Dockerfile non-leak (FR-9).
- NFR-7. The image **MUST NOT** contain any committed secrets. (None exist in the repo today; this is a forward-looking constraint.) The Dockerfile **MUST NOT** use `--build-arg` for secret values; all `--build-arg` usage is for non-secret metadata (`VERSION`, `REVISION`, `CREATED`).
- NFR-8. The workflow **MUST** use `secrets.GITHUB_TOKEN` (the automatic per-job token), not a long-lived PAT. **MUST NOT** define an organisation-level PAT for this workflow.
- NFR-9. The base image (`python:3.X-slim`) **SHOULD** be the most recent point release at the time of merge; the workflow does NOT pin the digest, but the parser test **MAY** assert a base-image freshness check (e.g. require the `-slim-<distro>` form to be present, ruling out very old tags). For this iteration, freshness assertion is **deferred** — the parser test only checks the regex in FR-2.

### Reproducibility / Determinism

- NFR-10. The workflow **MUST** pin all `uses:` references to a major-version tag or commit SHA. No `@latest`. (FR-31.)
- NFR-11. The Dockerfile **SHOULD** minimise non-deterministic operations (e.g. avoid `apt-get update` without paired `apt-get install --no-install-recommends`; avoid implicit network fetches outside the builder stage). Compliance is reviewer-judged, not parser-test asserted.

### Performance

- NFR-12. End-to-end build duration in CI **SHOULD** be < 10 minutes for both arches combined, including QEMU emulation of arm64. Not a strict pass/fail gate; tracked in CI metrics only.
- NFR-13. `docker pull` of `:latest` from a warm cache **SHOULD** complete in < 30 seconds on a typical CI runner (≈100 Mbps egress). Informational; not gated.

### Testability

- NFR-14. The parser test **MUST** run in < 1 second on the standard test runner (no `docker build`, no network). (FR-36.)
- NFR-15. The parser test **MUST** be deterministic — no time, network, or environment-variable dependencies beyond the repo's own checked-in files. (FR-36/41.)

### Cross-feature awareness

- NFR-16. The image's published path (`ghcr.io/asnapper/master-replicator`) and tag scheme (`:latest`, `:sha-<short>`, `:vX.Y.Z`) are the **contract** that downstream consumers (including the sibling Helm chart pipeline) will reference. This document does NOT prescribe any other Helm-specific behaviour, values, or chart structure; it only guarantees the registry coordinates and the tag classes.

---

## Open Questions (with proposed defaults)

| # | Question | Proposed default | Rationale |
|---|---|---|---|
| Q1 | Which Python base — `python:3.13-slim` or `python:3.12-slim`? And which Debian suffix — `:slim`, `:slim-bookworm`, or pinned digest? | **`python:3.13-slim-bookworm`** for both builder and runtime stages. | 3.13 is GA since Oct 2024 and `pyproject.toml` already declares `requires-python = ">=3.10"`. The `-bookworm` suffix locks the Debian release so `apt-get` upgrades within the base stay predictable. If a CI cold-start issue is observed with 3.13, fall back to `python:3.12-slim-bookworm`. |
| Q2 | Which `USER` value — named `pipeline:pipeline` or numeric `65532:65532` (the `nonroot` convention)? | **`pipeline:pipeline` with UID:GID `65532:65532`** — Dockerfile creates the named user/group at UID/GID 65532, and the `USER` directive references the name. | Best of both: the named form is human-readable in `docker inspect`, the numeric pin matches the widely-used "nonroot" convention (distroless, kube `runAsNonRoot`), and downstream Helm-style `securityContext.runAsUser: 65532` works. |
| Q3 | Should `.dockerignore` exclude all of `.claude/` or only `.claude/state/archive/`? | **Exclude all of `.claude/`.** | `/repo` is bind-mounted at runtime, so the image never uses its baked-in copy of state. Excluding the whole directory keeps the build context smaller and avoids accidentally baking in feature-request / requirements / adr files from the building branch. |
| Q4 | Canonical repo URL for the `org.opencontainers.image.source` label — `https://github.com/asnapper/master-replicator`? Or a different org? | **`https://github.com/asnapper/master-replicator`**, matching the GHCR namespace. | The image publishes to `ghcr.io/asnapper/master-replicator`; the source URL should match the same owner namespace for consistency. If the repo is forked / moved, this label updates in the same PR. |
| Q5 | Should the image's `ENTRYPOINT` be `["pipeline-status"]` or `["python", "-m", "pipeline_status"]`? | **`["pipeline-status"]`** (the console script declared in `pyproject.toml`'s `[project.scripts]`). | Simpler, faster (no extra interpreter invocation through `runpy`), and the console script is the documented public interface. The `python -m` form is acceptable as a fallback if the console script's `bin/` install location isn't on `PATH` for the non-root user. |
| Q6 | Should we add `:master` as an additional moving tag alongside `:latest` on master pushes? | **No.** Only `:latest` and `:sha-<short>` on master pushes; only `:vX.Y.Z` and `:latest` on tag pushes. | Two moving tags pointing at the same thing is needless. `:latest` already covers the "always-newest master build" use case; `:sha-<short>` covers immutable pinning. A Helm chart should pin `:sha-<short>` or `:vX.Y.Z`, not `:master`. |
| Q7 | Should `docker/metadata-action@v5` be used for tagging, or an inline `tags:` block? | **Use `docker/metadata-action@v5`.** | It generates correct `:latest`, `:sha-<short>`, and `:vX.Y.Z` tags from `on: push:` / `on: pull_request:` events with one config block, and it also emits the OCI labels (`org.opencontainers.image.created` etc.) for free. Inline `tags:` is the fallback if `metadata-action` adds undesirable complexity. |
| Q8 | Should the workflow also build on `workflow_dispatch:` (manual trigger)? | **Yes**, add `workflow_dispatch:` to the `on:` block. (Manual build path; pushes only if the manual run is on `master`.) | Cheap and useful for ad-hoc rebuilds (e.g. base-image refresh) without forcing a fake commit. Not strictly required by the feature-request, but standard practice. |
| Q9 | Should the parser test live in `tests/test_dockerfile.py` (next to existing tests) or in a new `tests/test_packaging/` subdir? | **`tests/test_dockerfile.py`** at the existing tests root. | Matches the existing layout (`tests/test_archive.py`, `tests/test_history.py`, etc.). One new file, no new directory. |
| Q10 | What is the exact stderr/exit behaviour when `/repo` is not mounted? Should the image error early with a clear message? | **No special-case.** `pipeline-status` already exits 2 with `pipeline-status: error: .claude/state/ not found or not a directory` when `WORKDIR /repo` has no `.claude/state/` — this is the v1 behaviour and is fine. The README documents the bind-mount requirement. | Adding image-side logic to detect "not mounted" would be the only code change to the existing CLI, which is explicitly out of scope (NG7). The v1 error message is clear enough. |

---

## Assumptions

- A1. The repository owner / GHCR namespace is `asnapper`. If the repository is forked / renamed / transferred, the image path in the Dockerfile labels and the workflow tags both update in lockstep.
- A2. The repository has GitHub Actions enabled and the default `GITHUB_TOKEN` has `packages: write` scope (this is the case for any repo where Actions is enabled; the workflow's `permissions:` block makes this explicit).
- A3. The `pipeline-status` console script declared by `pyproject.toml`'s `[project.scripts]` table (`pipeline-status = "pipeline_status.__main__:main"`) is the stable entry point name. If `pyproject.toml` ever renames this script, the Dockerfile and the parser test update in lockstep.
- A4. `pipeline-status` remains stdlib-only at runtime. If a future iteration introduces a third-party runtime dependency, the builder stage continues to handle it via `pip install .`, and the final stage's site-packages will pick it up automatically — no Dockerfile change needed unless the dep requires native libs (in which case the builder stage installs the toolchain, but the final stage stays clean per FR-3).
- A5. The host user mounting `/repo` either matches the container's UID:GID (`65532:65532` per Q2) or accepts that writes from `archive`/`restore` will be owned by UID 65532 on the host. The README documents this caveat with a `--user` override example (`docker run --user "$(id -u):$(id -g)" …`).
- A6. The repository's `.git/`, `.github/`, and `.claude/state/archive/` are large enough to matter for build context size; excluding them measurably reduces context-upload time and image-layer size for any accidental `COPY .` operations. (The Dockerfile is expected to use targeted `COPY pyproject.toml ./` / `COPY pipeline_status ./pipeline_status` rather than `COPY . .`, but `.dockerignore` is belt-and-braces.)
- A7. GHCR's free-tier rate limits and storage allowances are not a concern at the volume of one image per master push (a typical repo activity volume of ≤ ~100 pushes/month over the lifetime of this image).
- A8. `docker/build-push-action@v5`, `docker/login-action@v3`, `docker/setup-qemu-action@v3`, `docker/setup-buildx-action@v3`, and `docker/metadata-action@v5` are the latest stable major versions as of 2026-05-23 and will be available for the foreseeable future. If any of these are deprecated, this document's FRs that reference specific major versions update in lockstep.
- A9. Pull requests are filed from branches within the same repository (not from forks). Forks would lack the `GITHUB_TOKEN`'s `packages: write` scope; the PR-build-only path (FR-28) tolerates this because no login is attempted for PR events (FR-25). Forked PRs therefore still build successfully without push.
- A10. The sibling Helm-chart pipeline references the image only by registry path and tag class (`ghcr.io/asnapper/master-replicator:vX.Y.Z` or `:latest` or `:sha-<short>`). This document makes no further commitment about the image's filesystem layout, environment, or runtime behaviour beyond what the CLI requires.

---

## Notes for the Architect

- The Dockerfile is the only code artefact in this feature; everything else is config (workflow YAML, `.dockerignore`) or a stdlib-only parser test. The Architect ADR should focus on (a) the multi-stage layout and the choice between `pip install --prefix /install` vs `pip install --target` vs `pip wheel` for the builder→runtime handoff, (b) the user-creation sequence and where `WORKDIR /repo` falls relative to `USER`, and (c) the tagging matrix mapping `on:` events to the set of tags `docker/metadata-action@v5` should emit.
- The parser test is intentionally limited to text-level assertions because a real `docker build` in CI would require Docker-in-Docker on the test runner, which is brittle and slow. The contract is: "if the parser test passes, the Dockerfile satisfies every textual constraint in this document"; the CI workflow's actual `docker build` step provides the proof that the Dockerfile is also syntactically and semantically buildable.
- The image is the **prerequisite** for the sibling Helm chart, but this document does not coordinate sequencing with it. The Helm pipeline can develop in parallel against any agreed placeholder image (or against `:latest` once the first master push succeeds); merge order is the Orchestrator's concern, not this document's.
