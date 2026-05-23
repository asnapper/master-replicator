# Feature Request

## Feature
Ship a **Helm chart** for the `pipeline-status` CLI, deployable to a Kubernetes cluster, and publish it as an OCI artifact to **GitHub Container Registry** (`oci://ghcr.io/asnapper/charts/pipeline-status`).

The chart at `charts/pipeline-status/` MUST deliver:
- **`Chart.yaml`** with `name: pipeline-status`, `version: 0.1.0` (semver, bumped per release), `appVersion: 0.1.0` (matches the Python package `__version__`), `description`, `type: application`, `home`, `sources`, `maintainers`, `kubeVersion: ">=1.26-0"`.
- **`values.yaml`** with overridable defaults: image registry/repository/tag/pullPolicy, CronJob `schedule` (default `*/5 * * * *`), `args` (default `["history"]`), `successfulJobsHistoryLimit`, `failedJobsHistoryLimit`, `concurrencyPolicy: Forbid`, a `stateVolume` block (`{ enabled, mountPath, claimName }`) so the user supplies a PVC containing `.claude/state/`, plus `resources`, `securityContext`, `nodeSelector`, `tolerations`, `affinity`.
- **`templates/`**:
  - `_helpers.tpl` — fullname, labels, selector labels (boilerplate Helm conventions).
  - `cronjob.yaml` — a `batch/v1 CronJob` running the `ghcr.io/asnapper/master-replicator` image (this is the image shipped by the sibling Docker pipeline). Mounts the user-supplied `stateVolume.claimName` PVC at `stateVolume.mountPath` (default `/repo`). Sets `restartPolicy: OnFailure`, `runAsNonRoot: true`.
  - `serviceaccount.yaml` — a dedicated `ServiceAccount`, gated by `serviceAccount.create` (default `true`).
- **`README.md`** inside the chart directory documenting install, values, examples (e.g. `helm install ps oci://ghcr.io/asnapper/charts/pipeline-status --version 0.1.0 -f values.yaml`).

The CI/CD workflow MUST:
- Live at `.github/workflows/helm-publish.yml`.
- Trigger on push to `master` (publishes the current `Chart.yaml` version to GHCR if the version is new — uses `helm push` with `--force` disabled so a duplicate version fails the build).
- Trigger on tagged releases of the form `chart-v*` for chart-only version bumps.
- Use `azure/setup-helm@v4` (or equivalent) to install `helm`.
- Use `helm registry login ghcr.io -u ${{ github.actor }} --password-stdin` with `${{ secrets.GITHUB_TOKEN }}`.
- Run `helm lint charts/pipeline-status/` BEFORE packaging.
- Run `helm template charts/pipeline-status/` and validate the rendered YAML with `kubectl apply --dry-run=client -f -` (or `kubeconform` if available).
- On `pull_request:`, run lint + template + validate ONLY (no push).
- On `push: master`, additionally run `helm package` and `helm push oci://ghcr.io/asnapper/charts`.
- Declare `permissions: contents: read, packages: write`.

## Context
v1–v4 delivered the `pipeline-status` CLI. A sibling pipeline is concurrently delivering the Docker image at `ghcr.io/asnapper/master-replicator`. This Helm chart is the natural deployment unit for k8s workloads that want to monitor a pipeline state directory periodically.

Typical use case: a GitOps-style ops team mounts a shared `.claude/state/` directory (e.g., a Persistent Volume) into a `pipeline-status history` CronJob that runs every 5 minutes and logs the output. CI consumers can `kubectl logs` the most recent Job to gate on stage transitions.

## Constraints
- **Helm v3 only** (`apiVersion: v2`).
- **Chart-only — no umbrella subcharts**. No `requirements.yaml` / `dependencies:` block.
- **No `values.schema.json` in v5** — defer to v6 if requested.
- **OCI registry only** — no traditional `helm repo add` index. The chart is pushed and pulled as an OCI artifact.
- **Reproducibility**: the GHA workflow pins all action versions.
- **Cross-feature dependency**: the chart's default `image.repository: ghcr.io/asnapper/master-replicator` and `image.tag: latest` references the sibling pipeline's image. The chart MUST work even before the image exists on GHCR (helm install will succeed; the pods will `ImagePullBackOff` until the image lands — that's acceptable).
- **`helm lint` MUST pass** with zero warnings.
- **`helm template` MUST render** to valid Kubernetes YAML (parseable by `kubectl apply --dry-run=client`).
- **Tests** under `tests/test_helm_chart.py` (Python `unittest`) parse `Chart.yaml`, `values.yaml`, and each `templates/*.yaml` (Go-template stripped via a fixed-substitution fixture) and assert: chart metadata, default image reference, CronJob schedule format, non-root securityContext, ServiceAccount conditional, PVC volume mount. No real `helm` invocation is required for tests; the test verifies the static structure.

## Out of Scope
- A Kubernetes `Service`, `Ingress`, `HorizontalPodAutoscaler`, or `NetworkPolicy` — `pipeline-status` is not a long-running network service in v5.
- An `RBAC` / `Role` / `RoleBinding` template — the CronJob only reads a mounted volume; no in-cluster API access needed.
- A `Deployment` template — only `CronJob` for v5 (the natural fit for a periodic-check CLI).
- A `chart-testing` (ct) integration in CI — `helm lint` + `helm template` validation is enough for v5.
- Pre-built `values.yaml` examples for cloud providers (EKS, GKE, AKS) — generic `values.yaml` only.
- Multi-architecture chart variants — one chart, one OCI artifact.
- Modifying the Dockerfile (sibling pipeline) or any `pipeline_status/` Python code.
- Helm test (`helm test`) hooks — defer to v6.
