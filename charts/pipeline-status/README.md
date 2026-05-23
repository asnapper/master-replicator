# pipeline-status Helm chart

A Helm chart that runs the `pipeline-status` CLI as a Kubernetes `CronJob`,
inspecting `.claude/state/` from a mounted PVC at a fixed schedule. The chart
pairs with the sibling Docker image published by Feature A; the image and the
chart share a strict UID/GID `65532` non-root contract (see "Cross-feature
contract" below).

- **Chart name**: `pipeline-status`
- **Chart version**: `0.1.0`
- **App version**: `0.1.0`
- **Min Kubernetes**: `>=1.26-0`
- **Distribution**: OCI (`oci://ghcr.io/asnapper/charts/pipeline-status`)

---

## Cross-feature contract

The chart's default image is the multi-arch image published by the sibling
Docker pipeline at `ghcr.io/asnapper/master-replicator`. Both the image and
the chart's `podSecurityContext` / `containerSecurityContext` pin the runtime
user to UID/GID `65532`. If the Docker image ever changes its non-root UID,
the chart's `runAsUser` / `runAsGroup` defaults bump in lockstep via a
follow-up PR. Do not override `runAsUser` on a cluster that enforces the
`restricted` Pod Security Standard unless you know what you're doing.

The chart installs cleanly even before the image is published; pods will
remain in `ImagePullBackOff` until the matching image tag appears on GHCR.

---

## Install

The chart is distributed only as an OCI artifact. There is no
`helm repo add` step.

```bash
helm install ps oci://ghcr.io/asnapper/charts/pipeline-status --version 0.1.0
```

Pin a specific chart version in production deployments; `--version` is not
optional for OCI installs in CI.

### Install with a state volume (recommended)

The CronJob exits `2` on every run as long as `.claude/state/` is not
mounted. Pre-create a `PersistentVolumeClaim` carrying the state directory
and reference it by name:

```bash
helm install ps oci://ghcr.io/asnapper/charts/pipeline-status \
    --version 0.1.0 \
    --set stateVolume.enabled=true \
    --set stateVolume.claimName=pipeline-state \
    --set stateVolume.mountPath=/repo
```

### Install with a custom image tag

By default `image.tag` is empty, which resolves to `.Chart.AppVersion`
(`0.1.0`). To pin a different tag (for example a `sha-<short>` immutable
tag produced by the Docker workflow):

```bash
helm install ps oci://ghcr.io/asnapper/charts/pipeline-status \
    --version 0.1.0 \
    --set image.tag=sha-abc1234
```

### Install with the defaults only

The chart installs out of the box without overrides; the CronJob will run on
the default schedule and exit `2` until you wire up a state volume:

```bash
helm install ps oci://ghcr.io/asnapper/charts/pipeline-status --version 0.1.0
```

---

## Uninstall

```bash
helm uninstall ps
```

`helm uninstall` removes the `CronJob` and the optional `ServiceAccount`
created by the chart. It never touches the `PersistentVolumeClaim` referenced
by `stateVolume.claimName` — that PVC is owned by the operator and is left
intact so on-disk state survives chart lifecycle events.

---

## Values reference

| Key | Default | Description |
|---|---|---|
| `image.registry` | `ghcr.io` | OCI registry hostname. Set to `""` to skip the registry prefix. |
| `image.repository` | `asnapper/master-replicator` | Repository portion of the image reference. |
| `image.tag` | `""` | Image tag. Empty string resolves to `.Chart.AppVersion`. |
| `image.pullPolicy` | `IfNotPresent` | Container `imagePullPolicy`. |
| `imagePullSecrets` | `[]` | List of `imagePullSecrets` references for private registries. |
| `nameOverride` | `""` | Override `pipeline-status.name` template output. |
| `fullnameOverride` | `""` | Override `pipeline-status.fullname` template output. |
| `serviceAccount.create` | `true` | If `true`, render a `ServiceAccount` resource. |
| `serviceAccount.name` | `""` | Override the ServiceAccount name; empty = derived from fullname. |
| `serviceAccount.annotations` | `{}` | Annotations attached to the ServiceAccount. |
| `cronjob.schedule` | `"*/5 * * * *"` | Cron expression — every 5 minutes by default. |
| `cronjob.args` | `["history"]` | Arguments passed to the `pipeline-status` CLI. |
| `cronjob.successfulJobsHistoryLimit` | `3` | Successful-job history retained by the CronJob controller. |
| `cronjob.failedJobsHistoryLimit` | `1` | Failed-job history retained by the CronJob controller. |
| `cronjob.concurrencyPolicy` | `Forbid` | Reject overlapping runs (`Allow` / `Forbid` / `Replace`). |
| `cronjob.startingDeadlineSeconds` | `60` | Max scheduling delay before a missed run is skipped. |
| `cronjob.backoffLimit` | `0` | Number of retries before the Job is marked failed. |
| `cronjob.restartPolicy` | `OnFailure` | Pod `restartPolicy` (`OnFailure` or `Never`). |
| `stateVolume.enabled` | `false` | Mount a PVC at `mountPath` containing `.claude/state/`. |
| `stateVolume.mountPath` | `/repo` | Container path where the state PVC is mounted. |
| `stateVolume.claimName` | `""` | Name of the pre-existing PVC to bind. Required when `enabled: true`. |
| `podSecurityContext.runAsNonRoot` | `true` | Enforce non-root pod execution. |
| `podSecurityContext.runAsUser` | `65532` | Pod UID. Must match the Docker image's non-root user. |
| `podSecurityContext.runAsGroup` | `65532` | Pod GID. |
| `podSecurityContext.fsGroup` | `65532` | Filesystem group applied to mounted volumes. |
| `podSecurityContext.seccompProfile.type` | `RuntimeDefault` | Seccomp profile for the pod. |
| `containerSecurityContext.allowPrivilegeEscalation` | `false` | Block privilege escalation. |
| `containerSecurityContext.readOnlyRootFilesystem` | `true` | Read-only root FS for the container. |
| `containerSecurityContext.runAsNonRoot` | `true` | Container-level non-root enforcement. |
| `containerSecurityContext.runAsUser` | `65532` | Container UID. |
| `containerSecurityContext.runAsGroup` | `65532` | Container GID. |
| `containerSecurityContext.capabilities.drop` | `["ALL"]` | Drop all Linux capabilities. |
| `resources.limits.cpu` | `200m` | CPU limit. |
| `resources.limits.memory` | `128Mi` | Memory limit. |
| `resources.requests.cpu` | `50m` | CPU request. |
| `resources.requests.memory` | `64Mi` | Memory request. |
| `nodeSelector` | `{}` | Pod `nodeSelector`. |
| `tolerations` | `[]` | Pod tolerations. |
| `affinity` | `{}` | Pod affinity rules. |
| `env` | `[]` | Extra environment variables for the container. |
| `annotations` | `{}` | Extra annotations applied to the CronJob. |
| `labels` | `{}` | Extra labels applied to the CronJob. |

---

## Examples

### Default install

Quickest possible incantation. The CronJob will fire every 5 minutes and
exit `2` immediately — useful only as a smoke test that the chart renders
and applies on your cluster.

```bash
helm install ps oci://ghcr.io/asnapper/charts/pipeline-status --version 0.1.0
```

### Install with `stateVolume`

Production-shaped install. Mount your `.claude/state/` PVC and let the
CronJob inspect it on the default 5-minute cadence.

```bash
helm install ps oci://ghcr.io/asnapper/charts/pipeline-status \
    --version 0.1.0 \
    --set stateVolume.enabled=true \
    --set stateVolume.claimName=pipeline-state
```

### Install with a custom image tag

Pin to an immutable `sha-<short>` tag for reproducible deployments.

```bash
helm install ps oci://ghcr.io/asnapper/charts/pipeline-status \
    --version 0.1.0 \
    --set image.tag=sha-abc1234 \
    --set stateVolume.enabled=true \
    --set stateVolume.claimName=pipeline-state
```

### Install via a values file

```bash
cat > my-values.yaml <<'YAML'
image:
  tag: sha-abc1234
cronjob:
  schedule: "0 * * * *"   # hourly instead of every 5 minutes
stateVolume:
  enabled: true
  claimName: pipeline-state
YAML

helm install ps oci://ghcr.io/asnapper/charts/pipeline-status \
    --version 0.1.0 \
    -f my-values.yaml
```

---

## Inspecting a release

```bash
kubectl get cronjob ps-pipeline-status
kubectl get jobs --selector app.kubernetes.io/instance=ps
kubectl logs --selector app.kubernetes.io/instance=ps --tail=200
```

The chart emits the same commands tailored to your release name in the
post-install `NOTES.txt`.
