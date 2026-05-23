# ADR: Helm chart + GHCR OCI publish (Feature B / v5-helm)

**Status**: Proposed
**Date**: 2026-05-23
**Pipeline**: master-replicator v5 — Feature B (Helm)
**Sibling**: Feature A (Docker image at `ghcr.io/asnapper/master-replicator`)

---

## Context

The `pipeline-status` CLI (v1–v4) has shipped as a local Python invocation. A sibling pipeline (Feature A) is concurrently delivering a container image at `ghcr.io/asnapper/master-replicator`. To make in-cluster periodic checks deployable with one command, this feature adds:

1. A Helm v3 chart at `charts/pipeline-status/` that renders a `CronJob` (and optional `ServiceAccount`) running `pipeline-status history` on a configurable schedule.
2. A GitHub Actions workflow at `.github/workflows/helm-publish.yml` that lints, templates, validates, packages, and publishes the chart as an **OCI artifact** to `oci://ghcr.io/asnapper/charts/pipeline-status`.
3. A Python `unittest` test file at `tests/test_helm_chart.py` that parses the static chart files and asserts structural invariants — **stdlib only**, no PyYAML.
4. Docs: `charts/pipeline-status/README.md` (chart-local), plus a self-contained "Kubernetes / Helm" section appended to the repo root `README.md`.

The chart and the Docker image have a one-way cross-feature contract: the chart references the image by default. The chart MUST install cleanly even before the image lands on GHCR (pods will `ImagePullBackOff` until then — accepted per A-1).

### Frozen contracts from prior versions

- `pipeline_status.__version__` and `pyproject.toml`'s `[project].version` are both `"0.1.0"`. The chart's `appVersion` MUST equal this string.
- v1 ADR established a stdlib-only policy for the package. v5-helm preserves it: **no third-party Python dependency is added** (overriding PO's OQ-12).
- The v4 ADR pattern (parallel-fan-out: one file per engineer, no shared edits) is reused here.

### What this ADR does NOT touch

- `Dockerfile`, `.github/workflows/docker-publish.yml`, or anything else owned by Feature A.
- Any file under `pipeline_status/`.
- Any existing test file under `tests/`.
- `pyproject.toml`.
- `.claude/state/` (runtime state — read by the chart at deploy time, never modified by this PR).

---

## Decision Drivers

- **Parallel-fan-out (driver #0)**: PM will decompose this ADR into **4 tasks (B1–B4)**. Each owns one production file (or one tightly cohesive directory) plus optionally one test file. No two tasks edit the same file. Engineers run concurrently on isolated `git worktree`s. Inter-task contracts are documented as exact file paths, key names, and values so engineers can implement their slice without waiting for a sibling.
- **Stdlib-only Python**: tests parse Chart.yaml/values.yaml/templates via a tiny hand-rolled YAML subset parser inside the test file. **Overrides PO's OQ-12.** Rationale: the chart YAML uses only basic mappings, lists, and quoted strings — no anchors, no flow style, no multi-line scalars. A ~50-line `_parse_simple_yaml(text) -> dict` helper inside the test file is cheaper than introducing the project's first third-party dependency and the conditional `skipUnless` plumbing PO suggested.
- **Reproducibility over convenience**: default `image.tag` is `""`, which renders as `.Chart.AppVersion` = `"0.1.0"`. **Accepts PO's OQ-3.** Operators that want `latest` override with one `--set`. This aligns Helm releases with concrete image versions and matches Helm idioms.
- **Cross-feature isolation**: the chart works regardless of whether Feature A has merged. The image reference is a default that operators can override; helm install never queries GHCR; tests never assume image existence.
- **Cross-feature contract on UID**: the container's effective UID/GID MUST be `65532` (matches Feature A's non-root user per the Docker pipeline's contract). Documented in `values.yaml` comments and asserted in `tests/test_helm_chart.py`.
- **No `helm` binary at unit-test time**: tests parse static YAML; the workflow runs `helm` separately. This keeps test runtime under NFR-6's 5-second budget and removes any Helm-version coupling from local dev.
- **Self-contained repo-root README section**: both Feature A and Feature B append a new section to the repo root `README.md`. Each feature's section is self-contained and immediately follows the existing content with a level-2 heading; the eventual three-way merge between master, Feature A, and Feature B is mechanical (keep both new sections, in either order).
- **Boring CI**: `azure/setup-helm@v4`, `actions/checkout@v4`, `helm lint --strict`, `helm template | kubectl apply --dry-run=client -f -`, `helm package`, `helm push`. Pinned majors only.

---

## Considered Options

### Decision 1: Chart `apiVersion`

- **Option A**: `apiVersion: v1` (Helm 2 legacy). Rejected — Helm 2 is end-of-life.
- **Option B**: `apiVersion: v2` (Helm 3 native, supports `appVersion`, `type`, `kubeVersion`, `dependencies`).
- **Chosen: B**. Required by FR-2. The chart declares no `dependencies:` block but still uses the v2 schema for `appVersion`, `type: application`, and `kubeVersion`.

### Decision 2: Distribution channel — OCI vs traditional `index.yaml`

- **Option A**: Maintain an `index.yaml` on a GitHub Pages branch.
  - Cons: separate publish workflow, separate gh-pages branch, separate URL surface, separate auth surface.
- **Option B**: OCI push to `ghcr.io/asnapper/charts/pipeline-status` (Helm 3.8+ GA, `helm push oci://...`).
  - Pros: single registry for image + chart, single auth surface (`GITHUB_TOKEN`), no extra branch, atomic per-version push, no index churn.
- **Chosen: B**. Required by FR-31 / NFR-4. No `helm repo add` flow is offered; users install with `helm install ... oci://...`.

### Decision 3: Default `image.tag` (PO OQ-3)

- **Option A**: `image.tag: "latest"`. Convenient but operationally hazardous (silent drift, no reproducibility, `ImagePullBackOff` cache races on tag re-push).
- **Option B**: `image.tag: ""`, templated as `{{ .Values.image.tag | default .Chart.AppVersion }}`. Renders to `:0.1.0` by default; reproducible; aligns chart version with image version; one-line operator override (`--set image.tag=latest`).
- **Chosen: B**. **Confirms PO's OQ-3 recommendation.** The orchestrator's earlier draft suggested `"latest"`; this ADR rejects that for reproducibility. The chart's rendered image reference for a clean `helm install` is therefore `ghcr.io/asnapper/master-replicator:0.1.0`.

### Decision 4: `kubeVersion` constraint syntax (PO OQ-7)

- **Option A**: `">=1.26.0"`. Standard semver but misses pre-release versions of 1.26 (e.g. `1.26.0-rc.1`).
- **Option B**: `">=1.26-0"`. Helm-recommended form per Helm's `Chart.yaml` reference; matches both stable and pre-release tags of any 1.26+ release.
- **Chosen: B**. Required by FR-3. The unit test asserts on the exact string `">=1.26-0"`.

### Decision 5: Workload kind — CronJob vs Job vs Deployment

- **Option A**: `Deployment` with an in-container scheduler (e.g. `while true; do pipeline-status history; sleep 300; done`). Cons: requires pod hardening for indefinite-lived process; no Kubernetes-native retry; no history retention; conflicts with `readOnlyRootFilesystem`.
- **Option B**: `Job` re-applied via GitOps tooling. Cons: no native cadence; out of scope to require a sibling Argo Workflows / Flux setup.
- **Option C**: `CronJob` (`batch/v1`, GA since Kubernetes 1.21). Native cadence, native success/fail history retention, native concurrency policy, native `restartPolicy: OnFailure`. Matches the CLI's "run, log, exit" shape.
- **Chosen: C**. Required by FR-9, FR-10, FR-14, FR-15. `apiVersion: batch/v1`. Floor of `kubeVersion: ">=1.26-0"` is comfortable.

### Decision 6: ServiceAccount conditional

- **Option A**: Always render a ServiceAccount. Cons: operators with strict RBAC who want to reuse an existing SA can't disable it.
- **Option B**: Always reference an externally-managed SA. Cons: forces operators to pre-create one even for the simple case.
- **Option C**: Gate via `serviceAccount.create` (default `true`); when false, render no SA and have the CronJob's `serviceAccountName:` reference `.Values.serviceAccount.name`. A `_helpers.tpl` helper resolves the name in both cases.
- **Chosen: C**. Required by FR-22 / FR-23 / US-6. The CronJob's `serviceAccountName:` always renders to the resolved name.

### Decision 7: SecurityContext defaults

- **Option A**: Render nothing; rely on cluster admission policy.
- **Option B**: Render Kubernetes Pod Security Standards **restricted** profile defaults inline.
- **Chosen: B**. Required by FR-18 / FR-19 / NFR-7. The container `securityContext` defaults to:
  ```yaml
  runAsNonRoot: true
  runAsUser: 65532
  runAsGroup: 65532
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
  seccompProfile:
    type: RuntimeDefault
  ```
  And pod `securityContext` defaults to:
  ```yaml
  runAsNonRoot: true
  runAsUser: 65532
  runAsGroup: 65532
  fsGroup: 65532
  seccompProfile:
    type: RuntimeDefault
  ```

  **Cross-feature contract**: UID/GID `65532` matches the non-root user defined by Feature A's Docker image (sibling pipeline). This number is the conventional "nonroot" UID used by `gcr.io/distroless/*:nonroot` and by Chainguard's `nonroot` images. **If Feature A picks a different UID, both features bump together via a follow-up PR.** Documented in `values.yaml` comments and in `charts/pipeline-status/README.md`.

  Note that the requirements (FR-18) do NOT mention `runAsUser`/`runAsGroup`; we add them here as a stricter default than FR-18 mandates. This is a defensible deviation: it improves the default's restricted-profile compliance on clusters that enforce explicit UIDs (some PSA implementations of `restricted` require an explicit non-zero `runAsUser`). Operators can still override via `values.yaml`.

### Decision 8: State-volume binding pattern

- **Option A**: Render an inline `PersistentVolumeClaim` template. Cons: chart owns PVC lifecycle (delete on uninstall = data loss); requires storageClass guessing.
- **Option B**: Operator pre-creates the PVC; chart binds to its name via `stateVolume.claimName`.
- **Option C**: Hybrid (gate a PVC template behind a `stateVolume.create` flag). Out of scope for v5-helm (FR — no PVC template in this version).
- **Chosen: B**. Required by FR-16 / FR-17 / A-8. Defaults: `stateVolume.enabled: false`, `stateVolume.mountPath: "/repo"`, `stateVolume.claimName: ""`. When `enabled: true`, the pod gets one `volumes[]` entry of kind `persistentVolumeClaim` referencing `claimName`, and the container gets one `volumeMounts[]` entry at `mountPath`. When `enabled: false`, both blocks are conditionally omitted via Go-template `{{- if ... }}` gating.

### Decision 9: CI validation pipeline (PO OQ-1)

- **Option A**: `helm lint` only.
- **Option B**: `helm lint --strict` + `helm template` + `kubectl apply --dry-run=client`.
- **Option C**: `helm lint --strict` + `helm template` + `kubeconform` (richer schema validation, requires download + chmod step).
- **Chosen: B**. Confirms PO's OQ-1. `kubectl` is already on `ubuntu-latest`; the small static manifest set doesn't need kubeconform's deeper schema graph. If future templates surface validation gaps, swap to `kubeconform` as a one-line change. `--strict` (`helm lint --strict`) makes warnings fail the step, satisfying NFR-1.

### Decision 10: YAML parsing in tests (PO OQ-12 — **overridden**)

- **Option A** (PO's suggestion): Add `PyYAML>=6.0` as `[project.optional-dependencies].test` in `pyproject.toml`. Import in tests with `skipUnless` fallback.
- **Option B** (Chosen): Implement a tiny `_parse_simple_yaml(text) -> dict` inside `tests/test_helm_chart.py`. Stdlib only.
- **Chosen: B**. **Overrides PO's OQ-12.** Rationale:
  1. The v1 ADR established a stdlib-only baseline; introducing the first third-party dep for what is essentially regex-equivalent work breaks that invariant for low value.
  2. The chart YAML is hand-authored and uses a small, predictable subset: mappings of `key: scalar`, mappings of `key:` followed by indented children, lists as `key: [a, b]` or `- item` blocks, quoted-string scalars (`"..."` and `'...'`), bare-word scalars, integers, booleans, and the empty-map / empty-list literals (`{}`, `[]`). No anchors, no flow mappings (`{key: value}` as nested), no multi-line scalars (`|`, `>`), no merge keys (`<<`), no tags. A parser that handles this subset is ~40–60 lines of Python.
  3. Templates contain Go-template expressions (`{{ ... }}`). The test fixture substitutes those with deterministic placeholders **before** parsing — exactly as requirements FR-42 anticipates. The substitution table covers `{{ .Release.Name }}`, `{{ .Release.Namespace }}`, `{{ .Chart.AppVersion }}`, `{{ include "pipeline-status.fullname" . }}`, `{{ include "pipeline-status.serviceAccountName" . }}`, and `{{ .Values.<dot.path> }}` references (resolved against the parsed `values.yaml`). Any remaining `{{ ... }}` expression (including helper invocations like `toYaml`) is stripped to its right-hand fallback or to an empty string before parsing.

  **Parser limitations to document inside the test file** (in a module-level docstring):
  - Supports: `key: scalar` (string/int/bool/empty), `key:` + indented child block, `key: []` and `key: {}` empty literals, single-line flow lists `key: [a, b, c]`, block-style lists with `- ` prefix, quoted strings (`"..."` and `'...'`), bare-word scalars, integers, booleans (`true`/`false`).
  - Does NOT support: YAML anchors (`&`/`*`), merge keys (`<<:`), block-style flow mappings (`{a: 1, b: 2}` nested), multi-line scalars (`|`, `>`), tags (`!!str`), document separators (`---`), comments mid-line (only full-line `# ...` comments are stripped).
  - If a future chart template needs a feature outside this subset, the parser is extended (or that template is asserted via regex, not YAML parsing).

### Decision 11: Workflow trigger surface (PO OQ-9)

- **Option A**: Run on all PRs against `master` (validate-only path).
- **Option B**: Run only on PRs that touch `charts/**` or `.github/workflows/helm-publish.yml`.
- **Chosen: A**. Confirms PO's OQ-9. Validation is fast (<30s warm), and the chart depends transitively on the broader repo (e.g. the README cross-link, the cross-feature image reference). If runtime becomes a concern, add a `paths:` filter as a follow-up.

### Decision 12: Workflow concurrency

- **Option A**: No concurrency block. Two pushes to `master` in quick succession race on the publish step.
- **Option B**: `concurrency: { group: helm-publish-${{ github.ref }}, cancel-in-progress: false }`. Pushes to the same ref serialise; pushes to different refs (master vs a `chart-v*` tag) run in parallel.
- **Chosen: B**. Required by FR-39. `cancel-in-progress: false` prevents losing a half-completed publish.

### Decision 13: Workflow error policy on duplicate version push (PO OQ-8)

- **Option A**: `helm push --force` — overwrite on duplicate.
- **Option B**: `helm push` (no `--force`) — fail on duplicate.
- **Chosen: B**. Required by FR-37. Confirms PO's OQ-8. Re-publishing the same version is always a bug (forgot to bump `Chart.yaml.version`). The workflow surfaces it as a build failure on `master`; the operator bumps the version and re-merges.

### Decision 14: Workflow secret handling

- **Option A**: `helm registry login ghcr.io -u ${{ github.actor }} -p ${{ secrets.GITHUB_TOKEN }}` (token on the command line). Token leaks to process listing.
- **Option B**: `echo "${{ secrets.GITHUB_TOKEN }}" | helm registry login ghcr.io -u ${{ github.actor }} --password-stdin`. Token never touches argv.
- **Chosen: B**. Required by NFR-9. Workflow uses `--password-stdin`.

### Decision 15: PyYAML test dependency (PO OQ-12) — final call

See Decision 10. **Override** PO's recommendation. No third-party dep. Stdlib-only parser inside `tests/test_helm_chart.py`. `pyproject.toml` is **untouched** by this feature.

### Decision 16: Where the "missing source image" risk is documented

- **Option A**: Silent — operator surprised by `ImagePullBackOff`.
- **Option B**: A prominent note in `charts/pipeline-status/README.md` ("Cross-feature dependency: ...") plus a comment in `values.yaml` above the `image:` block.
- **Chosen: B**. Required by FR-30 and A-1.

### Decision 17: Cross-feature README section ownership

- Both Feature A (Docker) and Feature B (Helm) add a new top-level section to the repo root `README.md`. To make the merge mechanical:
  - Feature B owns a section titled exactly `## Kubernetes (Helm)`. It appears as the last top-level section in this PR's README diff.
  - Feature A owns a separate section (`## Docker` or similar). The Architect for Feature A controls that name.
  - Both sections are self-contained: no cross-references between them at merge time. (A follow-up cleanup PR can add cross-links once both have landed.)
  - The merge strategy is "keep both": `git merge` should produce no conflicts because each section appends at end-of-file. If both PRs touch the same trailing newline, a one-line manual resolution is acceptable.

---

## Architecture

### File ownership table (parallel-fan-out — 4 tasks)

| Task | Production files (sole owner) | Test files (sole owner) | Imports from master | Imports from sibling tasks |
|---|---|---|---|---|
| **B1** | `charts/pipeline-status/Chart.yaml`, `charts/pipeline-status/values.yaml`, `charts/pipeline-status/.helmignore` | — | none | none |
| **B2** | `charts/pipeline-status/templates/_helpers.tpl`, `charts/pipeline-status/templates/cronjob.yaml`, `charts/pipeline-status/templates/serviceaccount.yaml`, `charts/pipeline-status/templates/NOTES.txt` | — | none (refers to B1's values by key) | none (relies only on contract: key names + defaults in this ADR) |
| **B3** | `.github/workflows/helm-publish.yml` | — | none | none (relies only on contract: chart lives at `charts/pipeline-status/`) |
| **B4** | `charts/pipeline-status/README.md`, repo-root `README.md` (append new section only), `tests/test_helm_chart.py` | (same — test file is bundled here) | reads `pyproject.toml` (text only) | reads B1/B2 chart files via filesystem at test time |

**Parallel-fan-out invariant**: each task's worktree contains only its own files plus the master snapshot. Tasks B1, B2, B3 run truly in parallel (no inter-task imports). Task B4 has a **soft dependency** on B1 and B2 because its tests read those files from disk. The PM has two options:

1. **Sequential merge stage for B4** (recommended): B1/B2/B3 run in parallel; B4 runs on a worktree forked from the post-B1/B2 merge tip. B3 still runs in parallel with B4. Net: 2-stage dispatch with 3 tasks in stage 1 and 1 task in stage 2.
2. **Fully parallel with stubs**: B4's worktree pre-creates stub copies of `Chart.yaml`, `values.yaml`, and the templates as test fixtures, exercises the parser against them, and merges last. Requires B4's engineer to mirror the contract from this ADR (paths + values) into test fixtures; small but doable.

PM chooses; the ADR makes either work because every value pinned below is also documented here, so B4's engineer doesn't need to wait on B1's PR to know what the parser is going to read.

**No two tasks edit the same file.** B4 is the only task that edits the repo-root `README.md`; B4 is the only task in the `tests/` directory; B1/B2/B3 each own a disjoint subset of `charts/`.

### Chart structure (target tree)

```
charts/pipeline-status/
├── Chart.yaml                  (B1)
├── values.yaml                 (B1)
├── .helmignore                 (B1)
├── README.md                   (B4)
└── templates/
    ├── _helpers.tpl            (B2)
    ├── cronjob.yaml            (B2)
    ├── serviceaccount.yaml     (B2)
    └── NOTES.txt               (B2)

.github/workflows/
└── helm-publish.yml            (B3)

tests/
└── test_helm_chart.py          (B4)

README.md                       (B4 — append "## Kubernetes (Helm)" section only)
```

### B1 — `Chart.yaml` (exact content contract)

The file MUST contain these fields with these exact values:

```yaml
apiVersion: v2
name: pipeline-status
description: A periodic Kubernetes CronJob that runs the pipeline-status CLI against a mounted .claude/state/ directory.
type: application
version: 0.1.0
appVersion: "0.1.0"
kubeVersion: ">=1.26-0"
home: https://github.com/asnapper/master-replicator
sources:
  - https://github.com/asnapper/master-replicator
maintainers:
  - name: asnapper
    url: https://github.com/asnapper
```

Notes:
- `appVersion` is **quoted** (`"0.1.0"`). The unit test asserts this exact string.
- `version` is **unquoted** semver — Helm permits both, but matching the requirements text exactly is cleanest.
- `description` is one sentence per FR-3.
- No `dependencies:` block; no `requirements.yaml`.
- No `icon:`, no `keywords:`, no `annotations:` in v5-helm (deferred).

### B1 — `values.yaml` (exact key set, types, and defaults)

The file MUST expose **every** key below with **exactly** the indicated default. Comments above each top-level block explain its purpose (FR-29).

```yaml
# -- Container image coordinates.
# The default repository (ghcr.io/asnapper/master-replicator) is the image
# produced by the sibling Docker pipeline (Feature A). Operators with their
# own mirror should override `image.registry` and/or `image.repository`.
# `image.tag` defaults to "" which resolves to .Chart.AppVersion (0.1.0)
# at render time — keeping chart releases and image releases in lockstep.
image:
  registry: ghcr.io
  repository: asnapper/master-replicator
  tag: ""
  pullPolicy: IfNotPresent

# -- imagePullSecrets is a stub for private-registry mirrors.
imagePullSecrets: []

# -- Override the default name / fullname.
nameOverride: ""
fullnameOverride: ""

# -- ServiceAccount management.
# When `create: true` (default), a dedicated SA is rendered. When false, the
# operator must pre-create an SA and set `serviceAccount.name` to its name.
serviceAccount:
  create: true
  name: ""
  annotations: {}

# -- CronJob behaviour.
cronjob:
  schedule: "*/5 * * * *"
  args: ["history"]
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 1
  concurrencyPolicy: Forbid

# -- State volume binding. Operator opts in by setting enabled: true and
# providing the name of a PersistentVolumeClaim that contains the
# .claude/state/ tree to inspect. The chart does NOT render a PVC.
stateVolume:
  enabled: false
  mountPath: "/repo"
  claimName: ""

# -- Resource requests/limits (operator opts in).
resources: {}

# -- Pod-level securityContext. Defaults satisfy the Kubernetes Pod Security
# Standards "restricted" profile. UID/GID 65532 matches the non-root user
# in the sibling Docker image.
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 65532
  runAsGroup: 65532
  fsGroup: 65532
  seccompProfile:
    type: RuntimeDefault

# -- Container-level securityContext.
securityContext:
  runAsNonRoot: true
  runAsUser: 65532
  runAsGroup: 65532
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop:
      - ALL
  seccompProfile:
    type: RuntimeDefault

# -- Scheduling constraints.
nodeSelector: {}
tolerations: []
affinity: {}
```

Notes:
- The `runAsUser`/`runAsGroup`/`fsGroup: 65532` defaults are slightly stricter than FR-18/FR-19 mandate (which omitted them). This is the cross-feature contract with Feature A; the test asserts these exact values.
- `capabilities.drop` is rendered as a block-style list (`- ALL`) rather than the flow-style `["ALL"]` that the requirements draft uses — both are valid YAML and equivalent at template-render time; block style is friendlier to the stdlib YAML subset parser.
- `cronjob.args` is `["history"]` in flow style — the parser supports flow lists of bare-word scalars, which is sufficient here.
- `serviceAccount.annotations` defaults to `{}` (empty map literal); the template gates the `annotations:` field on non-empty.

### B1 — `.helmignore`

Standard `helm create` skeleton (excludes `.git`, `.DS_Store`, IDE files, `*.tgz`, `tests/`, etc.). One file; B2 doesn't depend on its content.

### B2 — `_helpers.tpl` (Go-template helper contract)

Four helpers, exact names below (FR-8):

- `pipeline-status.name` — name override, max 63 chars, trim trailing `-`.
- `pipeline-status.fullname` — fullname override or `<release>-<chart>`, max 63 chars, trim trailing `-`.
- `pipeline-status.chart` — `<name>-<version>` for the `helm.sh/chart` label.
- `pipeline-status.labels` — full standard label set:
  ```yaml
  helm.sh/chart: {{ include "pipeline-status.chart" . }}
  {{ include "pipeline-status.selectorLabels" . }}
  app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
  app.kubernetes.io/managed-by: {{ .Release.Service }}
  ```
- `pipeline-status.selectorLabels` — minimal selector set:
  ```yaml
  app.kubernetes.io/name: {{ include "pipeline-status.name" . }}
  app.kubernetes.io/instance: {{ .Release.Name }}
  ```
- `pipeline-status.serviceAccountName` — `.Values.serviceAccount.create` ? `pipeline-status.fullname` (or override) : `.Values.serviceAccount.name`.

Standard `helm create` boilerplate; engineer can mirror it verbatim.

### B2 — `templates/cronjob.yaml` (annotated)

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ include "pipeline-status.fullname" . }}
  labels:
    {{- include "pipeline-status.labels" . | nindent 4 }}
spec:
  schedule: {{ .Values.cronjob.schedule | quote }}
  concurrencyPolicy: {{ .Values.cronjob.concurrencyPolicy }}
  successfulJobsHistoryLimit: {{ .Values.cronjob.successfulJobsHistoryLimit }}
  failedJobsHistoryLimit: {{ .Values.cronjob.failedJobsHistoryLimit }}
  jobTemplate:
    spec:
      template:
        metadata:
          labels:
            {{- include "pipeline-status.selectorLabels" . | nindent 12 }}
        spec:
          restartPolicy: OnFailure
          serviceAccountName: {{ include "pipeline-status.serviceAccountName" . }}
          {{- with .Values.imagePullSecrets }}
          imagePullSecrets:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          securityContext:
            {{- toYaml .Values.podSecurityContext | nindent 12 }}
          containers:
            - name: {{ .Chart.Name }}
              image: "{{ .Values.image.registry }}/{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
              imagePullPolicy: {{ .Values.image.pullPolicy }}
              args:
                {{- toYaml .Values.cronjob.args | nindent 16 }}
              securityContext:
                {{- toYaml .Values.securityContext | nindent 16 }}
              {{- with .Values.resources }}
              resources:
                {{- toYaml . | nindent 16 }}
              {{- end }}
              {{- if .Values.stateVolume.enabled }}
              volumeMounts:
                - name: state
                  mountPath: {{ .Values.stateVolume.mountPath }}
              {{- end }}
          {{- if .Values.stateVolume.enabled }}
          volumes:
            - name: state
              persistentVolumeClaim:
                claimName: {{ .Values.stateVolume.claimName }}
          {{- end }}
          {{- with .Values.nodeSelector }}
          nodeSelector:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .Values.tolerations }}
          tolerations:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .Values.affinity }}
          affinity:
            {{- toYaml . | nindent 12 }}
          {{- end }}
```

Notes for B2's engineer:
- The container `image:` is **always quoted** (per the leading `"`). This avoids YAML parser confusion if a future `image.tag` includes `:` or `@sha256:...` syntax.
- `volumes:` and the container's `volumeMounts:` are BOTH gated by `.Values.stateVolume.enabled`; when false, neither block appears in the rendered output (FR-17, US-2).
- The `{{- with .Values.X }}` idiom (rather than `{{- if .Values.X }}`) ensures empty maps/lists render no block at all (FR-21).
- `restartPolicy: OnFailure` is hardcoded (FR-10). Not a knob.
- `serviceAccountName:` always renders to the resolved name (FR-22 / Decision 6 / US-6).

### B2 — `templates/serviceaccount.yaml`

```yaml
{{- if .Values.serviceAccount.create -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "pipeline-status.serviceAccountName" . }}
  labels:
    {{- include "pipeline-status.labels" . | nindent 4 }}
  {{- with .Values.serviceAccount.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}
```

When `serviceAccount.create: false`, the entire file collapses to empty (no resource emitted). FR-23 / US-6.

### B2 — `templates/NOTES.txt`

Brief post-install message printed by `helm install`:
```
1. Verify the CronJob is registered:
     kubectl get cronjob {{ include "pipeline-status.fullname" . }} -n {{ .Release.Namespace }}

2. To trigger a one-off run:
     kubectl create job --from=cronjob/{{ include "pipeline-status.fullname" . }} \
       {{ include "pipeline-status.fullname" . }}-manual -n {{ .Release.Namespace }}

{{- if not .Values.stateVolume.enabled }}

NOTE: stateVolume.enabled=false (the default). The CronJob will run, but
"pipeline-status history" has no mounted .claude/state/ to inspect. Set
stateVolume.enabled=true and stateVolume.claimName=<your-pvc> to point at
real state.
{{- end }}
```

Not asserted by unit tests; included for operator UX.

### B3 — `.github/workflows/helm-publish.yml` (shape)

```yaml
name: helm-publish

on:
  push:
    branches: [master]
    tags: ["chart-v*"]
  pull_request:
    branches: [master]

permissions:
  contents: read
  packages: write

concurrency:
  group: helm-publish-${{ github.ref }}
  cancel-in-progress: false

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Helm
        uses: azure/setup-helm@v4

      - name: Lint
        run: helm lint --strict charts/pipeline-status/

      - name: Render templates
        run: helm template release-name charts/pipeline-status/ > /tmp/rendered.yaml

      - name: Validate rendered manifests
        run: kubectl apply --dry-run=client -f /tmp/rendered.yaml

      - name: Log in to GHCR
        if: github.event_name == 'push'
        run: echo "${{ secrets.GITHUB_TOKEN }}" | helm registry login ghcr.io -u ${{ github.actor }} --password-stdin

      - name: Package chart
        if: github.event_name == 'push'
        run: |
          mkdir -p /tmp/dist
          helm package charts/pipeline-status/ --destination /tmp/dist

      - name: Push to GHCR
        if: github.event_name == 'push'
        run: helm push /tmp/dist/pipeline-status-*.tgz oci://ghcr.io/asnapper/charts
```

Notes for B3's engineer:
- Each conditional step uses `if: github.event_name == 'push'`. This satisfies FR-38 (PR runs lint+template+validate only).
- `helm lint --strict` makes lint warnings fail the build (NFR-1 / NFR-11).
- No `--force` on `helm push` (FR-37, Decision 13). A duplicate version fails the step.
- Action versions pinned to major (`@v4`). No floating tags (FR-34, NFR-5).
- `kubectl` is preinstalled on `ubuntu-latest`; no setup step needed. (If a future runner image drops it, add a `setup-kubectl` step.)
- The `concurrency:` block (FR-39) serialises pushes to the same ref.
- No `paths:` filter on the triggers (Decision 11 / OQ-9).

### B4 — `tests/test_helm_chart.py` (stdlib-only test plan)

File layout:

```python
"""
Static structural tests for charts/pipeline-status/.

Uses a hand-rolled YAML subset parser (no PyYAML) to keep the project
stdlib-only. The parser supports:
    - key: scalar  (string/int/bool/empty/quoted)
    - key:         followed by indented child block (mapping or list)
    - key: []      empty flow list
    - key: {}      empty flow map
    - key: [a, b]  single-line flow list of bare scalars/quoted strings
    - - item       block-style list entries
    - "..." and '...' quoted strings
    - bare-word scalars, integers, booleans (true/false)
    - full-line '# comment' (stripped)

It does NOT support:
    - YAML anchors (& / *) or merge keys (<<:)
    - block-style flow mappings ({a: 1, b: 2}) nested as values
    - multi-line scalars (| or >)
    - tags (!!str etc.)
    - mid-line comments
    - document separators (---)

For template files, a fixture function `_render_simple(text, values)`
substitutes Go-template expressions with deterministic placeholders BEFORE
parsing:
    {{ .Release.Name }}                          -> "release"
    {{ .Release.Namespace }}                     -> "default"
    {{ .Chart.AppVersion }}                      -> "0.1.0" (from Chart.yaml)
    {{ .Chart.Name }}                            -> "pipeline-status"
    {{ include "pipeline-status.fullname" . }}   -> "release-pipeline-status"
    {{ include "pipeline-status.serviceAccountName" . }} -> "release-pipeline-status"
    {{ .Values.<dot.path> }} (e.g. .Values.image.registry) -> values.yaml default
    {{ ... | quote }}                            -> the substituted value, double-quoted
    {{- if .Values.X.enabled }}...{{- end }}     -> kept if X.enabled is true in values; else dropped
    {{- with .Values.X }}...{{- end }}           -> kept if .Values.X is truthy; else dropped
    {{- toYaml .Values.X | nindent N }}          -> rendered inline as indented YAML

Anything else not in the above map is replaced with the empty string.
"""
```

Test cases (one `unittest.TestCase` per logical group; FR-43):

1. `TestChartYaml`:
   - `Chart.yaml` parses.
   - `apiVersion == "v2"`, `name == "pipeline-status"`, `type == "application"`.
   - `version == "0.1.0"` and `appVersion == "0.1.0"` (string).
   - `kubeVersion == ">=1.26-0"` (exact string).
   - `description` is a non-empty string.
   - `home`, `sources`, `maintainers` are present and non-empty.
   - `appVersion` equals the `version` field read from `pyproject.toml` (regex: `^version\s*=\s*"([^"]+)"`). The test reads `pyproject.toml` as text — no `tomllib` required (stdlib 3.10 doesn't have it stably; we use a regex extract).

2. `TestValuesYaml`:
   - `values.yaml` parses.
   - Every key listed in FR-27's table is present with the documented default type.
   - `image.registry == "ghcr.io"`, `image.repository == "asnapper/master-replicator"`, `image.tag == ""`, `image.pullPolicy == "IfNotPresent"`.
   - `cronjob.schedule == "*/5 * * * *"`, `cronjob.args == ["history"]`, `cronjob.concurrencyPolicy == "Forbid"`.
   - `serviceAccount.create == True`, `serviceAccount.name == ""`.
   - `stateVolume.enabled == False`, `stateVolume.mountPath == "/repo"`.
   - `securityContext.runAsNonRoot == True`, `securityContext.runAsUser == 65532`, `securityContext.runAsGroup == 65532`, `securityContext.allowPrivilegeEscalation == False`, `securityContext.readOnlyRootFilesystem == True`.
   - `securityContext.capabilities.drop == ["ALL"]`.
   - `podSecurityContext.runAsNonRoot == True`, `podSecurityContext.runAsUser == 65532`.

3. `TestCronJobTemplate`:
   - With default values, the rendered `templates/cronjob.yaml` parses.
   - It contains exactly one resource with `apiVersion: batch/v1` and `kind: CronJob`.
   - `spec.schedule == "*/5 * * * *"`.
   - `spec.concurrencyPolicy == "Forbid"`.
   - `spec.successfulJobsHistoryLimit == 3` and `spec.failedJobsHistoryLimit == 1`.
   - `spec.jobTemplate.spec.template.spec.restartPolicy == "OnFailure"`.
   - The container `image:` contains the substring `ghcr.io/asnapper/master-replicator:0.1.0`.
   - The container `args` equals `["history"]`.
   - The container `securityContext.runAsNonRoot == True`.
   - The container `securityContext.runAsUser == 65532`.
   - With `stateVolume.enabled == false` (the default), neither `volumes:` nor a `volumeMounts:` block referring to `name: state` appears in the rendered output. (Check via substring search on the pre-parse rendered text or via parsed structure; substring is simplest given parser limitations on toYaml-rendered blocks.)
   - With `stateVolume.enabled = True` and `claimName = "my-pvc"` overridden in the fixture, a `volumes:` block with `claimName: my-pvc` appears AND a `volumeMounts:` entry with `mountPath: /repo` appears.

4. `TestServiceAccountTemplate`:
   - With default values (`create: true`), `templates/serviceaccount.yaml` renders exactly one `ServiceAccount` with `kind: ServiceAccount` and the resolved name.
   - With `serviceAccount.create = False` overridden, the rendered template is empty (no `kind:` line).

5. `TestNoForbiddenResources` (FR-24):
   - Concatenated rendered output of all templates contains none of: `kind: Service`, `kind: Ingress`, `kind: HorizontalPodAutoscaler`, `kind: NetworkPolicy`, `kind: Deployment`, `kind: Role`, `kind: RoleBinding`, `kind: ClusterRole`, `kind: ClusterRoleBinding`, `kind: PodDisruptionBudget`, `kind: ConfigMap`.

Total test count target: ~20–25 assertions across 5 test cases. Runtime target: <1 second locally.

### B4 — `charts/pipeline-status/README.md` (content contract)

Sections, in order:

1. **Overview** — one paragraph: what the chart does, the CronJob model, the cross-feature note that the default image references the sibling Docker pipeline and pods will `ImagePullBackOff` until that image lands.
2. **Prerequisites** — Helm 3.8+ (for OCI), Kubernetes 1.26+.
3. **Install** — three worked examples:
   - Install with defaults: `helm install ps oci://ghcr.io/asnapper/charts/pipeline-status --version 0.1.0`
   - Install with a custom PVC: same command + `--set stateVolume.enabled=true --set stateVolume.claimName=my-state-pvc`
   - Install with a custom schedule: same command + `--set cronjob.schedule="0 */6 * * *"`
   - Install with `serviceAccount.create=false`: same command + `--set serviceAccount.create=false --set serviceAccount.name=existing-sa`
4. **Values reference** — table with one row per top-level key from FR-27. Columns: `Key`, `Type`, `Default`, `Description`.
5. **Security defaults** — note the `runAsNonRoot: true`, `runAsUser: 65532` defaults and the cross-feature UID contract with Feature A.
6. **Uninstall** — `helm uninstall ps`. Note that the PVC (if any) is **not** managed by this chart and survives uninstall.
7. **Versioning** — `version` (chart) vs `appVersion` (image). When and why they bump.

### B4 — Repo-root `README.md` append

Append a new section after the existing content:

```markdown
## Kubernetes (Helm)

A Helm chart at `charts/pipeline-status/` deploys `pipeline-status` as a
periodic `CronJob` in a Kubernetes cluster (>= 1.26). Published as an OCI
artifact to `ghcr.io/asnapper/charts/pipeline-status`.

```sh
helm install ps oci://ghcr.io/asnapper/charts/pipeline-status --version 0.1.0
```

See `charts/pipeline-status/README.md` for full values reference and
worked examples.
```

The section is self-contained: no references to (or from) Feature A's
section. The eventual merge with Feature A is mechanical: keep both.

### Workflow shape — exit-code matrix

| Event | Steps run | Expected exit | On failure |
|---|---|---|---|
| `pull_request` against `master` | checkout, setup-helm, lint, template, validate | 0 | Job fails; PR blocked. |
| `push` to `master` (Chart.yaml version unchanged from last GHCR push) | all of the above + login + package + push | non-zero at `Push to GHCR` step (duplicate version) | Job fails; commit author bumps version and re-pushes. |
| `push` to `master` (Chart.yaml version is new) | all of the above | 0 | Chart now on GHCR at the new version. |
| `push` to tag `chart-v*` | same as master push | 0 if version is new on GHCR; non-zero otherwise | Same as master push. |

---

## Implementation Notes

### Per-task notes for engineers

**B1 — Chart metadata + values**

- Source `appVersion` from `pyproject.toml`'s `version = "0.1.0"`. Hardcode it — the unit test will catch drift if the package version bumps.
- `values.yaml`: every top-level key from the FR-27 table must be present. Order matches the table for readability. Each top-level block gets a `#` comment above it documenting purpose (FR-29).
- `.helmignore`: copy the standard `helm create` output. Excludes VCS artefacts, OS artefacts, IDE files, packaged charts (`*.tgz`).
- Do NOT add a `values.schema.json` (FR-7).

**B2 — Templates**

- `_helpers.tpl`: standard `helm create` boilerplate adapted to the chart name `pipeline-status`. Five helpers total.
- `cronjob.yaml`: use the annotated template above as the structural reference. Every `nindent` value matters for YAML correctness; double-check against `helm lint --strict`.
- `serviceaccount.yaml`: trivial gated template.
- `NOTES.txt`: operator UX, not asserted by tests.
- Run `helm lint --strict charts/pipeline-status/` locally before opening the PR; the CI workflow enforces zero warnings.
- The container UID/GID is `65532`. Both pod and container level securityContexts set this.
- Verify with `helm template release-name charts/pipeline-status/ | kubectl apply --dry-run=client -f -` locally.

**B3 — Workflow**

- Pin actions to major version: `actions/checkout@v4`, `azure/setup-helm@v4`. No `@main`, no `@latest`.
- Use `--password-stdin` for `helm registry login` (NFR-9). Never put `${{ secrets.GITHUB_TOKEN }}` on the command line as a `-p` value.
- Top-level `permissions:` block sets `{ contents: read, packages: write }` and nothing else (NFR-8 / FR-33).
- The `if: github.event_name == 'push'` conditional gates login / package / push (FR-38).
- `concurrency:` block uses `github.ref` so master pushes and tag pushes serialise independently (FR-39).
- No `--force` on `helm push` (FR-37).
- Do NOT add a job for pushing to a separate repo / mirror; OCI only (NFR-4).

**B4 — Docs + tests**

- The YAML subset parser is the test-time risk. Keep it simple: tokenise lines, track indentation, branch on `key:`, `- `, `key: scalar`. ~50 lines. The parser should accept the chart YAML this PR ships and reject malformed input loudly (raise `ValueError`); it does NOT need to be a general-purpose YAML parser.
- For template rendering, the substitution map is the heart of the test fixture. Keep it as a flat dict of source-pattern → replacement-string, applied via `str.replace` in deterministic order (longest pattern first to avoid `{{ .Release.Name }}` matching inside `{{ .Release.Namespace }}` — though they have unique tails so order rarely matters in practice).
- `toYaml` blocks (e.g. `securityContext: {{- toYaml .Values.securityContext | nindent 16 }}`) are rendered by the fixture by walking the parsed `values.yaml` dict and re-emitting the relevant subtree as YAML. This is the most involved part of the fixture; a small recursive `_to_yaml(obj, indent)` helper is sufficient.
- Tests run via `python -m unittest tests.test_helm_chart` from the repo root. NFR-6 budget: < 5s. Realistic actual: <1s.
- The repo-root README append uses a level-2 heading (`## Kubernetes (Helm)`) and is the LAST section in the file at merge time. Feature A's section, when it arrives, can append below — both will live as siblings.

### Known edge cases

1. **PyYAML absent**: tests never import it. No `skipUnless`. Plain stdlib.
2. **`pyproject.toml` version drift**: test regex-extracts `version = "..."` from `pyproject.toml` and asserts equality with `Chart.yaml.appVersion`. Catches drift on the next CI run.
3. **Empty `serviceAccount.annotations`**: template uses `{{- with .Values.serviceAccount.annotations }}` to omit the entire `annotations:` block. Asserted via substring absence in `TestServiceAccountTemplate`.
4. **`stateVolume.enabled: true` but empty `claimName`**: chart still renders (Helm doesn't validate logical consistency); the pod will fail to schedule. Documented in the chart README as operator responsibility. Not asserted by tests.
5. **Image reference quoting**: the container `image:` field is always rendered in double quotes (`"..."`) so future tag formats (`@sha256:...`) parse cleanly. Asserted in tests via substring.
6. **`helm lint --strict` warning surface**: known warnings to pre-empt:
   - `icon` missing → acceptable; lint only warns at `--strict` if a maintainer entry has no `email`. Our maintainer has `url:` instead, which `helm lint --strict` accepts.
   - `appVersion` should be quoted → we quote it.
   - Template indentation mistakes → fixed at authoring time; the rendered YAML must be valid.
7. **Duplicate-version push**: `helm push` rejects without `--force` (Decision 13). Workflow step exits non-zero. Maintainer bumps version and re-pushes. No automation reverts the failed push state — the version was never published.
8. **Concurrent master pushes**: `concurrency:` group serialises. The second push waits, then runs against the post-merge tip.
9. **PR from a fork**: `${{ secrets.GITHUB_TOKEN }}` has read-only scope; the `if: github.event_name == 'push'` conditional skips login/push. PR validation still runs.
10. **Cross-feature image absence at install time**: documented in chart README. `helm install` succeeds; pods cycle through `ImagePullBackOff` until Feature A merges and the image lands.

---

## Consequences

**Easier after this change:**

- One-command Kubernetes deployment of `pipeline-status` as a periodic check.
- The chart is published as an OCI artifact on every master merge that bumps the version, so downstream consumers always have the latest at a known URL.
- Operators can deploy with confidence in the security defaults (PSA `restricted` profile by default).
- The chart and the Python CLI version are linked via `appVersion` (asserted in tests), preventing drift.
- The four-task parallel-fan-out matches the v4 ADR pattern; engineers can ship four PRs concurrently.

**Harder or more complex:**

- The repo now has Helm-specific tooling expectations (`helm`, `kubectl`) in CI. New contributors need to install Helm locally for chart development (the test suite itself does not).
- The chart and the Docker image have an implicit UID/GID contract (`65532`). If either side changes it, both must change together.
- The stdlib-only YAML parser in `tests/test_helm_chart.py` is one more piece of test-time code to maintain. Documented limitations; small surface (~50 lines).
- Chart-version bumps are manual (per FR / A-6). A future automation could read `pyproject.toml` and stamp `Chart.yaml.appVersion` in a pre-commit hook.

**Technical debt introduced:**

- The YAML subset parser duplicates a fraction of what PyYAML does. Documented limitation; if the chart grows features outside the supported subset (anchors, multi-line scalars), the parser is extended or PyYAML is reconsidered.
- `_TRACKED_ARTEFACTS`-style duplication does NOT apply here; the chart and the CLI don't share a manifest list at runtime.
- `values.schema.json` is deferred to v6. Without it, `helm install --set` typos are not caught at template time.
- `helm test` hooks are deferred to v6.
- No automatic chart-version bump from `pyproject.toml`. Drift caught by tests, not prevented at authoring time.

---

## Out of Scope

- Docker image build / push (Feature A's domain).
- Modifying `pipeline_status/*.py` or any existing test file.
- Modifying `pyproject.toml`. (PO's OQ-12 overridden.)
- `values.schema.json` (deferred to v6).
- `helm test` hooks (deferred to v6).
- A `Service`, `Ingress`, `HPA`, `NetworkPolicy`, `Deployment`, `Role`, `RoleBinding`, `ClusterRole`, `ClusterRoleBinding`, `PodDisruptionBudget`, or `ConfigMap` template (FR-24).
- A `PersistentVolumeClaim` template (FR — operator pre-creates).
- An umbrella chart / `dependencies:` block (FR-6).
- A traditional `index.yaml` Helm repository (NFR-4 — OCI only).
- `chart-testing` (`ct`) CI integration.
- Pre-built cloud-provider examples (EKS / GKE / AKS).
- Multi-architecture chart variants.
- Helm-version pin in CI (`azure/setup-helm@v4` tracks latest stable 3.x).
- `paths:` filter on workflow triggers (run on all PRs against master).
- `--force` on `helm push`.
- A `priorityClassName` value (OQ-15).
- `--watch` mode for the chart (it's a CronJob; cluster does the scheduling).
- Helm 2 support (`apiVersion: v1`).
- Automated chart-version bumping from `pyproject.toml`.
- Cross-task deduplication between Feature A and Feature B (e.g. a shared `SECURITY_UID` constant). Each feature hardcodes `65532`; a follow-up PR can consolidate once both have landed.
