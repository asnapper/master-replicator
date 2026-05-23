"""Parser tests for Dockerfile, .dockerignore, and the GHA publish workflow.

These tests treat the three artefacts as text and assert structural properties
via regular expressions and line scans. They never invoke `docker`, never use
`subprocess`, and never import any third-party module (per ADR Decision 5/9).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


# --------------------------------------------------------------------------- #
# Repo-root discovery + cached file contents                                  #
# --------------------------------------------------------------------------- #

def _repo_root() -> Path:
    """Walk up from this test file until a directory containing pyproject.toml is found."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("Could not locate repo root (no pyproject.toml found walking up).")


REPO_ROOT = _repo_root()
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
DOCKERIGNORE_PATH = REPO_ROOT / ".dockerignore"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "docker-publish.yml"


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _split_stages(lines: list[str]) -> list[list[str]]:
    """Return each Dockerfile stage as a list of lines. A stage starts at FROM."""
    stages: list[list[str]] = []
    cur: list[str] | None = None
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


def _dockerignore_patterns() -> list[str]:
    """Return non-comment, non-blank lines from .dockerignore, stripped."""
    out: list[str] = []
    for raw in _read_lines(DOCKERIGNORE_PATH):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


# --------------------------------------------------------------------------- #
# Dockerfile                                                                  #
# --------------------------------------------------------------------------- #

class TestDockerfile(unittest.TestCase):
    """Assertions about the production Dockerfile (Task A1)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.lines = _read_lines(DOCKERFILE_PATH) if DOCKERFILE_PATH.exists() else []
        cls.text = "\n".join(cls.lines)
        cls.stages = _split_stages(cls.lines)
        # Runtime stage is the *last* stage; builder is the first.
        cls.builder_lines = cls.stages[0] if cls.stages else []
        cls.runtime_lines = cls.stages[-1] if cls.stages else []
        cls.builder_text = "\n".join(cls.builder_lines)
        cls.runtime_text = "\n".join(cls.runtime_lines)

    def test_dockerfile_exists(self) -> None:
        self.assertTrue(
            DOCKERFILE_PATH.is_file(),
            f"Expected Dockerfile at {DOCKERFILE_PATH}",
        )

    def test_syntax_directive_present(self) -> None:
        self.assertIn(
            "# syntax=docker/dockerfile:1.7",
            self.text,
            "Missing '# syntax=docker/dockerfile:1.7' directive at top of Dockerfile.",
        )

    def test_exactly_two_from_python_lines(self) -> None:
        from_lines = [
            ln for ln in self.lines
            if re.match(r"^FROM\s+python:3\.13-slim-bookworm", ln)
        ]
        self.assertEqual(
            len(from_lines),
            2,
            f"Expected exactly two 'FROM python:3.13-slim-bookworm' lines; got {len(from_lines)}: {from_lines!r}",
        )

    def test_first_from_has_as_builder(self) -> None:
        # The first FROM line must declare the builder stage alias.
        from_lines = [ln for ln in self.lines if re.match(r"^FROM\s+", ln)]
        self.assertGreaterEqual(len(from_lines), 1, "No FROM lines at all.")
        self.assertRegex(
            from_lines[0],
            r"^FROM\s+python:3\.13-slim-bookworm\s+AS\s+builder\s*$",
            "First FROM line must end with 'AS builder' (multi-stage builder alias).",
        )

    def test_builder_uses_workdir_src_and_pip_install(self) -> None:
        self.assertTrue(
            any(re.match(r"^\s*WORKDIR\s+/src\s*$", ln) for ln in self.builder_lines),
            "Builder stage must declare 'WORKDIR /src'.",
        )
        self.assertIn(
            "pip install --no-cache-dir --prefix=/install .",
            self.builder_text,
            "Builder stage must run 'pip install --no-cache-dir --prefix=/install .' against the local context.",
        )

    def test_runtime_copies_install_from_builder(self) -> None:
        self.assertIn(
            "COPY --from=builder /install /usr/local",
            self.runtime_text,
            "Runtime stage must copy /install from the builder stage to /usr/local.",
        )

    def test_runtime_declares_build_args(self) -> None:
        self.assertIn('ARG VERSION="0.1.0"', self.runtime_text)
        self.assertIn('ARG REVISION=""', self.runtime_text)
        self.assertIn('ARG CREATED=""', self.runtime_text)

    def test_runtime_has_seven_oci_image_labels(self) -> None:
        label_lines = [
            ln for ln in self.runtime_lines
            if re.match(r"^\s*LABEL\s+org\.opencontainers\.image\.", ln)
        ]
        self.assertEqual(
            len(label_lines),
            7,
            f"Expected 7 'LABEL org.opencontainers.image.*' lines in runtime stage; got {len(label_lines)}: {label_lines!r}",
        )
        # Each required suffix is present somewhere among the seven.
        required_suffixes = (
            "title",
            "description",
            "source",
            "licenses",
            "version",
            "revision",
            "created",
        )
        joined = "\n".join(label_lines)
        for suffix in required_suffixes:
            self.assertRegex(
                joined,
                rf"LABEL\s+org\.opencontainers\.image\.{suffix}=",
                f"Missing required LABEL org.opencontainers.image.{suffix}=...",
            )

    def test_runtime_label_title_and_source_values(self) -> None:
        self.assertIn(
            'LABEL org.opencontainers.image.title="pipeline-status"',
            self.runtime_text,
            "Runtime LABEL org.opencontainers.image.title must equal 'pipeline-status'.",
        )
        self.assertIn(
            'LABEL org.opencontainers.image.source="https://github.com/asnapper/master-replicator"',
            self.runtime_text,
            "Runtime LABEL org.opencontainers.image.source must equal the repo URL.",
        )

    def test_runtime_creates_non_root_user_with_pinned_uid_gid(self) -> None:
        # Match the useradd line that pins both --uid 65532 and --gid 65532.
        self.assertRegex(
            self.runtime_text,
            r"useradd[^\n]*--uid\s+65532",
            "Runtime must create a user with '--uid 65532'.",
        )
        self.assertRegex(
            self.runtime_text,
            r"useradd[^\n]*--gid\s+65532",
            "Runtime useradd must pin '--gid 65532'.",
        )
        self.assertRegex(
            self.runtime_text,
            r"groupadd[^\n]*--gid\s+65532\s+pipeline",
            "Runtime must groupadd a 'pipeline' group with '--gid 65532'.",
        )

    def test_runtime_user_directive_is_non_root(self) -> None:
        user_lines = [
            ln.strip() for ln in self.runtime_lines
            if re.match(r"^\s*USER\s+", ln)
        ]
        self.assertEqual(
            len(user_lines),
            1,
            f"Expected exactly one USER directive in runtime stage; got {len(user_lines)}: {user_lines!r}",
        )
        self.assertEqual(
            user_lines[0],
            "USER pipeline:pipeline",
            "Runtime stage USER directive must be 'USER pipeline:pipeline'.",
        )
        # Explicit guard against root.
        for ln in self.runtime_lines:
            self.assertFalse(
                re.match(r"^\s*USER\s+(root|0|0:0)\s*$", ln),
                f"Runtime stage must NOT declare USER root / 0 / 0:0 (found: {ln!r}).",
            )

    def test_runtime_workdir_repo(self) -> None:
        self.assertTrue(
            any(re.match(r"^\s*WORKDIR\s+/repo\s*$", ln) for ln in self.runtime_lines),
            "Runtime stage must declare 'WORKDIR /repo'.",
        )

    def test_runtime_entrypoint_exec_form(self) -> None:
        self.assertIn(
            'ENTRYPOINT ["pipeline-status"]',
            self.runtime_text,
            "Runtime stage must use exec-form ENTRYPOINT [\"pipeline-status\"].",
        )
        # Negative: no shell-form ENTRYPOINT line.
        for ln in self.runtime_lines:
            if re.match(r"^\s*ENTRYPOINT\s+", ln) and "[" not in ln:
                self.fail(f"Found shell-form ENTRYPOINT line (must be exec-form JSON): {ln!r}")

    def test_runtime_cmd_empty_exec_form(self) -> None:
        cmd_lines = [
            ln.strip() for ln in self.runtime_lines
            if re.match(r"^\s*CMD\b", ln)
        ]
        self.assertEqual(
            cmd_lines,
            ["CMD []"],
            f"Runtime stage must declare exactly 'CMD []' (empty exec-form); got {cmd_lines!r}",
        )

    def test_no_expose_healthcheck_volume_or_no_color_env(self) -> None:
        forbidden_directives = (
            (r"^\s*EXPOSE\b", "EXPOSE"),
            (r"^\s*HEALTHCHECK\b", "HEALTHCHECK"),
            (r"^\s*VOLUME\b", "VOLUME"),
        )
        for pattern, name in forbidden_directives:
            for ln in self.lines:
                self.assertNotRegex(
                    ln,
                    pattern,
                    f"Dockerfile must NOT contain a {name} directive (found: {ln!r}).",
                )
        # ENV NO_COLOR / FORCE_COLOR / TERM are explicitly forbidden in the runtime stage.
        for forbidden_env in ("NO_COLOR", "FORCE_COLOR", "TERM"):
            for ln in self.runtime_lines:
                self.assertFalse(
                    re.match(rf"^\s*ENV\s+{forbidden_env}\b", ln),
                    f"Runtime stage must NOT set ENV {forbidden_env}=... (found: {ln!r}).",
                )


# --------------------------------------------------------------------------- #
# .dockerignore                                                               #
# --------------------------------------------------------------------------- #

class TestDockerignore(unittest.TestCase):
    """Assertions about .dockerignore (Task A1)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.patterns = _dockerignore_patterns() if DOCKERIGNORE_PATH.exists() else []

    def test_dockerignore_exists(self) -> None:
        self.assertTrue(
            DOCKERIGNORE_PATH.is_file(),
            f"Expected .dockerignore at {DOCKERIGNORE_PATH}",
        )

    def test_excludes_required_paths(self) -> None:
        required = (
            ".git",
            ".github",
            ".claude",
            "__pycache__",
            "tests",
            "Dockerfile",
            ".dockerignore",
            "README.md",
        )
        for path in required:
            self.assertIn(
                path,
                self.patterns,
                f".dockerignore must contain a line for {path!r}; got: {self.patterns!r}",
            )

    def test_does_not_exclude_required_build_context(self) -> None:
        # These MUST be in the build context, so they MUST NOT be ignored.
        for must_keep in ("pipeline_status", "pyproject.toml"):
            self.assertNotIn(
                must_keep,
                self.patterns,
                f".dockerignore must NOT exclude {must_keep!r} (required in build context).",
            )


# --------------------------------------------------------------------------- #
# .github/workflows/docker-publish.yml                                        #
# --------------------------------------------------------------------------- #

# Sibling task A2 owns this file. On an isolated worktree it may not yet
# exist; the assertions below run on master once both PRs land. Skipping
# keeps the worktree-local test run green.
_WORKFLOW_EXISTS = WORKFLOW_PATH.is_file()


@unittest.skipUnless(_WORKFLOW_EXISTS, "workflow not yet on worktree (sibling task A2 owns it)")
class TestWorkflow(unittest.TestCase):
    """Assertions about .github/workflows/docker-publish.yml (Task A2)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW_PATH.read_text(encoding="utf-8") if _WORKFLOW_EXISTS else ""

    def test_workflow_exists(self) -> None:
        self.assertTrue(
            WORKFLOW_PATH.is_file(),
            f"Expected workflow at {WORKFLOW_PATH}",
        )

    def test_image_reference_present(self) -> None:
        self.assertIn(
            "ghcr.io/asnapper/master-replicator",
            self.text,
            "Workflow must reference the GHCR image path 'ghcr.io/asnapper/master-replicator'.",
        )

    def test_permissions_packages_write_and_contents_read(self) -> None:
        self.assertRegex(
            self.text,
            r"\bpermissions:",
            "Workflow must declare a 'permissions:' block.",
        )
        self.assertIn(
            "packages: write",
            self.text,
            "Workflow permissions must include 'packages: write'.",
        )
        self.assertIn(
            "contents: read",
            self.text,
            "Workflow permissions must include 'contents: read'.",
        )

    def test_multi_arch_platforms(self) -> None:
        self.assertIn(
            "linux/amd64,linux/arm64",
            self.text,
            "Workflow must build for 'linux/amd64,linux/arm64'.",
        )

    def test_pinned_docker_actions_present(self) -> None:
        for action in (
            "docker/build-push-action@v5",
            "docker/login-action@v3",
            "docker/metadata-action@v5",
        ):
            self.assertIn(
                action,
                self.text,
                f"Workflow must reference '{action}' (pinned major version).",
            )

    def test_has_pull_request_and_push_triggers(self) -> None:
        self.assertRegex(
            self.text,
            r"\bpull_request:",
            "Workflow must declare a 'pull_request:' trigger.",
        )
        self.assertRegex(
            self.text,
            r"\bpush:",
            "Workflow must declare a 'push:' trigger.",
        )

    def test_push_step_is_build_only_on_pull_request(self) -> None:
        # The build step's 'push:' input must be gated by github.event_name != 'pull_request'.
        self.assertRegex(
            self.text,
            r"push:\s*\$\{\{\s*github\.event_name\s*!=\s*'pull_request'\s*\}\}",
            "build-push-action 'push:' input must be gated by "
            "${{ github.event_name != 'pull_request' }} (build-only on PRs).",
        )


if __name__ == "__main__":
    unittest.main()
