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

### Step 1.2 — Allow passwordless sudo for ctr on the primary VM

`backup-images.sh` uses `ctr` (containerd CLI) to pull and export images. `ctr` requires root. The script runs non-interactively via GitHub Actions, so sudo must not prompt for a password.

> **Security note:** this grants passwordless `ctr` access to `ehbstudent`. Since `ctr` can run arbitrary containers, this is effectively root access for container operations. Acceptable on this managed school VM; do not apply to production systems without further hardening.

Run this **once on the primary VM**:

```bash
echo "ehbstudent ALL=(ALL) NOPASSWD: /usr/bin/ctr, /usr/bin/chown" \
  | sudo tee /etc/sudoers.d/ctr-backup
sudo chmod 440 /etc/sudoers.d/ctr-backup

# Verify — both must return without a password prompt
sudo -n /usr/bin/ctr version
sudo -n /usr/bin/chown --version
```

### Step 1.3 — Encrypt and transfer the secrets file

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

### Step 1.4 — Set up the Git mirror on the backup VM

SSH into the backup VM and run:

```bash
ssh groep1@integration.switzerlandnorth.cloudapp.azure.com

# On the backup VM:
git clone --mirror https://tombomeke-ehb:<GITHUB_PAT>@github.com/IntegrationProject-Groep1/Infra.git ~/git-mirrors/infra.git
exit
```

### Step 1.5 — Add GitHub Actions secrets

In the GitHub repository: **Settings → Secrets and variables → Actions → New repository secret**

Add these three secrets:

| Secret name | Value |
|---|---|
| `BACKUP_VM_HOST` | `integration.switzerlandnorth.cloudapp.azure.com` |
| `BACKUP_VM_USER` | `groep1` |
| `BACKUP_SSH_KEY` | Run `cat ~/.ssh/backup_key` on the primary VM, paste the full output |

Once these secrets are added, the `backup.yml` workflow runs automatically every night at 02:00 UTC and handles database dumps, the Git mirror sync, and image exports. No cron job on the VM is needed.

### Step 1.6 — Run the first image backup (one-time, requires primary VM)

The `backup-images.sh` script runs on the primary VM because it needs access to the running k3s cluster to know which images to export. Run this once manually to populate `~/backups/images/` on the backup VM. After this, the `backup.yml` workflow keeps it up to date automatically.

```bash
# On the primary VM (cluster must be running)
export BACKUP_VM_USER=groep1
export BACKUP_VM_HOST=integration.switzerlandnorth.cloudapp.azure.com
export BACKUP_VM_KEY=~/.ssh/backup_key
cd ~/shiftfestival

bash scripts/backup-images.sh
```

Expected result: `.tar` files on the backup VM under `~/backups/images/` — one per unique GHCR image currently running in the cluster.

Verify on the backup VM:
```bash
ssh -i ~/.ssh/backup_key groep1@integration.switzerlandnorth.cloudapp.azure.com \
  "ls -lh ~/backups/images/"
```

### Step 1.7 — Test the full backup

Either trigger the workflow from GitHub Actions (Actions → Backup Databases & Sync Git Mirror → Run workflow), or run on the primary VM:

```bash
export BACKUP_VM_USER=groep1
export BACKUP_VM_HOST=integration.switzerlandnorth.cloudapp.azure.com
export BACKUP_VM_KEY=~/.ssh/backup_key
cd ~/shiftfestival

bash scripts/backup-databases.sh
bash scripts/backup-images.sh
```

Expected result:
- 6 `.sql.gz` files on the backup VM under `~/backups/databases/<today>/`
- `.tar` files on the backup VM under `~/backups/images/` (refreshed)

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

### Step 3.2 — Restore the Infra repo

**If the GitHub organisation still exists:**
```bash
git clone https://github.com/IntegrationProject-Groep1/Infra.git ~/Infra
```

**If the GitHub organisation is also gone (worst case):**
```bash
# 1. Create a new empty repo on GitHub (personal account is fine)
#    e.g. https://github.com/<your-username>/Infra

# 2. Push the local mirror to it
cd ~/git-mirrors/infra.git
git remote add new-origin https://<your-username>:<GITHUB_PAT>@github.com/<your-username>/Infra.git
git push new-origin --mirror

# 3. Clone from the new repo
git clone https://github.com/<your-username>/Infra.git ~/Infra

# 4. After ArgoCD is running (Step 3.5), update it to point to the new URL:
kubectl edit application shift-festival-prod -n argocd
# Change: spec.source.repoURL to https://github.com/<your-username>/Infra.git
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

# Create namespace and Kubernetes secrets
kubectl create namespace shift-festival
bash scripts/create-secret.sh base/setup/.env shift-festival

# IMPORTANT: cloudflare-tunnel-secret is a separate secret not covered by create-secret.sh
# It is required for cloudflared to start and provide external access.
TUNNEL_TOKEN=$(grep '^CLOUDFLARE_TUNNEL_TOKEN=' base/setup/.env | cut -d= -f2-)
kubectl create secret generic cloudflare-tunnel-secret \
  --from-literal=CLOUDFLARE_TUNNEL_TOKEN="$TUNNEL_TOKEN" \
  -n shift-festival

# Install Argo Rollouts controller (must exist before ArgoCD syncs Rollout resources)
kubectl apply -k argocd/rollouts/

# Install ArgoCD + Image Updater + Application CR
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=180s
kubectl apply -k argocd/

# Restore the RabbitMQ definitions secret (users, vhosts, permissions).
# Without this, RabbitMQ cannot start and all services that depend on it will crash.
gpg --batch --decrypt ~/secrets/rabbitmq-definitions.gpg \
  | kubectl create secret generic rabbitmq-definitions \
      --from-file=definitions.json=/dev/stdin \
      -n shift-festival
```

ArgoCD detects the Git repo and syncs the full stack automatically.

### Step 3.6 — Wait for ArgoCD to sync, then wait for database pods

```bash
# Check ArgoCD sync status (target: Synced / Healthy)
kubectl get application shift-festival-prod -n argocd

# If SYNC STATUS stays "Unknown" after 2 minutes, unblock with a direct apply:
kubectl apply -k .

# Watch until all *-db pods show Running (3–5 minutes while images pull)
kubectl get pods -n shift-festival -l 'app in (postgredb,kassa-db,chatbot-db,frontend-db,facturatie-db,crm-db)'
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
| Application source repos | Only the Infra repo is mirrored | Images are backed up separately — source code is not needed for recovery |

---

## Part 4 — Testing disaster recovery

Run these tests **before** you actually need them.

### Scenario A — VM gone, GitHub org still available

This is the most likely failure. Test it by deploying the full stack on the backup VM from scratch, without touching the primary VM.

```bash
# 1. SSH into backup VM
ssh groep1@integration.switzerlandnorth.cloudapp.azure.com

# 2. Install k3s (skip if already installed from a previous test)
curl -sfL https://get.k3s.io | sh -
sudo chmod 644 /etc/rancher/k3s/k3s.yaml
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl get nodes   # should show Ready

# 3. Clone Infra from GitHub (org still exists)
git clone https://github.com/IntegrationProject-Groep1/Infra.git ~/Infra-test
cd ~/Infra-test

# 4. Decrypt secrets
gpg --decrypt ~/secrets/shift-festival.env.gpg > base/setup/.env

# 5. Apply secrets and deploy
kubectl create namespace shift-festival
bash scripts/create-secret.sh base/setup/.env shift-festival
TUNNEL_TOKEN=$(grep '^CLOUDFLARE_TUNNEL_TOKEN=' base/setup/.env | cut -d= -f2-)
kubectl create secret generic cloudflare-tunnel-secret \
  --from-literal=CLOUDFLARE_TUNNEL_TOKEN="$TUNNEL_TOKEN" \
  -n shift-festival

# Install Argo Rollouts controller (must exist before ArgoCD syncs Rollout resources)
kubectl apply -k argocd/rollouts/

kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=180s
kubectl apply -k argocd/

# Restore RabbitMQ definitions secret (required for RabbitMQ to start)
gpg --batch --decrypt ~/secrets/rabbitmq-definitions.gpg \
  | kubectl create secret generic rabbitmq-definitions \
      --from-file=definitions.json=/dev/stdin \
      -n shift-festival

# 6. Wait for ArgoCD to sync (usually 1–2 minutes after step 5)
kubectl get application shift-festival-prod -n argocd
# Target: SYNC STATUS = Synced, HEALTH STATUS = Healthy
#
# If SYNC STATUS stays "Unknown" after 2 minutes, ArgoCD has a field-ownership
# conflict (common on first deploy). Unblock it by applying manifests directly:
kubectl apply -k .
# ArgoCD will reconcile and take over management from here.

# 7. Watch database pods come up (required before restore)
kubectl get pods -n shift-festival -l 'app in (postgredb,kassa-db,chatbot-db,frontend-db,facturatie-db,crm-db)'
# Wait until all show Running — this can take 3–5 minutes while images pull.

# 8. Restore databases (use most recent backup date)
ls ~/backups/databases/       # pick a date
bash scripts/restore-databases.sh <date> ~/backups/databases/<date>

# 9. Cleanup after test
kubectl delete namespace shift-festival
/usr/local/bin/k3s-uninstall.sh
rm -rf ~/Infra-test
```

Expected result: all pods Running, databases restored, Cloudflare tunnel up.

---

### Scenario B — Everything gone (VM + GitHub org deleted)

Images are backed up daily to `~/backups/images/` on the backup VM via `backup-images.sh`, so all custom GHCR images are available even if the org is gone.

```bash
# 1. SSH into backup VM — this is now your only machine
ssh groep1@integration.switzerlandnorth.cloudapp.azure.com

# 2. Install k3s
curl -sfL https://get.k3s.io | sh -
sudo chmod 644 /etc/rancher/k3s/k3s.yaml
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# 3. Import backed-up images into k3s before deploying
for tar in ~/backups/images/*.tar; do
  echo "Importing $tar"
  sudo ctr images import "$tar"
done
sudo ctr images list | grep ghcr.io   # verify

# 4. Create a new empty GitHub repo on a personal account (do this in the browser)
#    e.g. https://github.com/<your-username>/Infra

# 5. Push the local mirror to the new repo
cd ~/git-mirrors/infra.git
git remote add new-origin https://<your-username>:<GITHUB_PAT>@github.com/<your-username>/Infra.git
git push new-origin --mirror

# 6. Clone from the new repo
git clone https://github.com/<your-username>/Infra.git ~/Infra
cd ~/Infra

# 7. Decrypt secrets and deploy (same as Scenario A steps 4–7)
gpg --decrypt ~/secrets/shift-festival.env.gpg > base/setup/.env
kubectl create namespace shift-festival
bash scripts/create-secret.sh base/setup/.env shift-festival
TUNNEL_TOKEN=$(grep '^CLOUDFLARE_TUNNEL_TOKEN=' base/setup/.env | cut -d= -f2-)
kubectl create secret generic cloudflare-tunnel-secret \
  --from-literal=CLOUDFLARE_TUNNEL_TOKEN="$TUNNEL_TOKEN" -n shift-festival
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=180s
kubectl apply -k argocd/

# 8. Update ArgoCD to point to the new repo URL
kubectl edit application shift-festival-prod -n argocd
# Change: spec.source.repoURL → https://github.com/<your-username>/Infra.git

# 9. Restore databases
bash scripts/restore-databases.sh <date> ~/backups/databases/<date>

# 10. Verify everything is running
kubectl get pods -n shift-festival
```

---

## Resetting the backup VM after a test

After running Scenario A or B, reset the backup VM back to a clean standby state. The actual backups (`~/backups/`, `~/git-mirrors/`, `~/secrets/`) must be kept — only the test deployment is removed.

```bash
# SSH into the backup VM
ssh groep1@integration.switzerlandnorth.cloudapp.azure.com

# 1. Uninstall k3s (removes all pods, namespaces, and cluster state)
/usr/local/bin/k3s-uninstall.sh

# 2. Remove the cloned Infra repo (adjust the name if you used a different one)
rm -rf ~/Infra
rm -rf ~/Infra-test

# 3. If you tested Scenario B and added a new-origin remote to the git mirror, clean it up
cd ~/git-mirrors/infra.git
git remote remove new-origin 2>/dev/null || true
cd ~

# 4. Verify backups are still intact
ls ~/backups/databases/     # should show date folders
ls ~/backups/images/        # should show .tar files
ls ~/git-mirrors/infra.git/ # should show a bare git repo
ls ~/secrets/               # should show shift-festival.env.gpg
```

The backup VM is now a clean standby again — ready for the next test or a real recovery.

---

## Recovery checklist (quick reference)

**Scenario A — VM gone, GitHub org still available:**
```
□ SSH into backup VM
□ git clone https://github.com/IntegrationProject-Groep1/Infra.git ~/Infra
□ Install k3s (curl -sfL https://get.k3s.io | sh -)
□ Decrypt .env: gpg --decrypt ~/secrets/shift-festival.env.gpg > base/setup/.env
□ Apply shift-secrets: bash scripts/create-secret.sh base/setup/.env shift-festival
□ Apply cloudflare-tunnel-secret (see Step 3.5 — separate secret!)
□ Install Argo Rollouts: kubectl apply -k argocd/rollouts/
□ Install ArgoCD + apply argocd/ manifests: kubectl apply -k argocd/
□ Restore rabbitmq-definitions secret: gpg --batch --decrypt ~/secrets/rabbitmq-definitions.gpg | kubectl create secret generic rabbitmq-definitions --from-file=definitions.json=/dev/stdin -n shift-festival
□ Wait for *-db pods to be Running
□ Run restore: bash scripts/restore-databases.sh <date> ~/backups/databases/<date>
□ Restart apps: kubectl rollout restart deployment -n shift-festival
□ Verify tunnel: kubectl logs -n shift-festival -l app=cloudflared --tail=20
□ Verify: kubectl get pods -n shift-festival
```

**Scenario B — VM gone AND GitHub org deleted:**
```
□ SSH into backup VM
□ Import images: for tar in ~/backups/images/*.tar; do sudo ctr images import "$tar"; done
□ Create new empty GitHub repo on personal account
□ Push mirror: cd ~/git-mirrors/infra.git && git remote add new-origin <url> && git push new-origin --mirror
□ git clone new repo → ~/Infra
□ Install k3s
□ Decrypt .env and apply secrets (same as Scenario A)
□ Install Argo Rollouts: kubectl apply -k argocd/rollouts/
□ Install ArgoCD + apply argocd/ manifests: kubectl apply -k argocd/
□ Restore rabbitmq-definitions secret (see Scenario A checklist)
□ Update ArgoCD repoURL to new repo (kubectl edit application shift-festival-prod -n argocd)
□ Restore databases: bash scripts/restore-databases.sh <date> ~/backups/databases/<date>
□ Verify: kubectl get pods -n shift-festival
```
