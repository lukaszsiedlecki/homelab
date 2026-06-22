# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A homelab Kubernetes GitOps repository. ArgoCD watches this repo and automatically syncs changes to a Talos Linux cluster. There are no build steps — changes take effect when pushed to `main` and ArgoCD reconciles.

## Applying changes manually

```bash
# Apply a single manifest directly (bypassing ArgoCD)
kubectl apply -f apps/linkding/03-deployment.yaml

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
apps/            Per-application raw Kubernetes manifests (numbered for apply order)
  linkding/      Bookmarks manager
  mealie/        Recipe manager (uses external Postgres at 192.168.0.78:5432)
infra/           Infrastructure configs installed separately (not managed by an ArgoCD app)
  longhorn/      Helm values for Longhorn distributed storage
  metallb/       MetalLB IP pool config (192.168.20.10–192.168.20.100)
kafka/           Strimzi-based Kafka cluster (not under argocd/ — applied manually)
```

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
