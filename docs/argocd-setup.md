# ArgoCD Setup Guide

**Last updated:** 2026-05-07

This document covers two things:
1. **How it works** — the GitOps architecture, overlay structure, image update flow, and rollback system
2. **Bootstrap steps** — one-time setup to get ArgoCD running on the cluster

---

## How It Works

### From push-based to GitOps

**Before ArgoCD:**
```
Developer pushes → GitHub Actions → SCP to VM → kubectl apply -k on VM
```

**With ArgoCD:**
```
Developer pushes → ArgoCD detects Git change → ArgoCD applies to cluster
```
ArgoCD runs inside the cluster and watches the Git repository directly. No SSH, no SCP, no manual kubectl needed.

---

### Overlay structure

The repo uses a base/overlay pattern:

```
base/               Shared manifests — no namespace set here
overlays/
  prod/             namespace: shift-festival    ← ArgoCD watches main branch
  dev/              namespace: shift-festival-dev ← ArgoCD watches dev branch
```

ArgoCD runs `kustomize build overlays/prod` internally (same as `kubectl kustomize overlays/prod`) and applies the result. The `base/` directory is never applied directly.

**Two ArgoCD Applications:**

| Application | Branch | Namespace |
|---|---|---|
| `shift-festival-prod` | `main` | `shift-festival` |
| `shift-festival-dev` | `dev` | `shift-festival-dev` |

Every commit to `main` triggers a prod sync. Every commit to `dev` triggers a dev sync. Changes are live within ~3 minutes.

---

### Self-healing

ArgoCD continuously compares the cluster state to Git. If someone manually runs `kubectl edit` or `kubectl delete` on the cluster, ArgoCD detects the drift and reverts it automatically within ~3 minutes.

**Git is the only source of truth. Do not hand-edit the cluster.**

---

### Image update flow

ArgoCD Image Updater polls GHCR every 2 minutes per image. When a new tag is detected:

1. Image Updater commits the new image tag to Git (e.g., `image: ghcr.io/.../frontend:prod` → new SHA)
2. ArgoCD detects the Git change
3. ArgoCD deploys the new image via a rolling update

**Deploy flow per environment:**

| Environment | Trigger | Image tag |
|---|---|---|
| Dev | Push to `dev` branch in team repo | `:dev` |
| Prod | Release tag (`v*`) after CI passes in team repo | `:prod` |

The per-team pipeline (`pipelines/deploy.yml`) handles building and pushing images. The Infra repo's `deploy.yml` no longer runs `kubectl apply` — ArgoCD handles all apply operations.

---

### Secrets

ArgoCD does **not** manage secrets. It runs `kustomize build` internally and does not have access to `.env` files on the VM. The `shift-secrets` Secret is bootstrapped manually and ArgoCD is configured to ignore it (`ignoreDifferences`).

**Rule: whenever `setup/.env` changes, re-run the secret bootstrap script manually.**

```bash
./scripts/create-secret.sh setup/.env shift-festival
./scripts/create-secret.sh setup/.env shift-festival-dev
```

The `cloudflare-tunnel-secret` is also managed manually (not by ArgoCD):
```bash
TUNNEL_TOKEN=$(grep CLOUDFLARE_TUNNEL_TOKEN ~/shiftfestival/setup/.env | cut -d'=' -f2)
kubectl create secret generic cloudflare-tunnel-secret \
  --from-literal=CLOUDFLARE_TUNNEL_TOKEN="$TUNNEL_TOKEN" \
  -n shift-festival --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic cloudflare-tunnel-secret \
  --from-literal=CLOUDFLARE_TUNNEL_TOKEN="$TUNNEL_TOKEN" \
  -n shift-festival-dev --dry-run=client -o yaml | kubectl apply -f -
```

---

### Rollback system — what ArgoCD covers vs the old script

`scripts/runtime-rollback.sh` is deprecated. Below is an honest comparison of every flow it handled.

#### ✅ Fully covered by ArgoCD

| Scenario | Old script | ArgoCD |
|---|---|---|
| Manual cluster change (kubectl edit/delete) | Not detected | Self-heal reverts within ~3 min |
| Team pushes a fixed image | Auto pin release via SHA polling | Image Updater detects new tag, commits to Git, ArgoCD deploys |
| Rollback to previous version | Sticky SHA pin | `argocd app rollback shift-festival-prod <revision>` or Git revert |
| Multiple services degraded at once | "Infra issue" detection, no rollback | All affected apps show Degraded in UI; visible immediately |

#### ⚠️ Partially covered (behavior difference)

| Scenario | Old script | ArgoCD |
|---|---|---|
| New image causes CrashLoopBackOff | Automatic rollback after 6-step diagnosis | ArgoCD marks app Degraded — **human must trigger rollback manually** |
| Container still running but heartbeat missing | Notifies team: likely config/env issue | ArgoCD shows pod as Running; no diagnosis. Manual investigation needed |
| Database down → app crash | No rollback, notifies DB owner | ArgoCD shows app Degraded; human checks logs |

The key difference: the old script performed **automatic rollbacks**. ArgoCD intentionally does not — in GitOps, every state change goes through Git. An automatic rollback that bypasses Git would create drift. The tradeoff is that a human must react when an app goes Degraded.

#### How to roll back manually

```bash
# View revision history
argocd app history shift-festival-prod

# Roll back to a specific revision (temporarily disables auto-sync)
argocd app rollback shift-festival-prod <revision>

# Re-enable auto-sync after investigation
argocd app set shift-festival-prod --sync-policy automated --self-heal --auto-prune
```

Preferred approach: revert the Git commit and push — ArgoCD auto-syncs within minutes and keeps Git as the source of truth.

---

### Teams notifications

The old script sent notifications to both #Infra and the owning team channel on every event (rollback, crash, DB down, pin release). ArgoCD has a built-in **Notifications Controller** that can replicate this.

#### How ArgoCD notifications work

ArgoCD Notifications watches Application health and sync status. When a trigger fires (e.g., app becomes Degraded), it sends a message to a configured channel.

#### Configure Teams notifications

**Step 1** — Create a ConfigMap with the Teams webhook and message template:

```bash
kubectl apply -n argocd -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: argocd-notifications-cm
  namespace: argocd
data:
  service.teams: |
    recipientUrls:
      infra: <TEAMS_WEBHOOK_INFRA_URL>
  template.app-degraded: |
    teams:
      facts:
        - name: Application
          value: "{{.app.metadata.name}}"
        - name: Namespace
          value: "{{.app.spec.destination.namespace}}"
        - name: Sync Status
          value: "{{.app.status.sync.status}}"
      potentialAction:
        - "@type": OpenUri
          name: Open ArgoCD
          targets:
            - os: default
              uri: "https://argocd.desiderius.me/applications/{{.app.metadata.name}}"
      title: "❌ App Degraded: {{.app.metadata.name}}"
      summary: "{{.app.metadata.name}} is Degraded in {{.app.spec.destination.namespace}}"
      themeColor: "#FF0000"
  template.app-sync-succeeded: |
    teams:
      title: "✅ Deployed: {{.app.metadata.name}}"
      summary: "{{.app.metadata.name}} synced successfully to {{.app.spec.destination.namespace}}"
      themeColor: "#00CC00"
  trigger.on-degraded: |
    - when: app.status.health.status == 'Degraded'
      send: [app-degraded]
  trigger.on-sync-succeeded: |
    - when: app.status.operationState.phase in ['Succeeded']
      send: [app-sync-succeeded]
EOF
```

**Step 2** — Annotate the Applications to subscribe to triggers:

```bash
kubectl annotate application shift-festival-prod -n argocd \
  notifications.argoproj.io/subscribe.on-degraded.teams=infra \
  notifications.argoproj.io/subscribe.on-sync-succeeded.teams=infra

kubectl annotate application shift-festival-dev -n argocd \
  notifications.argoproj.io/subscribe.on-degraded.teams=infra
```

After this, ArgoCD sends a Teams message to #Infra whenever:
- An app becomes Degraded (bad image, crash, etc.)
- A sync succeeds (new deploy)

> **Note**: Replace `<TEAMS_WEBHOOK_INFRA_URL>` with the actual webhook URL from `setup/.env` (`TEAMS_WEBHOOK_INFRA`). Keep the URL out of Git — apply the ConfigMap manually on the VM or inject it via a Secret.

---

### Automatic rollback plan (to be implemented)

> **Status**: Designed, not yet implemented. Implementation planned — see below for full design.

#### Why `git revert` does NOT lose developer work

The auto-rollback uses `git revert HEAD` on the **Infra repo**. This is important to understand:

- `git revert` creates a **new commit** that undoes the previous one. The original commit stays in git history. Nothing is deleted, nothing is lost.
- The Infra repo only contains Kubernetes manifests and image tag annotations written by ArgoCD Image Updater. When Image Updater deploys a new image, it commits something like:
  ```
  Update image frontend: sha256:abc → sha256:def
  ```
  Reverting that commit undoes the image tag change — it does NOT touch the developer's code.
- Developer code lives in their **own repos** (team-frontend, kassa, etc.). The auto-rollback on the Infra repo never touches those repos.

#### Full flow

```
New image deployed to prod
    ↓
Pods start crashing → ArgoCD detects Degraded
    ↓  (grace period: 2 minutes continuous Degraded, ignores slow startups)
ArgoCD Notifications fires simultaneously:
  → Webhook → GitHub Actions (triggers auto-rollback workflow)
  → Teams message to #Infra + owning team: "⚠️ Rollback started for <app>"
    ↓
GitHub Actions auto-rollback workflow:
  → git revert HEAD on main (undoes Image Updater's image tag commit)
  → push to main
    ↓
ArgoCD detects new commit → syncs → previous working image deployed
    ↓
ArgoCD Notifications:
  → Teams message: "✅ Rollback succesvol — <app> draait op vorige versie"
```

#### Safeguards needed

| Risk | Safeguard |
|---|---|
| Service is just slow to start (not actually broken) | Grace period: only trigger after 2+ min continuous Degraded |
| Previous version also broken → revert loop | Workflow checks if app is still Degraded after rollback; if yes, stops and sends CRITICAL alert — no further auto action |
| Infra outage (RabbitMQ down) triggers false rollback | Only trigger if Degraded started within 10 min of a sync event, otherwise notify-only |
| Revert touches wrong commit | Workflow verifies last commit is from `github-actions[bot]` (Image Updater) before reverting |

#### What needs to be built

1. **`.github/workflows/auto-rollback.yml`** — workflow triggered by ArgoCD webhook, does the `git revert` and sends Teams notifications at each step
2. **ArgoCD Notifications config** — webhook trigger to GitHub Actions + Teams messages (Degraded + sync succeeded)
3. **GitHub PAT secret** in the Infra repo with `contents: write` scope for the rollback workflow to push the revert commit
4. **Teams channel mapping** — which app maps to which team channel (to be defined with TL)

---

---

## Prerequisites

- `kubectl` configured to connect to the cluster
- `argocd` CLI installed (`brew install argocd` or from the [releases page](https://github.com/argoproj/argo-cd/releases))
- Access to the VM (via SSH or GitHub Actions)
- A GitHub Personal Access Token (PAT) with `repo` read scope for `integrationproject-groep1/Infra`

---

## Step 1 — Install ArgoCD

```bash
# Apply ArgoCD namespace + pinned install manifest
kubectl apply -k argocd/

# Wait for ArgoCD to be ready
kubectl -n argocd rollout status deployment/argocd-server --timeout=5m
```

Get the initial admin password:
```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d && echo
```

---

## Step 2 — Connect to the ArgoCD API

Forward the ArgoCD server locally to use the CLI:
```bash
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

In another terminal:
```bash
argocd login localhost:8080 --username admin --password <password-from-step-1> --insecure
```

---

## Step 3 — Connect the GitHub Repository

ArgoCD needs credentials to read the private Infra repo:
```bash
argocd repo add https://github.com/integrationproject-groep1/Infra.git \
  --username <github-username> \
  --password <github-PAT-with-repo-read>
```

---

## Step 4 — Bootstrap Secrets

The `shift-secrets` Kubernetes Secret is NOT created by kustomize. Run this on the VM (where `setup/.env` exists):
```bash
./scripts/create-secret.sh setup/.env shift-festival
./scripts/create-secret.sh setup/.env shift-festival-dev
```

Re-run this command whenever secrets in `setup/.env` change.

---

## Step 5 — Deploy Applications

```bash
kubectl apply -k argocd/applications/
```

This creates two Application objects in ArgoCD:
- `shift-festival-prod` — watches `main` branch → `shift-festival` namespace
- `shift-festival-dev` — watches `dev` branch → `shift-festival-dev` namespace

Check sync status:
```bash
argocd app list
argocd app get shift-festival-prod
```

Trigger a manual sync if needed:
```bash
argocd app sync shift-festival-prod
```

---

## Step 6 — Expose ArgoCD UI via Cloudflare Tunnel

The cluster uses a **token-based** Cloudflare Tunnel (no local config file — routes are managed in the Cloudflare Zero Trust dashboard).

### In Cloudflare Zero Trust Dashboard:
1. Go to **Networks → Tunnels** → select your tunnel
2. Click **Edit** → **Public Hostname** → **Add a public hostname**
3. Fill in:
   - **Subdomain**: `argocd`
   - **Domain**: `desiderius.me`
   - **Type**: `HTTPS`
   - **URL**: `argocd-server.argocd.svc.cluster.local:443`
   - **TLS**: Enable "No TLS Verify" (ArgoCD uses a self-signed cert internally)
4. Save

### In Cloudflare DNS:
The CNAME record is created automatically when you add a public hostname in the tunnel config.

### Optional — Cloudflare Access (recommended for production):
Add a Zero Trust Access policy in front of `argocd.desiderius.me` to require SSO authentication (GitHub, Google, etc.) before reaching the ArgoCD login page.

After setup: `https://argocd.desiderius.me`

---

## Step 7 — Change the Admin Password

```bash
argocd account update-password \
  --current-password <initial-password> \
  --new-password <your-secure-password>
```

---

## Step 8 — Configure Image Updater Write-Back Credentials

ArgoCD Image Updater needs a Git write credential to commit new image tags back to the repo:

```bash
kubectl create secret generic git-creds \
  --namespace argocd \
  --from-literal=username=<github-username> \
  --from-literal=password=<github-PAT-with-repo-write>
```

Then annotate the Applications to use this secret:
```bash
argocd app edit shift-festival-prod
```
Add under `metadata.annotations`:
```yaml
argocd-image-updater.argoproj.io/git-repository-secret: argocd/git-creds
```

---

## Rollback

```bash
# View revision history
argocd app history shift-festival-prod

# Roll back to a specific revision
argocd app rollback shift-festival-prod <revision>
```

Or use the ArgoCD UI: select the application → **History and Rollback** tab.

---

## Useful Commands

```bash
# List all apps
argocd app list

# Force sync (useful after a push that ArgoCD missed)
argocd app sync shift-festival-prod

# Show why an app is out of sync
argocd app diff shift-festival-prod

# Hard refresh (clears cache, re-reads Git)
argocd app get shift-festival-prod --hard-refresh

# View ArgoCD logs
kubectl -n argocd logs -l app.kubernetes.io/name=argocd-application-controller -f
```
