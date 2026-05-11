# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Does

This is the central infrastructure repository for **ShiftFestival**. It contains the Kubernetes manifests, Kustomize layers, GitHub Actions workflows, and support scripts used to operate the platform.

## Repository Structure

```
base/               Shared manifests — no namespace set here
  setup/            Secrets template, storage, configmaps
  core/             RabbitMQ, PostgreSQL, Cloudflared, Dashboard
  team-frontend/    Drupal + MariaDB + Nginx proxy
  team-kassa/       Odoo + PostgreSQL + Nginx proxy + integration sidecar
  team-facturatie/  FossBilling + MariaDB + Nginx proxy
  integrations/     CRM, Planning, Identity Service
  monitoring/       ELK stack + monitoring agent

overlays/
  prod/             namespace: shift-festival
  dev/              namespace: shift-festival-dev

argocd/             ArgoCD installation manifests + Application CRDs
  applications/     prod-app.yaml and dev-app.yaml (point ArgoCD at overlays/)
  image-updater/    ArgoCD Image Updater (replaces Keel)
keel/               DEPRECATED — image updater replaced by ArgoCD Image Updater
scripts/            Bootstrap + notification scripts
docs/               Architecture and process documentation
```

See `docs/overlay-structure.md` for the full explanation of the base/overlay pattern.

## Common Commands

```bash
# Render prod manifests locally (requires overlays/prod/.env)
kubectl kustomize overlays/prod

# Render dev manifests locally (requires overlays/dev/.env)
kubectl kustomize overlays/dev

# Validate without applying
kubectl apply -k overlays/prod --dry-run=client
kubectl apply -k overlays/dev  --dry-run=client

# Lint YAML
find . -type f \( -name "*.yml" -o -name "*.yaml" \) -not -path "./.git/*" -print0 | xargs -0 yamllint

# Lint bash scripts
shellcheck scripts/*.sh

# Run CI checks manually
# → Use GitHub Actions "Run workflow" button on ci.yml

# Emergency deploy (bypasses waiting for a push to main)
# → GitHub Actions → "Deploy Infra to Kubernetes" → Run workflow → main
```

## Architecture

The stack runs on Kubernetes and is composed of:

**Core Infrastructure:**
- **RabbitMQ** — Central async message broker; team services communicate through team-prefixed queues (for example `kassa.orders` and `crm.customer.created`).
- **PostgreSQL** — Shared database for the identity service and other shared workloads.
- **ELK Stack** (Elasticsearch + Logstash + Kibana) — Centralized logging and observability.
- **Cloudflared** — Secure external access tunnel for selected services. Routes are managed in the Cloudflare Zero Trust dashboard (token-based, no local config file).
- **ArgoCD** — GitOps controller installed in the `argocd` namespace. Watches the Git repo and auto-syncs changes to the cluster. Exposed at `argocd.desiderius.me` via Cloudflare Tunnel.
- **ArgoCD Image Updater** — Polls GHCR for new image tags and writes back the updated tag to Git, triggering an ArgoCD sync. Replaces Keel.

**Team Services:**
- Frontend (Drupal, ports 30020–30029)
- Facturatie (FossBilling, 30010–30019)
- Kassa (Odoo, 30030–30039)
- CRM (Salesforce receiver, 30040–30049)
- Planning (Office 365 integration, 30050–30059)
- Identity (UUID service, 30070–30100)

Most team workloads follow the pattern: application container + heartbeat sidecar, with an Nginx proxy when public access is required.

**Namespaces:**
- `shift-festival` — prod application namespace
- `shift-festival-dev` — dev application namespace
- `argocd` — ArgoCD controller and Image Updater

## CI/CD Pipeline

**CI (runs on every push/PR):**
1. Write a placeholder `.env` to `overlays/prod/.env` and render with `kubectl kustomize overlays/prod`.
2. Lint all YAML files with `yamllint`.
3. Lint bash scripts with `shellcheck` (SC2034 and SC1091 are suppressed).
4. Scan git history for secrets with Gitleaks.
5. Scan Kubernetes manifests with Trivy in `config` mode.
6. Verify `.env` files are not committed and that obvious hardcoded secrets are absent.

**Deploy — GitOps via ArgoCD (primary mechanism):**
ArgoCD watches the Git repo directly and auto-syncs on every commit:
main branch → shift-festival namespace (prod)
- dev branch → shift-festival-dev namespace (dev)

Self-healing is enabled: any manual cluster change is reverted within ~1 minute.

**Deploy — via GitHub Actions (secondary/emergency):**
The .github/workflows/deploy.yml SCP pipeline still runs but no longer applies manifests directly. It is retained for:
- Emergency access to the VM
- Secret refresh: running ./scripts/create-secret.sh setup/.env <namespace> when secrets change

**Image updates:**
ArgoCD Image Updater polls GHCR every 1 minute. When a new prod or dev tag is detected, it commits the new tag to Git, which triggers an ArgoCD sync. The per-team pipelines/deploy.yml build pipelines are unchanged.

**Secrets Bootstrap (one-time per environment):**
Secrets are not managed by kustomize — ArgoCD does not have filesystem access to `.env` files. Run once on the VM:
```bash
./scripts/create-secret.sh setup/.env shift-festival
./scripts/create-secret.sh setup/.env shift-festival-dev
```
Re-run whenever `setup/.env` changes.

## Rollback and Recovery

- **Single deployment rollback**: `argocd app rollback shift-festival-prod <revision>` (find the revision in the ArgoCD UI History tab at `argocd.desiderius.me`).
- **Namespace-wide recovery**: Revert the Git commit and push — ArgoCD auto-syncs within minutes.
- **Self-healing**: ArgoCD reverts manual `kubectl` changes automatically. Do not hand-edit the cluster.
- **Crash scenario**: Kubernetes restarts the pod; ArgoCD marks the app Degraded. Investigate logs, push a fix to Git, ArgoCD syncs it.
- `scripts/runtime-rollback.sh` is deprecated — do not run it. ArgoCD covers its use cases.

## Key Constraints

- **Never commit `.env` files** — real secrets live only on the VM. Templates are in `overlays/prod/.env.example` and `overlays/dev/.env.example`.
- **Secrets are NOT managed by kustomize secretGenerator** — use `./scripts/create-secret.sh setup/.env <namespace>` to bootstrap the `shift-secrets` Secret per environment. ArgoCD ignores this Secret.
- **Do not add `keel.sh/` annotations to new deployments** — ArgoCD Image Updater handles image polling.
- **ArgoCD is the source of truth** — do not run `kubectl apply -k` directly on the VM. ArgoCD auto-syncs from Git; manual applies will be reverted by self-healing.
- **Database workloads should use `strategy: type: Recreate`** — this prevents volume mount conflicts (Multi-Attach errors) when updating deployments using ReadWriteOnce PVCs.
- **Strictly adhere to non-root policies** — avoid `runAsUser: 0` in initContainers. Use `fsGroup` in the pod's securityContext to manage volume permissions instead of root-level `chown` commands.
- **All Kubernetes manifests must render with `kubectl kustomize overlays/prod`** (or `overlays/dev`).
- **NodePorts must stay within the assigned ranges** for each team.
- **Team-prefixed RabbitMQ queues** are mandatory; shared heartbeat routing keeps its own convention.

## Documentation Requirements

All significant changes to this repository **must** be documented. This applies to Claude Code and human contributors alike:

- **New service added** → update `CLAUDE.md` (architecture section) and the structure table in `README.md`.
- **Security finding identified or fixed** → document it in `SECURITY.md` (severity, affected file with line number, exploit scenario, fix applied).
- **CI/CD pipeline changed** → update the pipeline steps in `CLAUDE.md`.
- **Rollback or monitoring logic changed** → update the relevant sections in `CLAUDE.md` and `README.md`.
- **New environment variable added** → add it to `overlays/prod/.env.example` and `overlays/dev/.env.example` and document its purpose.
- **Overlay structure changed** → update `docs/overlay-structure.md`.

Documentation must be written in English, must be detailed enough that a new team member can act on it without asking follow-up questions, and must stay in sync with the code.

`SECURITY.md` tracks all audit findings and their remediation status. Keep the checklist in that file up to date when issues are fixed.

## Adding a New Team Service

1. Add the manifest(s) to the correct folder under `base/`.
2. Update that folder's `kustomization.yaml`.
3. Add required secrets or config keys to `overlays/prod/.env.example` and `overlays/dev/.env.example` and document them.
4. Ensure the service has a heartbeat sidecar if it needs liveness reporting.
5. Add a Service/NodePort only if the workload must be publicly exposed.
6. Add the workload to the deploy workflow rollout checks in `.github/workflows/deploy.yml`.
