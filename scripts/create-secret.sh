#!/usr/bin/env bash
# Creates the shift-secrets Kubernetes Secret from a .env file.
# Usage: ./scripts/create-secret.sh <env-file> <namespace>
# Example: ./scripts/create-secret.sh setup/.env shift-festival
#          ./scripts/create-secret.sh setup/.env shift-festival-dev
set -euo pipefail

ENV_FILE="${1:?usage: $0 <env-file> <namespace>}"
NAMESPACE="${2:?usage: $0 <env-file> <namespace>}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: env file '$ENV_FILE' not found" >&2
  exit 1
fi

kubectl create secret generic shift-secrets \
  --from-env-file="$ENV_FILE" \
  --namespace="$NAMESPACE" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "shift-secrets created/updated in namespace $NAMESPACE"
