#!/usr/bin/env bash
# =============================================================================
# create-secret.sh
# ShiftFestival — Infra Team
#
# PURPOSE:
#   Creates or updates the shift-secrets Kubernetes Secret from a .env file.
#   Safe to run multiple times — uses the --dry-run=client | kubectl apply
#   pattern so it acts as an upsert: creates on first run, updates in place
#   on subsequent runs without failing with "already exists".
#
# USAGE:
#   ./scripts/create-secret.sh <env-file> <namespace>
#
# EXAMPLES:
#   ./scripts/create-secret.sh base/setup/.env shift-festival
#   ./scripts/create-secret.sh base/setup/.env shift-festival-dev
#
# WHEN TO RUN:
#   - Initial cluster bootstrap (before ArgoCD first sync)
#   - Any time base/setup/.env changes (new credential or rotated password)
#   - During disaster recovery (see docs/disaster-recovery.md Step 2)
#
# NOTE: This script manages shift-secrets only. The cloudflare-tunnel-secret
#   must be created separately — see docs/disaster-recovery.md.
# =============================================================================
set -euo pipefail

ENV_FILE="${1:?usage: $0 <env-file> <namespace>}"
NAMESPACE="${2:?usage: $0 <env-file> <namespace>}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: env file '$ENV_FILE' not found" >&2
  exit 1
fi

# --dry-run=client -o yaml generates the Secret manifest locally without
# contacting the API server, then kubectl apply -f - performs a server-side
# merge. This is idempotent: the secret is created if absent, or updated in
# place if it already exists — no "AlreadyExists" error on re-runs.
kubectl create secret generic shift-secrets \
  --from-env-file="$ENV_FILE" \
  --namespace="$NAMESPACE" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "shift-secrets created/updated in namespace $NAMESPACE"
