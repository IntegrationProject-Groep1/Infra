# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Does

This is the central infrastructure repository for **ShiftFestival**. It now contains the Kubernetes manifests, Kustomize layers, GitHub Actions workflows, and support scripts used to operate the platform.

## Common Commands

```bash
# Create or update a local setup/.env file before rendering
# Kustomize needs it for the secretGenerator in setup/kustomization.yaml.

# Render manifests locally
kubectl kustomize .

# Validate without applying
kubectl apply -k . --dry-run=client

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
- **Keel** — Image updater automation, kept in a separate namespace-based manifest set.

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
- `keel` — separate namespace for the image updater

## CI/CD Pipeline

**CI (runs on every push/PR):**
1. Render the Kustomize tree with `kubectl kustomize .` using a temporary local `setup/.env`.
2. Lint all YAML files with `yamllint`.
3. Lint bash scripts with `shellcheck` (SC2034 and SC1091 are suppressed).
4. Scan git history for secrets with Gitleaks.
5. Scan Kubernetes manifests with Trivy in `config` mode.
6. Verify `.env` files are not committed and that obvious hardcoded secrets are absent.

**Deploy (runs only after CI passes, only on `main`):**
1. SCP repo files to the VM (excludes `.github/`, `assets/`, and markdown docs).
2. Verify `setup/.env` exists on the VM.
3. Run `kubectl apply -k .` from the repository root.
4. Wait for rollout completion and capture pod/service status.
5. Notify Teams on success or failure.

## Rollback and Recovery

- Preferred rollback is `kubectl rollout undo deployment/<name> -n shift-festival`.
- Namespace-wide recovery should be done by re-applying the last known-good Git commit.
- Keep all manifests versioned in Git; do not hand-edit the VM.
- The legacy Docker Compose rollback scripts in `scripts/` are kept for reference only and should not be extended for new Kubernetes work.

## Key Constraints

- **Never commit `setup/.env`** — real secrets live only on the VM or in the secret store.
- **Database workloads should use `strategy: type: Recreate`** — this prevents volume mount conflicts (Multi-Attach errors) when updating deployments using ReadWriteOnce PVCs.
- **Strictly adhere to non-root policies** — avoid `runAsUser: 0` in initContainers. Use `fsGroup` in the pod's securityContext to manage volume permissions instead of root-level `chown` commands.
- **All Kubernetes manifests must render with `kubectl kustomize .`**.
- **NodePorts must stay within the assigned ranges** for each team.
- **Team-prefixed RabbitMQ queues** are mandatory; shared heartbeat routing keeps its own convention.
- **Do not manually edit the VM** — the deploy pipeline overwrites the runtime tree on each deploy.

## Documentation Requirements

All significant changes to this repository **must** be documented. This applies to Claude Code and human contributors alike:

- **New service added** → update `CLAUDE.md` (architecture section) and the structure table in `README.md`.
- **Security finding identified or fixed** → document it in `SECURITY.md` (severity, affected file with line number, exploit scenario, fix applied).
- **CI/CD pipeline changed** → update the pipeline steps in `CLAUDE.md`.
- **Rollback or monitoring logic changed** → update the relevant sections in `CLAUDE.md` and `README.md`.
- **New environment variable added** → add it to the appropriate secret or env file and document its purpose.

Documentation must be written in English, must be detailed enough that a new team member can act on it without asking follow-up questions, and must stay in sync with the code.

`SECURITY.md` tracks all audit findings and their remediation status. Keep the checklist in that file up to date when issues are fixed.

## Adding a New Team Service

1. Add the manifest(s) to the correct folder in this repo.
2. Update that folder's `kustomization.yaml`.
3. Add required secrets or config keys to `setup/.env` and document them.
4. Ensure the service has a heartbeat sidecar if it needs liveness reporting.
5. Add a Service/NodePort only if the workload must be publicly exposed.
6. Add the workload to the deploy workflow rollout checks in `.github/workflows/deploy.yml`.
