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
  prod/             namespace: shift-festival  (includes Keel)
  dev/              namespace: shift-festival-dev

keel/               Image updater (deployed via prod overlay only)
scripts/            Rollback + notification scripts
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
- **Cloudflared** — Secure external access tunnel for selected services.
- **Keel** — Image updater automation, deployed only to prod via `overlays/prod`.

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
- `keel` — separate namespace for the image updater

## CI/CD Pipeline

**CI (runs on every push/PR):**
1. Write a placeholder `.env` to `overlays/prod/.env` and render with `kubectl kustomize overlays/prod`.
2. Lint all YAML files with `yamllint`.
3. Lint bash scripts with `shellcheck` (SC2034 and SC1091 are suppressed).
4. Scan git history for secrets with Gitleaks.
5. Scan Kubernetes manifests with Trivy in `config` mode.
6. Verify `.env` files are not committed and that obvious hardcoded secrets are absent.

**Deploy — prod (push to `main`, after CI passes):**
1. SCP repo files to the VM (excludes `.github/`, `assets/`, and markdown docs).
2. Verify `overlays/prod/.env` exists on the VM.
3. Run `kubectl apply -k overlays/prod` from the repository root.
4. Wait for rollout completion and capture pod/service status.
5. Notify Teams on success or failure.

**Deploy — dev (push to `dev` branch):**
1. SCP repo files to the VM.
2. Verify `overlays/dev/.env` exists on the VM.
3. Run `kubectl apply -k overlays/dev`.
4. Wait for rollout completion.

## Rollback and Recovery

- Preferred rollback is `kubectl rollout undo deployment/<name> -n shift-festival`.
- Namespace-wide recovery should be done by re-applying the last known-good Git commit.
- Keep all manifests versioned in Git; do not hand-edit the VM.
- The legacy Docker Compose rollback scripts in `scripts/` are kept for reference only and should not be extended for new Kubernetes work.

## Key Constraints

- **Never commit `.env` files** — real secrets live only on the VM. Templates are in `overlays/prod/.env.example` and `overlays/dev/.env.example`.
- **Database workloads should use `strategy: type: Recreate`** — this prevents volume mount conflicts (Multi-Attach errors) when updating deployments using ReadWriteOnce PVCs.
- **Strictly adhere to non-root policies** — avoid `runAsUser: 0` in initContainers. Use `fsGroup` in the pod's securityContext to manage volume permissions instead of root-level `chown` commands.
- **All Kubernetes manifests must render with `kubectl kustomize overlays/prod`** (or `overlays/dev`).
- **NodePorts must stay within the assigned ranges** for each team.
- **Team-prefixed RabbitMQ queues** are mandatory; shared heartbeat routing keeps its own convention.
- **Do not manually edit the VM** — the deploy pipeline overwrites the runtime tree on each deploy.
- **Keel is prod-only** — never add the `keel/` reference to the dev overlay.

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
