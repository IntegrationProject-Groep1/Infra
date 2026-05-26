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
                                                         │ ~/secrets/rabbitmq-definitions.gpg     │
                                                         │ ~/secrets/cloudflare-tunnel.gpg        │
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

# Prompt for the GPG passphrase (this is the GPG_PASSPHRASE GitHub Actions secret).
# It is used to decrypt all three secret backups below.
read -s -p "GPG passphrase: " GPG_PASS; echo

# Decrypt the .env file
gpg --batch --passphrase "$GPG_PASS" --decrypt ~/secrets/shift-festival.env.gpg > base/setup/.env

# Create namespace and Kubernetes secrets from .env
kubectl create namespace shift-festival
bash scripts/create-secret.sh base/setup/.env shift-festival

# Restore the RabbitMQ definitions secret (users, vhosts, permissions).
# Without this, RabbitMQ cannot start and all services that depend on it will crash.
gpg --batch --passphrase "$GPG_PASS" --decrypt ~/secrets/rabbitmq-definitions.gpg \
  | kubectl create secret generic rabbitmq-definitions \
      --from-file=definitions.json=/dev/stdin \
      -n shift-festival

# Restore the Cloudflare tunnel secret.
# The tunnel token is set via CI pipeline and is NOT stored in the .env file.
# The .env contains a placeholder only — always use the GPG backup here.
CF_TOKEN=$(gpg --batch --passphrase "$GPG_PASS" --decrypt ~/secrets/cloudflare-tunnel.gpg)
kubectl create secret generic cloudflare-tunnel-secret \
  --from-literal=CLOUDFLARE_TUNNEL_TOKEN="$CF_TOKEN" \
  -n shift-festival
unset CF_TOKEN GPG_PASS

# Install Argo Rollouts controller (must exist before ArgoCD syncs Rollout resources)
kubectl apply -k argocd/rollouts/

# Install ArgoCD + Image Updater + Application CR
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=180s
kubectl apply -k argocd/
```

ArgoCD detects the Git repo and syncs the full stack automatically.

### Step 3.6 — Fix kernel limits, wait for ArgoCD sync, disable auto-sync, scale down non-essentials

```bash
# IMPORTANT: Raise inotify limits first. k3s with many pods will hit the default
# limit of 128 inotify instances, causing pods to fail with "too many open files".
sudo sysctl -w fs.inotify.max_user_instances=512
sudo sysctl -w fs.inotify.max_user_watches=524288

# Check ArgoCD sync status (target: Synced / Healthy)
kubectl get application shift-festival-prod -n argocd

# If SYNC STATUS stays "Unknown" after 2 minutes, unblock with a direct apply:
kubectl apply -k .

# IMPORTANT: Disable ArgoCD auto-sync immediately after the initial sync.
# Without this, ArgoCD will revert every manual scale-down you make.
kubectl patch application shift-festival-prod -n argocd --type=merge \
  -p='{"spec":{"syncPolicy":null}}'

# Get the ArgoCD initial admin password (username is always "admin"):
kubectl get secret argocd-initial-admin-secret -n argocd \
  -o jsonpath='{.data.password}' | base64 -d; echo

# IMPORTANT: Scale down monitoring and MCP services — the backup VM cannot run
# the full stack. ELK alone needs 1–2 GB JVM heap; all MCPs + heartbeat add up.
# elastic-agent runs as a DaemonSet — disable it via nodeSelector, not replicas.
kubectl patch daemonset elastic-agent -n shift-festival \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"non-existing":"true"}}}}}'

# Scale down ELK stack.
# NOTE: Elasticsearch is a StatefulSet named "elasticsearch", NOT a Deployment.
#       Logstash and Kibana are Deployments named "logstash" and "kibana" (no "-deployment" suffix).
#       Using wrong names silently returns "not found" and leaves ELK running,
#       consuming ~1-2 GB RAM and leaving too little memory for business pods (kassa-web goes Pending).
kubectl scale statefulset elasticsearch -n shift-festival --replicas=0
kubectl scale deployment logstash kibana -n shift-festival --replicas=0

# Scale down Rollouts (MCP servers, monitoring, chatbot sidecars, and detector — not needed in DR).
# detector is included because its init container waits for Elasticsearch; if Elasticsearch is at
# 0 replicas, detector will be stuck in Init:0/1 forever.
for name in heartbeat-monitor monitoring-agent monitoring-mcp \
            kassa-mcp crm-mcp facturatie-mcp frontend-mcp chatbot-proxy detector; do
  kubectl patch rollout "$name" -n shift-festival \
    --type=merge -p='{"spec":{"replicas":0}}' 2>/dev/null || true
done

# IMPORTANT: The ingress-nginx-controller starts at 0 replicas on the backup VM.
# Without it, cloudflared cannot route any traffic — all hostnames return 502.
kubectl scale deployment ingress-nginx-controller -n shift-festival --replicas=1
kubectl rollout status deployment ingress-nginx-controller -n shift-festival

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

### Step 3.8 — Restart application pods and fix RabbitMQ queue conflicts

```bash
# Restart RabbitMQ so it loads the definitions secret (users, vhosts, queues).
# It may have started before the secret existed and is missing its configuration.
kubectl rollout restart deployment rabbitmq-broker -n shift-festival
kubectl rollout status deployment rabbitmq-broker -n shift-festival

# Wait for RabbitMQ to finish loading its definitions before flushing queues.
# rollout status returns as soon as the pod is ready, but RabbitMQ needs a few
# more seconds to import the definitions file. Running exec too early hits the
# old (terminating) pod and fails with "task not found".
kubectl exec -n shift-festival deployment/rabbitmq-broker -- \
  rabbitmqctl await_startup

# FIX: RabbitMQ queue argument mismatch after definitions restore.
#
# Root cause: the definitions backup stores queue configurations as they existed
# on the primary VM (e.g. with or without x-dead-letter-exchange). When services
# restart and try to redeclare queues with different arguments, RabbitMQ rejects
# the redeclaration with PRECONDITION_FAILED 406. This affects multiple queues
# across teams (crm.incoming, kassa.payments, etc.).
#
# Fix: delete ALL queues after RabbitMQ loads its definitions. The users, vhosts,
# and permissions from the definitions are preserved — only the queue objects are
# removed. Services recreate their queues with the correct arguments on startup.
RMQUSER=$(kubectl get secret shift-secrets -n shift-festival \
  -o jsonpath='{.data.RABBITMQ_DEFAULT_USER}' | base64 -d)
RMQPASS=$(kubectl get secret shift-secrets -n shift-festival \
  -o jsonpath='{.data.RABBITMQ_DEFAULT_PASS}' | base64 -d)

kubectl exec -n shift-festival deployment/rabbitmq-broker -- \
  bash -c "rabbitmqadmin -H localhost -u '$RMQUSER' -p '$RMQPASS' \
  list queues name -f tsv | tail -n +2 | \
  while read q; do rabbitmqadmin -H localhost -u '$RMQUSER' -p '$RMQPASS' \
  delete queue name=\"\$q\"; done"
unset RMQUSER RMQPASS

# After RabbitMQ is back up, restart all application deployments.
# Services recreate their queues with the correct arguments on startup.
kubectl rollout restart deployment -n shift-festival
```

### Step 3.9 — Update Cloudflare Tunnel (only if a new tunnel token is needed)

The tunnel token was already restored from the GPG backup in Step 3.5. Skip this step unless
the backup VM requires a **different** Cloudflare tunnel token (e.g., the old tunnel was deleted
and a new one was created in the Cloudflare dashboard).

```bash
# Replace the tunnel token with a new one (do NOT use nano — non-interactive only).
NEW_CF_TOKEN="<paste-new-token-here>"
sed -i "s|^CLOUDFLARE_TUNNEL_TOKEN=.*|CLOUDFLARE_TUNNEL_TOKEN=${NEW_CF_TOKEN}|" base/setup/.env

# Re-create the cloudflare-tunnel-secret and restart cloudflared.
kubectl delete secret cloudflare-tunnel-secret -n shift-festival --ignore-not-found
kubectl create secret generic cloudflare-tunnel-secret \
  --from-literal=CLOUDFLARE_TUNNEL_TOKEN="$NEW_CF_TOKEN" \
  -n shift-festival
kubectl rollout restart deployment cloudflared -n shift-festival
unset NEW_CF_TOKEN
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

# 4. Decrypt secrets (GPG_PASSPHRASE = the GitHub Actions secret of the same name)
read -s -p "GPG passphrase: " GPG_PASS; echo
gpg --batch --passphrase "$GPG_PASS" --decrypt ~/secrets/shift-festival.env.gpg > base/setup/.env

# 5. Apply secrets and deploy
kubectl create namespace shift-festival
bash scripts/create-secret.sh base/setup/.env shift-festival

# Restore RabbitMQ definitions secret (required for RabbitMQ to start)
gpg --batch --passphrase "$GPG_PASS" --decrypt ~/secrets/rabbitmq-definitions.gpg \
  | kubectl create secret generic rabbitmq-definitions \
      --from-file=definitions.json=/dev/stdin \
      -n shift-festival

# Restore Cloudflare tunnel secret (token is set via CI, NOT stored in .env)
CF_TOKEN=$(gpg --batch --passphrase "$GPG_PASS" --decrypt ~/secrets/cloudflare-tunnel.gpg)
kubectl create secret generic cloudflare-tunnel-secret \
  --from-literal=CLOUDFLARE_TUNNEL_TOKEN="$CF_TOKEN" \
  -n shift-festival
unset CF_TOKEN GPG_PASS

# Install Argo Rollouts controller (must exist before ArgoCD syncs Rollout resources)
kubectl apply -k argocd/rollouts/

kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=180s
kubectl apply -k argocd/

# 6. Raise inotify limits (default 128 is too low for k3s with many pods)
sudo sysctl -w fs.inotify.max_user_instances=512
sudo sysctl -w fs.inotify.max_user_watches=524288

# Wait for ArgoCD to sync (usually 1–2 minutes after step 5)
kubectl get application shift-festival-prod -n argocd
# Target: SYNC STATUS = Synced, HEALTH STATUS = Healthy
#
# If SYNC STATUS stays "Unknown" after 2 minutes, unblock with a direct apply:
kubectl apply -k .

# 6b. Disable auto-sync immediately to prevent ArgoCD from reverting scale-downs
kubectl patch application shift-festival-prod -n argocd --type=merge \
  -p='{"spec":{"syncPolicy":null}}'

# 6c. Scale down monitoring and non-essential services (backup VM is undersized)
kubectl patch daemonset elastic-agent -n shift-festival \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"non-existing":"true"}}}}}'
# Elasticsearch is a StatefulSet named "elasticsearch" (NOT a Deployment; no "-deployment" suffix)
kubectl scale statefulset elasticsearch -n shift-festival --replicas=0
kubectl scale deployment logstash kibana -n shift-festival --replicas=0
# detector has an init container that waits for Elasticsearch; scale it to 0 or it hangs forever
for name in heartbeat-monitor monitoring-agent monitoring-mcp \
            kassa-mcp crm-mcp facturatie-mcp frontend-mcp chatbot-proxy detector; do
  kubectl patch rollout "$name" -n shift-festival \
    --type=merge -p='{"spec":{"replicas":0}}' 2>/dev/null || true
done

# 6d. The ingress-nginx-controller starts at 0 replicas — scale it up.
# Without it, all hostnames return 502 (cloudflared can't reach any service).
kubectl scale deployment ingress-nginx-controller -n shift-festival --replicas=1
kubectl rollout status deployment ingress-nginx-controller -n shift-festival

# 7. Watch database pods come up (required before restore)
kubectl get pods -n shift-festival -l 'app in (postgredb,kassa-db,chatbot-db,frontend-db,facturatie-db,crm-db)'
# Wait until all show Running — this can take 3–5 minutes while images pull.

# 7b. Restart RabbitMQ so it loads the definitions secret, then restart dependents
kubectl rollout restart deployment rabbitmq-broker -n shift-festival
kubectl rollout status deployment rabbitmq-broker -n shift-festival

# 8. Restore databases (use most recent backup date)
ls ~/backups/databases/       # pick a date
bash scripts/restore-databases.sh <date> ~/backups/databases/<date>

# 9. Cleanup after test
kubectl delete namespace shift-festival
/usr/local/bin/k3s-uninstall.sh
rm -rf ~/Infra-test
```

Expected result: all *-db pods Running, databases restored, cloudflared Running, frontend reachable. ELK is intentionally scaled to 0 (insufficient CPU on backup VM).

> **Note on missing keys in shift-secrets — `CreateContainerConfigError`:** If a pod shows `CreateContainerConfigError` and events say `couldn't find key X in Secret shift-festival/shift-secrets`, the root cause is almost always that the key has an **empty value** in `.env` (e.g. `ADMIN_CREDENTIALS=` with nothing after the `=`). `kubectl create secret generic --from-env-file` silently skips keys with empty values — they do not appear in the secret at all.
>
> Known affected pods:
> - `chatbot` — needs `ADMIN_CREDENTIALS` to have a non-empty value
> - `facturatie-connector` — needs `BILLING_WEB_URL` to have a non-empty value  
> - `kibana` — needs `XPACK_ENCRYPTEDSAVEDOBJECTS_ENCRYPTIONKEY` (less critical; kibana is not running in DR mode)
>
> Fix: patch the secret manually with the correct value, then restart the pod:
> ```bash
> kubectl patch secret shift-secrets -n shift-festival --type=merge \
>   -p='{"stringData":{"ADMIN_CREDENTIALS":"<value>","BILLING_WEB_URL":"https://facturatie.desiderius.me"}}'
> kubectl rollout restart rollout/chatbot rollout/facturatie-connector -n shift-festival
> ```

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
# k3s uses /run/k3s/containerd/containerd.sock and the k8s.io namespace
for tar in ~/backups/images/*.tar; do
  echo "Importing $tar"
  sudo ctr --address /run/k3s/containerd/containerd.sock --namespace k8s.io images import "$tar"
done
sudo ctr --address /run/k3s/containerd/containerd.sock --namespace k8s.io images list | grep ghcr.io   # verify

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
read -s -p "GPG passphrase: " GPG_PASS; echo
gpg --batch --passphrase "$GPG_PASS" --decrypt ~/secrets/shift-festival.env.gpg > base/setup/.env
kubectl create namespace shift-festival
bash scripts/create-secret.sh base/setup/.env shift-festival
gpg --batch --passphrase "$GPG_PASS" --decrypt ~/secrets/rabbitmq-definitions.gpg \
  | kubectl create secret generic rabbitmq-definitions \
      --from-file=definitions.json=/dev/stdin -n shift-festival
CF_TOKEN=$(gpg --batch --passphrase "$GPG_PASS" --decrypt ~/secrets/cloudflare-tunnel.gpg)
kubectl create secret generic cloudflare-tunnel-secret \
  --from-literal=CLOUDFLARE_TUNNEL_TOKEN="$CF_TOKEN" -n shift-festival
unset CF_TOKEN GPG_PASS
kubectl apply -k argocd/rollouts/
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

## Scenario C — Primary VM still running but databases are corrupted

Use this when the primary Kubernetes cluster is healthy but one or more databases contain corrupted or incorrect data and need to be rolled back to a backup point.

> **Warning:** This overwrites live database contents. All data written after the backup date is permanently lost.

```bash
# SSH into the primary VM
ssh -p 60022 ehbstudent@<primary-vm-ip>

# Go to the Infra repo (deploy.yml keeps it synced here)
cd ~/shiftfestival

# 1. Scale down all application pods that write to the databases.
#    This prevents new writes while the restore is in progress.
#    Leave the *-db pods running — they need to be up for the restore.
kubectl scale deployment frontend-drupal fossbilling-app kassa-web \
  integration-crm integration-planning identity-service chatbot \
  facturatie-connector mailing-service \
  -n shift-festival --replicas=0 2>/dev/null || true

# 2. List available backup dates and pick the most recent good one
ls ~/backups/databases/
# Example output: 2026-05-23  2026-05-24  2026-05-25

# 3. Restore — the script downloads from the backup VM automatically
#    if no local directory is given
export BACKUP_VM_USER=groep1
export BACKUP_VM_HOST=integration.switzerlandnorth.cloudapp.azure.com
export BACKUP_VM_KEY=$HOME/.ssh/backup_key
bash scripts/restore-databases.sh 2026-05-24
# Or restore from a local copy already on the VM:
# bash scripts/restore-databases.sh 2026-05-24 ~/backups/databases/2026-05-24

# 4. Restart application pods
kubectl rollout restart deployment -n shift-festival
```

**Partial restore** — if only one database is affected, edit `restore-databases.sh` temporarily or restore a single database manually. Example for the CRM MySQL database only:

```bash
LOCAL_DIR=~/backups/databases/2026-05-24

db_user=$(kubectl get secret shift-secrets -n shift-festival \
  -o jsonpath='{.data.MYSQL_USER}' | base64 -d)
db_pass=$(kubectl get secret shift-secrets -n shift-festival \
  -o jsonpath='{.data.MYSQL_PASSWORD}' | base64 -d)
db_name=$(kubectl get secret shift-secrets -n shift-festival \
  -o jsonpath='{.data.MYSQL_DATABASE}' | base64 -d)
pod=$(kubectl get pod -n shift-festival -l app=crm-db \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')

gunzip -c "$LOCAL_DIR/crm-mysql.sql.gz" | \
  kubectl exec -i -n shift-festival "$pod" -- mysql -u "$db_user" -p"$db_pass" "$db_name"
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
□ git clone https://github.com/IntegrationProject-Groep1/Infra.git ~/Infra && cd ~/Infra
□ Install k3s (curl -sfL https://get.k3s.io | sh -) + sudo chmod 644 /etc/rancher/k3s/k3s.yaml
□ read -s -p "GPG passphrase: " GPG_PASS; echo
□ Decrypt .env: gpg --batch --passphrase "$GPG_PASS" --decrypt ~/secrets/shift-festival.env.gpg > base/setup/.env
□ Apply shift-secrets: kubectl create namespace shift-festival && bash scripts/create-secret.sh base/setup/.env shift-festival
□ Restore rabbitmq-definitions: gpg --batch --passphrase "$GPG_PASS" --decrypt ~/secrets/rabbitmq-definitions.gpg | kubectl create secret generic rabbitmq-definitions --from-file=definitions.json=/dev/stdin -n shift-festival
□ Restore cloudflare-tunnel-secret: CF_TOKEN=$(gpg --batch --passphrase "$GPG_PASS" --decrypt ~/secrets/cloudflare-tunnel.gpg) && kubectl create secret generic cloudflare-tunnel-secret --from-literal=CLOUDFLARE_TUNNEL_TOKEN="$CF_TOKEN" -n shift-festival && unset CF_TOKEN GPG_PASS
□ Install Argo Rollouts: kubectl apply -k argocd/rollouts/
□ Install ArgoCD + apply argocd/ manifests: kubectl create namespace argocd && kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml && kubectl apply -k argocd/
□ Disable ArgoCD auto-sync: kubectl patch application shift-festival-prod -n argocd --type=merge -p='{"spec":{"syncPolicy":null}}'
□ Scale down ELK + detector: kubectl patch daemonset elastic-agent -n shift-festival -p '{"spec":{"template":{"spec":{"nodeSelector":{"non-existing":"true"}}}}}' && kubectl scale statefulset elasticsearch -n shift-festival --replicas=0 && kubectl scale deployment logstash kibana -n shift-festival --replicas=0 && for name in heartbeat-monitor monitoring-agent monitoring-mcp kassa-mcp crm-mcp facturatie-mcp frontend-mcp chatbot-proxy detector; do kubectl patch rollout "$name" -n shift-festival --type=merge -p='{"spec":{"replicas":0}}' 2>/dev/null || true; done
□ Wait for *-db pods to be Running
□ Restart RabbitMQ and wait for startup: kubectl rollout restart deployment rabbitmq-broker -n shift-festival && kubectl rollout status deployment rabbitmq-broker -n shift-festival && kubectl exec -n shift-festival deployment/rabbitmq-broker -- rabbitmqctl await_startup
□ Run restore: bash scripts/restore-databases.sh <date> ~/backups/databases/<date>
□ Restart apps: kubectl rollout restart deployment -n shift-festival
□ Verify tunnel: kubectl logs -n shift-festival -l app=cloudflared --tail=20
□ Verify: kubectl get pods -n shift-festival
```

**Scenario B — VM gone AND GitHub org deleted:**
```
□ SSH into backup VM
□ Import images: for tar in ~/backups/images/*.tar; do sudo ctr --address /run/k3s/containerd/containerd.sock --namespace k8s.io images import "$tar"; done
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
