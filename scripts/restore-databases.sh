#!/usr/bin/env bash
# Restores ShiftFestival databases from a backup created by backup-databases.sh.
# Usage: ./scripts/restore-databases.sh <backup-date> [backup-source-dir]
#
# Examples:
#   ./scripts/restore-databases.sh 2026-05-22
#   ./scripts/restore-databases.sh 2026-05-22 /tmp/downloaded-backup
#
# If backup-source-dir is omitted the script downloads from the backup VM.
# Required env vars (when downloading): BACKUP_VM_USER, BACKUP_VM_HOST
set -euo pipefail

BACKUP_DATE="${1:?Usage: $0 <backup-date> [backup-source-dir]  e.g. $0 2026-05-22}"
LOCAL_DIR="${2:-}"

NAMESPACE="shift-festival"
BACKUP_VM_USER="${BACKUP_VM_USER:-}"
BACKUP_VM_HOST="${BACKUP_VM_HOST:-}"
BACKUP_VM_KEY="${BACKUP_VM_KEY:-$HOME/.ssh/backup_key}"
SSH_OPTS="-i $BACKUP_VM_KEY -o StrictHostKeyChecking=no -o BatchMode=yes"

log()  { echo "[$(date +%T)] $*"; }
fail() { echo "[$(date +%T)] ERROR: $*" >&2; exit 1; }
confirm() {
  read -r -p "$1 [y/N] " ans
  [[ "$ans" =~ ^[yY]$ ]] || { log "Aborted."; exit 0; }
}

# ──────────────────────────────────────────────
# Download backup from backup VM if no local dir given
# ──────────────────────────────────────────────
if [[ -z "$LOCAL_DIR" ]]; then
  [[ -n "$BACKUP_VM_USER" && -n "$BACKUP_VM_HOST" ]] || \
    fail "Set BACKUP_VM_USER and BACKUP_VM_HOST, or pass a local backup directory as \$2"
  LOCAL_DIR="/tmp/sf-restore-$BACKUP_DATE"
  mkdir -p "$LOCAL_DIR"
  log "Downloading backup $BACKUP_DATE from $BACKUP_VM_USER@$BACKUP_VM_HOST …"
  rsync -az -e "ssh $SSH_OPTS" \
    "$BACKUP_VM_USER@$BACKUP_VM_HOST:~/backups/databases/$BACKUP_DATE/" \
    "$LOCAL_DIR/"
fi

[[ -d "$LOCAL_DIR" ]] || fail "Backup directory not found: $LOCAL_DIR"

log "Backup files found:"
ls -lh "$LOCAL_DIR"/*.sql.gz 2>/dev/null || fail "No .sql.gz files in $LOCAL_DIR"

confirm "
WARNING: This will OVERWRITE the current database contents with the $BACKUP_DATE backup.
All data newer than $BACKUP_DATE will be lost. Are you sure?"

# Verify cluster is reachable
kubectl cluster-info --request-timeout=5s > /dev/null 2>&1 || fail "Cannot reach Kubernetes cluster"

# ──────────────────────────────────────────────
# Helper: find the first running pod by app label
# ──────────────────────────────────────────────
get_pod() {
  local app="$1"
  kubectl get pod -n "$NAMESPACE" -l "app=$app" \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null \
    || fail "No running pod found for app=$app — is the cluster up?"
}

# ──────────────────────────────────────────────
# PostgreSQL restore
# ──────────────────────────────────────────────
restore_postgres() {
  local app="$1" dump_file="$2"
  [[ -f "$LOCAL_DIR/$dump_file" ]] || { log "  SKIP: $dump_file not found"; return; }
  local pod
  pod=$(get_pod "$app")
  log "  Restoring PostgreSQL: $app ($pod) ← $dump_file"
  gunzip -c "$LOCAL_DIR/$dump_file" | kubectl exec -i -n "$NAMESPACE" "$pod" -- sh -c \
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" "${POSTGRES_DB:-$POSTGRES_USER}"' \
    || fail "psql restore failed for $app"
}

# ──────────────────────────────────────────────
# MariaDB / MySQL restore
# ──────────────────────────────────────────────
restore_mysql() {
  local app="$1" dump_file="$2"
  [[ -f "$LOCAL_DIR/$dump_file" ]] || { log "  SKIP: $dump_file not found"; return; }
  local pod
  pod=$(get_pod "$app")
  log "  Restoring MariaDB/MySQL: $app ($pod) ← $dump_file"
  gunzip -c "$LOCAL_DIR/$dump_file" | kubectl exec -i -n "$NAMESPACE" "$pod" -- sh -c \
    'mysql -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' \
    || fail "mysql restore failed for $app"
}

# ──────────────────────────────────────────────
# Run all restores
# ──────────────────────────────────────────────
log "=== Restoring PostgreSQL databases ==="
restore_postgres "postgredb"    "central-postgres.sql.gz"
restore_postgres "kassa-db"     "kassa-postgres.sql.gz"
restore_postgres "planning-db"  "planning-postgres.sql.gz"

log "=== Restoring MariaDB / MySQL databases ==="
restore_mysql "frontend-db"    "frontend-mariadb.sql.gz"
restore_mysql "facturatie-db"  "facturatie-mariadb.sql.gz"
restore_mysql "crm-db"         "crm-mysql.sql.gz"

log "=== Restore complete ==="
log "Restart application pods to pick up the restored data:"
log "  kubectl rollout restart deployment -n $NAMESPACE"
log "  kubectl argo rollouts restart <rollout-name> -n $NAMESPACE"
