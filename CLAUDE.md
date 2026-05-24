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
- **PostgreSQL** — Shared database for the identity service (`postgredb-service`). The chatbot has its own dedicated PostgreSQL instance (`chatbot-db-service`).
- **ELK Stack** (Elasticsearch + Logstash + Kibana) — Centralized logging and observability.
- **Cloudflared** — Secure external access tunnel for selected services. Routes are managed in the Cloudflare Zero Trust dashboard (token-based, no local config file).
- **ArgoCD** — GitOps controller installed in the `argocd` namespace. Watches the Git repo and auto-syncs changes to the cluster. Exposed at `argocd.desiderius.me` via Cloudflare Tunnel.
- **ArgoCD Image Updater** — Polls GHCR for new image tags and writes back the updated tag to Git, triggering an ArgoCD sync.
- **metrics-server** — Installed in `kube-system`. Provides CPU/memory metrics to the Kubernetes HPA controller. Uses `--kubelet-insecure-tls` because the VM uses self-signed kubelet certs (standard kubeadm setup). Managed via `base/metrics-server/`.
- **HPA (HorizontalPodAutoscaler)** — Autoscales 7 stateless Rollouts based on CPU utilization (target 70%). All HPAs use `scaleTargetRef.kind: Rollout` (argoproj.io/v1alpha1). `spec.replicas` is omitted from HPA-managed Rollout manifests; ArgoCD ignores this field via `ignoreDifferences` to prevent selfHeal conflicts. Each HPA-managed Rollout also has `progressDeadlineSeconds` set so that a pod stuck in CrashLoopBackOff automatically aborts the rollout and restores the previous version.

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
- `kube-system` — metrics-server

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

All team services use Argo Rollouts (canary strategy). Rollback is managed through Argo Rollouts, not standard Deployment rollback.

### Deployment flow (what happens on every git push)

```
1. ArgoCD detects commit on main → applies updated Rollout manifest
2. Argo Rollouts starts the canary:
   a. Starts new pod with updated image
   b. progressDeadlineSeconds timer starts (120s or 180s depending on service)
   c. setWeight: 100 → all traffic immediately goes to new pod
   d. pause: 3m → 3-minute manual observation window
   e. After 3 minutes with no abort → rollout is marked complete
```

### Failure scenario flows

**Scenario A — Pod never starts (crash on boot, bad image, OOMkill):**
```
New pod enters CrashLoopBackOff
  → Kubernetes restarts it (restartPolicy: Always): 10s, 20s, 40s backoff
  → After progressDeadlineSeconds (120–180s), pod is still not Ready
  → Argo Rollouts automatically aborts the rollout
  → Previous stable pod stays running — no manual action needed
  → ArgoCD marks app Degraded; Rollout stays in Aborted state until fixed
```

**Scenario B — Pod starts and runs, but crashes later (runtime error):**
```
Pod was healthy at deploy time → rollout completed successfully
Pod crashes after some time
  → Kubernetes restarts it automatically (restartPolicy: Always)
  → If it keeps crashing → CrashLoopBackOff
  → Manual action required: kubectl argo rollouts undo <name> -n shift-festival
  → Or: git revert the bad commit and push → ArgoCD redeploys previous version
```

**Scenario C — Pod is running but returns errors internally (false healthy):**
```
Pod passes health probes → rollout completes
App returns HTTP errors, wrong data, etc. — Kubernetes cannot detect this
  → Spotted during the 3-minute observation window (check logs in Kibana)
  → Manually abort: kubectl argo rollouts abort <name> -n shift-festival
  → Previous version is restored immediately
Note: automated detection of this scenario requires Prometheus + AnalysisTemplate
      (not currently installed — ELK is the only observability stack)
```

**Scenario D — Probe is flaky at startup (app healthy but probe fails transiently):**
```
Pod starts correctly but probe fails once or twice at startup
  → The 3-minute pause acts as an observation buffer
  → If the pod stabilises → rollout completes normally
  → Long-term fix: tune probe parameters (initialDelaySeconds, failureThreshold)
      rather than relying on the pause to hide transient failures
```

### How to trigger a manual rollback

**Option 1 — Argo Rollouts CLI (fastest, no Git history):**
```bash
kubectl argo rollouts undo <rollout-name> -n shift-festival
# Examples:
kubectl argo rollouts undo frontend-drupal -n shift-festival
kubectl argo rollouts undo identity-service -n shift-festival
```

**Option 2 — Git revert (preferred, full audit trail):**
```bash
git revert <bad-commit-sha>
git push origin main
# ArgoCD auto-syncs within the next cycle and deploys the reverted manifest.
```

**Option 3 — ArgoCD UI:** Open the ArgoCD UI, find the Rollout resource, use the Abort or Rollback button.

### HPA interaction with rollbacks

When HPA manages a Rollout's replica count, a rollback only restores the previous pod spec (image, env vars, etc.) — it does not reset the replica count. HPA continues owning the replica count and scales normally after the rollback. This is correct behaviour.

### Automated analysis (future improvement)

Argo Rollouts supports AnalysisTemplates that query Prometheus metrics (e.g. HTTP error rate) to automatically gate or abort the canary window. This would cover Scenario C automatically. Not currently configured — requires a Prometheus instance, which adds memory overhead on the single-VM cluster and is out of scope for now.

## Backup and Disaster Recovery

All 6 databases (3× PostgreSQL, 2× MariaDB, 1× MySQL) are backed up daily to a separate backup VM. The Infra repo is mirrored there as well for GitHub resilience. Full recovery procedure is documented in `docs/disaster-recovery.md`.

**Key scripts:**
- `scripts/backup-databases.sh` — dumps all databases and rsyncs to the backup VM (cron job, runs 02:00 daily)
- `scripts/restore-databases.sh` — restores a specific date's backup to the running cluster

**When `.env` changes:** re-encrypt and transfer to backup VM:
```bash
gpg --symmetric --cipher-algo AES256 --output /tmp/env.gpg base/setup/.env
scp -i ~/.ssh/backup_key /tmp/env.gpg groep1@integration.switzerlandnorth.cloudapp.azure.com:~/secrets/shift-festival.env.gpg
rm /tmp/env.gpg
```

**Estimated RTO (Recovery Time Objective):** 30–60 minutes for a full VM loss.

## Key Constraints

- **Prefer Rollouts over Deployments** for mission-critical team services to enable automated health-based rollbacks.
- **Never commit `.env` files** — real secrets live only on the VM. Template is in `base/setup/.env.example`.
- **Scripts Deprecated**: Do not use `scripts/runtime-rollback.sh`. It is legacy code replaced by Argo Rollouts.
- **Database workloads should use `strategy: type: Recreate`** — this prevents volume mount conflicts (Multi-Attach errors) when updating deployments using ReadWriteOnce PVCs.
- **Strictly adhere to non-root policies** — avoid `runAsUser: 0` in initContainers. Use `fsGroup` in the pod's securityContext to manage volume permissions instead of root-level `chown` commands.
- **All Kubernetes manifests must render with `kubectl kustomize .`.**
- **NodePorts must stay within the assigned ranges** for each team.
- **Team-prefixed RabbitMQ queues** are mandatory; shared heartbeat routing keeps its own convention.
- **HPA-managed Rollouts must omit `spec.replicas`** — If present, ArgoCD selfHeal resets the replica count on every sync, fighting the HPA. Leave the field absent so HPA has exclusive ownership. The `ignoreDifferences` entry in `argocd/applications/prod-app.yaml` handles runtime drift as an additional safeguard.
- **Do not remove `base/metrics-server`** — All HPAs depend on metrics-server. Without it, HPAs report `<unknown>` utilization and stop scaling.

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
