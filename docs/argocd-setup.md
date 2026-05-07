# ArgoCD Setup Guide

**Last updated:** 2026-05-07

This document walks through the one-time bootstrap steps to get ArgoCD running on the cluster and connected to the Git repository.

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
