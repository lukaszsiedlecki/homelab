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
infra/           Infrastructure configs installed separately (not managed by an ArgoCD app)
  longhorn/      Helm values for Longhorn distributed storage
  metallb/       MetalLB IP pool config (192.168.20.10–192.168.20.100)
  keycloak/      Helm values + realm import for Keycloak (shortliner auth/authz)
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
