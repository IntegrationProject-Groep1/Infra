#!/usr/bin/env bash
# Backs up all ShiftFestival databases and syncs them to the backup VM.
# Run via cron on the primary VM (see docs/disaster-recovery.md for setup).
# Usage: ./scripts/backup-databases.sh
# Required env vars: BACKUP_VM_USER, BACKUP_VM_HOST
# Optional env vars: BACKUP_VM_KEY (default: ~/.ssh/backup_key), KEEP_DAYS (default: 14)
set -euo pipefail

NAMESPACE="shift-festival"
BACKUP_VM_USER="${BACKUP_VM_USER:?Set BACKUP_VM_USER (e.g. export BACKUP_VM_USER=ubuntu)}"
BACKUP_VM_HOST="${BACKUP_VM_HOST:?Set BACKUP_VM_HOST (e.g. export BACKUP_VM_HOST=10.0.0.5)}"
BACKUP_VM_KEY="${BACKUP_VM_KEY:-$HOME/.ssh/backup_key}"
KEEP_DAYS="${KEEP_DAYS:-14}"

TODAY=$(date +%Y-%m-%d)
LOCAL_DIR="/tmp/sf-backup-$TODAY"
REMOTE_DIR="$HOME/backups/databases/$TODAY"
SSH_OPTS="-i $BACKUP_VM_KEY -o StrictHostKeyChecking=no -o BatchMode=yes"

log()  { echo "[$(date +%T)] $*"; }
fail() { echo "[$(date +%T)] ERROR: $*" >&2; exit 1; }

# Verify kubectl is available and cluster is reachable
kubectl cluster-info --request-timeout=5s > /dev/null 2>&1 || fail "Cannot reach Kubernetes cluster"

mkdir -p "$LOCAL_DIR"
log "Starting backup → $LOCAL_DIR"

# ──────────────────────────────────────────────
# Helper: find the first running pod by app label
# ──────────────────────────────────────────────
get_pod() {
  local app="$1"
  kubectl get pod -n "$NAMESPACE" -l "app=$app" \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null \
    || fail "No running pod found for app=$app"
}

# ──────────────────────────────────────────────
# PostgreSQL dump
# Reads POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB from the pod's env.
# POSTGRES_DB falls back to POSTGRES_USER if not set (Docker default).
# ──────────────────────────────────────────────
dump_postgres() {
  local app="$1" out_name="$2"
  local pod
  pod=$(get_pod "$app")
  log "  pg_dump: $app ($pod) → $out_name"
  kubectl exec -n "$NAMESPACE" "$pod" -- sh -c \
    'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" "${POSTGRES_DB:-$POSTGRES_USER}" --no-password' \
    | gzip > "$LOCAL_DIR/$out_name" \
    || fail "pg_dump failed for $app"
}

# ──────────────────────────────────────────────
# MariaDB / MySQL dump
# Reads MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE from the pod's env.
# ──────────────────────────────────────────────
dump_mysql() {
  local app="$1" out_name="$2"
  local pod
  pod=$(get_pod "$app")
  log "  mysqldump: $app ($pod) → $out_name"
  kubectl exec -n "$NAMESPACE" "$pod" -- sh -c \
    'mysqldump -u "$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" --single-transaction --routines' \
    | gzip > "$LOCAL_DIR/$out_name" \
    || fail "mysqldump failed for $app"
}

# ──────────────────────────────────────────────
# Run all database dumps
# ──────────────────────────────────────────────
log "=== PostgreSQL databases ==="
dump_postgres "postgredb"    "central-postgres.sql.gz"
dump_postgres "kassa-db"     "kassa-postgres.sql.gz"
dump_postgres "planning-db"  "planning-postgres.sql.gz"

log "=== MariaDB / MySQL databases ==="
dump_mysql "frontend-db"    "frontend-mariadb.sql.gz"
dump_mysql "facturatie-db"  "facturatie-mariadb.sql.gz"
dump_mysql "crm-db"         "crm-mysql.sql.gz"

# ──────────────────────────────────────────────
# Transfer to backup VM
# ──────────────────────────────────────────────
log "=== Transferring to backup VM ==="
# shellcheck disable=SC2029
ssh $SSH_OPTS "$BACKUP_VM_USER@$BACKUP_VM_HOST" "mkdir -p $REMOTE_DIR"
rsync -az --progress -e "ssh $SSH_OPTS" \
  "$LOCAL_DIR/" "$BACKUP_VM_USER@$BACKUP_VM_HOST:$REMOTE_DIR/"

log "=== Cleaning up backup VM (keeping last $KEEP_DAYS days) ==="
# shellcheck disable=SC2029
ssh $SSH_OPTS "$BACKUP_VM_USER@$BACKUP_VM_HOST" \
  "find ~/backups/databases -maxdepth 1 -mindepth 1 -type d -mtime +$KEEP_DAYS -exec rm -rf {} +"

log "=== Cleaning up local temp dir ==="
rm -rf "$LOCAL_DIR"

log "=== Backup complete: $TODAY ==="
log "Files available at $BACKUP_VM_USER@$BACKUP_VM_HOST:~/backups/databases/$TODAY/"
