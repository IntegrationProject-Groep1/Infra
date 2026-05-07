# ShiftFestival — Infrastructure Architecture

**Last updated:** 2026-05-07
**Author:** Infra Team

This document explains how the ShiftFestival Kubernetes infrastructure works: how manifests are structured, how code gets deployed, how images are updated, and how the system recovers from failures. Written for all team members, not just infra.

---

## Table of Contents

1. [Big picture](#1-big-picture)
2. [Repository structure](#2-repository-structure)
3. [Kustomize — base and overlays](#3-kustomize--base-and-overlays)
4. [Two environments — prod and dev](#4-two-environments--prod-and-dev)
5. [ArgoCD — GitOps controller](#5-argocd--gitops-controller)
6. [How a deploy actually works](#6-how-a-deploy-actually-works)
7. [Image updates — ArgoCD Image Updater](#7-image-updates--argocd-image-updater)
8. [Secrets](#8-secrets)
9. [Rollback and recovery](#9-rollback-and-recovery)
10. [Planned: automatic rollback with Teams notifications](#10-planned-automatic-rollback-with-teams-notifications)
11. [What this means for developers](#11-what-this-means-for-developers)

---

## 1. Big picture

```
Team repo (frontend, kassa, ...)
    │
    │  push to dev branch → CI builds :dev image → GHCR
    │  release tag v* → CI builds :prod image → GHCR
    ▼
GHCR (GitHub Container Registry)
    │
    │  ArgoCD Image Updater polls every 2 min
    │  detects new image tag → commits new tag to Infra repo
    ▼
Infra repo (this repo)
    │
    │  ArgoCD watches the repo continuously
    │  detects every new commit → runs kustomize build → applies to cluster
    ▼
Kubernetes cluster (Azure VM)
    ├── namespace: shift-festival        ← production
    └── namespace: shift-festival-dev   ← development
```

**Before this setup:** GitHub Actions SSH'd into the VM and ran `kubectl apply` manually on every push. Now ArgoCD handles all applies directly from Git — no SSH needed for deploys.

---

## 2. Repository structure

```
base/                   All Kubernetes manifests — shared between environments
  setup/                Secrets template, PersistentVolumeClaims, ConfigMaps
  core/                 RabbitMQ, PostgreSQL, Cloudflared tunnel, Dashboard
  team-frontend/        Drupal + MariaDB + Nginx proxy
  team-kassa/           Odoo + PostgreSQL + Nginx proxy + integration sidecar
  team-facturatie/      FossBilling + MariaDB + Nginx proxy
  integrations/         CRM receiver, Planning service, Identity service
  monitoring/           Elasticsearch + Logstash + Kibana + monitoring agent

overlays/
  prod/                 Production — namespace: shift-festival
  dev/                  Development — namespace: shift-festival-dev

argocd/                 ArgoCD installation + Application CRDs
  applications/         prod-app.yaml and dev-app.yaml
  image-updater/        ArgoCD Image Updater

scripts/                Bootstrap and utility scripts
docs/                   This and other documentation
pipelines/              Reference deploy pipeline for team repos
```

The `base/` directory is never applied directly. It is always rendered through an overlay.

---

## 3. Kustomize — base and overlays

**Kustomize** is a tool built into `kubectl` that lets you manage multiple environments from a single set of manifests, without duplicating YAML files.

### How it works

```
base/                    ← shared manifests, no namespace set
    core/rabbitmq.yaml
    team-frontend/drupal.yaml
    ...

overlays/prod/           ← environment layer on top of base
    kustomization.yaml   ← sets namespace: shift-festival, references base/
    namespace.yaml       ← creates the shift-festival Namespace object

overlays/dev/
    kustomization.yaml   ← sets namespace: shift-festival-dev, references base/
    namespace.yaml       ← creates the shift-festival-dev Namespace object
```

When ArgoCD (or a developer locally) runs `kubectl kustomize overlays/prod`, Kustomize:

1. Loads all manifests from `base/`
2. Sets `namespace: shift-festival` on every resource
3. Outputs a single merged YAML stream

**Key point:** the source files in `base/` are never modified. The namespace transformation happens in memory. A PVC that says `namespace: shift-festival` in its source file will say `namespace: shift-festival-dev` in the rendered output when going through the dev overlay.

### Why this matters

Without overlays, you would need two full copies of every manifest — one for prod, one for dev. Every change would have to be made twice, and the environments would slowly drift apart. With overlays, there is zero duplication. Every change to `base/` automatically applies to both environments the next time ArgoCD syncs.

### What this means for you as a developer

The overlay system means **you never touch the Infra repo** during normal development or deployment. The full workflow from your perspective:

**During development:**
1. Push to the `dev` branch in your own team repo
2. CI builds a Docker image and pushes it to GHCR with the `:dev` tag
3. ArgoCD Image Updater detects the new image (within 2 minutes) and commits the new tag to the Infra repo
4. ArgoCD syncs the dev overlay → your new code is live in `shift-festival-dev` within ~5 minutes of your push

You can verify by checking the ArgoCD UI or running `kubectl get pods -n shift-festival-dev`.

**When releasing to production:**
1. Create a release tag (`v1.2.3`) on `main` in your team repo — only after dev has been stable
2. CI pipeline runs and must pass
3. A new Docker image is built and pushed with the `:prod` tag
4. ArgoCD Image Updater detects it, commits to the Infra repo, ArgoCD syncs the prod overlay
5. Your change is live in `shift-festival` within ~5 minutes of the tag

**The key point:** the overlays handle the namespace routing automatically. The `:dev` image goes to `shift-festival-dev`, the `:prod` image goes to `shift-festival`. You do not configure this — it is wired up in the overlays and ArgoCD Applications. As a developer, you only control when something gets deployed by choosing when to push to `dev` or create a release tag.

---

## 4. Two environments — prod and dev

| | Production | Development |
|---|---|---|
| Namespace | `shift-festival` | `shift-festival-dev` |
| ArgoCD app | `shift-festival-prod` | `shift-festival-dev` |
| Git branch | `main` | `dev` |
| Image tag | `:prod` | `:dev` |
| Trigger | Release tag (`v*`) after CI | Every push to `dev` branch |

Both environments run on the same cluster but are fully isolated by Kubernetes namespaces. A service in `shift-festival` cannot accidentally talk to a service in `shift-festival-dev` — they have separate Service DNS entries.

The purpose of the dev environment is to catch problems before they reach production. Every change should be tested in dev first. A release tag on `main` should only be created after dev has been stable.

### How to access your dev service

Both prod and dev are exposed via the same Cloudflare Tunnel — no NodePorts needed for dev. The tunnel runs as a pod in each namespace and routes traffic from a public subdomain directly to the Kubernetes Service inside that namespace.

| Service | Production URL | Development URL |
|---|---|---|
| Frontend (Drupal) | `frontend.desiderius.me` | `dev-frontend.desiderius.me` |
| Kassa (Odoo) | `kassa.desiderius.me` | `dev-kassa.desiderius.me` |
| Facturatie (FossBilling) | `facturatie.desiderius.me` | `dev-facturatie.desiderius.me` |
| CRM | `crm.desiderius.me` | `dev-crm.desiderius.me` |
| Planning | `planning.desiderius.me` | `dev-planning.desiderius.me` |
| ArgoCD | `argocd.desiderius.me` | — (infra only) |

> The exact dev subdomain format (`dev-kassa` vs `kassa-dev`) is configured by the infra team in the Cloudflare Zero Trust dashboard and may differ from the table above. Check with the infra team if a URL does not resolve.

**As a developer:** you do not configure anything for this. Once Tom has set up the tunnel routes in the Cloudflare dashboard, your dev service is reachable at the dev URL automatically — as long as your pod is running in `shift-festival-dev`. Push to your `dev` branch, wait ~5 minutes, and open the dev URL in your browser.

---

## 5. ArgoCD — GitOps controller

ArgoCD is the core of the deployment system. It runs in the `argocd` namespace on the same cluster and is responsible for keeping the cluster in sync with what Git says.

### How ArgoCD syncs

1. ArgoCD continuously watches the Infra Git repo (polls every ~3 minutes, or triggered immediately on a webhook push)
2. It runs `kustomize build overlays/prod` internally and compares the result to what is currently running on the cluster
3. If there is any difference, ArgoCD applies the changes

This happens automatically — no human action needed for a normal deploy.

### Self-healing

If someone manually edits a resource on the cluster (`kubectl edit`, `kubectl delete`, etc.), ArgoCD detects the drift within ~3 minutes and reverts it back to the Git state.

**Git is the only source of truth. Do not hand-edit the cluster.** Any manual change will be overwritten by ArgoCD automatically.

### ArgoCD UI

The ArgoCD dashboard shows the health and sync status of every application. It is accessible via SSH tunnel for now, and will be exposed via the Cloudflare tunnel at `argocd.desiderius.me` once that route is configured.

```bash
# Forward ArgoCD to your local machine (run on the VM, then access https://localhost:8080 on your laptop via SSH tunnel)
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

### Application health states

| State | Meaning |
|---|---|
| `Synced + Healthy` | Everything is running and matches Git |
| `Synced + Progressing` | Pods are starting up — normal after a deploy |
| `Synced + Degraded` | Something is running but unhealthy (crash, probe failure) |
| `OutOfSync` | Cluster differs from Git — ArgoCD is about to apply changes |
| `Unknown` | ArgoCD cannot determine health |

---

## 6. How a deploy actually works

### Dev deploy (every push to `dev` branch in a team repo)

```
Developer pushes to dev branch in their repo
    ↓
Team CI Pipeline runs and passes
    ↓
pipelines/deploy.yml runs: builds Docker image, pushes to GHCR with :dev tag
    ↓
ArgoCD Image Updater detects the new :dev tag (within 2 minutes)
    ↓
Image Updater commits the new tag to the Infra repo (main or dev branch)
    ↓
ArgoCD detects the new commit in the dev branch
    ↓
ArgoCD syncs overlays/dev to shift-festival-dev namespace
    ↓
New pods start, old pods terminate
```

### Prod deploy (release tag on main)

```
Developer creates a release tag (v1.2.3) on main in their repo
    ↓
Team CI Pipeline runs and passes
    ↓
pipelines/deploy.yml runs: builds Docker image, pushes to GHCR with :prod tag
    ↓
ArgoCD Image Updater detects the new :prod tag
    ↓
Image Updater commits the new tag to the Infra repo (main branch)
    ↓
ArgoCD detects the new commit in the main branch
    ↓
ArgoCD syncs overlays/prod to shift-festival namespace
    ↓
Rolling update: new pods start one at a time, old pods terminate
```

### Infra change deploy (manifest change in this repo)

```
Infra developer pushes a change to base/ or overlays/ on main
    ↓
ArgoCD detects the new commit within 3 minutes
    ↓
ArgoCD applies the change to the cluster
    ↓
Done — no manual kubectl needed
```

---

## 7. Image updates — ArgoCD Image Updater

ArgoCD Image Updater replaces the old Keel image watcher.

### How it works

Image Updater polls GHCR every 2 minutes for each image listed in the ArgoCD Application. When it detects a new tag:

1. It writes a commit to the Infra repo that updates the image tag reference
2. ArgoCD picks up the new commit and deploys it

The commit looks like:
```
Update image frontend: sha256:abc123 → sha256:def456
```

This keeps Git as the full audit trail — you can always look at the Infra repo's git log to see exactly which image was deployed at any point in time.

### Why this matters for rollback

Because the image tag is stored in Git, rolling back a bad deploy is a Git operation — not a cluster operation. See section 9.

### Image tag convention

| Branch | Image tag | Environment |
|---|---|---|
| `dev` in team repo | `:dev` | `shift-festival-dev` |
| Release tag `v*` on `main` | `:prod` | `shift-festival` |

The `pipelines/deploy.yml` file in this repo is the reference pipeline that every team copies into their own repo. It handles building and pushing both `:dev` and `:prod` tags automatically based on the trigger.

---

## 8. Secrets

Secrets are **not** managed by ArgoCD or Kustomize. ArgoCD runs `kustomize build` by cloning the Git repo — it never has access to `.env` files on the VM.

### How secrets get into the cluster

A single script bootstraps all secrets from `setup/.env` on the VM:

```bash
./scripts/create-secret.sh setup/.env shift-festival
./scripts/create-secret.sh setup/.env shift-festival-dev
```

This creates a Kubernetes Secret named `shift-secrets` in each namespace. ArgoCD is configured to ignore this Secret — it will never overwrite or delete it.

### Secret rules

- **Never commit `.env` files** — they are gitignored and must stay on the VM only
- **`setup/.env` is the master secrets file** — all credentials live here
- **Re-run `create-secret.sh` whenever credentials change** — ArgoCD will not pick up `.env` changes automatically
- **Templates** are committed: `overlays/prod/.env.example` and `overlays/dev/.env.example` — these show which keys are needed with placeholder values

### Cloudflare tunnel secret

Cloudflared requires a separate secret (`cloudflare-tunnel-secret`) that must also be created manually per namespace. The token comes from the Cloudflare Zero Trust dashboard and is stored in `setup/.env` under `CLOUDFLARE_TUNNEL_TOKEN`.

---

## 9. Rollback and recovery

### Manual rollback via ArgoCD

```bash
# View the deployment history
argocd app history shift-festival-prod

# Roll back to a specific revision
argocd app rollback shift-festival-prod <revision>

# Re-enable auto-sync after the rollback
argocd app set shift-festival-prod --sync-policy automated --self-heal --auto-prune
```

The ArgoCD UI also has a **History and Rollback** tab for each application.

### Rollback via Git revert (preferred)

Because every image update is a Git commit from Image Updater, rolling back is simply reverting that commit:

```bash
git revert HEAD   # undoes the last Image Updater commit
git push origin main
```

ArgoCD detects the revert commit and deploys the previous image version within minutes. This approach is preferred because the revert is visible in Git history — it keeps a full audit trail of what happened and why.

**Important:** `git revert` does not delete history. It creates a new commit that undoes the previous one. No developer work is ever lost. The team's code in their own repos is never touched — only the image tag commit in this Infra repo is reverted.

### Recovery scenarios

| Scenario | What happens | Action needed |
|---|---|---|
| Bad image → CrashLoopBackOff | ArgoCD marks app Degraded | Rollback via ArgoCD CLI or Git revert |
| Manual kubectl change | ArgoCD self-heals within ~3 min | None — ArgoCD reverts it automatically |
| Secret changes | ArgoCD does NOT update secrets | Re-run `create-secret.sh` on VM |
| Multiple services down (infra issue) | All affected apps show Degraded | Check RabbitMQ, Elasticsearch, VM health |
| Bad Infra manifest pushed | ArgoCD sync fails | Fix the manifest, push to main |

### `runtime-rollback.sh` — deprecated

The old `scripts/runtime-rollback.sh` daemon that ran on the VM is deprecated. ArgoCD self-healing replaces its drift-detection role. Manual rollback via ArgoCD replaces its image-pinning role. The script is kept in the repo for reference only.

---

## 10. Planned: automatic rollback with Teams notifications

> **Status:** Designed, not yet implemented.

The current system requires a human to trigger rollback when a prod deploy fails. The planned improvement is a fully automatic rollback loop with Teams notifications at every step.

### How it will work

```
New :prod image deployed → ArgoCD syncs
    ↓
Pods crash → ArgoCD detects Degraded
    ↓  (after 2 minute grace period — normal slow startups are ignored)
ArgoCD Notifications fires:
  → Teams message to #Infra and owning team: "⚠️ Rollback gestart voor <service>"
  → Webhook triggers GitHub Actions auto-rollback workflow
    ↓
GitHub Actions:
  → git revert HEAD on Infra repo (undoes Image Updater's image tag commit)
  → push to main
    ↓
ArgoCD detects the revert commit → syncs → previous image deployed
    ↓
ArgoCD Notifications:
  → Teams message: "✅ Rollback succesvol — <service> draait op vorige versie"
```

### Why developer work is safe

The auto-rollback only touches the Infra repo — specifically, the automatic commit that Image Updater wrote when it updated the image tag. Developer code lives in the team repos and is never touched. The revert creates a new commit (no history is lost), and the team can create a new release tag once the issue is fixed.

### Safeguards

- **Grace period** — only fires after 2+ minutes of continuous Degraded state, so slow-starting pods are not mistaken for failures
- **Loop prevention** — if the reverted version is also Degraded, the system stops and sends a CRITICAL alert instead of reverting again
- **Infra issue detection** — if multiple apps go Degraded simultaneously (RabbitMQ down, VM issue), the system notifies only and does not rollback — a rollback cannot fix an infrastructure outage

### What needs to be built

- `.github/workflows/auto-rollback.yml` — the rollback workflow
- ArgoCD Notifications config — webhook trigger + Teams messages
- GitHub PAT secret for the rollback workflow to push commits
- Teams channel mapping per team (to be defined with TL)

---

## 11. What this means for developers

### For all teams

| What you do | What happens |
|---|---|
| Push to your `dev` branch | New `:dev` image built, deployed to `shift-festival-dev` within ~5 min |
| Create release tag `v*` on `main` | New `:prod` image built, deployed to `shift-festival` within ~5 min |
| Push a hotfix to `dev` | Same as a regular push — dev environment updates automatically |
| Need to see what is running | Check the ArgoCD UI or run `kubectl get pods -n shift-festival` |

### For the Infra team

| What you do | What happens |
|---|---|
| Push a manifest change to `main` | ArgoCD syncs to prod within 3 minutes |
| Push a manifest change to `dev` | ArgoCD syncs to dev within 3 minutes |
| Manually change something on the cluster | ArgoCD reverts it within 3 minutes — do not do this |
| Need to deploy urgently | `argocd app sync shift-festival-prod` forces an immediate sync |
| Secrets change | Re-run `./scripts/create-secret.sh setup/.env <namespace>` on the VM |

### Things that no longer need to happen manually

- `kubectl apply` on the VM — ArgoCD handles all applies
- Watching for new images and restarting pods — Image Updater handles this
- Running the runtime-rollback.sh daemon — deprecated, ArgoCD covers this

### Adding a new service (summary)

1. Add the manifest to the correct folder under `base/`
2. Add it to that folder's `kustomization.yaml`
3. Add any new secret keys to `overlays/prod/.env.example` and `overlays/dev/.env.example`
4. Add the real values to `setup/.env` on the VM and re-run `create-secret.sh`
5. Push to `main` — ArgoCD deploys it automatically

Full instructions: see `CLAUDE.md` → "Adding a New Team Service".
