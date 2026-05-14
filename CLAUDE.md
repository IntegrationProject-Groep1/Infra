# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Does

This is the central infrastructure repository for **ShiftFestival**. It contains the Kubernetes manifests, Kustomize layers, GitHub Actions workflows, and support scripts used to operate the platform.

## Repository Structure

```
base/               Main manifests — namespace set to shift-festival
  setup/            Secrets template, storage, configmaps
  core/             RabbitMQ, PostgreSQL, Cloudflared, Dashboard
  team-frontend/    Drupal + MariaDB + Nginx proxy
  team-kassa/       Odoo + PostgreSQL + Nginx proxy + integration sidecar
  team-facturatie/  FossBilling + MariaDB + Nginx proxy
  integrations/     CRM, Planning, Identity Service
  monitoring/       ELK stack + monitoring agent

argocd/             # ArgoCD installation manifests + Application CRDs
  applications/     prod-app.yaml (points ArgoCD at root)
  image-updater/    ArgoCD Image Updater
scripts/            Bootstrap + notification scripts

docs/               Architecture and process documentation
```

## Common Commands

```bash
# Render manifests locally
kubectl kustomize .

# Validate without applying
kubectl apply -k . --dry-run=client

# Lint YAML
find . -type f \( -name "*.yml" -o -name "*.yaml" \) -not -path "./.git/*" -print0 | xargs -0 yamllint

# Lint bash scripts
shellcheck scripts/*.sh
```

## Architecture

The stack runs on Kubernetes and is composed of:

**Core Infrastructure:**
- **RabbitMQ** — Central async message broker; team services communicate through team-prefixed queues (for example `kassa.orders` and `crm.customer.created`).
- **PostgreSQL** — Shared database for the identity service and other shared workloads.
- **ELK Stack** (Elasticsearch + Logstash + Kibana) — Centralized logging and observability.
- **Cloudflared** — Secure external access tunnel for selected services. Routes are managed in the Cloudflare Zero Trust dashboard (token-based, no local config file).
- **ArgoCD** — GitOps controller installed in the `argocd` namespace. Watches the Git repo and auto-syncs changes to the cluster. Exposed at `argocd.desiderius.me` via Cloudflare Tunnel.
- **ArgoCD Image Updater** — Polls GHCR for new image tags and writes back the updated tag to Git, triggering an ArgoCD sync.

**Team Services:**
- Frontend (Drupal, ports 30020–30029)
- Facturatie (FossBilling, 30010–30019)
- Kassa (Odoo, 30030–30039)
- CRM (Salesforce receiver, 30040–30049)
- Planning (Office 365 integration, 30050–30059)
- Identity (UUID service, 30070–30100)

Most team workloads follow the pattern: application container + heartbeat sidecar, with an Nginx proxy when public access is required.

**Namespaces:**
- `shift-festival` — main application namespace
- `argocd` — ArgoCD controller and Image Updater

## CI/CD Pipeline

**CI (runs on every push/PR):**
1. Render with `kubectl kustomize .`.
2. Lint all YAML files with `yamllint`.
3. Lint bash scripts with `shellcheck`.
4. Scan git history for secrets with Gitleaks.
5. Scan Kubernetes manifests with Trivy in `config` mode.

**Deploy — GitOps via ArgoCD (primary mechanism):**
ArgoCD watches the Git repo directly and auto-syncs on every commit to the main branch.

**Image updates:**
ArgoCD Image Updater polls GHCR every 1 minute. When a new tag is detected, it commits the new tag to Git (in `base/kustomization.yaml`), which triggers an ArgoCD sync. The per-team pipelines/deploy.yml build pipelines are unchanged.

**Secrets Bootstrap:**
Secrets are not managed by kustomize — ArgoCD does not have filesystem access to `.env` files. Run once on the VM:
```bash
./scripts/create-secret.sh setup/.env shift-festival
```
Re-run whenever `setup/.env` changes.

## Rollback and Recovery

- **Automatic Rollback**: Managed via **Argo Rollouts**. If a pod crashes during deployment, the rollout is automatically aborted and the previous version is kept.
- **Manual Rollback**: `kubectl argo rollouts rollback <name> -n shift-festival` or via the ArgoCD UI.
- **Namespace-wide recovery**: Revert the Git commit and push — ArgoCD auto-syncs within minutes.
- **Crash scenario**: Kubernetes restarts the pod; ArgoCD marks the app Degraded. Argo Rollouts handles automated rollback for new deployments.

## Key Constraints

- **Prefer Rollouts over Deployments** for mission-critical team services to enable automated health-based rollbacks.
- **Never commit `.env` files** — real secrets live only on the VM. Template is in `base/setup/.env.example`.
- **Scripts Deprecated**: Do not use `scripts/runtime-rollback.sh`. It is legacy code replaced by Argo Rollouts.
- **Database workloads should use `strategy: type: Recreate`** — this prevents volume mount conflicts (Multi-Attach errors) when updating deployments using ReadWriteOnce PVCs.
- **Strictly adhere to non-root policies** — avoid `runAsUser: 0` in initContainers. Use `fsGroup` in the pod's securityContext to manage volume permissions instead of root-level `chown` commands.
- **All Kubernetes manifests must render with `kubectl kustomize .`.**
- **NodePorts must stay within the assigned ranges** for each team.
- **Team-prefixed RabbitMQ queues** are mandatory; shared heartbeat routing keeps its own convention.

## Documentation Requirements

All significant changes to this repository **must** be documented.

- **New service added** → update `CLAUDE.md` (architecture section) and the structure table in `README.md`.
- **Security finding identified or fixed** → document it in `SECURITY.md`.
- **CI/CD pipeline changed** → update the pipeline steps in `CLAUDE.md`.
- **New environment variable added** → add it to `base/setup/.env.example` and document its purpose.

Documentation must be written in English and stay in sync with the code.

## Adding a New Team Service

1. Add the manifest(s) to the correct folder under `base/`.
2. Update that folder's `kustomization.yaml`.
3. Add required secrets or config keys to `base/setup/.env.example` and document them.
4. Ensure the service has a heartbeat sidecar if it needs liveness reporting.
5. Add a Service/NodePort only if the workload must be publicly exposed.
