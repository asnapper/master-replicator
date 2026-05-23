# Requirements: Helm Chart + GHCR OCI Publish Workflow (v5-helm)

**Status**: Draft
**Author**: PO Agent (Feature B — Helm)
**Date**: 2026-05-23
**Scope label**: `feature/helm`

> **Out of scope for this document**: The Docker image build (`Dockerfile`, `.github/workflows/docker-publish.yml`, `ghcr.io/asnapper/master-replicator`) is owned by Feature A's sibling pipeline. This document references the image only as a consumed default and never prescribes its internals.

---

## 1. Problem Statement

The `pipeline-status` CLI (v1–v4) currently runs only as a local Python invocation. Teams that operate Kubernetes clusters cannot deploy it as a periodic in-cluster check without hand-rolling a `CronJob` manifest, a `ServiceAccount`, and a volume mount.

We need a **Helm v3 chart** at `charts/pipeline-status/` plus a **GitHub Actions workflow** that lints, templates, packages, and publishes the chart as an **OCI artifact** to GitHub Container Registry (`oci://ghcr.io/asnapper/charts/pipeline-status`). Once published, an operator can deploy the periodic check with one command:

```sh
helm install ps oci://ghcr.io/asnapper/charts/pipeline-status --version 0.1.0 -f my-values.yaml
```

The chart MUST install cleanly even before the sibling Docker image is published to GHCR — installation succeeds; pods will `ImagePullBackOff` until the image lands. That is the accepted cross-feature contract.

---

## 2. Goals

1. Deliver a self-contained Helm chart at `charts/pipeline-status/` that installs a `CronJob` (and an optional `ServiceAccount`) running `pipeline-status history` on a configurable schedule.
2. Make every operator-tunable knob (image coordinates, schedule, args, history limits, concurrency policy, state-volume PVC binding, resources, security context, service account, scheduling constraints) reachable via `values.yaml` with sensible production defaults.
3. Publish the chart as an OCI artifact to GHCR on every push to `master` whose `Chart.yaml` version is new; reject duplicate-version pushes.
4. Validate every PR with `helm lint` (0 warnings, 0 errors) and `helm template ... | kubectl apply --dry-run=client -f -` without publishing.
5. Cover the chart's static structure with Python `unittest` tests under `tests/test_helm_chart.py` that parse `Chart.yaml`, `values.yaml`, and the rendered templates (with a tiny in-test `{{ ... }}` substitution fixture) so CI catches structural drift without needing a real `helm` binary in the unit test job.

---

## 3. Non-Goals

- **Docker image build/push.** Owned by Feature A. No `Dockerfile`, no docker-publish workflow.
- **Modifying any `pipeline_status/*.py` Python source.** Frozen.
- **Modifying `pyproject.toml` or any existing test under `tests/`.** Tests for the chart go in a brand-new file (`tests/test_helm_chart.py`).
- **Kubernetes `Service`, `Ingress`, `HPA`, `NetworkPolicy`, `Deployment`, `Role`/`RoleBinding`.** `pipeline-status` has no network surface and no in-cluster API needs.
- **`values.schema.json`** (defer to a later version).
- **`helm test` hooks** (defer to a later version).
- **Umbrella subcharts / `dependencies:` block.** This is a single self-contained chart.
- **`chart-testing` (`ct`) integration in CI.** `helm lint` + `helm template` validation is enough.
- **Pre-built cloud-provider examples (EKS / GKE / AKS).** Generic `values.yaml` only.
- **Multi-architecture chart variants.** One chart, one OCI artifact per version.
- **`helm repo add` / index.yaml traditional repo.** OCI only.
- **Live `helm` invocation from Python unit tests.** Tests parse static YAML; the workflow runs `helm` separately.
- **Automated chart-version bumping.** The author bumps `Chart.yaml`'s `version` field manually as part of the PR that ships a release.

---

## 4. User Stories

### US-1 — Cluster operator installs the chart
**As** a Kubernetes operator
**I want to** install the `pipeline-status` chart from GHCR with one `helm install` command
**So that** I can monitor a pipeline state directory in-cluster without authoring Kubernetes YAML myself.

**Acceptance criteria:**
- `helm install ps oci://ghcr.io/asnapper/charts/pipeline-status --version 0.1.0` succeeds against a Kubernetes ≥ 1.26 cluster.
- A `CronJob` named per the release fullname is created.
- The `CronJob` schedule defaults to `*/5 * * * *`.
- The `CronJob` args default to `["history"]` (i.e. it runs `pipeline-status history`).
- If the user does not override `image.tag` or pull-policy, the pod manifest references `ghcr.io/asnapper/master-replicator:latest`.

### US-2 — Operator mounts a state PVC
**As** an operator
**I want to** point the chart at an existing PVC that contains a `.claude/state/` tree
**So that** the periodic `pipeline-status history` run inspects my real pipeline state.

**Acceptance criteria:**
- Setting `stateVolume.enabled=true`, `stateVolume.claimName=my-state-pvc`, `stateVolume.mountPath=/repo` causes the rendered `CronJob` pod spec to declare a single `volumes:` entry of type `persistentVolumeClaim` with `claimName: my-state-pvc`, and a matching `volumeMounts:` entry at `mountPath: /repo`.
- Setting `stateVolume.enabled=false` removes the `volumes:` and `volumeMounts:` blocks entirely from the rendered manifest.
- Default `stateVolume.enabled` is `false` (the user MUST opt in by providing a `claimName`).

### US-3 — Operator overrides image coordinates
**As** an operator running an air-gapped or mirrored registry
**I want to** override `image.registry`, `image.repository`, `image.tag`, and `image.pullPolicy`
**So that** I can pull the container from my own registry.

**Acceptance criteria:**
- The rendered container `image:` field equals `<registry>/<repository>:<tag>` when all three are set.
- `image.pullPolicy` defaults to `IfNotPresent` and is written verbatim to the container's `imagePullPolicy:`.
- Overriding any of the four keys via `--set image.tag=v0.2.0` produces the expected rendered value.

### US-4 — Operator tunes the cron behaviour
**As** an operator
**I want to** adjust the schedule, args, history retention, and concurrency policy
**So that** the cadence matches my SLO and I don't pile up stale Jobs.

**Acceptance criteria:**
- `cronjob.schedule` controls `spec.schedule` (default `*/5 * * * *`).
- `cronjob.args` (list of strings; default `["history"]`) controls the container's `args:`.
- `cronjob.successfulJobsHistoryLimit` (default `3`) and `cronjob.failedJobsHistoryLimit` (default `1`) control the respective fields.
- `cronjob.concurrencyPolicy` (default `Forbid`) controls `spec.concurrencyPolicy`.

### US-5 — Security-conscious operator runs non-root
**As** a security-conscious operator
**I want** the pod to run as a non-root user by default with a least-privilege security context
**So that** PodSecurity admission's `restricted` profile admits the pod.

**Acceptance criteria:**
- Default `securityContext` on the container sets `runAsNonRoot: true`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities: { drop: ["ALL"] }`, and `seccompProfile: { type: RuntimeDefault }`.
- A default `podSecurityContext` block sets `runAsNonRoot: true` and `seccompProfile: { type: RuntimeDefault }`.
- The user can override or extend either block via `values.yaml`.

### US-6 — Operator scopes the ServiceAccount
**As** an operator with a strict RBAC policy
**I want to** either let the chart create a dedicated `ServiceAccount` or bind to a pre-existing one
**So that** the `CronJob` runs under an identity I control.

**Acceptance criteria:**
- `serviceAccount.create=true` (default) renders a `ServiceAccount` named per `fullname` (or `serviceAccount.name` if set) with optional `serviceAccount.annotations`.
- `serviceAccount.create=false` skips the `ServiceAccount` template entirely and the `CronJob` pod spec's `serviceAccountName:` references `serviceAccount.name` (which the user is then responsible for having pre-created).
- The `CronJob`'s `serviceAccountName:` always renders to the resolved name (a Helm `_helpers.tpl` helper computes this).

### US-7 — CI maintainer pushes a new chart version
**As** a maintainer
**I want** the GHA workflow to publish the chart to GHCR on `master` pushes that bump `Chart.yaml`'s `version`
**So that** a `helm install ... --version X.Y.Z` command works for downstream consumers immediately after merge.

**Acceptance criteria:**
- Pushing a commit to `master` that does NOT change `Chart.yaml`'s `version` and where the version was already published causes the publish step to fail (because `helm push` does not overwrite by default and the workflow MUST NOT pass `--force`).
- Pushing a commit to `master` that bumps `Chart.yaml`'s `version` to a not-yet-published value publishes the chart to `oci://ghcr.io/asnapper/charts/pipeline-status:<version>` and the workflow exits 0.
- A `pull_request` against `master` runs lint + template + dry-run validation only and never logs into GHCR / never pushes.

### US-8 — Maintainer cuts a chart-only release
**As** a maintainer
**I want to** push a `chart-vX.Y.Z` Git tag
**So that** I can publish a chart bump without coupling it to a `master` push.

**Acceptance criteria:**
- A tag matching `chart-v*` triggers the publish workflow on the tagged ref.
- The version published is taken from `Chart.yaml`'s `version` field (which MUST already match `X.Y.Z` for the tag to make sense; the workflow does not parse the tag).
- A tag that points at a commit whose `Chart.yaml` version is already on GHCR fails the publish step (same reason as US-7).

### US-9 — Maintainer runs the chart-static tests locally
**As** a maintainer
**I want to** run `python -m unittest tests.test_helm_chart` locally
**So that** I can catch structural mistakes in the chart without installing Helm.

**Acceptance criteria:**
- `python -m unittest tests.test_helm_chart` exits 0 on a clean checkout.
- The test parses `charts/pipeline-status/Chart.yaml`, `charts/pipeline-status/values.yaml`, and each file under `charts/pipeline-status/templates/*.yaml`.
- For each template, a tiny in-test fixture substitutes `{{ ... }}` expressions with deterministic placeholders (e.g. `{{ .Release.Name }}` → `release`) so that `yaml.safe_load_all(...)` produces parseable documents.
- Assertions cover the requirements in Section 6 (chart metadata, default image reference, CronJob schedule format, non-root security context, ServiceAccount conditional, PVC volume mount conditional, args default, concurrency policy default).

---

## 5. Functional Requirements

Conventions: **MUST** = required for v5-helm; **SHOULD** = strongly recommended; **MAY** = optional/permitted.

### 5.1 Chart structure

- **FR-1 (MUST)** The chart lives at `charts/pipeline-status/` (single directory under repo root).
- **FR-2 (MUST)** `Chart.yaml` MUST set `apiVersion: v2` (Helm 3 native).
- **FR-3 (MUST)** `Chart.yaml` MUST set `name: pipeline-status`, `type: application`, `version: 0.1.0`, `appVersion: "0.1.0"`, `description` (one sentence), `home`, `sources` (list with at least the GitHub repo URL), `maintainers` (list with at least one entry containing `name` and either `email` or `url`), and `kubeVersion: ">=1.26-0"`.
- **FR-4 (MUST)** `appVersion` MUST equal the Python package version sourced from `pyproject.toml` (`0.1.0` at v5-helm time). Future chart bumps that change `appVersion` MUST keep this invariant; the unit test asserts equality.
- **FR-5 (MUST)** The chart MUST contain `charts/pipeline-status/values.yaml`, `charts/pipeline-status/.helmignore`, `charts/pipeline-status/README.md`, and a `charts/pipeline-status/templates/` directory.
- **FR-6 (MUST)** No `requirements.yaml` and no `dependencies:` block in `Chart.yaml`. The chart is self-contained.
- **FR-7 (MUST NOT)** Ship a `values.schema.json` in v5-helm. (Deferred.)

### 5.2 Templates

- **FR-8 (MUST)** `templates/_helpers.tpl` MUST define at least three Go-template helpers in the standard idiom:
  - `pipeline-status.fullname` — release-name-aware fullname (max 63 chars, trimmed of trailing `-`).
  - `pipeline-status.labels` — standard set of labels (`helm.sh/chart`, `app.kubernetes.io/name`, `app.kubernetes.io/instance`, `app.kubernetes.io/version`, `app.kubernetes.io/managed-by`).
  - `pipeline-status.selectorLabels` — `app.kubernetes.io/name` + `app.kubernetes.io/instance`.
  - `pipeline-status.serviceAccountName` — resolves to either the created SA's name or `serviceAccount.name`, mirroring the standard `helm create` idiom.
- **FR-9 (MUST)** `templates/cronjob.yaml` MUST render exactly one `batch/v1 CronJob` resource named `{{ include "pipeline-status.fullname" . }}` with labels from `pipeline-status.labels`.
- **FR-10 (MUST)** The `CronJob.spec.jobTemplate.spec.template.spec` MUST set `restartPolicy: OnFailure` and `serviceAccountName: {{ include "pipeline-status.serviceAccountName" . }}`.
- **FR-11 (MUST)** The container's `image:` MUST render as `{{ .Values.image.registry }}/{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}` (so an unset `image.tag` falls back to `appVersion`).
- **FR-12 (MUST)** The container's `imagePullPolicy:` MUST render from `.Values.image.pullPolicy`.
- **FR-13 (MUST)** The container's `args:` MUST render from `.Values.cronjob.args` (a YAML list). Default `["history"]`.
- **FR-14 (MUST)** `spec.schedule:` MUST render from `.Values.cronjob.schedule`.
- **FR-15 (MUST)** `spec.successfulJobsHistoryLimit:`, `spec.failedJobsHistoryLimit:`, and `spec.concurrencyPolicy:` MUST render from the corresponding `cronjob.*` value keys.
- **FR-16 (MUST)** When `stateVolume.enabled` is `true`, the pod spec MUST include `volumes: [{ name: state, persistentVolumeClaim: { claimName: <stateVolume.claimName> } }]` and the container MUST include `volumeMounts: [{ name: state, mountPath: <stateVolume.mountPath> }]`.
- **FR-17 (MUST)** When `stateVolume.enabled` is `false`, the rendered pod spec MUST omit both the `volumes:` block (or render an empty/absent block — verified by the unit test that `volumes` is either absent or empty) and the container's `volumeMounts:` block.
- **FR-18 (MUST)** The container's `securityContext:` MUST render from `.Values.securityContext` and default to `{ runAsNonRoot: true, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: ["ALL"] }, seccompProfile: { type: RuntimeDefault } }`.
- **FR-19 (MUST)** The pod's `securityContext:` (pod-level) MUST render from `.Values.podSecurityContext` and default to `{ runAsNonRoot: true, seccompProfile: { type: RuntimeDefault } }`.
- **FR-20 (MUST)** The container's `resources:` MUST render from `.Values.resources` (default `{}` — operator opts in).
- **FR-21 (MUST)** The pod spec MUST honour `.Values.nodeSelector`, `.Values.tolerations`, and `.Values.affinity`, each conditionally rendered (omitted entirely when empty).
- **FR-22 (MUST)** `templates/serviceaccount.yaml` MUST render exactly one `ServiceAccount` when `.Values.serviceAccount.create` is `true`, with name `{{ include "pipeline-status.serviceAccountName" . }}`, labels from `pipeline-status.labels`, and `metadata.annotations:` set to `.Values.serviceAccount.annotations` (omitted when empty).
- **FR-23 (MUST)** When `.Values.serviceAccount.create` is `false`, `templates/serviceaccount.yaml` MUST render zero resources (use `{{- if .Values.serviceAccount.create -}}` gating).
- **FR-24 (MUST NOT)** Templates MUST NOT render any `Service`, `Ingress`, `HorizontalPodAutoscaler`, `NetworkPolicy`, `Deployment`, `Role`, `RoleBinding`, `ClusterRole`, `ClusterRoleBinding`, `PodDisruptionBudget`, or `ConfigMap` resource in v5-helm.
- **FR-25 (MUST)** Every rendered Kubernetes manifest MUST carry the standard label set defined by `pipeline-status.labels` on `metadata.labels`.

### 5.3 `values.yaml`

- **FR-26 (MUST)** `values.yaml` MUST be valid YAML with comments documenting every top-level key.
- **FR-27 (MUST)** `values.yaml` MUST expose, at minimum, the following keys with the indicated defaults:

  | Key | Type | Default |
  |---|---|---|
  | `image.registry` | string | `ghcr.io` |
  | `image.repository` | string | `asnapper/master-replicator` |
  | `image.tag` | string | `""` (empty falls back to `.Chart.AppVersion`) |
  | `image.pullPolicy` | string | `IfNotPresent` |
  | `imagePullSecrets` | list | `[]` |
  | `nameOverride` | string | `""` |
  | `fullnameOverride` | string | `""` |
  | `serviceAccount.create` | bool | `true` |
  | `serviceAccount.name` | string | `""` |
  | `serviceAccount.annotations` | map | `{}` |
  | `cronjob.schedule` | string | `"*/5 * * * *"` |
  | `cronjob.args` | list | `["history"]` |
  | `cronjob.successfulJobsHistoryLimit` | int | `3` |
  | `cronjob.failedJobsHistoryLimit` | int | `1` |
  | `cronjob.concurrencyPolicy` | string | `"Forbid"` |
  | `stateVolume.enabled` | bool | `false` |
  | `stateVolume.mountPath` | string | `"/repo"` |
  | `stateVolume.claimName` | string | `""` |
  | `resources` | map | `{}` |
  | `podSecurityContext` | map | `{ runAsNonRoot: true, seccompProfile: { type: RuntimeDefault } }` |
  | `securityContext` | map | `{ runAsNonRoot: true, allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: ["ALL"] }, seccompProfile: { type: RuntimeDefault } }` |
  | `nodeSelector` | map | `{}` |
  | `tolerations` | list | `[]` |
  | `affinity` | map | `{}` |

- **FR-28 (MUST)** Default `image.repository` is `asnapper/master-replicator` so that the rendered image reference becomes `ghcr.io/asnapper/master-replicator:0.1.0` (where `0.1.0` is sourced from `.Chart.AppVersion` because the default `image.tag` is empty). The image is the sibling pipeline's output.
- **FR-29 (SHOULD)** `values.yaml` SHOULD include a one-line comment above each top-level key explaining its purpose.

### 5.4 Chart `README.md`

- **FR-30 (MUST)** `charts/pipeline-status/README.md` MUST document:
  - Installation command (OCI form: `helm install ... oci://ghcr.io/asnapper/charts/pipeline-status --version 0.1.0`).
  - The cross-feature note that the default `image.repository` references a sibling pipeline's image which may not exist yet on first install (pods will `ImagePullBackOff` until it lands).
  - A complete values reference table (one row per top-level key, derived from FR-27).
  - One worked example for each of: install with defaults, install with a custom PVC, install with a custom schedule, install with `serviceAccount.create=false` plus a pre-existing SA name.
  - The supported `kubeVersion` range (`>=1.26-0`).

### 5.5 GitHub Actions workflow

- **FR-31 (MUST)** The workflow file lives at `.github/workflows/helm-publish.yml`.
- **FR-32 (MUST)** Triggers MUST include:
  - `push` to `master` (publishes if version is new).
  - `push` to tags matching `chart-v*` (publishes the chart at the tagged ref).
  - `pull_request` against `master` (validate-only; no login, no publish).
- **FR-33 (MUST)** Top-level `permissions:` MUST be set to `{ contents: read, packages: write }`. No other permissions are granted.
- **FR-34 (MUST)** Action versions MUST be pinned to a major (e.g. `actions/checkout@v4`, `azure/setup-helm@v4`). The workflow MUST NOT use floating tags like `@main` or unpinned action references.
- **FR-35 (MUST)** The job MUST run on `ubuntu-latest`.
- **FR-36 (MUST)** Steps, in order:
  1. `actions/checkout@v4`.
  2. `azure/setup-helm@v4` (Helm ≥ 3.13).
  3. `helm lint charts/pipeline-status/` — MUST exit 0 with zero warnings AND zero errors. Any non-zero warning count fails the step (use `helm lint --strict` to enforce).
  4. `helm template release-name charts/pipeline-status/ > /tmp/rendered.yaml`.
  5. `kubectl apply --dry-run=client -f /tmp/rendered.yaml` (or `kubeconform` if a step swaps in `kubeconform` instead; either is acceptable, but exactly one MUST run).
  6. **Only on `push: master` or `push: tag chart-v*`** (i.e. NOT on `pull_request`):
     - `helm registry login ghcr.io -u ${{ github.actor }} --password-stdin <<< ${{ secrets.GITHUB_TOKEN }}`.
     - `helm package charts/pipeline-status/ --destination /tmp/dist`.
     - `helm push /tmp/dist/pipeline-status-*.tgz oci://ghcr.io/asnapper/charts`.
- **FR-37 (MUST NOT)** Pass `--force` to `helm push`. A duplicate-version push MUST fail the build.
- **FR-38 (MUST)** On `pull_request`, the workflow MUST NOT call `helm registry login` and MUST NOT call `helm push`.
- **FR-39 (MUST)** The workflow MUST set `concurrency: { group: helm-publish-${{ github.ref }}, cancel-in-progress: false }` so concurrent pushes to the same ref serialise.

### 5.6 Tests (`tests/test_helm_chart.py`)

- **FR-40 (MUST)** A new file `tests/test_helm_chart.py` MUST be added. It MUST use `unittest` (consistent with the rest of the test suite) and MUST be runnable via `python -m unittest tests.test_helm_chart` from the repo root.
- **FR-41 (MUST)** The test file MUST NOT invoke `helm` or any external binary. It parses static files with `yaml.safe_load` / `yaml.safe_load_all` only.
- **FR-42 (MUST)** A tiny in-test fixture function MUST convert `{{ ... }}` Go-template expressions into deterministic placeholders before YAML parsing. The fixture MUST handle at minimum: `{{ .Release.Name }}` → `release`, `{{ .Release.Namespace }}` → `default`, `{{ .Chart.AppVersion }}` → the appVersion read from `Chart.yaml`, `{{ include "pipeline-status.fullname" . }}` → `release-pipeline-status`, `{{ include "pipeline-status.serviceAccountName" . }}` → `release-pipeline-status`, and any `{{ .Values.<dot.path> }}` reference → the corresponding default from `values.yaml`. Anything else MAY be replaced with an empty string; the test author is responsible for keeping the fixture small but adequate.
- **FR-43 (MUST)** Tests MUST cover at minimum:
  - `Chart.yaml` has the required keys with the required values (FR-2–FR-4).
  - `appVersion` in `Chart.yaml` equals the `version` in `pyproject.toml`.
  - `values.yaml` parses and contains every key listed in FR-27 with the documented default type.
  - `templates/cronjob.yaml` renders exactly one `CronJob` resource with `apiVersion: batch/v1`.
  - The rendered CronJob's `spec.schedule` equals `cronjob.schedule` default.
  - The rendered CronJob's container `image` references `ghcr.io/asnapper/master-replicator`.
  - The rendered CronJob's container args equal `["history"]`.
  - The rendered CronJob's container `securityContext.runAsNonRoot` is `true`.
  - The rendered CronJob's pod `restartPolicy` is `OnFailure`.
  - With `stateVolume.enabled=true` and a `claimName`, the rendered CronJob has a matching `volumes`/`volumeMounts` pair.
  - With `stateVolume.enabled=false` (the default), the rendered CronJob has no `volumes`/`volumeMounts` related to `state`.
  - `templates/serviceaccount.yaml` renders exactly one `ServiceAccount` when `serviceAccount.create=true` (default).
  - `templates/serviceaccount.yaml` renders zero resources when `serviceAccount.create=false`.

---

## 6. Non-Functional Requirements

- **NFR-1 (Helm lint cleanliness)** `helm lint --strict charts/pipeline-status/` MUST report **0 warnings and 0 errors**. The workflow fails on any non-zero exit.
- **NFR-2 (Template validity)** `helm template charts/pipeline-status/` piped to `kubectl apply --dry-run=client -f -` MUST succeed (exit 0) on a `kubectl` version compatible with k8s 1.26+.
- **NFR-3 (Kubernetes compatibility)** The chart MUST target Kubernetes **>= 1.26**. `Chart.yaml.kubeVersion` enforces this. The `CronJob` `apiVersion: batch/v1` is GA from k8s 1.21+, so the floor 1.26 has comfortable margin.
- **NFR-4 (OCI registry)** Publishing target is `oci://ghcr.io/asnapper/charts/pipeline-status`. No traditional Helm repository / `index.yaml` is created or maintained.
- **NFR-5 (Reproducibility)** All GHA action references are pinned to a major version (`@v4`, etc.). No `@main` or `@latest` references.
- **NFR-6 (Test runtime)** `python -m unittest tests.test_helm_chart` MUST complete in under 5 seconds on commodity CI hardware.
- **NFR-7 (Security — pod hardening)** The pod's default security context MUST satisfy the Kubernetes Pod Security Standards **restricted** profile (non-root, no privilege escalation, read-only rootfs, all caps dropped, RuntimeDefault seccomp).
- **NFR-8 (Security — workflow scope)** Only `helm-publish.yml` requires `packages: write`. No other workflow gains this permission as a side effect of this PR.
- **NFR-9 (Secrets handling)** `GITHUB_TOKEN` is passed to `helm registry login` via `--password-stdin`. The workflow MUST NOT echo the token or pass it on a command line.
- **NFR-10 (Backwards compatibility)** Adding the chart and workflow MUST NOT modify any file under `pipeline_status/` or any existing file under `tests/`. The Python CLI's runtime behaviour is byte-identical to v4.
- **NFR-11 (Static analysis cleanliness)** No `helm lint` warning is suppressed by `# ignored:` comments or `--ignore-rule` flags in CI. If lint complains, fix the template.
- **NFR-12 (Documentation completeness)** `charts/pipeline-status/README.md` MUST cover every key in FR-27. A test step MAY assert that every key appears in the README, but this is not mandatory for v5-helm.
- **NFR-13 (Idempotency of publish)** Re-running the publish workflow on an unchanged commit MUST fail at the `helm push` step (duplicate version), not silently succeed.

---

## 7. Open Questions (with proposed defaults)

These are the decisions the Architect will lock in. Each has a **proposed default** that the PO recommends; the Architect may override.

### OQ-1 — Which validator: `kubectl --dry-run=client` or `kubeconform`?
**Proposed default**: Use `kubectl apply --dry-run=client -f -`. Reasoning: `kubectl` is already available on every GHA `ubuntu-latest` runner; `kubeconform` would add a setup step (download + chmod) without strengthening validation for our small static-rendered manifest set. The CronJob, ServiceAccount, volumes, and labels all parse fine under `kubectl`'s client-side validation. If a follow-up surfaces a real gap, swap in `kubeconform` as a single-line change.

### OQ-2 — Default `cronjob.schedule`?
**Proposed default**: `*/5 * * * *` (every five minutes). Matches the feature request and the user-story narrative. Operators tuning this is a one-line `values.yaml` override; the default is the documented happy-path use case.

### OQ-3 — Default `image.tag`: pin to chart's `appVersion` or use `latest`?
**Proposed default**: `image.tag: ""` (empty string), with `templates/cronjob.yaml` rendering `tag | default .Chart.AppVersion`. This means the default rendered image is `ghcr.io/asnapper/master-replicator:0.1.0` (not `:latest`). Reasoning: pinning to a concrete tag is reproducible and aligns Helm releases with image versions; `latest` is operationally hazardous. The feature-request narrative mentions `:latest` as a fallback, but pinning to `appVersion` is the documented Helm best practice and is just as easy for the operator to override (`--set image.tag=latest`). NOTE: this differs slightly from the orchestrator's stated default (`image.tag: latest`); flagging for the human/architect to confirm.

### OQ-4 — Default `stateVolume.enabled`: `true` or `false`?
**Proposed default**: `false`. Reasoning: a default-`true` requires the operator to provide a `claimName`, otherwise `helm install` fails template validation (or worse, succeeds with an invalid PVC reference). Default-`false` lets a basic `helm install` succeed out of the box; operators who want a real state volume opt in explicitly. The chart `README.md` documents the opt-in clearly.

### OQ-5 — Default `image.pullPolicy`?
**Proposed default**: `IfNotPresent`. Standard Helm idiom; avoids re-pull churn for pinned tags. Operators using `:latest` can override to `Always`.

### OQ-6 — Where does `Chart.yaml.home` point?
**Proposed default**: `https://github.com/asnapper/master-replicator`. Same URL as `sources[0]`. If a docs site appears later, the architect can split them.

### OQ-7 — `kubeVersion` constraint syntax: `">=1.26-0"` or `">=1.26.0"`?
**Proposed default**: `">=1.26-0"` (with the `-0` pre-release suffix). This is the Helm-recommended form that ensures pre-release versions of 1.26 (rare but possible) also match. Documented in Helm's `Chart.yaml` reference. The unit test asserts on the exact string.

### OQ-8 — Should `helm push` accept duplicate versions (`--force`) or reject them?
**Proposed default**: **Reject** (do NOT pass `--force`). The release process is: bump `Chart.yaml.version`, merge, push — duplicate-version pushes are bugs, not features. FR-37 enforces this.

### OQ-9 — Does the workflow run on every PR or only PRs that touch chart files?
**Proposed default**: Run on every PR against `master` (no `paths:` filter). Reasoning: the chart depends transitively on the broader repo (the image, the README, etc.), and a one-line `paths:` filter can quietly miss a relevant change. The validate-only path is fast (< 30s on a warm runner). If runtime becomes a concern, revisit and add `paths: ["charts/**", ".github/workflows/helm-publish.yml"]`.

### OQ-10 — How does the test fixture handle template features not used today (e.g. `range`, `with`, conditionals)?
**Proposed default**: The fixture handles **only** simple `{{ ... }}` substitution. If the chart introduces a `{{- range ... -}}` block, the test fixture is extended at that time. The v5-helm templates SHOULD avoid `range`/`with` for the keys covered by FR-27 (a small chart needs none of them). If a template MUST use them (e.g. for `imagePullSecrets`), the test for that template MAY skip parsing the affected block and assert only the surrounding structure.

### OQ-11 — Should the chart pin the Helm CLI version in the workflow?
**Proposed default**: No explicit pin in v5-helm; use `azure/setup-helm@v4`'s default, which tracks the latest stable Helm 3.x. If a follow-up surfaces a reproducibility issue, pin `version: "3.13.x"` in the `setup-helm` step.

### OQ-12 — Should `tests/test_helm_chart.py` introduce a new third-party dependency (PyYAML)?
**Proposed default**: **Yes**, add `PyYAML` as a test-only dependency. Reasoning: hand-rolling a YAML parser is out of scope, and PyYAML is the de-facto standard. Add it to a new `[project.optional-dependencies]` group `test = ["PyYAML>=6.0"]` in `pyproject.toml` (this is the ONE permitted edit to `pyproject.toml` for this feature). The unit test imports `yaml` and falls back to a clear `unittest.skipUnless` if PyYAML is unavailable. NOTE: This is the only deviation from the "do not modify `pyproject.toml`" guideline; flagging for the architect.

### OQ-13 — `imagePullSecrets`: include the key in `values.yaml` even though we omit private-registry support?
**Proposed default**: Yes, include `imagePullSecrets: []` as a stub. Costs one line; aligns with the standard `helm create` skeleton; lets private-registry users override without a chart change.

### OQ-14 — Default `cronjob.concurrencyPolicy`?
**Proposed default**: `Forbid`. Reasoning: `pipeline-status history` is fast (sub-second on a small state dir) and idempotent, but running two at once on the same PVC during a backlog could produce noisy log spam. `Forbid` is the safer default; operators with deep retention preferences can switch to `Replace`.

### OQ-15 — Should the chart bundle a default `priorityClassName`?
**Proposed default**: No. Operators with cluster-wide PriorityClasses can override via a future `pod.priorityClassName` value; for v5-helm we omit the key entirely. Out of scope.

---

## 8. Assumptions

- **A-1** A `ghcr.io/asnapper/master-replicator` Docker image will exist by the time an operator runs `helm install ps oci://...`. Until then, the chart installs cleanly and pods show `ImagePullBackOff`. This is the documented contract with Feature A.
- **A-2** The repository's GitHub Actions workflows run on `ubuntu-latest` runners with internet access to `ghcr.io`. `actions/checkout@v4` and `azure/setup-helm@v4` are reachable.
- **A-3** The repository's default `GITHUB_TOKEN` has `packages: write` available when granted in the workflow's `permissions:` block. (This is the GHA default once the repo is configured for GHCR; no org-level secrets needed.)
- **A-4** PyYAML 6.x is acceptable as a test-time dependency (Open Question OQ-12). If the architect rejects this, the unit test falls back to a hand-rolled minimal YAML parser keyed only to the subset of YAML the chart emits — feasible but tedious.
- **A-5** Helm 3.13+ is the target. `azure/setup-helm@v4` installs the latest stable 3.x by default. OCI publishing has been GA since Helm 3.8.
- **A-6** The Python package version in `pyproject.toml` is the canonical source of truth for `Chart.yaml.appVersion`. Manual sync at chart-bump time is acceptable; a CI lint asserting equality (FR-43) catches drift.
- **A-7** The `kubeVersion: ">=1.26-0"` floor is acceptable to the project. v5-helm does not need to support k8s 1.21–1.25.
- **A-8** Operators using `stateVolume.enabled=true` are responsible for pre-creating the `PersistentVolumeClaim`. The chart does NOT render a `PersistentVolumeClaim` template (out of scope for v5-helm; a future version could add a gated PVC template).
- **A-9** The orchestrator's stated default `image.tag: latest` is interpreted as "the default registered tag is whatever the image-publish pipeline pushes as the moving tag." We pin to `appVersion` instead (OQ-3) for reproducibility; if the architect prefers `latest`, the change is a one-line `values.yaml` and one-line template edit.
- **A-10** The sibling Feature A pipeline produces the image at `ghcr.io/asnapper/master-replicator`. The chart's image reference is exactly that.
- **A-11** No existing files in `pipeline_status/`, no existing tests, and (apart from OQ-12's possible `[project.optional-dependencies]` addition) no existing `pyproject.toml` content are modified.
- **A-12** All file paths in this document are POSIX paths relative to the repo root unless explicitly absolute (`/home/matt/src/master-replicator/...`).

---

## 9. Acceptance Summary (gate-able checklist)

The Architect / PM may treat this as the GATE 1 closeout checklist:

- [ ] `charts/pipeline-status/Chart.yaml` exists with the required fields (FR-2–FR-4).
- [ ] `charts/pipeline-status/values.yaml` exposes every key in FR-27 with the documented default.
- [ ] `charts/pipeline-status/templates/_helpers.tpl`, `cronjob.yaml`, `serviceaccount.yaml` exist.
- [ ] `charts/pipeline-status/README.md` documents install + a complete values table + four worked examples (FR-30).
- [ ] `.github/workflows/helm-publish.yml` exists with the trigger / step / permission structure in FR-31–FR-39.
- [ ] `tests/test_helm_chart.py` exists, runs in < 5s with `python -m unittest`, and covers the assertions listed in FR-43.
- [ ] `helm lint --strict charts/pipeline-status/` reports 0 warnings and 0 errors in CI.
- [ ] `helm template charts/pipeline-status/` produces YAML that `kubectl apply --dry-run=client -f -` accepts in CI.
- [ ] No file under `pipeline_status/` was modified.
- [ ] `pyproject.toml` was either unchanged or (per OQ-12) gained at most a `test = ["PyYAML>=6.0"]` optional-dependency group.
