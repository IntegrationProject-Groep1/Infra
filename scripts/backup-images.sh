#!/usr/bin/env bash
# Exports all custom GHCR images from the running k3s cluster and transfers
# them to the backup VM. Run after each deploy or on a schedule via backup.yml.
# Usage: ./scripts/backup-images.sh
# Required env vars: BACKUP_VM_USER, BACKUP_VM_HOST
# Optional env vars: BACKUP_VM_KEY (default: ~/.ssh/backup_key)
# Prerequisite: passwordless sudo for ctr on the primary VM — see docs/disaster-recovery.md Step 1.2
set -euo pipefail

NAMESPACE="shift-festival"
BACKUP_VM_USER="${BACKUP_VM_USER:?Set BACKUP_VM_USER}"
BACKUP_VM_HOST="${BACKUP_VM_HOST:?Set BACKUP_VM_HOST}"
BACKUP_VM_KEY="${BACKUP_VM_KEY:-$HOME/.ssh/backup_key}"
REMOTE_DIR="backups/images"
SSH_OPTS="-i $BACKUP_VM_KEY -o StrictHostKeyChecking=no -o BatchMode=yes"

log()  { echo "[$(date +%T)] $*"; }
fail() { echo "[$(date +%T)] ERROR: $*" >&2; exit 1; }

kubectl cluster-info --request-timeout=5s > /dev/null 2>&1 || fail "Cannot reach Kubernetes cluster"

# Use relative path — rsync resolves user@host:relative/path relative to remote home dir
ssh $SSH_OPTS "$BACKUP_VM_USER@$BACKUP_VM_HOST" "mkdir -p $REMOTE_DIR"

IMAGES=$(kubectl get pods -n "$NAMESPACE" \
  -o jsonpath='{range .items[*]}{range .spec.containers[*]}{.image}{"\n"}{end}{end}' \
  | grep 'ghcr.io/integrationproject' | sort -u)

if [[ -z "$IMAGES" ]]; then
  log "No custom GHCR images found in namespace $NAMESPACE — nothing to export"
  exit 0
fi

log "Images to export:"
echo "$IMAGES" | sed 's/^/  /'

for image in $IMAGES; do
  name=$(echo "$image" | tr '/:@' '_')
  tmp="/tmp/${name}.tar"

  log "Pulling $image"
  # Use absolute path so the sudoers NOPASSWD rule matches regardless of PATH in the SSH session
  sudo -n /usr/bin/ctr images pull "$image" || fail "Failed to pull $image — ensure NOPASSWD is configured for ctr (see docs/disaster-recovery.md Step 1.2)"

  log "Exporting → $tmp"
  sudo -n /usr/bin/ctr images export "$tmp" "$image" || fail "Failed to export $image"

  log "Transferring to backup VM"
  rsync -az --progress -e "ssh $SSH_OPTS" \
    "$tmp" "$BACKUP_VM_USER@$BACKUP_VM_HOST:$REMOTE_DIR/${name}.tar"

  # Export creates the tar as root — remove with sudo
  sudo rm -f "$tmp"
  log "Done: $image"
done

log "=== Image backup complete ==="
log "Files available at $BACKUP_VM_USER@$BACKUP_VM_HOST:~/$REMOTE_DIR/"
