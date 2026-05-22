# Disaster Recovery Guide

This guide explains how to recover ShiftFestival after a partial or complete infrastructure failure. Two failure scenarios are covered:

1. **VM lost** — the primary Azure school VM is destroyed or inaccessible
2. **GitHub lost** — the GitHub organisation or repository is unavailable

---

## Architecture overview

```
Primary VM (Azure school)              Backup VM
┌──────────────────────────┐          ┌─────────────────────────────┐
│  Kubernetes + ArgoCD     │ rsync ──► │  ~/backups/databases/       │
│  6 live databases        │          │  ~/git-mirrors/infra.git    │
│  ShiftFestival stack     │          │  ~/secrets/shift-festival.  │
│  GitHub remote           │          │    env.gpg                  │
└──────────────────────────┘          └─────────────────────────────┘
```

The backup VM is a **warm standby** — it stores data but does not run Kubernetes simultaneously. You start Kubernetes on it only when the primary VM is gone.

---

## Part 1 — Initial setup (do this once)

### Step 1.1 — Generate SSH key on the primary VM

Run these commands **on the primary VM**:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/backup_key -N ""
ssh-copy-id -i ~/.ssh/backup_key.pub <backup-vm-user>@<backup-vm-ip>

# Test that passwordless login works
ssh -i ~/.ssh/backup_key <backup-vm-user>@<backup-vm-ip> "echo OK"
```

### Step 1.2 — Create the backup directory on the backup VM

```bash
ssh <backup-vm-user>@<backup-vm-ip> "mkdir -p ~/backups/databases ~/git-mirrors ~/secrets"
```

### Step 1.3 — Set up the Git mirror on the backup VM

Run these commands **on the backup VM**:

```bash
# Clone the Infra repo as a bare mirror
git clone --mirror https://github.com/<your-org>/Infra.git ~/git-mirrors/infra.git

# Add a daily cron job to keep it in sync (runs at 03:00)
(crontab -l 2>/dev/null; echo "0 3 * * * cd ~/git-mirrors/infra.git && git fetch --all --prune") | crontab -
```

### Step 1.4 — Back up the secrets file to the backup VM

The `.env` file must never be committed to Git. Store an encrypted copy on the backup VM.

```bash
# On the primary VM — encrypt and transfer
gpg --symmetric --cipher-algo AES256 --output /tmp/shift-festival.env.gpg base/setup/.env
scp -i ~/.ssh/backup_key /tmp/shift-festival.env.gpg \
  <backup-vm-user>@<backup-vm-ip>:~/secrets/shift-festival.env.gpg
rm /tmp/shift-festival.env.gpg
```

> **Both team members must know the GPG passphrase.** Store it in your shared password manager.

Re-run this step whenever you change the `.env` file.

### Step 1.5 — Set up the daily backup cron job on the primary VM

Add these lines to the crontab on the primary VM (`crontab -e`):

```
BACKUP_VM_USER=<backup-vm-user>
BACKUP_VM_HOST=<backup-vm-ip>
BACKUP_VM_KEY=/home/<your-user>/.ssh/backup_key

# Daily database backup at 02:00
0 2 * * * cd /path/to/Infra && bash scripts/backup-databases.sh >> /var/log/sf-backup.log 2>&1
```

Replace `/path/to/Infra` with the actual path where you cloned this repo on the VM.

### Step 1.6 — Test the backup manually

```bash
export BACKUP_VM_USER=<backup-vm-user>
export BACKUP_VM_HOST=<backup-vm-ip>
export BACKUP_VM_KEY=~/.ssh/backup_key

cd /path/to/Infra
bash scripts/backup-databases.sh
```

Expected output: 6 `.sql.gz` files on the backup VM under `~/backups/databases/<today>/`.

---

## Part 2 — Running a manual backup or restore

### Manual backup

```bash
export BACKUP_VM_USER=ubuntu
export BACKUP_VM_HOST=10.0.0.5   # replace with actual IP

cd /path/to/Infra
bash scripts/backup-databases.sh
```

### Manual restore (databases still running, data corrupted)

```bash
export BACKUP_VM_USER=ubuntu
export BACKUP_VM_HOST=10.0.0.5

# Restore from a specific date (downloads from backup VM automatically)
bash scripts/restore-databases.sh 2026-05-22

# Or restore from a local copy of the backup files
bash scripts/restore-databases.sh 2026-05-22 /path/to/local/backup/
```

---

## Part 3 — Full recovery: primary VM is completely gone

This recovers the full platform on the backup VM. Estimated time: **30–60 minutes**.

### Step 3.1 — Install k3s on the backup VM

```bash
curl -sfL https://get.k3s.io | sh -

# Verify it started
sudo kubectl get nodes
```

### Step 3.2 — Install Argo Rollouts CLI

```bash
curl -sLO https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
chmod +x kubectl-argo-rollouts-linux-amd64
sudo mv kubectl-argo-rollouts-linux-amd64 /usr/local/bin/kubectl-argo-rollouts
```

### Step 3.3 — Restore the Infra repo

```bash
# Push the local mirror to a new remote (GitLab, Codeberg, etc.)
cd ~/git-mirrors/infra.git
git remote add new-origin https://gitlab.com/<your-namespace>/Infra.git
git push new-origin --mirror

# Clone it fresh
cd ~
git clone https://gitlab.com/<your-namespace>/Infra.git
cd Infra
```

### Step 3.4 — Restore the secrets

```bash
gpg --decrypt ~/secrets/shift-festival.env.gpg > base/setup/.env
# Enter the GPG passphrase when prompted
```

### Step 3.5 — Apply secrets and deploy the stack

```bash
# Create the Kubernetes secret
bash scripts/create-secret.sh base/setup/.env shift-festival

# Install ArgoCD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Wait for ArgoCD to be ready
kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=120s

# Deploy the full stack
kubectl apply -k argocd/
```

ArgoCD will detect the Git repo and sync the full stack automatically.

### Step 3.6 — Wait for database pods to be running

```bash
kubectl get pods -n shift-festival -w
# Wait until all *-db pods show Running status
```

### Step 3.7 — Restore databases

```bash
# Find the most recent backup date on the backup VM
ls ~/backups/databases/

# Restore (no download needed — we are on the backup VM)
cd ~/Infra
bash scripts/restore-databases.sh 2026-05-22 ~/backups/databases/2026-05-22
```

### Step 3.8 — Restart application pods

```bash
kubectl rollout restart deployment -n shift-festival
```

### Step 3.9 — Update Cloudflare Tunnel

The tunnel token is tied to the Cloudflare Zero Trust dashboard, not the VM's IP. If the backup VM has a different IP, you only need to update the tunnel token in the `.env` file and re-run the secrets bootstrap:

```bash
# Edit .env with the new tunnel token
nano base/setup/.env
bash scripts/create-secret.sh base/setup/.env shift-festival

# Restart the cloudflared pod
kubectl rollout restart deployment cloudflared -n shift-festival
```

---

## Part 4 — Recovery if GitHub is unavailable

### Option A — Push to a new Git host

```bash
# On the backup VM
cd ~/git-mirrors/infra.git

# Push to GitLab (or Codeberg, etc.)
git remote add new-origin https://gitlab.com/<your-namespace>/Infra.git
git push new-origin --mirror
```

Then update the ArgoCD Application to point to the new remote:

```bash
kubectl edit application shift-festival-prod -n argocd
# Change spec.source.repoURL to the new URL
```

### Option B — Deploy directly without ArgoCD (fastest)

```bash
cd ~/git-mirrors/infra.git
# Checkout a working tree
git worktree add /tmp/infra-restore HEAD
cd /tmp/infra-restore

# Apply secrets first
bash scripts/create-secret.sh base/setup/.env shift-festival

# Deploy
kubectl apply -k .
```

---

## Backup retention

Backups are kept for **14 days** by default (controlled by `KEEP_DAYS` in the backup script). To change this, update your crontab:

```
KEEP_DAYS=30
```

---

## Checking backup health

```bash
# List backups on the backup VM
ssh -i ~/.ssh/backup_key <backup-vm-user>@<backup-vm-ip> "ls -lh ~/backups/databases/"

# Check last backup log
tail -50 /var/log/sf-backup.log

# Check cron job is registered
crontab -l
```

---

## What is NOT backed up

| Component | Why | Recovery |
|---|---|---|
| Elasticsearch logs | Log data, not business-critical | Accept data loss; logs start fresh |
| RabbitMQ messages | In-flight messages are transient | Accept message loss; queues are recreated by ArgoCD |
| pgAdmin config | UI-only tool, trivial to reconfigure | Reconnect manually after restore |
| Drupal file uploads | TODO — add to backup script if needed | Re-upload or restore from Drupal admin |
