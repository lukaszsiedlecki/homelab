# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A homelab Kubernetes GitOps repository. ArgoCD watches this repo and automatically syncs changes to a Talos Linux cluster. There are no build steps — changes take effect when pushed to `main` and ArgoCD reconciles.

## Applying changes manually

```bash
# Apply a single manifest directly (bypassing ArgoCD)
kubectl apply -f apps/linkding/03-deployment.yaml

# apps/shortliner is a local Helm chart (see Architecture below) — apply it with:
helm template shortliner apps/shortliner | kubectl apply -f -

# Force ArgoCD to sync immediately instead of waiting
argocd app sync <app-name>

# Check sync status
argocd app get <app-name>
```

## Secrets workflow

All secrets use **Bitnami Sealed Secrets** (`bitnami.com/v1alpha1/SealedSecret`). Never commit plaintext secrets. To create or update a sealed secret:

```bash
# Seal a secret (requires kubeseal and cluster access)
kubectl create secret generic my-secret --from-literal=key=value --dry-run=client -o yaml \
  | kubeseal --format yaml > apps/myapp/01-sealed-secret.yaml
```

Sealed secrets are cluster-specific — they can only be decrypted by the Sealed Secrets controller running in the cluster.

## Architecture

```
argocd/          ArgoCD Application CRDs — each file points ArgoCD at a path in apps/
apps/            Per-application raw Kubernetes manifests (numbered for apply order),
                 except shortliner/ which is a local Helm chart (ArgoCD auto-detects
                 the source type from the presence of Chart.yaml — no extra config needed)
  linkding/      Bookmarks manager
  mealie/        Recipe manager (uses external Postgres at 192.168.0.78:5432)
  shortliner/    URL shortener + analytics + payment + frontend; one Helm chart,
                 one entry per service in values.yaml `services:` list. Image tags are
                 bumped in values.yaml by .github/workflows/deploy-image.yml (yq, selects
                 by service name) instead of editing a per-service deployment file.
  monitoring/    NOT a raw-manifest app: values/ holds Helm values for the upstream
                 kube-prometheus-stack, loki and alloy charts (referenced via ArgoCD
                 multi-source `$values`), manifests/ holds the basic-auth SealedSecret
                 and the prometheus.local / loki.local Ingresses. See "Monitoring" below.
infra/           Infrastructure configs installed separately (not managed by an ArgoCD app)
  longhorn/      Helm values for Longhorn distributed storage
  metallb/       MetalLB IP pool config (192.168.20.10–192.168.20.100)
  keycloak/      Helm values + realm import for Keycloak (shortliner auth/authz)
  grafana/       Datasource provisioning + dashboard JSON for the Grafana LXC (manual install)
kafka/           Strimzi-based Kafka cluster (not under argocd/ — applied manually)
```

## Related repo: Proxmox IaC

The Proxmox hypervisor layer *underneath* this cluster (the Talos VMs, LXC
containers, storage, backups) is managed with OpenTofu in a separate repo:
`~/repo/proxmox`.

## Key infrastructure

- **ArgoCD**: GitOps engine — all apps under `argocd/` sync automatically (`prune: true`, `selfHeal: true`)
- **Longhorn**: Default storage; `longhorn-single` StorageClass (1 replica) used for Kafka to save disk
- **MetalLB**: Provides LoadBalancer IPs from `192.168.20.10–192.168.20.100`; nginx-ingress gets `192.168.20.100`
- **nginx-ingress**: Single ingress controller; apps use `.local` hostnames (e.g. `linkding.local`, `mealie.local`)
- **Sealed Secrets**: All secret files are `SealedSecret` objects; the `template.metadata` section defines the resulting `Secret` name and namespace

## Conventions for adding a new app

1. Create `apps/<appname>/` with numbered manifests: `01-sealed-secret.yaml`, `02-pvc.yaml`, `03-deployment.yaml`, `04-service.yaml`, `05-ingress.yaml`
2. Add `argocd/<appname>.yaml` — an ArgoCD `Application` pointing `path: apps/<appname>` with `namespace: homelab`
3. Use `strategy.type: Recreate` for stateful apps with a single PVC
4. Apply pod security context (`runAsNonRoot: true`, `seccompProfile: RuntimeDefault`, `fsGroup/runAsUser/runAsGroup: 1000`) and drop all capabilities

## Kafka specifics

Kafka is managed by the **Strimzi operator** (`kafka.strimzi.io/v1`) and lives in the `kafka` namespace. The cluster uses KRaft mode (no ZooKeeper). External access uses fixed MetalLB IPs: bootstrap at `192.168.20.11`, brokers at `.13` and `.14`. The `kafka/` directory is applied manually, not via ArgoCD.

## Keycloak specifics

Keycloak is shortliner's identity provider (OIDC), deployed as raw manifests in `infra/keycloak/` using the official `quay.io/keycloak/keycloak` image directly — not a Helm chart (Bitnami's free Keycloak image tags were retired in 2025), not managed by ArgoCD, same manual-apply pattern as Kafka. Uses external Postgres (`192.168.0.78:5432`, DB `keycloak`), exposed at `http://keycloak.local` through the shared nginx-ingress controller (no dedicated MetalLB IP, no TLS anywhere in this cluster — `KC_HOSTNAME` is set explicitly with an `http://` scheme in `04-deployment.yaml` rather than derived, since guessing from `KC_PROXY_HEADERS` defaults to `https`). Realm/client config lives as code in `infra/keycloak/realm-shortliner.json`, mounted via ConfigMap and imported on every startup with `--import-realm` (idempotent). Shortliner is being refactored to put a gateway in front of the backend services — the frontend will only talk to the gateway, not to Keycloak or the backends directly — so OIDC env vars are deliberately **not** wired into `apps/shortliner/values.yaml` yet; that wiring depends on where the gateway settles the auth boundary.

## Monitoring specifics

Metrics and logs for the cluster live in the `monitoring` namespace, managed by three ArgoCD apps (`argocd/monitoring-*.yaml`), each pinning an upstream chart version:

- **kube-prometheus-stack** (`fullnameOverride: kps`) — Prometheus (15d / 20Gi on `longhorn-single`), operator, kube-state-metrics, node-exporter. Grafana and Alertmanager are disabled. Controller-manager/scheduler/etcd/kube-proxy scraping is disabled because Talos binds their metrics to 127.0.0.1. Prometheus selects ServiceMonitors/PodMonitors from **all namespaces without a release label**. This app also creates the namespace with `pod-security.kubernetes.io/enforce: privileged` (node-exporter needs host access, Talos enforces `baseline`), and needs `ServerSideApply=true` for the operator CRDs.
- **loki** — single-binary, filesystem storage (7d / 20Gi `longhorn-single`), `auth_enabled: false`, no gateway/caches.
- **alloy** — single-replica Deployment that tails pod logs through the Kubernetes API (no hostPath), for namespaces listed in `apps/monitoring/values/alloy.yaml`, and promotes the ECS `log.level` to a `level` label.

Grafana is **not** in the cluster: it is an LXC on Proxmox (`192.168.0.124`, managed in `~/repo/proxmox`). It queries `http://prometheus.local` and `http://loki.local` (nginx basic auth, user `grafana`, password in Secret `monitoring/monitoring-basic-auth` key `password`). Datasource provisioning and dashboards are versioned in `infra/grafana/` with fixed datasource UIDs `prometheus-k8s` / `loki-k8s`. A separate Prometheus LXC (`192.168.0.25`) only scrapes the Proxmox host's node_exporter.

Shortliner backends expose actuator on a dedicated management port (`managementPort: 9090` in `values.yaml` → `MANAGEMENT_SERVER_PORT` env), **not** on the app port, because the frontend proxies `/api/<service>/*` to the app port and would otherwise expose `/actuator/**` publicly. Probes and the ServiceMonitor (`metrics.enabled`) use that port. Logs are ECS JSON via `LOGGING_STRUCTURED_FORMAT_CONSOLE=ecs`. Because the chart contains `ServiceMonitor` objects, kube-prometheus-stack (its CRDs) must be installed before the shortliner app can sync on a fresh cluster.
