# homelab

Personal Kubernetes homelab running on [Talos Linux](https://www.talos.dev/), managed with GitOps via ArgoCD. Pushing to `main` is enough to deploy — ArgoCD reconciles automatically.

## Cluster

| Component | Role |
|-----------|------|
| **Talos Linux** | Immutable OS on all nodes |
| **ArgoCD** | GitOps engine — watches this repo, auto-syncs with `prune` and `selfHeal` |
| **Longhorn** | Distributed block storage; default StorageClass is `longhorn-single` (1 replica) |
| **MetalLB** | LoadBalancer IPs from `192.168.20.10–192.168.20.100` (L2 mode) |
| **nginx-ingress** | Single ingress controller at `192.168.20.100` |
| **Sealed Secrets** | All secrets are encrypted `SealedSecret` objects — safe to commit |

## Applications

Apps live under `apps/` and are registered in `argocd/`. ArgoCD syncs each app's directory to the `homelab` namespace.

| App | Description | URL |
|-----|-------------|-----|
| **linkding** | Bookmark manager | http://linkding.local |
| **mealie** | Recipe manager (uses external Postgres at `192.168.0.78:5432`) | http://mealie.local |

> `.local` hostnames resolve via your local DNS or `/etc/hosts` pointing to `192.168.20.100`.

## Longhorn

Distributed block storage, installed manually via Helm from `infra/longhorn/values.yaml` — not managed by ArgoCD.

| Component | Details |
|-----------|---------|
| **Chart version** | 1.12.0 |
| **Namespace** | `longhorn-system` |
| **Default StorageClass** | `longhorn-single` (1 replica — chosen to save disk; a stuck-forever attach on this single-replica setup caused the 2026-07-18 NVMe-saturation incident, see the homelab post-mortem in Obsidian) |

```bash
helm repo add longhorn https://charts.longhorn.io  # if not already added
helm repo update longhorn
helm upgrade --install longhorn longhorn/longhorn -n longhorn-system --create-namespace \
  --version 1.12.0 -f infra/longhorn/values.yaml
```

To upgrade: check the [release notes](https://github.com/longhorn/longhorn/releases) for breaking changes affecting this cluster's config (data engine version, frontend type, backing images), bump `--version` in the command above and in this table, then re-run it.

## Kafka

Strimzi-based Kafka cluster in KRaft mode (no ZooKeeper), applied manually from `kafka/`. Not managed by ArgoCD.

| Component | Details |
|-----------|---------|
| **Broker nodes** | 2 × dual-role (controller + broker), Kafka 4.2.0 |
| **Storage** | 10 Gi each, `longhorn-single` StorageClass |
| **Internal bootstrap** | `my-cluster-kafka-bootstrap:9092` (plaintext) |
| **External bootstrap** | `192.168.20.11:9094` |
| **Broker IPs** | broker-0 → `192.168.20.13`, broker-1 → `192.168.20.14` |
| **Kafka UI** | http://192.168.20.15 |

```bash
# Apply Kafka manifests
kubectl apply -f kafka/longhorn-single.yaml
kubectl apply -f kafka/kafka-two-nodes.yaml
kubectl apply -f kafka/kafka-ui.yaml
```

## Repository layout

```
argocd/          ArgoCD Application CRDs — each file registers one app
apps/            Per-application Kubernetes manifests (numbered for apply order)
  linkding/      Bookmark manager
  mealie/        Recipe manager
infra/           Infrastructure configs applied outside ArgoCD
  longhorn/      Helm values for Longhorn
  metallb/       MetalLB IP pool config
kafka/           Strimzi Kafka cluster (applied manually)
```

## Secrets

All secrets use **Bitnami Sealed Secrets**. Never commit plaintext secrets. To create or rotate a secret:

```bash
kubectl create secret generic my-secret --from-literal=key=value --dry-run=client -o yaml \
  | kubeseal --format yaml > apps/myapp/01-sealed-secret.yaml
```

Sealed secrets are cluster-specific — only the Sealed Secrets controller in this cluster can decrypt them.

## Adding a new app

1. Create `apps/<name>/` with numbered manifests: `01-sealed-secret.yaml`, `02-pvc.yaml`, `03-deployment.yaml`, `04-service.yaml`, `05-ingress.yaml`
2. Add `argocd/<name>.yaml` pointing `path: apps/<name>` at `namespace: homelab`
3. Use `strategy.type: Recreate` for stateful apps with a single PVC
4. Apply pod security: `runAsNonRoot: true`, `seccompProfile: RuntimeDefault`, drop all capabilities
