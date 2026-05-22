# Disaster Recovery Guide

This guide explains how to recover ShiftFestival after a partial or complete infrastructure failure. Two failure scenarios are covered:

1. **VM lost** — the primary Azure school VM is destroyed or inaccessible
2. **GitHub lost** — the GitHub organisation or repository is unavailable

---

## Architecture overview

```
GitHub Actions (backup.yml)
       │
       ├──SSH port 60022──► Primary VM (HV01)           Backup VM (integration.switzerlandnorth.cloudapp.azure.com)
       │                    ┌────────────────┐           ┌───────────────────────────────────────┐
       │                    │ Kubernetes     │  rsync ──►│ ~/backups/databases/<date>/            │
       │                    │ 6 databases    │           │   central-postgres.sql.gz              │
       │                    │ ShiftFestival  │           │   kassa-postgres.sql.gz                │
       └──SSH port 22──────►│ stack          │           │   planning-postgres.sql.gz             │
  (mirror sync)             └────────────────┘           │   frontend-mariadb.sql.gz              │
                                                         │   facturatie-mariadb.sql.gz            │
                                                         │   crm-mysql.sql.gz                     │
                                                         │                                        │
                                                         │ ~/git-mirrors/infra.git (bare mirror)  │
                                                         │ ~/secrets/shift-festival.env.gpg       │
                                                         └───────────────────────────────────────┘
```

The backup VM is a **warm standby** — it stores data but does not run Kubernetes simultaneously. You start Kubernetes on it only when the primary VM is gone.

---

## Part 1 — One-time setup

### Step 1.1 — Copy SSH key to backup VM

The SSH key was already generated on the primary VM. Now authorise it on the backup VM. Run this **on the primary VM**:

```bash
ssh-copy-id -i ~/.ssh/backup_key.pub groep1@integration.switzerlandnorth.cloudapp.azure.com
# Enter the groep1 password when prompted — only needed this once

# Verify passwordless login works
ssh -i ~/.ssh/backup_key groep1@integration.switzerlandnorth.cloudapp.azure.com "echo OK"
```

### Step 1.2 — Encrypt and transfer the secrets file

Run these commands **on the primary VM** from inside the Infra repo directory:

```bash
cd ~/shiftfestival    # or wherever the repo is cloned

gpg --symmetric --cipher-algo AES256 --output /tmp/env.gpg base/setup/.env
# Choose a strong passphrase and share it with your team via password manager

scp -i ~/.ssh/backup_key /tmp/env.gpg \
  groep1@integration.switzerlandnorth.cloudapp.azure.com:~/secrets/shift-festival.env.gpg

rm /tmp/env.gpg
```

Re-run this step whenever `base/setup/.env` changes.

### Step 1.3 — Set up the Git mirror on the backup VM

SSH into the backup VM and run:

```bash
ssh groep1@integration.switzerlandnorth.cloudapp.azure.com

# On the backup VM:
git clone --mirror https://github.com/EHB-TI/integration-project-groep-1.git ~/git-mirrors/infra.git
exit
```

### Step 1.4 — Add GitHub Actions secrets

In the GitHub repository: **Settings → Secrets and variables → Actions → New repository secret**

Add these three secrets:

| Secret name | Value |
|---|---|
| `BACKUP_VM_HOST` | `integration.switzerlandnorth.cloudapp.azure.com` |
| `BACKUP_VM_USER` | `groep1` |
| `BACKUP_SSH_KEY` | Run `cat ~/.ssh/backup_key` on the primary VM, paste the full output |

Once these secrets are added, the `backup.yml` workflow runs automatically every night at 02:00 UTC and handles both the database dumps and the Git mirror sync. No cron job on the VM is needed.

### Step 1.5 — Test the backup manually

Either trigger the workflow from GitHub Actions (Actions → Backup Databases & Sync Git Mirror → Run workflow), or run on the primary VM:

```bash
export BACKUP_VM_USER=groep1
export BACKUP_VM_HOST=integration.switzerlandnorth.cloudapp.azure.com
export BACKUP_VM_KEY=~/.ssh/backup_key
cd ~/shiftfestival
bash scripts/backup-databases.sh
```

Expected result: 6 `.sql.gz` files on the backup VM under `~/backups/databases/<today>/`.

---

## Part 2 — Manual backup or restore

### Run a manual backup

```bash
# On the primary VM
export BACKUP_VM_USER=groep1
export BACKUP_VM_HOST=integration.switzerlandnorth.cloudapp.azure.com
cd ~/shiftfestival
bash scripts/backup-databases.sh
```

### Restore databases (cluster still running, data corrupted)

```bash
# On the primary VM
export BACKUP_VM_USER=groep1
export BACKUP_VM_HOST=integration.switzerlandnorth.cloudapp.azure.com
cd ~/shiftfestival

# Downloads from backup VM automatically and restores
bash scripts/restore-databases.sh 2026-05-22
```

### Check what backups exist

```bash
ssh -i ~/.ssh/backup_key groep1@integration.switzerlandnorth.cloudapp.azure.com \
  "ls -lh ~/backups/databases/"
```

---

## Part 3 — Full recovery: primary VM is completely gone

This recovers the full platform on the backup VM. Estimated time: **30–60 minutes**.

### Step 3.1 — SSH into backup VM

```bash
ssh groep1@integration.switzerlandnorth.cloudapp.azure.com
```

All remaining steps are run **on the backup VM**.

### Step 3.2 — Restore the Infra repo from local mirror

```bash
# Option A: deploy to a new Git remote first (recommended for full GitOps)
cd ~/git-mirrors/infra.git
git remote add new-origin https://gitlab.com/<your-namespace>/Infra.git
git push new-origin --mirror
git clone https://gitlab.com/<your-namespace>/Infra.git ~/Infra

# Option B: use the mirror directly (no Git remote needed)
git clone ~/git-mirrors/infra.git ~/Infra
```

### Step 3.3 — Install k3s

```bash
curl -sfL https://get.k3s.io | sh -
sudo chmod 644 /etc/rancher/k3s/k3s.yaml
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Verify
kubectl get nodes
```

### Step 3.4 — Install Argo Rollouts plugin

```bash
curl -sLO https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
chmod +x kubectl-argo-rollouts-linux-amd64
sudo mv kubectl-argo-rollouts-linux-amd64 /usr/local/bin/kubectl-argo-rollouts
```

### Step 3.5 — Restore secrets and deploy the stack

```bash
cd ~/Infra

# Decrypt the .env file (you need the GPG passphrase from your password manager)
gpg --decrypt ~/secrets/shift-festival.env.gpg > base/setup/.env

# Create namespace and Kubernetes secret
kubectl create namespace shift-festival
bash scripts/create-secret.sh base/setup/.env shift-festival

# Install ArgoCD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=180s

# Deploy the full stack
kubectl apply -k argocd/
```

ArgoCD detects the Git repo and syncs the full stack automatically.

### Step 3.6 — Wait for database pods

```bash
# Watch until all *-db pods show Running
kubectl get pods -n shift-festival -w | grep "\-db"
```

### Step 3.7 — Restore all databases

```bash
cd ~/Infra

# List available backup dates
ls ~/backups/databases/

# Restore (reads directly from local backup, no download needed)
bash scripts/restore-databases.sh 2026-05-22 ~/backups/databases/2026-05-22
```

### Step 3.8 — Restart application pods

```bash
kubectl rollout restart deployment -n shift-festival
```

### Step 3.9 — Update Cloudflare Tunnel

If the backup VM has a different external IP, update the tunnel token in `.env` and re-apply:

```bash
nano base/setup/.env          # update CLOUDFLARE_TUNNEL_TOKEN
bash scripts/create-secret.sh base/setup/.env shift-festival
kubectl rollout restart deployment cloudflared -n shift-festival
```

---

## Part 4 — Recovery if GitHub is unavailable

### Option A — Push mirror to a new host

```bash
# On the backup VM
cd ~/git-mirrors/infra.git
git remote add new-origin https://gitlab.com/<your-namespace>/Infra.git
git push new-origin --mirror
```

Then update ArgoCD to point to the new remote:

```bash
kubectl edit application shift-festival-prod -n argocd
# Change: spec.source.repoURL
```

### Option B — Deploy directly without ArgoCD (fastest)

```bash
cd ~/git-mirrors/infra.git
git worktree add /tmp/infra-restore HEAD
cd /tmp/infra-restore

bash scripts/create-secret.sh base/setup/.env shift-festival
kubectl apply -k .
```

---

## Backup retention and health

Backups are kept for **14 days** (`KEEP_DAYS` variable in `scripts/backup-databases.sh`).

```bash
# Check last backup log (GitHub Actions → backup.yml → latest run)
# Or check manually on primary VM:
tail -50 /var/log/sf-backup.log   # only if cron is also set up

# List all backup dates on backup VM
ssh -i ~/.ssh/backup_key groep1@integration.switzerlandnorth.cloudapp.azure.com \
  "ls ~/backups/databases/"

# Verify a specific backup file is not corrupted
ssh -i ~/.ssh/backup_key groep1@integration.switzerlandnorth.cloudapp.azure.com \
  "gunzip -t ~/backups/databases/2026-05-22/central-postgres.sql.gz && echo OK"
```

---

## What is NOT backed up

| Component | Why | Recovery |
|---|---|---|
| Elasticsearch logs | Log data, not business-critical | Accept data loss; logs restart fresh |
| RabbitMQ messages | In-flight messages are transient | Accept message loss; queues recreated by ArgoCD |
| pgAdmin config | UI-only tool | Reconnect manually after restore |
| Drupal file uploads | TODO: add to backup script if needed | Re-upload via Drupal admin |

---

## Recovery checklist (quick reference)

```
□ SSH into backup VM
□ Restore Infra repo from ~/git-mirrors/infra.git
□ Install k3s (curl -sfL https://get.k3s.io | sh -)
□ Decrypt .env: gpg --decrypt ~/secrets/shift-festival.env.gpg > base/setup/.env
□ Apply secrets: bash scripts/create-secret.sh base/setup/.env shift-festival
□ Install ArgoCD + apply argocd/ manifests
□ Wait for *-db pods to be Running
□ Run restore: bash scripts/restore-databases.sh <date> ~/backups/databases/<date>
□ Restart apps: kubectl rollout restart deployment -n shift-festival
□ Update Cloudflare tunnel token if VM IP changed
□ Verify: kubectl get pods -n shift-festival
```
