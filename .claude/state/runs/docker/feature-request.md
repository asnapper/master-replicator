# Feature Request

## Feature
Containerise the `pipeline-status` CLI and publish a Docker image to **GitHub Container Registry** (`ghcr.io/asnapper/master-replicator`).

The image MUST:
- Be invokable as `docker run --rm -v /path/to/repo:/repo ghcr.io/asnapper/master-replicator [SUBCOMMAND] [ARGS...]` — the user mounts their repo (which contains `.claude/state/`) at `/repo`, and the image runs `pipeline-status` against it.
- Default working directory inside the container: `/repo`.
- Default `CMD`: empty (so `docker run … :latest` runs `pipeline-status` with no args — the v1 one-shot path).
- Allow override: `docker run … :latest history` runs `pipeline-status history`; `docker run … :latest archive --name foo` runs `archive`; etc.
- Pass `NO_COLOR` env var through (the image should NOT set it; the user's environment governs).
- Run as a non-root user (the `USER` directive in the Dockerfile MUST be a numeric or named non-root user; the v1 ADR says no privileged ops, and writing into `/repo` requires the host volume to be writable by that user — we'll document the UID mapping caveat).
- Be slim: use `python:3.13-slim` (or `python:3.12-slim` if 3.13 has cold-start issues) as the base, install only what's necessary, and not include build toolchains in the final layer (multi-stage build preferred).

The CI/CD workflow MUST:
- Live at `.github/workflows/docker-publish.yml`.
- Trigger on push to `master` (publishes `:latest` + `:sha-<short>`) and on tagged releases of the form `v*` (publishes `:vX.Y.Z` + `:latest`).
- Use `docker/login-action@v3` with `${{ secrets.GITHUB_TOKEN }}` against `ghcr.io`.
- Use `docker/build-push-action@v5` with `platforms: linux/amd64,linux/arm64` (multi-arch via buildx).
- Have `permissions: contents: read, packages: write` declared.
- NOT trigger on pull requests (we don't want to publish images for in-flight PRs).
- Add a `pull_request:` trigger for **build only** (no push) so PRs validate the Dockerfile without polluting GHCR.

## Context
v1–v4 delivered the `pipeline-status` CLI with `archive`, `history`, `diff`, `restore` subcommands plus `--watch`. The tool is currently distributed as a pip-installable package; the only way to use it is to `pip install -e .` in a repo with the source. Containerising it makes the tool consumable from any CI runner or developer workstation without a Python environment setup, and the GHCR publish makes it shareable across the org without rebuilding.

This pairs naturally with the **Helm chart** feature being shipped concurrently — the chart will reference this image.

## Constraints
- **Stdlib-only Python** (inherited from v1 ADR). The Docker image install is just `pip install .` against the existing `pyproject.toml`; no new runtime dependencies are introduced.
- **Multi-stage Dockerfile**: builder stage compiles wheels, final stage copies only the installed package + Python runtime.
- **Image size**: target `<150 MB` compressed for the `linux/amd64` variant.
- **Non-root execution**: a `USER pipeline:pipeline` (or numeric `USER 65532:65532` for `nonroot` distroless-style) directive. The home directory and `/repo` mount point MUST be writable by this user for `archive` and `restore` subcommands to function.
- **Reproducibility**: the GHA workflow pins all action versions (`@v3`, `@v5`, not `@latest`).
- **Image labels**: include OCI standard labels — `org.opencontainers.image.source`, `.title`, `.description`, `.licenses`, `.version`, `.revision`, `.created`.
- **No secrets in image**: nothing in `pyproject.toml`, `README.md`, or any other committed source needs to be redacted, but the Dockerfile MUST NOT `COPY` `.git/`, `.claude/state/archive/`, or `.github/`.
- **Tests**: a Python `unittest`-based test in `tests/test_dockerfile.py` parses the `Dockerfile` and asserts: `FROM python:3.*-slim`, multi-stage (`AS builder`), final `USER` is non-root, `ENTRYPOINT` invokes `pipeline-status`, `CMD` is empty, `WORKDIR /repo`. No real `docker build` is required by tests.

## Out of Scope
- Pre-built images for Windows or macOS (Linux-only).
- Distroless or scratch base — `python:slim` is fine for v5.
- Image signing (`cosign`) — defer to v6 if requested.
- SBOM generation — defer.
- Multi-arch beyond `linux/amd64` + `linux/arm64`.
- A `docker-compose.yml` example.
- Publishing to Docker Hub — GHCR only.
- Modifying the existing `pipeline_status/` package code — the image just wraps it.
